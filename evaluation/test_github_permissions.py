"""Five independent public-contract workflows with real isolated browser sessions."""

import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Locator, Page, expect
from test_github_requirements import create_file, register_and_sign_in, sign_in


@dataclass
class Participant:
    page: Page
    username: str
    email: str
    role: str


@dataclass
class Collaboration:
    owner: Page
    organization: str
    organization_url: str
    repository: str
    repository_url: str
    participants: dict[str, Participant]


def choose(page: Page | Locator, label: str, option: str) -> None:
    control = page.get_by_role("combobox", name=label, exact=True)
    if control.evaluate("element => element.tagName") == "SELECT":
        control.select_option(label=option)
    else:
        control.click()
        page.get_by_role("option", name=option, exact=True).click()


def unavailable(control: Locator) -> None:
    if control.count():
        expect(control).to_be_disabled()
    else:
        expect(control).to_have_count(0)


def choose_owner(page: Page, organization: str) -> None:
    control = page.get_by_label("Owner", exact=True)
    name = re.compile(rf"(?<!\w){re.escape(organization)}(?!\w)")
    if control.evaluate("element => element.tagName") == "SELECT":
        options = control.locator("option").filter(has_text=name)
        expect(options).to_have_count(1)
        control.select_option(label=options.inner_text())
    else:
        control.click()
        option = page.get_by_role("option", name=name).or_(page.get_by_role("button", name=name))
        option.filter(visible=True).click()


def new_repository(page: Page, application: str, organization: str | None = None) -> tuple[str, str]:
    page.goto(application)
    page.get_by_role("link", name="New repository", exact=True).click()
    if organization:
        choose_owner(page, organization)
    name = "independent-" + uuid.uuid4().hex[:12]
    page.get_by_label("Repository name", exact=True).fill(name)
    page.get_by_role("radio", name="Private", exact=True).check()
    page.get_by_role("checkbox", name="Add a README file", exact=True).check()
    page.get_by_role("button", name="Create repository", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile(re.escape(name))).first).to_be_visible()
    return name, page.url


def grant_access(owner: Page, repository_url: str, username: str, role: str) -> None:
    owner.goto(repository_url)
    owner.get_by_role("link", name="Settings", exact=True).click()
    owner.get_by_role("link", name="Manage access", exact=True).click()
    owner.get_by_role("button", name="Add people or teams", exact=True).click()
    owner.get_by_role("textbox", name="Search", exact=True).fill(username)
    owner.get_by_role("option", name=re.compile(rf"(?<!\w){re.escape(username)}(?!\w)")).click()
    picker = (
        owner.locator("form")
        .filter(has=owner.get_by_role("textbox", name="Search", exact=True))
        .filter(has=owner.get_by_role("button", name="Add", exact=True))
    )
    expect(picker).to_have_count(1)
    choose(picker, "Role", role)
    picker.get_by_role("button", name="Add", exact=True).click()
    row = owner.get_by_role("row", name=re.compile(re.escape(username)))
    expect(row).to_have_count(1)
    expect(row.get_by_role("combobox", name="Role", exact=True).locator("option:checked")).to_have_text(role)


def prepare_collaboration(page: Page, sessions, application: str, roles: dict[str, str]) -> Collaboration:
    register_and_sign_in(page, application)
    page.get_by_role("button", name="Account menu", exact=True).click()
    page.get_by_role("link", name="Your organizations", exact=True).click()
    page.get_by_role("link", name="New organization", exact=True).click()
    organization = "collaboration-" + uuid.uuid4().hex[:12]
    page.get_by_label("Organization name", exact=True).fill(organization)
    page.get_by_label("Display name", exact=True).fill(organization)
    page.get_by_role("button", name="Create organization", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile(re.escape(organization))).first).to_be_visible()
    organization_url = page.url
    participants = {}
    for label, role in roles.items():
        visitor = sessions.open(label)
        username, email = register_and_sign_in(visitor, application)
        participants[label] = Participant(visitor, username, email, role)
        page.goto(organization_url)
        page.get_by_role("link", name="People", exact=True).click()
        page.get_by_role("button", name="Add member", exact=True).click()
        page.get_by_label("Username or email", exact=True).fill(email)
        choose(page, "Role", "Member")
        page.get_by_role("button", name="Add member", exact=True).last.click()
        expect(page.get_by_text(username, exact=True)).to_have_count(1)
    repository, repository_url = new_repository(page, application, organization)
    for participant in participants.values():
        grant_access(page, repository_url, participant.username, participant.role)
        participant.page.goto(repository_url)
        expect(participant.page.get_by_role("heading", name=re.compile(re.escape(repository))).first).to_be_visible()
    return Collaboration(page, organization, organization_url, repository, repository_url, participants)


def create_issue(owner: Page, repository_url: str) -> tuple[str, str]:
    owner.goto(repository_url)
    owner.get_by_role("link", name="Issues", exact=True).click()
    owner.get_by_role("link", name="New issue", exact=True).click()
    title = "Permission check " + uuid.uuid4().hex[:12]
    owner.get_by_label("Title", exact=True).fill(title)
    owner.get_by_label("Description", exact=True).fill("Keep this description unchanged.")
    owner.get_by_role("button", name="Submit new issue", exact=True).click()
    expect(owner.get_by_role("heading", name=title, exact=True)).to_be_visible()
    return title, owner.url


def current_branch(page: Page) -> str:
    selector = page.get_by_role("button", name=re.compile(r"^Branch .+"))
    expect(selector).to_have_count(1)
    match = re.search(r'button "Branch ([^"]+)"', selector.aria_snapshot())
    assert match, "Current branch must be present in the selector's accessible name"
    return match.group(1)


@pytest.mark.requirements("REQ-1-1-2", "REQ-3-1", "REQ-4-2-3", "REQ-4-3-1", "REQ-5-1-1", "REQ-5-1-2")
def test_github_full_seed_objects_survive_independent_sessions(page, sessions, application):
    """Verify only explicitly named seed state; do not invent another seeded account's password."""
    page.goto(application)
    search = page.get_by_role("searchbox", name="Search", exact=True)
    search.fill("acme-docs")
    search.press("Enter")
    page.get_by_role("link", name="acme-docs", exact=True).click()
    repository_url = page.url
    page.get_by_role("link", name="Code", exact=True).click()
    page.get_by_role("searchbox", name="Search", exact=True).fill("search flow")
    page.get_by_role("searchbox", name="Search", exact=True).press("Enter")
    page.get_by_role("link", name="Code", exact=True).click()
    page.get_by_role("link", name="README.md", exact=True).click()
    expect(page.get_by_text("search flow", exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_text("search flow", exact=True).first).to_be_visible()
    page.goto(repository_url)
    page.get_by_role("link", name="Issues", exact=True).click()
    page.get_by_role("link", name="Open", exact=True).click()
    page.get_by_role("searchbox", name="Search issues", exact=True).fill("Improve onboarding")
    page.get_by_role("link", name="Improve onboarding", exact=True).click()
    expect(page.get_by_text("Describe the onboarding improvement.", exact=True).first).to_be_visible()
    visitor = sessions.open("visitor")
    visitor.goto(application)
    visitor.get_by_role("searchbox", name="Search", exact=True).fill("secret-research")
    visitor.get_by_role("searchbox", name="Search", exact=True).press("Enter")
    expect(visitor.get_by_text("No results", exact=True).first).to_be_visible()
    expect(visitor.get_by_role("link", name="secret-research", exact=True)).to_have_count(0)
    signed_in = sessions.open("alice")
    sign_in(signed_in, application, "alice.dev@example.test")
    expect(signed_in.get_by_text("alice-dev", exact=True).first).to_be_visible()
    signed_in.reload()
    expect(signed_in.get_by_text("alice-dev", exact=True).first).to_be_visible()
    visitor.reload()
    expect(visitor.get_by_role("link", name="Sign in", exact=True).first).to_be_visible()


@pytest.mark.requirements(
    "REQ-1-1-1",
    "REQ-1-1-2",
    "REQ-2-1-2",
    "REQ-2-2-3",
    "REQ-2-3",
    "REQ-3-2-1",
    "REQ-5-1-2",
    "REQ-5-2-1",
    "REQ-5-2-2",
    "REQ-5-2-3",
    "REQ-5-3-1",
    "REQ-5-3-2",
    "REQ-5-3-3",
    "REQ-5-4",
)
def test_github_full_issue_roles_separate_writing_from_triage(page, sessions, application):
    collaboration = prepare_collaboration(
        page, sessions, application, {"reader": "Read", "triage": "Triage", "writer": "Write"}
    )
    title, issue_url = create_issue(page, collaboration.repository_url)
    for label, participant in collaboration.participants.items():
        visitor = participant.page
        visitor.goto(issue_url)
        expect(visitor.get_by_role("heading", name=title, exact=True)).to_be_visible()
        expect(visitor.get_by_text("Keep this description unchanged.", exact=True).first).to_be_visible()
        for name in ["Edit issue title", "Edit issue description", "Comment"]:
            button = visitor.get_by_role("button", name=name, exact=True)
            if label == "writer":
                expect(button).to_be_visible()
            else:
                unavailable(button)
        for name in ["Assignees", "Labels", "Milestone", "Close issue"]:
            button = visitor.get_by_role("button", name=name, exact=True)
            if label == "triage":
                expect(button).to_be_enabled()
            else:
                unavailable(button)
    writer = collaboration.participants["writer"].page
    writer.get_by_role("button", name="Edit issue title", exact=True).click()
    writer.get_by_label("Issue title", exact=True).fill(title + " updated")
    writer.get_by_role("button", name="Save issue title", exact=True).click()
    expect(writer.get_by_role("heading", name=title + " updated", exact=True)).to_be_visible()
    triage = collaboration.participants["triage"].page
    triage.reload()
    triage.get_by_role("button", name="Close issue", exact=True).click()
    expect(triage.get_by_text("Closed", exact=True).first).to_be_visible()
    writer.reload()
    unavailable(writer.get_by_role("button", name="Reopen issue", exact=True))
    expect(writer.get_by_text("Keep this description unchanged.", exact=True).first).to_be_visible()
    triage.get_by_role("button", name="Reopen issue", exact=True).click()
    expect(triage.get_by_role("button", name="Close issue", exact=True)).to_be_enabled()
    triage.reload()
    expect(triage.get_by_role("heading", name=title + " updated", exact=True)).to_be_visible()
    expect(triage.get_by_role("button", name="Close issue", exact=True)).to_be_enabled()


@pytest.mark.requirements(
    "REQ-1-1-1", "REQ-1-1-2", "REQ-2-1-2", "REQ-2-2-3", "REQ-2-3", "REQ-3-2-1", "REQ-5-2-1", "REQ-5-2-2"
)
def test_github_full_role_downgrade_revokes_stale_editing(page, sessions, application):
    collaboration = prepare_collaboration(page, sessions, application, {"writer": "Write"})
    title, issue_url = create_issue(page, collaboration.repository_url)
    writer = collaboration.participants["writer"]
    writer.page.goto(issue_url)
    writer.page.get_by_role("button", name="Edit issue title", exact=True).click()
    writer.page.get_by_label("Issue title", exact=True).fill("This stale write must not be saved")
    page.goto(collaboration.repository_url)
    page.get_by_role("link", name="Settings", exact=True).click()
    page.get_by_role("link", name="Manage access", exact=True).click()
    row = page.get_by_role("row", name=re.compile(re.escape(writer.username)))
    expect(row).to_have_count(1)
    row.get_by_role("combobox", name="Role", exact=True).select_option(label="Read")
    origin = urlsplit(page.url)[:2]
    with page.expect_response(
        lambda response: (
            response.request.method in {"POST", "PUT", "PATCH", "DELETE"} and urlsplit(response.url)[:2] == origin
        )
    ) as saved_response:
        row.get_by_role("button", name="Save", exact=True).click()
    saved_response.value.finished()
    page.reload()
    expect(row).to_have_count(1)
    expect(row.get_by_role("combobox", name="Role", exact=True).locator("option:checked")).to_have_text("Read")
    save = writer.page.get_by_role("button", name="Save issue title", exact=True)
    if save.is_visible() and save.is_enabled():
        save.click()
    page.goto(issue_url)
    page.reload()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    writer.page.reload()
    expect(writer.page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    unavailable(writer.page.get_by_role("button", name="Edit issue title", exact=True))
    unavailable(writer.page.get_by_role("button", name="Comment", exact=True))


@pytest.mark.requirements("REQ-1-1-1", "REQ-1-1-2", "REQ-2-1-2", "REQ-2-2-3", "REQ-2-2-4", "REQ-2-3", "REQ-3-2-1")
def test_github_full_membership_removal_revokes_private_access_but_keeps_personal_data(page, sessions, application):
    collaboration = prepare_collaboration(page, sessions, application, {"member": "Write"})
    member = collaboration.participants["member"]
    personal_name, personal_url = new_repository(member.page, application)
    member.page.goto(collaboration.repository_url)
    expect(
        member.page.get_by_role("heading", name=re.compile(re.escape(collaboration.repository))).first
    ).to_be_visible()
    page.goto(collaboration.organization_url)
    page.get_by_role("link", name="People", exact=True).click()
    page.get_by_role("button", name="Member menu " + member.username, exact=True).click()
    page.get_by_role("menuitem", name="Remove from organization", exact=True).click()
    page.get_by_role("button", name="Remove", exact=True).click()
    expect(page.get_by_text(member.username, exact=True)).to_have_count(0)
    page.reload()
    expect(page.get_by_text(member.username, exact=True)).to_have_count(0)
    member.page.reload()
    expect(member.page.get_by_text("Access denied", exact=True).first).to_be_visible()
    member.page.goto(personal_url)
    expect(member.page.get_by_role("heading", name=re.compile(re.escape(personal_name))).first).to_be_visible()
    reopened = sessions.open("member-new-session")
    sign_in(reopened, application, member.email)
    expect(reopened.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
    reopened.goto(personal_url)
    expect(reopened.get_by_role("heading", name=re.compile(re.escape(personal_name))).first).to_be_visible()
    reopened.goto(collaboration.repository_url)
    expect(reopened.get_by_text("Access denied", exact=True).first).to_be_visible()


@pytest.mark.requirements(
    "REQ-1-1-1",
    "REQ-1-1-2",
    "REQ-2-1-2",
    "REQ-2-2-3",
    "REQ-2-3",
    "REQ-3-2-1",
    "REQ-4-3-1",
    "REQ-4-3-2",
    "REQ-4-4",
    "REQ-6-1",
    "REQ-6-2-2",
    "REQ-6-2-3",
    "REQ-6-3-2",
    "REQ-6-3-4",
    "REQ-6-5",
)
def test_github_full_protected_merge_requires_review_and_check_from_distinct_roles(page, sessions, application):
    collaboration = prepare_collaboration(page, sessions, application, {"reviewer": "Write", "maintainer": "Maintain"})
    page.goto(collaboration.repository_url)
    page.get_by_role("link", name="Code", exact=True).click()
    base = current_branch(page)
    branch = "review-" + uuid.uuid4().hex[:12]
    page.get_by_role("button", name="Branch " + base, exact=True).click()
    page.get_by_role("textbox", name="Find branch", exact=True).fill(branch)
    page.get_by_role("option", name="Create branch: " + branch, exact=True).click()
    expect(page.get_by_role("button", name="Branch " + branch, exact=True)).to_be_visible()
    filename = "protected-" + uuid.uuid4().hex[:12] + ".md"
    content = "Content merged only after review and checks."
    create_file(page, filename, content, "Add protected review example")
    page.goto(collaboration.repository_url)
    page.get_by_role("link", name="Settings", exact=True).click()
    page.get_by_role("link", name="Branches", exact=True).click()
    page.get_by_role("button", name="Add branch protection rule", exact=True).click()
    page.get_by_label("Branch name pattern", exact=True).fill(base)
    page.get_by_role("checkbox", name="Require 1 approval", exact=True).check()
    page.get_by_role("checkbox", name="Require status check test", exact=True).check()
    page.get_by_role("button", name="Create", exact=True).click()
    expect(page.get_by_text("1 approval", exact=True).first).to_be_visible()
    page.goto(collaboration.repository_url)
    page.get_by_role("link", name="Pull requests", exact=True).click()
    page.get_by_role("link", name="New pull request", exact=True).click()
    page.get_by_role("combobox", name="base", exact=True).select_option(label=base)
    page.get_by_role("combobox", name="compare", exact=True).select_option(label=branch)
    page.get_by_role("button", name="Compare changes", exact=True).click()
    page.get_by_role("button", name="Create pull request", exact=True).click()
    title = "Protected merge " + uuid.uuid4().hex[:12]
    page.get_by_label("Title", exact=True).fill(title)
    page.get_by_role("button", name="Create pull request", exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    pull_request_url = page.url
    maintainer = collaboration.participants["maintainer"].page
    maintainer.goto(pull_request_url)
    expect(maintainer.get_by_role("button", name="Merge pull request", exact=True)).to_be_disabled()
    expect(maintainer.get_by_text("Review required by branch protection", exact=True).first).to_be_visible()
    expect(page.get_by_text("test: pending", exact=True).first).to_be_visible()
    choose(page, "test status", "success")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("test: success", exact=True).first).to_be_visible()
    maintainer.reload()
    expect(maintainer.get_by_role("button", name="Merge pull request", exact=True)).to_be_disabled()
    reviewer = collaboration.participants["reviewer"].page
    reviewer.goto(pull_request_url)
    reviewer.get_by_role("link", name="Files changed", exact=True).click()
    reviewer.get_by_role("button", name="Review changes", exact=True).click()
    reviewer.get_by_role("radio", name="Approve", exact=True).check()
    reviewer.get_by_role("button", name="Submit review", exact=True).click()
    expect(reviewer.get_by_text("Approved", exact=True).first).to_be_visible()
    maintainer.reload()
    expect(maintainer.get_by_role("button", name="Merge pull request", exact=True)).to_be_enabled()
    page.goto(collaboration.repository_url)
    page.get_by_role("link", name="Code", exact=True).click()
    expect(page.get_by_role("link", name=filename, exact=True)).to_have_count(0)
    maintainer.get_by_role("button", name="Merge pull request", exact=True).click()
    maintainer.get_by_role("button", name="Confirm merge", exact=True).click()
    expect(maintainer.get_by_text("Merged", exact=True).first).to_be_visible()
    maintainer.reload()
    expect(maintainer.get_by_text("Merged", exact=True).first).to_be_visible()
    page.reload()
    page.get_by_role("link", name=filename, exact=True).click()
    expect(page.get_by_text(content, exact=True).first).to_be_visible()
