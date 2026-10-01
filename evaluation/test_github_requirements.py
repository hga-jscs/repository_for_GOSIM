"""Public account, seed, file and pull-request contracts through visible controls."""

import re
import uuid

import pytest
from playwright.sync_api import Locator, Page, expect
from test_browser import register_account
from test_github_workflows import create_repository

PASSWORD = "Valid-password-123!"


def sign_in(page: Page, application: str, identity: str, password: str = PASSWORD) -> None:
    page.goto(application)
    page.get_by_role("link", name="Sign in", exact=True).first.click()
    submit_credentials(page, identity, password)


def submit_credentials(page: Page, identity: str, password: str) -> None:
    page.get_by_label("Username or email", exact=True).fill(identity)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()


def register_and_sign_in(page: Page, application: str) -> tuple[str, str]:
    username, email = register_account(page, application)
    submit_credentials(page, email, PASSWORD)
    expect(page.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
    return username, email


def navigation_control(page: Page, name: str) -> Locator:
    return (
        page.get_by_role("link", name=name, exact=True)
        .or_(page.get_by_role("button", name=name, exact=True))
        .or_(page.get_by_role("menuitem", name=name, exact=True))
        .filter(visible=True)
        .first
    )


def open_password_settings(page: Page) -> None:
    page.get_by_role("button", name="Account menu", exact=True).click()
    navigation_control(page, "Settings").click()
    current_password = page.get_by_label("Current password", exact=True)
    security_page = navigation_control(page, "Password and authentication")
    expect(current_password.or_(security_page).filter(visible=True).first).to_be_visible()
    if not current_password.is_visible():
        security_page.click()
    expect(current_password).to_be_visible()


def create_file(page: Page, name: str, content: str, message: str) -> None:
    page.get_by_role("button", name="Add file", exact=True).click()
    page.get_by_role("menuitem", name="Create new file", exact=True).click()
    page.get_by_label("File name", exact=True).fill(name)
    page.get_by_role("textbox", name="File contents", exact=True).fill(content)
    page.get_by_label("Commit message", exact=True).fill(message)
    page.get_by_role("button", name="Commit changes", exact=True).click()
    expect(page.get_by_role("button", name="Commit changes", exact=True)).not_to_be_visible()
    expect(page.get_by_text(content, exact=True).first).to_be_visible()


@pytest.mark.requirements("REQ-1-1-2")
def test_github_seed_account_authenticates_without_registration(page, application):
    sign_in(page, application, "alice.dev@example.test")
    expect(page.get_by_text("alice-dev", exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_text("alice-dev", exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Account menu", exact=True)).to_have_count(1)


@pytest.mark.requirements("REQ-3-1", "REQ-3-3", "REQ-4-1", "REQ-4-3-1")
def test_github_full_public_seed_repository_and_branch_are_readable(page, application):
    page.goto(application)
    search = page.get_by_role("searchbox", name="Search", exact=True)
    search.fill("acme-docs")
    search.press("Enter")
    page.get_by_role("link", name="acme-docs", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile(r".+\/acme-docs"))).to_be_visible()
    expect(page.get_by_text("Public", exact=True).first).to_be_visible()
    page.get_by_role("link", name="Code", exact=True).click()
    expect(page.get_by_role("link", name="README.md", exact=True)).to_be_visible()
    page.get_by_role("button", name="Branch main", exact=True).click()
    page.get_by_role("textbox", name="Find branch", exact=True).fill("feature-search")
    page.get_by_role("option", name="feature-search", exact=True).click()
    expect(page.get_by_role("link", name="main-only.md", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("button", name="Branch feature-search", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="main-only.md", exact=True)).to_be_visible()
    page.get_by_role("button", name="Branch feature-search", exact=True).click()
    page.get_by_role("textbox", name="Find branch", exact=True).fill("missing-" + uuid.uuid4().hex)
    expect(page.get_by_text("No matching branch", exact=True).first).to_be_visible()
    page.keyboard.press("Escape")
    page.reload()
    expect(page.get_by_role("button", name="Branch feature-search", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-1-2", "REQ-1-3", "REQ-1-1-1", "REQ-1-1-2")
def test_github_full_sign_out_cancel_and_current_session_only(page, browser, application):
    _, email = register_and_sign_in(page, application)
    open_password_settings(page)
    protected_url = page.url
    with browser.new_context() as context:
        other = context.new_page()
        sign_in(other, application, email)
        expect(other.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
        page.get_by_role("button", name="Account menu", exact=True).click()
        page.get_by_role("link", name="Sign out", exact=True).click()
        page.get_by_role("dialog", name="Sign out", exact=True).get_by_role("button", name="Cancel", exact=True).click()
        expect(page.get_by_label("Current password", exact=True)).to_be_visible()
        page.get_by_role("button", name="Account menu", exact=True).click()
        page.get_by_role("link", name="Sign out", exact=True).click()
        page.get_by_role("dialog", name="Sign out", exact=True).get_by_role(
            "button", name="Confirm sign out", exact=True
        ).click()
        expect(page.get_by_role("link", name="Sign in", exact=True).first).to_be_visible()
        page.goto(protected_url)
        page.reload()
        expect(page.get_by_role("link", name="Sign in", exact=True).first).to_be_visible()
        expect(page.get_by_label("Current password", exact=True)).to_have_count(0)
        other.reload()
        expect(other.get_by_role("button", name="Account menu", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-1-1-1", "REQ-1-1-2", "REQ-1-1-3")
def test_github_full_password_recovery_failure_and_success(page, browser, application):
    _, email = register_account(page, application)
    page.get_by_role("link", name="Forgot password", exact=True).click()
    with browser.new_context() as context:
        unknown = context.new_page()
        unknown.goto(application)
        unknown.get_by_role("link", name="Sign in", exact=True).first.click()
        unknown.get_by_role("link", name="Forgot password", exact=True).click()
        unknown.get_by_label("Email", exact=True).fill("missing-" + email)
        unknown.get_by_role("button", name="Send reset link", exact=True).click()
        expect(unknown.get_by_text("123456", exact=True).first).to_be_visible()
    page.get_by_label("Email", exact=True).fill(email)
    page.get_by_role("button", name="Send reset link", exact=True).click()
    expect(page.get_by_text("123456", exact=True).first).to_be_visible()
    replacement = "Replacement-password-456!"
    page.get_by_label("Verification code", exact=True).fill("000000")
    page.get_by_label("New password", exact=True).fill(replacement)
    page.get_by_label("Confirm password", exact=True).fill(replacement)
    page.get_by_role("button", name="Reset password", exact=True).click()
    expect(page.get_by_text("Verification code is invalid", exact=True).first).to_be_visible()
    with browser.new_context() as context:
        unchanged = context.new_page()
        sign_in(unchanged, application, email)
        expect(unchanged.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
    page.get_by_label("Verification code", exact=True).fill("123456")
    page.get_by_label("New password", exact=True).fill(replacement)
    page.get_by_label("Confirm password", exact=True).fill(replacement)
    page.get_by_role("button", name="Reset password", exact=True).click()
    expect(page.get_by_text("Password updated", exact=True).first).to_be_visible()
    sign_in(page, application, email)
    expect(page.get_by_text("Invalid credentials", exact=True).first).to_be_visible()
    submit_credentials(page, email, replacement)
    expect(page.get_by_role("button", name="Account menu", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-1-1-1", "REQ-1-1-2", "REQ-1-3")
def test_github_full_password_change_rejects_missing_current_and_persists(page, browser, application):
    _, email = register_and_sign_in(page, application)
    open_password_settings(page)
    replacement = "New-password-456!"
    page.get_by_label("New password", exact=True).fill(replacement)
    page.get_by_label("Confirm password", exact=True).fill(replacement)
    page.get_by_role("button", name="Update password", exact=True).click()
    expect(page.get_by_text("Current password is required", exact=True).first).to_be_visible()
    with browser.new_context() as context:
        unchanged = context.new_page()
        sign_in(unchanged, application, email)
        expect(unchanged.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
    page.get_by_label("Current password", exact=True).fill(PASSWORD)
    page.get_by_label("New password", exact=True).fill(replacement)
    page.get_by_label("Confirm password", exact=True).fill(replacement)
    page.get_by_role("button", name="Update password", exact=True).click()
    expect(page.get_by_text("Password updated", exact=True).first).to_be_visible()
    with browser.new_context() as context:
        updated = context.new_page()
        sign_in(updated, application, email)
        expect(updated.get_by_text("Invalid credentials", exact=True).first).to_be_visible()
        submit_credentials(updated, email, replacement)
        expect(updated.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
        updated.reload()
        expect(updated.get_by_role("button", name="Account menu", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-2-1-2", "REQ-2-2-1", "REQ-1-1-1", "REQ-1-1-2")
def test_github_full_organization_owner_can_create_a_persistent_team(page, application):
    register_and_sign_in(page, application)
    page.get_by_role("button", name="Account menu", exact=True).click()
    page.get_by_role("link", name="Your organizations", exact=True).click()
    page.get_by_role("link", name="New organization", exact=True).click()
    name = "organization-" + uuid.uuid4().hex[:12]
    page.get_by_label("Organization name", exact=True).fill(name)
    page.get_by_label("Display name", exact=True).fill("Independent organization")
    page.get_by_role("button", name="Create organization", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile(re.escape(name))).first).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name=re.compile(re.escape(name))).first).to_be_visible()
    page.get_by_role("link", name="Teams", exact=True).click()
    page.get_by_role("link", name="New team", exact=True).click()
    team = "team-" + uuid.uuid4().hex[:12]
    page.get_by_label("Team name", exact=True).fill(team)
    page.get_by_role("button", name="Create team", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile(re.escape(team))).first).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name=re.compile(re.escape(team))).first).to_be_visible()


@pytest.mark.requirements("REQ-3-2-1", "REQ-4-4", "REQ-4-2-1", "REQ-1-1-1", "REQ-1-1-2", "REQ-4-1")
def test_github_full_file_validation_creation_and_commit_history(page, application):
    create_repository(page, application, "Public")
    page.get_by_role("button", name="Add file", exact=True).click()
    page.get_by_role("menuitem", name="Create new file", exact=True).click()
    page.get_by_label("File name", exact=True).fill("../invalid.md")
    page.get_by_role("textbox", name="File contents", exact=True).fill("must not be saved")
    expect(page.get_by_label("Commit message", exact=True)).to_have_value("")
    page.get_by_role("button", name="Commit changes", exact=True).click()
    expect(
        page.get_by_text("Invalid file path", exact=True)
        .or_(page.get_by_text("Commit message is required", exact=True))
        .first
    ).to_be_visible()
    name = "file-" + uuid.uuid4().hex[:12] + ".md"
    content = "Visible content " + uuid.uuid4().hex
    message = "Add " + name
    page.get_by_label("File name", exact=True).fill(name)
    page.get_by_role("textbox", name="File contents", exact=True).fill(content)
    page.get_by_label("Commit message", exact=True).fill(message)
    page.get_by_role("button", name="Commit changes", exact=True).click()
    expect(page.get_by_role("button", name="Commit changes", exact=True)).not_to_be_visible()
    expect(page.get_by_text(content, exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_text(content, exact=True).first).to_be_visible()
    page.get_by_role("link", name="Commits", exact=True).click()
    expect(page.get_by_text(message, exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_text(message, exact=True).first).to_be_visible()


@pytest.mark.requirements(
    "REQ-3-2-1",
    "REQ-4-3-2",
    "REQ-4-4",
    "REQ-6-2-2",
    "REQ-6-2-3",
    "REQ-6-3-1",
    "REQ-6-3-2",
    "REQ-6-5",
    "REQ-6-6",
    "REQ-1-1-1",
    "REQ-1-1-2",
    "REQ-3-3",
    "REQ-4-1",
    "REQ-4-3-1",
    "REQ-6-2-1",
)
def test_github_full_pull_request_comparison_creation_and_real_merge(page, application):
    create_repository(page, application, "Public")
    repository_url = page.url
    branch_selector = page.get_by_role("button", name=re.compile(r"^Branch .+"))
    expect(branch_selector).to_have_count(1)
    match = re.search(r'button "Branch ([^"]+)"', branch_selector.aria_snapshot())
    assert match, "The current branch must be exposed in the Branch button's accessible name"
    base = match.group(1)
    branch = "change-" + uuid.uuid4().hex[:12]
    branch_selector.click()
    page.get_by_role("textbox", name="Find branch", exact=True).fill(branch)
    page.get_by_role("option", name="Create branch: " + branch, exact=True).click()
    expect(page.get_by_role("button", name="Branch " + branch, exact=True)).to_be_visible()
    name = "change-" + uuid.uuid4().hex[:12] + ".md"
    content = "Merged content " + uuid.uuid4().hex
    create_file(page, name, content, "Add " + name)
    page.goto(repository_url)
    page.get_by_role("link", name="Code", exact=True).click()
    expect(page.get_by_role("button", name="Branch " + base, exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=name, exact=True)).to_have_count(0)
    page.get_by_role("link", name="Pull requests", exact=True).click()
    page.get_by_role("link", name="New pull request", exact=True).click()
    page.get_by_role("combobox", name="base", exact=True).select_option(label=base)
    page.get_by_role("combobox", name="compare", exact=True).select_option(label=base)
    expect(page.get_by_text("No changes", exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Create pull request", exact=True)).to_be_disabled()
    page.get_by_role("combobox", name="compare", exact=True).select_option(label=branch)
    page.get_by_role("button", name="Compare changes", exact=True).click()
    expect(page.get_by_text(name, exact=True).first).to_be_visible()
    page.get_by_role("button", name="Create pull request", exact=True).click()
    expect(page.get_by_role("button", name="Create pull request", exact=True)).to_have_count(1)
    page.get_by_label("Title", exact=True).fill("   ")
    page.get_by_role("button", name="Create pull request", exact=True).click()
    expect(page.get_by_text("Title is required", exact=True).first).to_be_visible()
    title = "Review change " + uuid.uuid4().hex[:12]
    page.get_by_label("Title", exact=True).fill(title)
    page.get_by_label("Description", exact=True).fill("Independent end-to-end pull request")
    page.get_by_role("button", name="Create pull request", exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    detail_url = page.url
    page.reload()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    page.get_by_role("link", name="Files changed", exact=True).click()
    expect(page.get_by_text(name, exact=True).first).to_be_visible()
    page.goto(detail_url)
    page.get_by_role("button", name="Close pull request", exact=True).click()
    expect(page.get_by_text("Closed", exact=True).first).to_be_visible()
    page.get_by_role("button", name="Reopen pull request", exact=True).click()
    expect(page.get_by_text("Open", exact=True).first).to_be_visible()
    page.get_by_role("button", name="Merge pull request", exact=True).click()
    page.get_by_role("button", name="Confirm merge", exact=True).click()
    expect(page.get_by_text("Merged", exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_text("Merged", exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Close pull request", exact=True)).to_have_count(0)
    page.goto(repository_url)
    page.get_by_role("link", name="Code", exact=True).click()
    expect(page.get_by_role("button", name="Branch " + base, exact=True)).to_be_visible()
    page.get_by_role("link", name=name, exact=True).click()
    expect(page.get_by_text(content, exact=True).first).to_be_visible()
