"""Public browser contracts for organization, fork, clone, diff and review workflows."""

import re
import uuid
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect
from test_github_access import open_new_repository, repository_identity, repository_result, repository_source_link
from test_github_boundaries import create_branch, open_comparison
from test_github_permissions import choose_owner, organization_entry, prepare_collaboration, visible_identity
from test_github_requirements import create_file, navigation_control, register_and_sign_in, sign_in


def open_public_repository(page: Page, application: str) -> str:
    page.goto(application)
    search = page.get_by_role("searchbox", name="Search", exact=True)
    search.fill("acme-docs")
    search.press("Enter")
    repository_result(page, "alice-dev", "acme-docs").click()
    expect(page.get_by_role("heading", name=repository_identity("alice-dev", "acme-docs"))).to_be_visible()
    return page.url


def filter_public_repositories(page: Page) -> None:
    select = (
        page.get_by_role("combobox")
        .and_(page.locator("select"))
        .filter(has=page.get_by_role("option", name="Public", exact=True))
    )
    radio = page.get_by_role("radio", name="Public", exact=True)
    control = navigation_control(page, "Public")
    if select.count():
        select.select_option(label="Public")
    elif radio.count():
        radio.check()
    elif control.count():
        control.click()
    else:
        comboboxes = page.get_by_role("combobox").filter(visible=True)
        expect(comboboxes).not_to_have_count(0)
        for combobox in comboboxes.all():
            combobox.click()
            option = page.get_by_role("option", name="Public", exact=True).filter(visible=True)
            if option.count():
                option.click()
                return
            page.keyboard.press("Escape")
        raise AssertionError("The repository visibility filter must offer Public")


def create_team(page: Page, organization_url: str, prefix: str) -> tuple[str, str]:
    page.goto(organization_url)
    page.get_by_role("link", name="Teams", exact=True).click()
    page.get_by_role("link", name="New team", exact=True).click()
    name = prefix + "-" + uuid.uuid4().hex[:12]
    page.get_by_label("Team name", exact=True).fill(name)
    page.get_by_role("button", name="Create team", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile(re.escape(name))).first).to_be_visible()
    return name, page.url


def set_parent(page: Page, team_url: str, parent: str) -> None:
    page.goto(team_url)
    page.get_by_role("link", name="Settings", exact=True).click()
    page.get_by_role("combobox", name="Parent team", exact=True).select_option(label=parent)
    with page.expect_response(
        lambda response: (
            response.request.method in {"POST", "PUT", "PATCH"} and urlsplit(response.url)[:2] == urlsplit(page.url)[:2]
        )
    ) as saved:
        page.get_by_role("button", name="Save", exact=True).click()
    saved.value.finished()
    page.reload()
    expect(page.get_by_role("combobox", name="Parent team", exact=True).locator("option:checked")).to_have_text(parent)


def create_pull_request(page: Page, repository_url: str) -> tuple[str, str, str]:
    page.goto(repository_url)
    page.get_by_role("link", name="Code", exact=True).click()
    base, branch = create_branch(page, "review")
    filename = "review-" + uuid.uuid4().hex[:12] + ".md"
    create_file(page, filename, "Review this added line.", "Add " + filename)
    open_comparison(page, repository_url, base, branch)
    expect(page.get_by_text(filename, exact=True).first).to_be_visible()
    page.get_by_role("button", name="Create pull request", exact=True).click()
    title = "Independent review " + uuid.uuid4().hex[:12]
    page.get_by_label("Title", exact=True).fill(title)
    page.get_by_role("button", name="Create pull request", exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    return title, page.url, filename


@pytest.mark.requirements("REQ-1-1-1", "REQ-1-1-2", "REQ-2-1-1", "REQ-2-1-2", "REQ-3-2-1", "REQ-3-3")
def test_github_full_organization_filter_never_exposes_private_repositories(page, sessions, application):
    seed_owner = sessions.open("seed-owner")
    sign_in(seed_owner, application, "alice.dev@example.test")
    seed_owner.get_by_role("button", name="Account menu", exact=True).click()
    seed_owner.get_by_role("link", name="Your organizations", exact=True).click()
    organization_entry(seed_owner, "Acme Demo").click()
    seed_owner.get_by_role("link", name="Repositories", exact=True).click()
    seed_owner.get_by_role("textbox", name="Find a repository", exact=True).fill("acme-docs")
    seed_repository = seed_owner.get_by_role("link", name="acme-docs", exact=True)
    expect(seed_repository).to_be_visible()
    seed_repository.click()
    expect(seed_owner.get_by_role("heading", name=re.compile(r"\/acme-docs\b"))).to_be_visible()
    organization_repository = seed_owner.url
    seed_owner.reload()
    expect(seed_owner.get_by_role("heading", name=re.compile(r"\/acme-docs\b"))).to_be_visible()
    assert open_public_repository(seed_owner, application) != organization_repository
    collaboration = prepare_collaboration(page, sessions, application, {})
    open_new_repository(page)
    choose_owner(page, collaboration.organization)
    public_name = "public-" + uuid.uuid4().hex[:12]
    page.get_by_label("Repository name", exact=True).fill(public_name)
    page.get_by_role("radio", name="Public", exact=True).check()
    page.get_by_role("checkbox", name="Add a README file", exact=True).check()
    page.get_by_role("button", name="Create repository", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile(re.escape(public_name))).first).to_be_visible()

    visitor = sessions.open("visitor")
    visitor.goto(collaboration.organization_url)
    visitor.get_by_role("link", name="Repositories", exact=True).click()
    search = visitor.get_by_role("textbox", name="Find a repository", exact=True)
    search.fill(public_name)
    filter_public_repositories(visitor)
    expect(visitor.get_by_role("link", name=public_name, exact=True)).to_be_visible()
    expect(visitor.get_by_role("link", name=collaboration.repository, exact=True)).to_have_count(0)
    visitor.get_by_role("link", name=public_name, exact=True).click()
    expect(
        visitor.get_by_role("heading", name=collaboration.organization + "/" + public_name, exact=True)
    ).to_be_visible()
    visitor.go_back()
    expect(visitor.get_by_role("link", name=public_name, exact=True)).to_be_visible()
    search.fill(collaboration.repository)
    expect(visitor.get_by_role("link", name=collaboration.repository, exact=True)).to_have_count(0)
    visitor.reload()
    expect(visitor.get_by_role("textbox", name="Find a repository", exact=True)).to_be_visible()
    expect(visitor.get_by_role("link", name=collaboration.repository, exact=True)).to_have_count(0)


@pytest.mark.requirements(
    "REQ-1-1-1", "REQ-1-1-2", "REQ-2-1-2", "REQ-2-2-1", "REQ-2-2-2", "REQ-2-2-3", "REQ-2-3", "REQ-3-2-1"
)
def test_github_full_team_members_and_cycle_rejection_persist(page, sessions, application):
    collaboration = prepare_collaboration(page, sessions, application, {"member": "Read"})
    member = collaboration.participants["member"].username
    parent, parent_url = create_team(page, collaboration.organization_url, "parent")
    child, child_url = create_team(page, collaboration.organization_url, "child")
    descendant, descendant_url = create_team(page, collaboration.organization_url, "descendant")
    set_parent(page, child_url, parent)
    set_parent(page, descendant_url, child)

    page.goto(child_url)
    page.get_by_role("link", name="Members", exact=True).click()
    page.get_by_role("button", name="Add member", exact=True).click()
    page.get_by_role("textbox", name="Username", exact=True).fill(member)
    page.get_by_role("button", name="Add member", exact=True).click()
    expect(visible_identity(page, member)).to_have_count(1)
    page.reload()
    expect(visible_identity(page, member)).to_have_count(1)
    page.goto(parent_url)
    page.get_by_role("link", name="Members", exact=True).click()
    expect(visible_identity(page, member)).to_have_count(0)
    page.goto(child_url)
    page.get_by_role("link", name="Members", exact=True).click()
    page.get_by_role("button", name="Remove " + member, exact=True).click()
    expect(visible_identity(page, member)).to_have_count(0)
    page.reload()
    expect(visible_identity(page, member)).to_have_count(0)

    page.get_by_role("link", name="Settings", exact=True).click()
    page.get_by_role("combobox", name="Parent team", exact=True).select_option(label=descendant)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Cyclic team hierarchy is not allowed", exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_role("combobox", name="Parent team", exact=True).locator("option:checked")).to_have_text(parent)
    page.goto(descendant_url)
    page.get_by_role("link", name="Settings", exact=True).click()
    expect(page.get_by_role("combobox", name="Parent team", exact=True).locator("option:checked")).to_have_text(child)
    page.goto(parent_url)
    page.get_by_role("link", name="Members", exact=True).click()
    expect(visible_identity(page, member)).to_have_count(0)


@pytest.mark.requirements("REQ-1-1-1", "REQ-1-1-2", "REQ-3-1", "REQ-3-2-2", "REQ-3-3", "REQ-4-1", "REQ-4-4")
def test_github_full_fork_retains_source_and_keeps_new_commits_independent(page, application):
    register_and_sign_in(page, application)
    source_url = open_public_repository(page, application)
    page.get_by_role("button", name="Fork", exact=True).click()
    name = "fork-" + uuid.uuid4().hex[:12]
    page.get_by_label("Repository name", exact=True).fill(name)
    page.get_by_role("button", name="Create fork", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile(re.escape(name)))).to_be_visible()
    expect(page.get_by_text("Forked from acme-docs", exact=False).first).to_be_visible()
    expect(repository_source_link(page, source_url)).to_be_visible()
    fork_url = page.url
    page.reload()
    expect(page.get_by_role("heading", name=re.compile(re.escape(name)))).to_be_visible()
    expect(page.get_by_text("Forked from acme-docs", exact=False).first).to_be_visible()
    page.get_by_role("link", name="Code", exact=True).click()
    expect(page.get_by_role("link", name="README.md", exact=True)).to_be_visible()
    filename = "fork-only-" + uuid.uuid4().hex[:12] + ".md"
    create_file(page, filename, "Independent fork content.", "Add fork-only file")
    page.goto(source_url)
    page.get_by_role("link", name="Code", exact=True).click()
    expect(page.get_by_role("link", name="README.md", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=filename, exact=True)).to_have_count(0)
    page.goto(fork_url)
    page.get_by_role("link", name="Code", exact=True).click()
    page.get_by_role("link", name=filename, exact=True).click()
    expect(page.get_by_text("Independent fork content.", exact=True).first).to_be_visible()


@pytest.mark.requirements("REQ-3-1", "REQ-3-2-3", "REQ-3-3")
def test_github_full_clone_copies_both_complete_protocol_values(page, application):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=application)
    open_public_repository(page, application)
    heading = page.get_by_role("heading", name=repository_identity("alice-dev", "acme-docs"))
    repository = re.search(r"([A-Za-z0-9][A-Za-z0-9_.-]*)\s*/\s*acme-docs\b", heading.inner_text())
    assert repository, "The heading must identify the current repository owner and name"
    owner = repository.group(1)
    page.get_by_role("button", name="Code", exact=True).click()
    for protocol in ["HTTPS", "SSH"]:
        page.get_by_role("tab", name=protocol, exact=True).click()
        page.get_by_role("button", name="Copy clone value", exact=True).click()
        expect(page.get_by_text("Copied", exact=True).first).to_be_visible()
        value = page.evaluate("navigator.clipboard.readText()")
        if protocol == "HTTPS":
            assert re.fullmatch(r"https://[^/\s]+/" + re.escape(owner) + r"/acme-docs\.git", value)
        else:
            assert re.fullmatch(
                r"(?:[^@:\s/]+@)?[^:\s/]+:" + re.escape(owner) + r"/acme-docs\.git"
                r"|ssh://(?:[^@:\s/]+@)?[^:\s/]+(?::\d+)?/" + re.escape(owner) + r"/acme-docs\.git",
                value,
            )
        expect(heading).to_be_visible()


@pytest.mark.requirements("REQ-3-1", "REQ-3-3", "REQ-4-1", "REQ-4-2-1", "REQ-4-2-2")
def test_github_full_commit_diff_is_readable_after_direct_reopening(page, sessions, application):
    open_public_repository(page, application)
    page.get_by_role("link", name="Code", exact=True).click()
    page.get_by_role("link", name="Commits", exact=True).click()
    message = page.get_by_text("Document search flow", exact=True)
    expect(message).to_be_visible()
    title_link = page.get_by_role("link", name="Document search flow", exact=True)
    if title_link.count():
        title_link.click()
    else:
        message.locator("xpath=ancestor::*[.//a][1]").get_by_role("link", name=re.compile(r"^[0-9a-f]{1,40}$")).click()
    expect(page.get_by_text("Changed files", exact=True).first).to_be_visible()
    expect(page.get_by_text("src/search.ts", exact=True).first).to_be_visible()
    expect(page.locator("body")).to_contain_text(re.compile(r"\d+ additions,\s*\d+ deletions"))
    destination = page.url
    visitor = sessions.open("direct-diff-visitor")
    visitor.goto(destination)
    expect(visitor.get_by_text("Changed files", exact=True).first).to_be_visible()
    expect(visitor.get_by_text("src/search.ts", exact=True).first).to_be_visible()
    visitor.reload()
    expect(visitor.get_by_text("src/search.ts", exact=True).first).to_be_visible()
    expect(visitor.locator("body")).to_contain_text(re.compile(r"\d+ additions,\s*\d+ deletions"))


@pytest.mark.requirements(
    "REQ-1-1-1",
    "REQ-1-1-2",
    "REQ-2-1-2",
    "REQ-2-2-3",
    "REQ-2-3",
    "REQ-3-2-1",
    "REQ-4-1",
    "REQ-4-3-1",
    "REQ-4-3-2",
    "REQ-4-3-3",
    "REQ-4-4",
)
def test_github_full_default_branch_changes_for_new_visits_and_is_admin_only(page, sessions, application):
    collaboration = prepare_collaboration(page, sessions, application, {"reader": "Read"})
    page.goto(collaboration.repository_url)
    page.get_by_role("link", name="Code", exact=True).click()
    base, branch = create_branch(page, "default")
    filename = "new-default-" + uuid.uuid4().hex[:12] + ".md"
    create_file(page, filename, "New default branch content.", "Add default branch content")
    page.goto(collaboration.repository_url)
    page.get_by_role("link", name="Settings", exact=True).click()
    page.get_by_role("link", name="Branches", exact=True).click()
    settings_url = page.url
    page.get_by_role("combobox", name="Default branch", exact=True).select_option(label=branch)
    page.get_by_role("button", name="Update", exact=True).click()
    with page.expect_response(
        lambda response: (
            response.request.method in {"POST", "PUT", "PATCH"} and urlsplit(response.url)[:2] == urlsplit(page.url)[:2]
        )
    ) as saved:
        page.get_by_role("dialog").get_by_role("button", name="Confirm", exact=True).click()
    saved.value.finished()
    page.reload()
    expect(page.get_by_role("combobox", name="Default branch", exact=True).locator("option:checked")).to_have_text(
        branch
    )

    reader = collaboration.participants["reader"].page
    reader.goto(settings_url)
    expect(reader.get_by_role("combobox", name="Default branch", exact=True)).to_have_count(0)
    expect(reader.get_by_role("button", name="Update", exact=True)).to_have_count(0)
    reader.goto(collaboration.repository_url)
    reader.get_by_role("link", name="Code", exact=True).click()
    expect(reader.get_by_role("button", name="Branch " + branch, exact=True)).to_be_visible()
    expect(reader.get_by_role("link", name=filename, exact=True)).to_be_visible()
    reader.get_by_role("button", name="Branch " + branch, exact=True).click()
    reader.get_by_role("textbox", name="Find branch", exact=True).fill(base)
    reader.get_by_role("option", name=base, exact=True).click()
    expect(reader.get_by_role("button", name="Branch " + base, exact=True)).to_be_visible()
    expect(reader.get_by_role("link", name="README.md", exact=True)).to_be_visible()
    expect(reader.get_by_role("link", name=filename, exact=True)).to_have_count(0)
    page.goto(settings_url)
    expect(page.get_by_role("combobox", name="Default branch", exact=True).locator("option:checked")).to_have_text(
        branch
    )


@pytest.mark.requirements(
    "REQ-1-1-1",
    "REQ-1-1-2",
    "REQ-2-1-2",
    "REQ-2-2-3",
    "REQ-2-3",
    "REQ-3-2-1",
    "REQ-4-1",
    "REQ-4-3-1",
    "REQ-4-3-2",
    "REQ-4-4",
    "REQ-6-2-1",
    "REQ-6-2-2",
    "REQ-6-2-3",
    "REQ-6-3-2",
    "REQ-6-3-3",
    "REQ-6-3-4",
)
def test_github_full_inline_comments_publish_singly_but_drafts_stay_private(page, sessions, application):
    collaboration = prepare_collaboration(page, sessions, application, {"reviewer": "Write"})
    reviewer = collaboration.participants["reviewer"].page
    _, published_url, filename = create_pull_request(page, collaboration.repository_url)
    reviewer.goto(published_url)
    reviewer.get_by_role("link", name="Files changed", exact=True).click()
    expect(reviewer.get_by_text(filename, exact=True).first).to_be_visible()
    reviewer.get_by_role("button", name="Add comment", exact=True).first.click()
    published = "Published inline comment " + uuid.uuid4().hex[:12]
    reviewer.get_by_role("textbox", name="Comment", exact=True).fill(published)
    reviewer.get_by_role("button", name="Add single comment", exact=True).click()
    expect(reviewer.get_by_text(published, exact=True).first).to_be_visible()
    reviewer.reload()
    expect(reviewer.get_by_text(published, exact=True).first).to_be_visible()
    page.goto(published_url)
    page.get_by_role("link", name="Files changed", exact=True).click()
    expect(page.get_by_text(filename, exact=True).first).to_be_visible()
    expect(page.get_by_text(published, exact=True).first).to_be_visible()

    _, pending_url, pending_filename = create_pull_request(page, collaboration.repository_url)
    reviewer.goto(pending_url)
    reviewer.get_by_role("link", name="Files changed", exact=True).click()
    reviewer.get_by_role("button", name="Add comment", exact=True).first.click()
    pending = "Private review draft " + uuid.uuid4().hex[:12]
    reviewer.get_by_role("textbox", name="Comment", exact=True).fill(pending)
    reviewer.get_by_role("button", name="Start a review", exact=True).click()
    expect(reviewer.get_by_text(pending, exact=True).first).to_be_visible()
    expect(reviewer.get_by_text("Pending review", exact=True).first).to_be_visible()
    reviewer.reload()
    expect(reviewer.get_by_text(pending, exact=True).first).to_be_visible()
    expect(reviewer.get_by_text("Pending review", exact=True).first).to_be_visible()
    page.goto(pending_url)
    expect(page.get_by_text(pending, exact=True)).to_have_count(0)
    page.get_by_role("link", name="Files changed", exact=True).click()
    expect(page.get_by_text(pending_filename, exact=True).first).to_be_visible()
    expect(page.get_by_text(pending, exact=True)).to_have_count(0)
    reviewer.get_by_role("button", name="Review changes", exact=True).click()
    reviewer.get_by_role("radio", name="Comment", exact=True).check()
    with reviewer.expect_response(
        lambda response: (
            response.request.method in {"POST", "PUT", "PATCH"}
            and urlsplit(response.url)[:2] == urlsplit(reviewer.url)[:2]
        )
    ) as submitted:
        reviewer.get_by_role("button", name="Submit review", exact=True).click()
    submitted.value.finished()
    expect(reviewer.get_by_text("Pending review", exact=True)).not_to_be_visible()
    page.reload()
    expect(page.get_by_text(pending, exact=True).first).to_be_visible()


@pytest.mark.requirements(
    "REQ-1-1-1",
    "REQ-1-1-2",
    "REQ-2-1-2",
    "REQ-2-2-3",
    "REQ-2-3",
    "REQ-3-2-1",
    "REQ-4-1",
    "REQ-4-3-1",
    "REQ-4-3-2",
    "REQ-4-4",
    "REQ-6-2-1",
    "REQ-6-2-2",
    "REQ-6-2-3",
    "REQ-6-3-2",
    "REQ-6-3-4",
    "REQ-6-4",
)
def test_github_full_reviewer_request_removal_keeps_submitted_review(page, sessions, application):
    collaboration = prepare_collaboration(page, sessions, application, {"reviewer": "Write", "reader": "Read"})
    title, pull_url, _ = create_pull_request(page, collaboration.repository_url)
    reviewer = collaboration.participants["reviewer"]
    page.get_by_role("button", name="Reviewers", exact=True).click()
    search = page.get_by_role("textbox", name="Search", exact=True)
    search.fill(collaboration.participants["reader"].username)
    expect(page.get_by_role("option", name=collaboration.participants["reader"].username, exact=True)).to_have_count(0)
    search.fill(reviewer.username)
    page.get_by_role("option", name=reviewer.username, exact=True).click()
    expect(search).not_to_be_visible()
    expect(page.get_by_role("button", name="Remove " + reviewer.username, exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("button", name="Remove " + reviewer.username, exact=True)).to_be_visible()
    expect(page.get_by_text("Approved", exact=True)).to_have_count(0)

    reviewer.page.goto(pull_url)
    reviewer.page.get_by_role("link", name="Files changed", exact=True).click()
    reviewer.page.get_by_role("button", name="Review changes", exact=True).click()
    reviewer.page.get_by_role("radio", name="Approve", exact=True).check()
    reviewer.page.get_by_role("button", name="Submit review", exact=True).click()
    expect(reviewer.page.get_by_text("Approved", exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_text("Approved", exact=True).first).to_be_visible()
    page.get_by_role("button", name="Remove " + reviewer.username, exact=True).click()
    expect(page.get_by_role("button", name="Remove " + reviewer.username, exact=True)).to_have_count(0)
    expect(page.get_by_text("Approved", exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Remove " + reviewer.username, exact=True)).to_have_count(0)
    expect(page.get_by_text("Approved", exact=True).first).to_be_visible()
