"""Twelve exact current public organization and access scenarios."""

import re

import pytest
from playwright.sync_api import expect

from evolution_github_baseline_helpers import application, browser_errors, choose_option, form_with, login, organization, signed_repository


@pytest.mark.requirements("REQ-2-1-1")
@pytest.mark.parametrize(("private",), [
    pytest.param(False, marks=pytest.mark.scenarios("REQ-2-1-1#1")),
    pytest.param(True, marks=pytest.mark.scenarios("REQ-2-1-1#2")),
])
def test_evolution_github_organization_public_repository_filter(page, application, private):
    organization(page, application)
    page.get_by_role("link", name="Repositories", exact=True).click()
    name = "secret-research" if private else "acme-docs"
    page.get_by_role("textbox", name="Find a repository", exact=True).fill(name)
    if private:
        expect(page.get_by_role("link", name=name, exact=True)).to_have_count(0)
        page.reload()
        expect(page.get_by_role("link", name=name, exact=True)).to_have_count(0)
    else:
        page.get_by_role("link", name=name, exact=True).click()
        expect(page.get_by_role("heading", name=re.compile(name))).to_be_visible()
        page.get_by_role("link", name="Acme Demo", exact=True).click()
        page.get_by_role("link", name="Repositories", exact=True).click()
        expect(page.get_by_role("link", name=name, exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-2-2-1")
@pytest.mark.parametrize(("name", "valid"), [
    pytest.param("mobile-team", True, marks=pytest.mark.scenarios("REQ-2-2-1#1")),
    pytest.param("-invalid-team", False, marks=pytest.mark.scenarios("REQ-2-2-1#2")),
])
def test_evolution_github_organization_team_creation(page, application, name, valid):
    organization(page, application, "org-owner")
    page.get_by_role("link", name="Teams", exact=True).click()
    page.get_by_role("link", name="New team", exact=True).click()
    page.get_by_label("Team name", exact=True).fill(name)
    page.get_by_role("button", name="Create team", exact=True).click()
    if valid:
        expect(page.get_by_role("heading", name=re.compile(name))).to_be_visible()
        page.reload()
        expect(page.get_by_role("heading", name=re.compile(name))).to_be_visible()
    else:
        expect(page.get_by_text("Team name is invalid", exact=True)).to_be_visible()
        expect(page.get_by_label("Team name", exact=True)).to_have_value(name)


def frontend_team(page, application, tab):
    organization(page, application, "team-maintainer")
    page.get_by_role("link", name="Teams", exact=True).click()
    page.get_by_role("link", name="frontend-team", exact=True).click()
    page.get_by_role("link", name=tab, exact=True).click()


@pytest.mark.requirements("REQ-2-2-2")
@pytest.mark.scenarios("REQ-2-2-2#1")
def test_evolution_github_team_member_add_remove(page, application):
    frontend_team(page, application, "Members")
    page.get_by_role("button", name="Add member", exact=True).click()
    page.get_by_role("textbox", name="Username", exact=True).fill("bob-reviewer")
    form_with(page, "Username").get_by_role("button", name="Add member", exact=True).click()
    expect(page.get_by_text("bob-reviewer", exact=True)).to_be_visible()
    page.get_by_role("button", name="Remove bob-reviewer", exact=True).click()
    expect(page.get_by_text("bob-reviewer", exact=True)).to_have_count(0)
    page.reload()
    expect(page.get_by_text("bob-reviewer", exact=True)).to_have_count(0)


@pytest.mark.requirements("REQ-2-2-2")
@pytest.mark.scenarios("REQ-2-2-2#2")
def test_evolution_github_team_hierarchy_cycle_preserves_parent(page, application):
    frontend_team(page, application, "Settings")
    parent = page.get_by_role("combobox", name="Parent team", exact=True)
    expect(parent.locator("option:checked")).to_have_text("platform-team")
    parent.select_option(label="frontend-child")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Cyclic team hierarchy is not allowed", exact=True)).to_be_visible()
    page.reload()
    expect(parent.locator("option:checked")).to_have_text("platform-team")


@pytest.mark.requirements("REQ-2-2-3")
@pytest.mark.scenarios("REQ-2-2-3#1")
def test_evolution_github_add_member_preserves_private_access_boundary(page, sessions, application):
    organization_url = organization(page, application, "org-owner")
    page.get_by_role("link", name="People", exact=True).click()
    page.get_by_role("button", name="Add member", exact=True).click()
    page.get_by_label("Username or email", exact=True).fill("new-member")
    page.get_by_role("combobox", name="Role", exact=True).select_option(label="Member")
    form_with(page, "Username or email").get_by_role("button", name="Add member", exact=True).click()
    for refresh in (False, True):
        if refresh:
            page.reload()
        row = page.get_by_role("listitem").filter(has=page.get_by_text("new-member", exact=True))
        expect(row).to_have_count(1)
        expect(row.get_by_text("Member", exact=True)).to_be_visible()
        expect(page.get_by_text("Pending invitation", exact=True)).to_have_count(0)
    member = sessions.open("new-member")
    login(member, application, "new-member@example.test")
    member.get_by_role("button", name="Account menu", exact=True).click()
    member.get_by_role("link", name="Your organizations", exact=True).click()
    expect(member.get_by_role("link", name="Acme Demo", exact=True)).to_be_visible()
    # Capture the protected destination through the Owner's real visible link.
    page.goto(organization_url)
    page.get_by_role("link", name="Repositories", exact=True).click()
    page.get_by_role("textbox", name="Find a repository", exact=True).fill("secret-research")
    page.get_by_role("link", name="secret-research", exact=True).click()
    member.goto(page.url)
    expect(member.get_by_text("Access denied", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-2-2-3")
@pytest.mark.scenarios("REQ-2-2-3#2")
def test_evolution_github_add_member_rejects_existing_and_unknown(page, application):
    organization(page, application, "org-owner")
    page.get_by_role("link", name="People", exact=True).click()
    page.get_by_role("button", name="Add member", exact=True).click()
    for name, message in (("existing-member", "Account is already a member"), ("unknown-reviewer", "Account not found")):
        page.get_by_label("Username or email", exact=True).fill(name)
        form_with(page, "Username or email").get_by_role("button", name="Add member", exact=True).click()
        expect(page.get_by_text(message, exact=True)).to_be_visible()
    expect(page.get_by_text("existing-member", exact=True)).to_have_count(1)
    page.reload()
    expect(page.get_by_text("existing-member", exact=True)).to_have_count(1)
    expect(page.get_by_text("unknown-reviewer", exact=True)).to_have_count(0)


@pytest.mark.requirements("REQ-2-2-4")
@pytest.mark.scenarios("REQ-2-2-4#1")
def test_evolution_github_owner_removes_existing_member(page, application):
    organization(page, application, "org-owner")
    page.get_by_role("link", name="People", exact=True).click()
    expect(page.get_by_text("existing-member", exact=True)).to_be_visible()
    page.get_by_role("button", name="Member menu existing-member", exact=True).click()
    page.get_by_role("menuitem", name="Remove from organization", exact=True).or_(page.get_by_role("button", name="Remove from organization", exact=True)).click()
    page.get_by_role("dialog").get_by_role("button", name="Remove", exact=True).click()
    expect(page.get_by_text("existing-member", exact=True)).to_have_count(0)
    page.reload()
    expect(page.get_by_text("existing-member", exact=True)).to_have_count(0)


@pytest.mark.requirements("REQ-2-2-4")
@pytest.mark.scenarios("REQ-2-2-4#2")
def test_evolution_github_nonowner_cannot_remove_member(page, application):
    organization(page, application, "org-member")
    page.get_by_role("link", name="People", exact=True).click()
    expect(page.get_by_text("protected-member", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Member menu protected-member", exact=True)).to_have_count(0)
    expect(page.get_by_role("menuitem", name="Remove from organization", exact=True)).to_have_count(0)


def access(page, application):
    signed_repository(page, application, "repo-admin")
    page.get_by_role("link", name="Settings", exact=True).click()
    page.get_by_role("link", name="Manage access", exact=True).click()


@pytest.mark.requirements("REQ-2-3")
@pytest.mark.scenarios("REQ-2-3#1")
def test_evolution_github_team_access_grant_persists(page, application):
    access(page, application)
    page.get_by_role("button", name="Add people or teams", exact=True).click()
    page.get_by_role("textbox", name="Search", exact=True).fill("frontend-team")
    choose_option(page, "frontend-team")
    picker = form_with(page, "Search")
    picker.get_by_role("combobox", name="Role", exact=True).select_option(label="Write")
    picker.get_by_role("button", name="Add", exact=True).click()
    for refresh in (False, True):
        if refresh:
            page.reload()
        row = page.get_by_role("row", name=re.compile("frontend-team"))
        expect(row).to_have_count(1)
        expect(row.get_by_text("frontend-team", exact=True)).to_be_visible()
        expect(row.get_by_role("combobox", name="Role", exact=True).locator("option:checked")).to_have_text("Write")


@pytest.mark.requirements("REQ-2-3")
@pytest.mark.scenarios("REQ-2-3#2")
def test_evolution_github_team_access_role_updates_existing_row(page, application):
    access(page, application)
    row = page.get_by_role("row", name=re.compile("access-role-team"))
    expect(row).to_have_count(1)
    expect(row.get_by_role("combobox", name="Role", exact=True).locator("option:checked")).to_have_text("Write")
    row.get_by_role("combobox", name="Role", exact=True).select_option(label="Read")
    with page.expect_response(lambda response: response.request.method in {"PATCH", "PUT", "POST"}) as saved:
        row.get_by_role("button", name="Save", exact=True).click()
    assert saved.value.ok
    page.reload()
    expect(row).to_have_count(1)
    expect(row.get_by_role("combobox", name="Role", exact=True).locator("option:checked")).to_have_text("Read")
