"""REQ-2-4 public scenarios; real mutations, denials and persistent audit data."""

import time
import uuid
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from server import application_server
from tools import WorkspaceTools
from test_evolution_github_sessions import signed_in
from test_evolution_github_storage import create_user

ORGANIZATION = "evo-audit-org"


def organization(page, application, username):
    signed_in(page, application, username)
    page.get_by_role("button", name="Account menu", exact=True).click()
    page.get_by_role("link", name="Your organizations", exact=True).click()
    page.get_by_role("listitem").filter(has=page.get_by_text(ORGANIZATION, exact=True)).get_by_role("link").click()
    expect(page.get_by_role("heading", name=ORGANIZATION, exact=True)).to_be_visible()


def audit_log(page, application):
    organization(page, application, "evo-audit-owner")
    page.get_by_role("link", name="Audit log", exact=True).click()
    expect(page.get_by_role("heading", name="Audit log", exact=True)).to_be_visible()
    return page.get_by_role("table")


def events(client, organization):
    response = client.get(f"/api/orgs/{organization}/audit-log")
    assert response.is_success, "Only an authenticated organization Owner may list audit records"
    return response.json()["events"]


@pytest.mark.requirements("REQ-2-4")
@pytest.mark.scenarios("REQ-2-4#1")
def test_evolution_github_audit_has_semantic_columns_and_persisted_events(page, application):
    table = audit_log(page, application)
    for name in ("Actor", "Action", "Target", "Timestamp"):
        expect(table.get_by_role("columnheader", name=name, exact=True)).to_be_visible()
    expect(table.get_by_role("cell", name="Member added", exact=True)).not_to_have_count(0)
    rows = table.locator("tbody tr").all()
    assert rows
    for row in rows:
        cells = row.get_by_role("cell").all_text_contents()
        assert len(cells) == 4 and all(text.strip() for text in cells)
    before = table.inner_text()
    page.reload()
    expect(page.get_by_role("table")).to_have_text(before, use_inner_text=True)


@pytest.mark.requirements("REQ-2-4")
@pytest.mark.scenarios("REQ-2-4#2")
def test_evolution_github_audit_filter_persists_and_clears(page, application):
    table = audit_log(page, application)
    control = page.get_by_role("combobox", name="Filter action", exact=True)
    control.select_option(label="Member added")
    expect(table.get_by_role("cell", name="Member added", exact=True)).not_to_have_count(0)
    expect(table.get_by_role("cell", name="Repository created", exact=True)).to_have_count(0)
    page.reload()
    expect(control.locator("option:checked")).to_have_text("Member added")
    expect(table.get_by_role("cell", name="Member added", exact=True)).not_to_have_count(0)
    for row in table.locator("tbody tr").all():
        assert row.get_by_role("cell").all_text_contents()[1] == "Member added"
    control.select_option(label="All actions")
    expect(table.get_by_role("cell", name="Repository created", exact=True)).not_to_have_count(0)
    page.reload()
    expect(control.locator("option:checked")).to_have_text("All actions")
    expect(table.get_by_role("cell", name="Repository created", exact=True)).not_to_have_count(0)


@pytest.mark.requirements("REQ-2-4")
@pytest.mark.scenarios("REQ-2-4#3")
def test_evolution_github_audit_member_denied_in_ui_and_direct_requests(page, application):
    organization(page, application, "evo-audit-viewer")
    expect(page.get_by_role("link", name="Audit log", exact=True)).to_have_count(0)
    assert page.request.get(application + f"/api/orgs/{ORGANIZATION}/audit-log").status in {403, 404}
    page.goto(application + f"/orgs/{ORGANIZATION}/audit-log")
    expect(page.get_by_role("table")).to_have_count(0)
    expect(page.get_by_text("Repository created", exact=True)).to_have_count(0)
    assert page.request.get(application + "/api/auth/me").ok


@pytest.mark.requirements("REQ-2-4")
def test_evolution_github_actual_audit_writes_and_restarts(request, tmp_path):
    workspace = Path(request.config.getoption("--application"))
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(tmp_path / "audit.db")}
    name = "audit-" + uuid.uuid4().hex[:10]
    with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
        assert ready
        with httpx.Client(base_url=env["BASE_URL"], timeout=20) as owner, httpx.Client(base_url=env["BASE_URL"], timeout=20) as member:
            create_user(owner, name)
            create_user(member, name + "-member")
            assert owner.post("/api/orgs", json={"name": name + "-org", "displayName": "Audit persistence"}).is_success
            path = f"/api/orgs/{name}-org/people"
            before = events(owner, name + "-org")
            assert owner.post(path, json={"identifier": name + "-member", "role": "Member"}).is_success
            after = events(owner, name + "-org")
            added = [event for event in after if event not in before]
            assert len(added) == 1 and added[0]["action"] == "Member added"
            assert added[0]["actor"] == name and added[0]["target"] == name + "-member" and added[0]["timestamp"]
            assert not owner.post(path, json={"identifier": name + "-member"}).is_success
            assert events(owner, name + "-org") == after, "Rejected duplicate membership must not write a success event"
            assert member.get(f"/api/orgs/{name}-org/audit-log").status_code in {403, 404}
            assert owner.post("/api/repos", json={"owner": name + "-org", "name": "recorded", "visibility": "private", "with_readme": True}).status_code == 201
            final = events(owner, name + "-org")
            assert len(final) == len(after) + 1
            assert sum(event["action"] == "Repository created" for event in final) == 1
            assert len(owner.get(path).json()["people"]) == 2
            owner_cookies = dict(owner.cookies)
    for _ in range(2):
        with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
            assert ready
            with httpx.Client(base_url=env["BASE_URL"], cookies=owner_cookies, timeout=20) as owner:
                assert events(owner, name + "-org") == final, "Read/restart must neither recreate nor append audit events"
                assert len(owner.get(path).json()["people"]) == 2
