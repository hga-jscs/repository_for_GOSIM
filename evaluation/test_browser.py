"""Independent behavior checks derived from public requirement descriptions."""

import uuid

import pytest
from playwright.sync_api import expect


def registration(page, application):
    page.goto(application)
    page.get_by_role("link", name="Sign in", exact=True).first.click()
    page.get_by_role("link", name="Create an account", exact=True).click()


def register_account(page, application):
    registration(page, application)
    username = "user" + uuid.uuid4().hex[:12]
    email = username + "@example.com"
    for label, value in [
        ("Username", username),
        ("Email", email),
        ("Password", "Valid-password-123!"),
        ("Confirm password", "Valid-password-123!"),
    ]:
        page.get_by_label(label, exact=True).fill(value)
    page.get_by_role("checkbox", name="Agree to the terms").check()
    page.get_by_role("button", name="Create account", exact=True).click()
    expect(page.get_by_label("Username or email", exact=True)).to_be_visible()
    return username, email


@pytest.mark.requirements("REQ-1-1-1")
def test_github_registration_controls(page, application):
    registration(page, application)
    for label in ["Username", "Email", "Password", "Confirm password"]:
        expect(page.get_by_label(label, exact=True)).to_have_count(1)
        expect(page.get_by_label(label, exact=True)).to_be_visible()
    expect(page.get_by_role("checkbox", name="Agree to the terms")).not_to_be_checked()
    expect(page.get_by_role("button", name="Create account", exact=True)).to_be_enabled()


@pytest.mark.requirements("REQ-1-1-1")
def test_github_invalid_registration_preserves_safe_inputs(page, application):
    registration(page, application)
    for label, value in [
        ("Username", "-invalid"),
        ("Email", "invalid"),
        ("Password", "short"),
        ("Confirm password", "different"),
    ]:
        page.get_by_label(label, exact=True).fill(value)
    page.get_by_role("button", name="Create account", exact=True).click()
    for error in [
        "Username format is invalid",
        "Email format is invalid",
        "Password requirements are not satisfied",
        "Agree to terms is required",
    ]:
        expect(page.get_by_text(error, exact=False).first).to_be_visible()
    expect(page.get_by_label("Username", exact=True)).to_have_value("-invalid")
    expect(page.get_by_label("Email", exact=True)).to_have_value("invalid")
    expect(page.get_by_label("Password", exact=True)).to_have_value("")


@pytest.mark.requirements("REQ-1-1-2", "REQ-1-1-1")
def test_github_session_reload_and_browser_isolation(page, browser, application):
    username, email = register_account(page, application)
    page.get_by_label("Username or email", exact=True).fill(email)
    page.get_by_label("Password", exact=True).fill("Valid-password-123!")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_text(username, exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_text(username, exact=True).first).to_be_visible()
    with browser.new_context() as isolated:
        visitor = isolated.new_page()
        visitor.goto(application)
        expect(visitor.get_by_role("link", name="Sign in", exact=True).first).to_be_visible()
        expect(visitor.get_by_text(username, exact=True)).to_have_count(0)


@pytest.mark.requirements("REQ-1-1-2", "REQ-1-1-1")
def test_github_invalid_login_uses_same_error(page, application):
    _, email = register_account(page, application)
    for identity in [email, "missing-" + email]:
        page.get_by_label("Username or email", exact=True).fill(identity)
        page.get_by_label("Password", exact=True).fill("wrong-password")
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_text("Invalid credentials", exact=True).first).to_be_visible()


@pytest.mark.requirements("REQ-1-1-1")
def test_github_duplicate_username_keeps_input(page, application):
    username, email = register_account(page, application)
    page.get_by_role("link", name="Create an account", exact=True).click()
    for label, value in [
        ("Username", username),
        ("Email", "other-" + email),
        ("Password", "Valid-password-123!"),
        ("Confirm password", "Valid-password-123!"),
    ]:
        page.get_by_label(label, exact=True).fill(value)
    page.get_by_role("checkbox", name="Agree to the terms").check()
    page.get_by_role("button", name="Create account", exact=True).click()
    expect(page.get_by_text("Username already exists", exact=False).first).to_be_visible()
    expect(page.get_by_label("Email", exact=True)).to_have_value("other-" + email)


def create_workbook(page, application):
    page.goto(application)
    page.get_by_role("button", name="New blank workbook", exact=True).click()
    page.get_by_role("button", name="Create", exact=True).click()
    expect(page.get_by_role("grid", name="Worksheet grid")).to_be_visible()


@pytest.mark.requirements("REQ-1-2-1", "REQ-3-1-3")
def test_sheet_blank_workbook_state_and_reload(page, application):
    create_workbook(page, application)
    for _ in range(2):
        expect(page.get_by_role("tab")).to_have_count(1)
        expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("gridcell", name="A1", exact=True)).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("gridcell", name="A1", exact=True)).to_have_text("")
        expect(page.get_by_role("grid", name="Worksheet grid")).to_have_attribute("aria-multiselectable", "true")
        page.reload()


@pytest.mark.requirements("REQ-1-1-1", "REQ-1-2-1")
def test_sheet_workbook_direct_url_in_new_browser(page, browser, application):
    create_workbook(page, application)
    with browser.new_context() as context:
        reopened = context.new_page()
        reopened.goto(page.url)
        expect(reopened.get_by_role("grid", name="Worksheet grid")).to_be_visible()
        expect(reopened.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")


@pytest.mark.requirements("REQ-1-2-2", "REQ-1-1-1", "REQ-1-2-1")
def test_sheet_rename_validation_and_persistence(page, application):
    create_workbook(page, application)
    page.get_by_role("button", name="Rename workbook", exact=True).click()
    name = page.get_by_label("Workbook name", exact=True)
    assert name.input_value().strip()
    name.fill("   ")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Workbook name cannot be empty", exact=False).first).to_be_visible()
    saved = "Sales " + uuid.uuid4().hex[:10]
    name.fill("  " + saved + "  ")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text(saved, exact=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_text(saved, exact=True).first).to_be_visible()
    page.goto(application)
    page.get_by_role("link", name=saved, exact=True).click()
    expect(page.get_by_text(saved, exact=True).first).to_be_visible()
