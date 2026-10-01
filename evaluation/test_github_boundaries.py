"""Public issue-assignment and pull-request boundary workflows."""

import uuid

import pytest
from playwright.sync_api import Page, expect
from test_github_permissions import create_issue, current_branch, new_repository, prepare_collaboration
from test_github_requirements import create_file, register_and_sign_in


def create_branch(page: Page, prefix: str) -> tuple[str, str]:
    base = current_branch(page)
    branch = prefix + "-" + uuid.uuid4().hex[:12]
    page.get_by_role("button", name="Branch " + base, exact=True).click()
    page.get_by_role("textbox", name="Find branch", exact=True).fill(branch)
    page.get_by_role("option", name="Create branch: " + branch, exact=True).click()
    expect(page.get_by_role("button", name="Branch " + branch, exact=True)).to_be_visible()
    return base, branch


def open_comparison(page: Page, repository_url: str, base: str, compare: str) -> None:
    page.goto(repository_url)
    page.get_by_role("link", name="Pull requests", exact=True).click()
    page.get_by_role("link", name="New pull request", exact=True).click()
    page.get_by_role("combobox", name="base", exact=True).select_option(label=base)
    page.get_by_role("combobox", name="compare", exact=True).select_option(label=compare)
    page.get_by_role("button", name="Compare changes", exact=True).click()


@pytest.mark.requirements(
    "REQ-1-1-1",
    "REQ-1-1-2",
    "REQ-2-1-2",
    "REQ-2-2-3",
    "REQ-2-3",
    "REQ-3-2-1",
    "REQ-5-1-1",
    "REQ-5-1-2",
    "REQ-5-2-1",
    "REQ-5-3-1",
    "REQ-5-4",
)
def test_github_full_assignment_scope_and_issue_body_status_filters(page, sessions, application):
    collaboration = prepare_collaboration(page, sessions, application, {"triage": "Triage", "reader": "Read"})
    closed_title, issue_url = create_issue(page, collaboration.repository_url)
    eligible = collaboration.participants["triage"].username
    ineligible = collaboration.participants["reader"].username

    page.get_by_role("button", name="Assignees", exact=True).click()
    search = page.get_by_role("textbox", name="Search assignees", exact=True)
    search.fill(eligible)
    expect(page.get_by_role("option", name=eligible, exact=True)).to_be_visible()
    search.fill(ineligible)
    expect(page.get_by_role("option", name=eligible, exact=True)).to_have_count(0)
    expect(page.get_by_role("option", name=ineligible, exact=True)).to_have_count(0)
    search.fill(eligible)
    page.get_by_role("option", name=eligible, exact=True).click()
    expect(search).not_to_be_visible()
    expect(page.get_by_text(eligible, exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name=closed_title, exact=True)).to_be_visible()
    expect(page.get_by_text(eligible, exact=True).first).to_be_visible()

    reader = collaboration.participants["reader"].page
    reader.goto(issue_url)
    expect(reader.get_by_role("heading", name=closed_title, exact=True)).to_be_visible()
    expect(reader.get_by_text(eligible, exact=True).first).to_be_visible()

    page.get_by_role("button", name="Close issue", exact=True).click()
    expect(page.get_by_text("Closed", exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Reopen issue", exact=True)).to_be_enabled()
    expect(page.get_by_text(eligible, exact=True).first).to_be_visible()
    open_title, _ = create_issue(page, collaboration.repository_url)

    page.goto(collaboration.repository_url)
    page.get_by_role("link", name="Issues", exact=True).click()
    page.get_by_role("link", name="Open", exact=True).click()
    search = page.get_by_role("searchbox", name="Search issues", exact=True)
    body_keyword = "Keep this description unchanged."
    search.fill(body_keyword)
    expect(page.get_by_role("link", name=open_title, exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=closed_title, exact=True)).to_have_count(0)
    page.get_by_role("link", name="Closed", exact=True).click()
    expect(page.get_by_role("link", name=closed_title, exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=open_title, exact=True)).to_have_count(0)
    page.reload()
    expect(search).to_have_value(body_keyword)
    expect(page.get_by_role("link", name=closed_title, exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=open_title, exact=True)).to_have_count(0)
    page.get_by_role("link", name=closed_title, exact=True).click()
    expect(page.get_by_role("heading", name=closed_title, exact=True)).to_be_visible()
    expect(page.get_by_text(body_keyword, exact=True).first).to_be_visible()
    expect(page.get_by_text(eligible, exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Reopen issue", exact=True)).to_be_enabled()


@pytest.mark.requirements(
    "REQ-1-1-1",
    "REQ-1-1-2",
    "REQ-3-2-1",
    "REQ-4-1",
    "REQ-4-3-1",
    "REQ-4-3-2",
    "REQ-4-4",
    "REQ-6-2-1",
    "REQ-6-2-2",
    "REQ-6-2-4",
    "REQ-6-3-1",
    "REQ-6-3-2",
)
def test_github_full_draft_persists_and_becomes_ready_without_changing_content(page, sessions, application):
    register_and_sign_in(page, application)
    _, repository_url = new_repository(page, application)
    page.get_by_role("link", name="Code", exact=True).click()
    base, branch = create_branch(page, "draft")
    branch_url = page.url
    filename = "draft-content-" + uuid.uuid4().hex[:12] + ".md"
    content = "Draft content " + uuid.uuid4().hex
    create_file(page, filename, content, "Add draft content")
    open_comparison(page, repository_url, base, branch)
    expect(page.get_by_text(filename, exact=True).first).to_be_visible()
    page.get_by_role("button", name="Create draft pull request", exact=True).click()
    title = "Draft transition " + uuid.uuid4().hex[:12]
    description = "Unchanged description " + uuid.uuid4().hex
    page.get_by_label("Title", exact=True).fill(title)
    page.get_by_label("Description", exact=True).fill(description)
    page.get_by_role("button", name="Create draft pull request", exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.get_by_text("Draft", exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Merge pull request", exact=True)).to_be_disabled()
    detail_url = page.url
    page.reload()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.get_by_text(description, exact=True).first).to_be_visible()
    expect(page.get_by_text("Draft", exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Merge pull request", exact=True)).to_be_disabled()
    expect(page.locator("body")).to_contain_text(base)
    expect(page.locator("body")).to_contain_text(branch)

    page.get_by_role("button", name="Ready for review", exact=True).click()
    confirmation = page.get_by_role("button", name="Confirm", exact=True)
    open_status = page.get_by_text("Open", exact=True).filter(visible=True)
    expect(confirmation.or_(open_status).filter(visible=True).first).to_be_visible()
    if confirmation.is_visible():
        confirmation.click()
    expect(open_status.first).to_be_visible()
    expect(page.get_by_text("Ready for review", exact=True).first).to_be_visible()

    page.goto(detail_url)
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.get_by_text(description, exact=True).first).to_be_visible()
    expect(page.get_by_text("Open", exact=True).first).to_be_visible()
    expect(page.locator("body")).to_contain_text(base)
    expect(page.locator("body")).to_contain_text(branch)
    page.get_by_role("link", name="Files changed", exact=True).click()
    expect(page.get_by_text(filename, exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.get_by_text(filename, exact=True).first).to_be_visible()
    page.goto(branch_url)
    expect(page.get_by_role("button", name="Branch " + branch, exact=True)).to_be_visible()
    page.get_by_role("link", name=filename, exact=True).click()
    expect(page.get_by_text(content, exact=True).first).to_be_visible()


@pytest.mark.requirements(
    "REQ-1-1-1",
    "REQ-1-1-2",
    "REQ-3-2-1",
    "REQ-4-1",
    "REQ-4-3-1",
    "REQ-4-3-2",
    "REQ-4-4",
    "REQ-6-2-1",
    "REQ-6-2-2",
)
def test_github_full_distinct_branches_need_real_changes_before_creation(page, sessions, application):
    register_and_sign_in(page, application)
    _, repository_url = new_repository(page, application)
    page.get_by_role("link", name="Code", exact=True).click()
    base, branch = create_branch(page, "empty-comparison")
    branch_url = page.url
    open_comparison(page, repository_url, base, branch)
    expect(page.get_by_role("button", name="Create pull request", exact=True)).to_be_disabled()
    expect(page.get_by_role("combobox", name="base", exact=True).locator("option:checked")).to_have_text(base)
    expect(page.get_by_role("combobox", name="compare", exact=True).locator("option:checked")).to_have_text(branch)

    page.goto(branch_url)
    expect(page.get_by_role("button", name="Branch " + branch, exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="README.md", exact=True)).to_be_visible()
    filename = "comparison-content-" + uuid.uuid4().hex[:12] + ".md"
    content = "Actual new commit " + uuid.uuid4().hex
    create_file(page, filename, content, "Add one comparable change")
    open_comparison(page, repository_url, base, branch)
    expect(page.get_by_text(filename, exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Create pull request", exact=True)).to_be_enabled()

    page.goto(repository_url)
    page.get_by_role("link", name="Code", exact=True).click()
    expect(page.get_by_role("button", name="Branch " + base, exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=filename, exact=True)).to_have_count(0)
    page.goto(branch_url)
    expect(page.get_by_role("button", name="Branch " + branch, exact=True)).to_be_visible()
    page.get_by_role("link", name=filename, exact=True).click()
    expect(page.get_by_text(content, exact=True).first).to_be_visible()
