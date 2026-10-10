"""Current public YAML: original recovery, sign-out and password scenarios."""

import pytest
from playwright.sync_api import expect
from test_github_access import open_sign_in
from test_github_requirements import open_password_settings, sign_in

PASSWORD = "Valid-password-123!"


def reset_step(page, application, email):
    page.goto(application)
    open_sign_in(page)
    page.get_by_role("link", name="Forgot password", exact=True).click()
    page.get_by_label("Email", exact=True).fill(email)
    page.get_by_role("button", name="Send reset link", exact=True).click()
    expect(page.get_by_text("123456", exact=True)).to_be_visible()
    expect(page.get_by_label("Verification code", exact=True)).to_be_visible()
    expect(page.get_by_label("New password", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-1-1-3")
@pytest.mark.scenarios("REQ-1-1-3#1")
def test_evolution_github_recovery_known_and_unknown_email(page, application):
    for email in ("recovery-visibility@example.test", "unknown@example.test"):
        reset_step(page, application, email)


@pytest.mark.requirements("REQ-1-1-3")
@pytest.mark.parametrize(("username", "code", "message", "valid_password"), [
    pytest.param("recovery-invalid-code", "000000", "Verification code is invalid", PASSWORD, marks=pytest.mark.scenarios("REQ-1-1-3#2")),
    pytest.param("recovery-success", "123456", "Password updated", "Replacement-password-456!", marks=pytest.mark.scenarios("REQ-1-1-3#3")),
])
def test_evolution_github_recovery_credential_state(page, browser, application, username, code, message, valid_password):
    reset_step(page, application, username + "@example.test")
    page.get_by_label("Verification code", exact=True).fill(code)
    page.get_by_label("New password", exact=True).fill("Replacement-password-456!")
    page.get_by_label("Confirm password", exact=True).fill("Replacement-password-456!")
    page.get_by_role("button", name="Reset password", exact=True).click()
    expect(page.get_by_text(message, exact=True)).to_be_visible()
    with browser.new_context() as context:
        visitor = context.new_page()
        sign_in(visitor, application, username + "@example.test", valid_password)
        expect(visitor.get_by_text(username, exact=True)).to_be_visible()
        visitor.reload()
        expect(visitor.get_by_text(username, exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-1-3")
@pytest.mark.parametrize(("username", "current", "candidate", "confirmation", "message", "accepted"), [
    pytest.param("password-change-success", PASSWORD, "New-password-456!", "New-password-456!", "Password updated", True, marks=pytest.mark.scenarios("REQ-1-3#1")),
    pytest.param("password-change-invalid", PASSWORD + "-wrong", "Another-valid-password-123!", "does-not-match", "Current password is incorrect", False, marks=pytest.mark.scenarios("REQ-1-3#2")),
    pytest.param("password-change-required", "", "Required-password-789!", "Required-password-789!", "Current password is required", False, marks=pytest.mark.scenarios("REQ-1-3#3")),
])
def test_evolution_github_password_settings(page, browser, application, username, current, candidate, confirmation, message, accepted):
    sign_in(page, application, username, PASSWORD)
    open_password_settings(page)
    page.get_by_label("Current password", exact=True).fill(current)
    page.get_by_label("New password", exact=True).fill(candidate)
    page.get_by_label("Confirm password", exact=True).fill(confirmation)
    page.get_by_role("button", name="Update password", exact=True).click()
    expect(page.get_by_text(message, exact=True)).to_be_visible()
    with browser.new_context() as context:
        other = context.new_page()
        sign_in(other, application, username + "@example.test", PASSWORD if accepted else candidate)
        expect(other.get_by_text("Invalid credentials", exact=True)).to_be_visible()
        sign_in(other, application, username + "@example.test", candidate if accepted else PASSWORD)
        expect(other.get_by_text(username, exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-1-2")
@pytest.mark.parametrize(("cancel_first",), [
    pytest.param(True, marks=pytest.mark.scenarios("REQ-1-2#1")),
    pytest.param(False, marks=pytest.mark.scenarios("REQ-1-2#2")),
])
def test_evolution_github_sign_out_only_current_browser(page, browser, application, cancel_first):
    sign_in(page, application, "alice-dev", PASSWORD)
    open_password_settings(page)
    protected = page.url
    heading = page.get_by_role("heading", name="Password and authentication", exact=True)
    expect(heading).to_be_visible()
    with browser.new_context() as context:
        other = context.new_page()
        sign_in(other, application, "alice-dev", PASSWORD)
        page.get_by_role("button", name="Account menu", exact=True).click()
        expect(page.get_by_role("link", name="Sign out", exact=True)).to_have_count(1)
        page.get_by_role("link", name="Sign out", exact=True).click()
        dialog = page.get_by_role("dialog", name="Sign out", exact=True)
        if cancel_first:
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            expect(heading).to_be_visible()
            assert page.url == protected
            if not page.get_by_role("link", name="Sign out", exact=True).is_visible():
                page.get_by_role("button", name="Account menu", exact=True).click()
            page.get_by_role("link", name="Sign out", exact=True).click()
        dialog.get_by_role("button", name="Confirm sign out", exact=True).click()
        expect(page.get_by_role("link", name="Sign in", exact=True)).to_be_visible()
        for action in (page.reload, page.go_back, lambda: page.goto(protected)):
            action()
            expect(page.get_by_role("button", name="Account menu", exact=True)).to_have_count(0)
            expect(page.get_by_role("link", name="Sign in", exact=True)).to_be_visible()
            assert page.request.get(application + "/api/auth/me").status == 401
        other.reload()
        expect(other.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
