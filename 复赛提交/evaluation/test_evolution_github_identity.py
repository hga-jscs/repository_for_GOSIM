"""Public evolution YAML: registration, email sign-in and organization names."""

import re
import subprocess
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_github_access import open_sign_in
from test_github_requirements import sign_in

PASSWORD = "Evo-Password-987!"


def registration(page, application, username, email):
    page.goto(application)
    open_sign_in(page)
    page.get_by_role("link", name="Create an account", exact=True).click()
    page.get_by_label("Username", exact=True).fill(username)
    page.get_by_label("Email", exact=True).fill(email)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_label("Confirm password", exact=True).fill(PASSWORD)
    page.get_by_role("checkbox", name="Agree to the terms", exact=True).check()
    page.get_by_role("button", name="Create account", exact=True).click()


@pytest.mark.requirements("REQ-1-1-1")
@pytest.mark.scenarios("REQ-1-1-1#1")
def test_evolution_github_register_underscore_and_reload(page, application):
    registration(page, application, "evo_user_01", "evo.register.s1@evolution.test")
    expect(page.get_by_label("Username or email", exact=True)).to_be_visible()
    page.get_by_label("Username or email", exact=True).fill("evo.register.s1@evolution.test")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_text("evo_user_01", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_text("evo_user_01", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-1-1-1")
@pytest.mark.parametrize(("username", "email", "message"), [
    pytest.param("EvoUpper01", "evo.register.s2@evolution.test", "Username format is invalid", marks=pytest.mark.scenarios("REQ-1-1-1#2")),
    pytest.param("evo-register-existing", "evo.register.s3@evolution.test", "Username already exists", marks=pytest.mark.scenarios("REQ-1-1-1#3")),
])
def test_evolution_github_registration_rejection(page, application, username, email, message):
    registration(page, application, username, email)
    expect(page.get_by_text(message, exact=True)).to_be_visible()
    expect(page.get_by_label("Username", exact=True)).to_have_value(username)
    expect(page.get_by_label("Email", exact=True)).to_have_value(email)
    expect(page.get_by_label("Password", exact=True)).to_have_value("")
    expect(page.get_by_label("Confirm password", exact=True)).to_have_value("")
    expect(page.get_by_role("button", name="Create account", exact=True)).to_be_enabled()
    response = page.request.post(application + "/api/auth/login", data={"identifier": email, "password": PASSWORD})
    assert response.status == 401


@pytest.mark.requirements("REQ-1-1-2")
@pytest.mark.parametrize(("identifier", "password", "success"), [
    pytest.param("evo-login-case", PASSWORD, True, marks=pytest.mark.scenarios("REQ-1-1-2#1")),
    pytest.param("EVO.LOGIN.CASE@EVOLUTION.TEST", PASSWORD, True, marks=pytest.mark.scenarios("REQ-1-1-2#2")),
    pytest.param("evo.login.case@evolution.test", PASSWORD + "-incorrect", False, marks=pytest.mark.scenarios("REQ-1-1-2#3")),
])
def test_evolution_github_email_sign_in(page, application, identifier, password, success):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    sign_in(page, application, identifier, password)
    if success:
        expect(page.get_by_text("evo-login-case", exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_text("evo-login-case", exact=True)).to_be_visible()
        assert page.request.get(application + "/api/auth/me").ok
    else:
        expect(page.get_by_text("Invalid credentials", exact=True)).to_be_visible()
        assert page.request.get(application + "/api/auth/me").status == 401
        assert not errors, errors
        expect(page.get_by_label("Password", exact=True)).to_have_value("")


@pytest.mark.requirements("REQ-1-1-1", "REQ-1-1-2")
def test_evolution_github_identity_api_boundaries(page, application):
    for username in ("_prefix", "suffix_", "a__b", "a--b", "a-_b", "a_-b", "AName", "a" * 40):
        response = page.request.post(application + "/api/auth/register", data={"username": username, "email": uuid.uuid4().hex + "@example.test", "password": PASSWORD, "confirmPassword": PASSWORD, "terms": True})
        assert response.status == 422 and "Username format is invalid" in response.text(), (username, response.status, response.text())
    response = page.request.post(application + "/api/auth/login", data={"identifier": "EVO-LOGIN-CASE", "password": PASSWORD})
    assert response.status == 401


@pytest.mark.requirements("REQ-1-1-1", "REQ-1-1-2")
def test_evolution_github_new_accounts_can_sign_in_immediately(page, application):
    for _ in range(24):
        name = "registered-" + uuid.uuid4().hex[:10]
        response = page.request.post(application + "/api/auth/register", data={"username": name, "email": name + "@example.test", "password": PASSWORD, "confirmPassword": PASSWORD, "terms": True})
        assert response.ok, response.text()
        login = page.request.post(application + "/api/auth/login", data={"identifier": name, "password": PASSWORD})
        assert login.ok, (name, login.status, login.text())
        assert page.request.get(application + "/api/auth/me").json()["account"]["username"] == name
        assert page.request.post(application + "/api/auth/logout").ok


@pytest.mark.requirements("REQ-1-1-1", "REQ-1-1-2")
def test_evolution_github_existing_password_hash_remains_reliable(request):
    workspace = Path(request.config.getoption("--application")).resolve()
    script = r"""
const assert = require('node:assert/strict');
const {verifyPassword, hashPassword} = require('./src/auth/validation');
// Independent vector generated with Python bcrypt; cost 4 only speeds this fixture.
const password = 'Evo-Password-987!';
const oldHash = '$2b$04$abcdefghijklmnopqrstuu69R444zcyIKlOh41M/TZQQguhJzDfzC';
for (let i=0; i<1024; i++) {
  assert.equal(verifyPassword(password, oldHash), true, `Existing bcrypt hash rejected at ${i}`);
  if (i % 8 === 0) assert.equal(verifyPassword(password + '-wrong', oldHash), false);
}
for (let i=0; i<32; i++) {
  const hash = hashPassword(password);
  assert.equal(verifyPassword(password, hash), true, `New password hash rejected at ${i}`);
  assert.equal(verifyPassword(password + '-wrong', hash), false);
}
console.log('Existing hashes and new hashes verified without rewriting credentials');
"""
    result = subprocess.run(["node", "-e", script], cwd=workspace / "backend", capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr


def organization_form(page, application):
    sign_in(page, application, "evo-org-owner", PASSWORD)
    expect(page.get_by_text("evo-org-owner", exact=True)).to_be_visible()
    page.get_by_role("button", name="Account menu", exact=True).click()
    page.get_by_role("link", name="Your organizations", exact=True).click()
    page.get_by_role("link", name="New organization", exact=True).click()


@pytest.mark.requirements("REQ-2-1-2")
@pytest.mark.scenarios("REQ-2-1-2#1")
def test_evolution_github_organization_normalization(page, application):
    organization_form(page, application)
    page.get_by_label("Organization name", exact=True).fill("Evo-Lab-01")
    page.get_by_label("Display name", exact=True).fill("  Evo Lab One  ")
    page.get_by_role("button", name="Create organization", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile("evo-lab-01"))).to_be_visible()
    expect(page.get_by_text("Evo Lab One", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name=re.compile("evo-lab-01"))).to_be_visible()


@pytest.mark.requirements("REQ-2-1-2")
@pytest.mark.parametrize(("name", "display", "errors"), [
    pytest.param("EVO-LAB-02", "Evo Lab Duplicate", ["Organization name already exists"], marks=pytest.mark.scenarios("REQ-2-1-2#2")),
    pytest.param("-invalid-organization", "   ", ["Organization name format is invalid", "Display name is required"], marks=pytest.mark.scenarios("REQ-2-1-2#3")),
])
def test_evolution_github_organization_invalid(page, application, name, display, errors):
    organization_form(page, application)
    page.get_by_label("Organization name", exact=True).fill(name)
    page.get_by_label("Display name", exact=True).fill(display)
    page.get_by_role("button", name="Create organization", exact=True).click()
    for error in errors:
        expect(page.get_by_text(error, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Create organization", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name=re.compile("^evo-lab-02$"))).to_have_count(0)


@pytest.mark.requirements("REQ-2-1-2")
def test_evolution_github_organization_normalization_preserves_account_namespace(page, application):
    name = "namespace-" + uuid.uuid4().hex[:10]
    response = page.request.post(application + "/api/auth/register", data={"username": name, "email": name + "@example.test", "password": PASSWORD, "confirmPassword": PASSWORD, "terms": True})
    assert response.ok
    assert page.request.post(application + "/api/auth/login", data={"identifier": name, "password": PASSWORD}).ok
    response = page.request.post(application + "/api/orgs", data={"name": name.upper(), "displayName": "Rejected namespace collision"})
    assert response.status == 422 and "Organization name already exists" in response.text()
    assert page.request.get(application + "/api/orgs/" + name).status == 404
    assert page.request.get(application + "/api/auth/me").json()["account"]["username"] == name
