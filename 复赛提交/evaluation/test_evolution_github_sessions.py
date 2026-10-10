"""REQ-1-4 public scenarios and real independent-session persistence checks."""

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from server import application_server
from tools import WorkspaceTools
from test_evolution_github_storage import PASSWORD, create_user
from test_github_requirements import sign_in


def signed_in(page, application, username):
    sign_in(page, application, username, PASSWORD)
    expect(page.get_by_text(username, exact=True)).to_be_visible()


def active_sessions(page):
    page.get_by_role("button", name="Account menu", exact=True).click()
    page.get_by_role("link", name="Settings", exact=True).or_(page.get_by_role("menuitem", name="Settings", exact=True)).click()
    page.get_by_role("link", name="Active sessions", exact=True).click()
    expect(page.get_by_role("heading", name="Active sessions", exact=True)).to_be_visible()


def session_records(client):
    response = client.get("/api/auth/sessions")
    assert response.is_success, "Authenticated session listing must succeed"
    records = response.json()["sessions"]
    assert records and all(str(row["id"]) and row["device_label"] and row["last_active"] for row in records)
    assert sum(bool(row["is_current"]) for row in records) == 1
    for cookie in client.cookies.values():
        assert cookie not in json.dumps(records), "Session-list DTO must not disclose an authentication secret"
    return records


@pytest.mark.requirements("REQ-1-4")
@pytest.mark.scenarios("REQ-1-4#1")
def test_evolution_github_current_session_has_safe_visible_information(page, application):
    signed_in(page, application, "evo-session-owner")
    active_sessions(page)
    expect(page.get_by_text(re.compile(r"^(Current session|This session)$", re.I))).to_be_visible()
    expect(page.get_by_text(re.compile("Last active"))).not_to_have_count(0)
    response = page.request.get(application + "/api/auth/sessions")
    assert response.ok
    records = response.json()["sessions"]
    current = [row for row in records if row["is_current"]]
    assert len(current) == 1 and current[0]["device_label"] and current[0]["last_active"]
    expect(page.get_by_text(current[0]["device_label"], exact=True)).to_be_visible()
    for cookie in page.context.cookies():
        assert cookie["value"] not in json.dumps(records) and cookie["value"] not in page.content(), "Authentication secrets must stay out of API display records and markup"
    expect(page.get_by_role("button", name="Revoke session", exact=True)).to_have_count(0)


@pytest.mark.requirements("REQ-1-4")
@pytest.mark.parametrize(("username", "check_revoked_browser"), [
    pytest.param("evo-session-owner-s2", False, marks=pytest.mark.scenarios("REQ-1-4#2")),
    pytest.param("evo-session-owner-s3", True, marks=pytest.mark.scenarios("REQ-1-4#3")),
])
def test_evolution_github_revoke_another_real_browser(sessions, application, username, check_revoked_browser):
    current, other = sessions.open("current"), sessions.open("other")
    signed_in(current, application, username)
    signed_in(other, application, username)
    protected = other.url
    active_sessions(current)
    expect(current.get_by_role("button", name="Revoke session", exact=True)).to_have_count(1)
    current.get_by_role("button", name="Revoke session", exact=True).click()
    expect(current.get_by_text(re.compile(r"Session revoked|inactive", re.I))).not_to_have_count(0)
    current.reload()
    expect(current.get_by_role("heading", name="Active sessions", exact=True)).to_be_visible()
    expect(current.get_by_role("button", name="Revoke session", exact=True)).to_have_count(0)
    assert current.request.get(application + "/api/auth/me").ok
    assert other.request.get(application + "/api/auth/me").status == 401
    if check_revoked_browser:
        other.reload()
        expect(other.get_by_role("heading", name="Sign in", exact=True)).to_be_visible()
        other.goto(protected)
        expect(other.get_by_role("heading", name="Sign in", exact=True)).to_be_visible()
        current.get_by_role("button", name="Account menu", exact=True).click()
        expect(current.get_by_text(username, exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-1-4")
def test_evolution_github_session_boundaries_and_two_database_restarts(request, tmp_path):
    workspace = Path(request.config.getoption("--application"))
    database = tmp_path / "sessions.db"
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    username = "session-" + uuid.uuid4().hex[:10]
    with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
        assert ready
        with httpx.Client(base_url=env["BASE_URL"], timeout=20) as current, httpx.Client(base_url=env["BASE_URL"], timeout=20) as other, httpx.Client(base_url=env["BASE_URL"], timeout=20) as foreign:
            create_user(current, username)
            assert other.post("/api/auth/login", json={"identifier": username, "password": PASSWORD}).is_success
            create_user(foreign, username + "-other")
            records = session_records(current)
            assert len(records) == 2
            own = next(row["id"] for row in records if row["is_current"])
            target = next(row["id"] for row in records if not row["is_current"])
            foreign_id = session_records(foreign)[0]["id"]
            assert current.post(f"/api/auth/sessions/{own}/revoke").status_code in {400, 403, 409}
            assert current.post(f"/api/auth/sessions/{foreign_id}/revoke").status_code in {403, 404}
            assert foreign.get("/api/auth/me").is_success
            assert current.post(f"/api/auth/sessions/{target}/revoke").is_success
            assert current.get("/api/auth/me").is_success and other.get("/api/auth/me").status_code == 401
            active_cookies, revoked_cookies = dict(current.cookies), dict(other.cookies)
    for _ in range(2):
        with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
            assert ready
            with httpx.Client(base_url=env["BASE_URL"], cookies=active_cookies, timeout=20) as current, httpx.Client(base_url=env["BASE_URL"], cookies=revoked_cookies, timeout=20) as other:
                assert current.get("/api/auth/me").is_success
                assert other.get("/api/auth/me").status_code == 401
                assert all(row["is_current"] for row in session_records(current))
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE a.username=? AND s.active=1", (username,)).fetchone()[0] == 1
