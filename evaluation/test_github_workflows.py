import re
import uuid

from playwright.sync_api import expect
from test_browser import register_account


def create_repository(page, application, visibility="Private"):
    _, email = register_account(page, application)
    page.get_by_label("Username or email", exact=True).fill(email)
    page.get_by_label("Password", exact=True).fill("Valid-password-123!")
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("link", name="New repository", exact=True).click()
    name = "repository-" + uuid.uuid4().hex[:12]
    page.get_by_label("Repository name", exact=True).fill(name)
    page.get_by_label("Description", exact=True).fill("Repository created by Playwright")
    page.get_by_role("radio", name=visibility, exact=True).check()
    page.get_by_role("checkbox", name="Add a README file", exact=True).check()
    page.get_by_role("button", name="Create repository", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile(re.escape(name))).first).to_be_visible()
    return name


def test_github_full_private_repository_is_persistent_and_hidden_from_search(page, browser, application):
    name = create_repository(page, application)
    expect(page.get_by_text("Private", exact=True).first).to_be_visible()
    expect(page.get_by_role("link", name="README.md", exact=True)).to_be_visible()
    saved_url = page.url
    page.reload()
    expect(page.get_by_role("heading", name=re.compile(re.escape(name))).first).to_be_visible()
    with browser.new_context() as context:
        visitor = context.new_page()
        visitor.goto(saved_url)
        expect(visitor.get_by_role("link", name="README.md", exact=True)).to_have_count(0)
        visitor.goto(application)
        visitor.get_by_role("searchbox", name="Search", exact=True).fill(name)
        visitor.get_by_role("searchbox", name="Search", exact=True).press("Enter")
        expect(visitor.get_by_text("No results", exact=True).first).to_be_visible()
        expect(visitor.get_by_role("link", name=name, exact=True)).to_have_count(0)


def test_github_full_visibility_change_allows_visitors(page, browser, application):
    name = create_repository(page, application)
    saved_url = page.url
    page.get_by_role("link", name="Settings", exact=True).click()
    page.get_by_role("link", name="General", exact=True).click()
    page.get_by_role("button", name="Change visibility", exact=True).click()
    page.get_by_role("radio", name="Public", exact=True).check()
    page.get_by_role("button", name="Confirm visibility", exact=True).click()
    expect(page.get_by_text("Public", exact=True).first).to_be_visible()
    with browser.new_context() as context:
        visitor = context.new_page()
        visitor.goto(saved_url)
        expect(visitor.get_by_role("heading", name=re.compile(re.escape(name))).first).to_be_visible()
        expect(visitor.get_by_role("link", name="README.md", exact=True)).to_be_visible()


def test_github_full_issue_validation_creation_and_live_filter(page, application):
    create_repository(page, application, "Public")
    page.get_by_role("link", name="Issues", exact=True).click()
    listing = page.url
    page.get_by_role("link", name="New issue", exact=True).click()
    page.get_by_label("Title", exact=True).fill("   ")
    page.get_by_role("button", name="Submit new issue", exact=True).click()
    expect(page.get_by_text("Title is required", exact=True).first).to_be_visible()
    title = "Issue " + uuid.uuid4().hex[:10]
    page.get_by_label("Title", exact=True).fill(title)
    page.get_by_label("Description", exact=True).fill("Persisted issue description")
    page.get_by_role("button", name="Submit new issue", exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.get_by_text("Persisted issue description", exact=True).first).to_be_visible()
    page.goto(listing)
    search = page.get_by_role("searchbox", name="Search issues", exact=True)
    search.fill(title)
    expect(page.get_by_role("link", name=title, exact=True)).to_be_visible()
    page.reload()
    expect(search).to_have_value(title)
    expect(page.get_by_role("link", name=title, exact=True)).to_be_visible()
    search.fill("missing" + uuid.uuid4().hex)
    expect(page.get_by_role("link", name=title, exact=True)).to_have_count(0)


def test_github_full_branch_validation_and_reload(page, application):
    create_repository(page, application, "Public")
    page.get_by_role("button", name="Branch main", exact=True).click()
    find = page.get_by_role("textbox", name="Find branch", exact=True)
    find.fill("invalid..branch")
    expect(page.get_by_text("Invalid branch", exact=True).first).to_be_visible()
    branch = "branch-" + uuid.uuid4().hex[:10]
    find.fill(branch)
    page.get_by_role("option", name="Create branch: " + branch, exact=True).click()
    expect(page.get_by_role("button", name="Branch " + branch, exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("button", name="Branch " + branch, exact=True)).to_be_visible()
