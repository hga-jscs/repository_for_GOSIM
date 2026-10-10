"""Self-authored regression from public persistence and search-access contracts."""

import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from playwright.sync_api import expect

from server import application_server
from tools import WorkspaceTools
from test_evolution_github_discovery import open_repository, search
from test_github_requirements import sign_in

PASSWORD = "Evo-Password-987!"


def create_user(client, name):
    response = client.post("/api/auth/register", json={"username": name, "email": name + "@example.test", "password": PASSWORD, "confirmPassword": PASSWORD, "terms": True})
    assert response.is_success, response.text
    assert client.post("/api/auth/login", json={"identifier": name, "password": PASSWORD}).is_success


@pytest.mark.requirements("REQ-3-1")
def test_evolution_github_literal_description_and_private_permissions(page, application):
    name = "audit-" + uuid.uuid4().hex[:10]
    with httpx.Client(base_url=application, timeout=20) as user, httpx.Client(base_url=application, timeout=20) as guest:
        create_user(user, name)
        for suffix, visibility, description in (("public", "public", "literal_% token"), ("private", "private", "literal_% token"), ("decoy", "public", "literalZa token")):
            response = user.post("/api/repos", json={"owner": name, "name": name + "-" + suffix, "description": description, "visibility": visibility, "with_readme": False})
            assert response.status_code == 201, response.text
        response = user.get("/api/repos/search", params={"q": "LITERAL_%"})
        assert response.is_success
        visible = {row["name"] for row in response.json()["results"]}
        assert {name + "-public", name + "-private"} <= visible
        response = guest.get("/api/repos/search", params={"q": "LITERAL_%"})
        assert response.is_success
        visible = {row["name"] for row in response.json()["results"]}
        assert name + "-public" in visible
        assert name + "-private" not in visible and name + "-decoy" not in visible
        assert guest.get(f"/api/repos/{name}/{name}-private").status_code in {401, 403, 404}
        search(page, application, "LITERAL_%")
        expect(page.get_by_role("link", name=name + "-public", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name=name + "-private", exact=True)).to_have_count(0)
        expect(page.get_by_role("link", name=name + "-decoy", exact=True)).to_have_count(0)
        sign_in(page, application, name, PASSWORD)
        expect(page.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
        search(page, application, "literal_%")
        expect(page.get_by_role("link", name=name + "-private", exact=True)).to_be_visible()
        page.get_by_role("link", name=name + "-private", exact=True).click()
        page.reload()
        expect(page.get_by_text("Private", exact=True)).to_be_visible()


@pytest.mark.shared
def test_evolution_github_home_authentication_links_are_unique(page, application):
    page.goto(application)
    expect(page.get_by_role("link", name="Sign in", exact=True)).to_have_count(1)
    expect(page.get_by_role("link", name="Sign up", exact=True)).to_have_count(1)


@pytest.mark.requirements("REQ-4-3-1")
def test_evolution_github_slash_branch_does_not_change_default_or_history(page, application):
    name = "slash-" + uuid.uuid4().hex[:10]
    with httpx.Client(base_url=application, timeout=20) as user:
        create_user(user, name)
        response = user.post("/api/repos", json={"owner": name, "name": name, "visibility": "public", "with_readme": True})
        assert response.status_code == 201, response.text
        path = f"/api/repos/{name}/{name}"
        response = user.post(path + "/branches", json={"name": "feature/evolution", "base": "main"})
        assert response.is_success, response.text
        before = user.get(path).json()
        open_repository(page, application, name)
        page.get_by_role("button", name="Branch main", exact=True).click()
        page.get_by_role("textbox", name="Find branch", exact=True).fill("feature/evolution")
        page.get_by_role("option", name="feature/evolution", exact=True).click()
        expect(page.get_by_role("button", name="Branch feature/evolution", exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_role("button", name="Branch feature/evolution", exact=True)).to_be_visible()
        assert "feature/evolution" in unquote(page.url)
        expect(page.get_by_role("link", name="README.md", exact=True)).to_be_visible()
        after = user.get(path).json()
        assert before["repository"]["default_branch"] == after["repository"]["default_branch"] == "main"
        assert before["commits"] == after["commits"]


@pytest.mark.requirements("REQ-1-1-1", "REQ-1-1-2")
@pytest.mark.shared
def test_evolution_github_populated_database_upgrade_preserves_user_content_and_logout(request, tmp_path):
    workspace = Path(request.config.getoption("--application"))
    database = tmp_path / "persistent.db"
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    username = "restart-" + uuid.uuid4().hex[:10]
    with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
        assert ready, "Server must start before database preservation can be checked"
        with httpx.Client(base_url=env["BASE_URL"], timeout=20) as user:
            create_user(user, username)
            assert user.post("/api/auth/logout").is_success
            assert user.get("/api/auth/me").status_code == 401
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT f.id FROM files f JOIN repositories r ON r.id=f.repository_id WHERE r.name='acme-docs'").fetchall()
        assert rows, "Original public seed repositories must remain present"
        for row in rows:
            connection.execute("UPDATE files SET content='Persist this original user edit' WHERE id=?", (row[0],))
        organization_id = connection.execute("SELECT id FROM organizations WHERE name='acme-demo'").fetchone()[0]
        member_id = connection.execute("SELECT id FROM accounts WHERE username='existing-member'").fetchone()[0]
        administrator_id = connection.execute("SELECT id FROM accounts WHERE username='repo-admin'").fetchone()[0]
        repository_id = connection.execute("SELECT id FROM repositories WHERE organization_id=? AND name='acme-docs'", (organization_id,)).fetchone()[0]
        assert connection.execute("DELETE FROM org_members WHERE organization_id=? AND account_id=?", (organization_id, member_id)).rowcount == 1
        assert connection.execute("DELETE FROM repo_permissions WHERE repository_id=? AND account_id=?", (repository_id, administrator_id)).rowcount == 1
        # The injected old database does not have the new Agent's seed-version marker.
        if connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seed_versions'").fetchone():
            connection.execute("DROP TABLE seed_versions")
    for _ in range(2):
        with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
            assert ready, "Same-database restart failed"
            with sqlite3.connect(database) as connection:
                for row in rows:
                    assert connection.execute("SELECT content FROM files WHERE id=?", (row[0],)).fetchone() == ("Persist this original user edit",), "First evolution migration must preserve each owner's repository, not just one same-name result"
                assert connection.execute("SELECT COUNT(*) FROM accounts WHERE username=?", (username,)).fetchone()[0] == 1
                assert connection.execute("SELECT COUNT(*) FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE a.username=? AND s.active=1", (username,)).fetchone()[0] == 0
                assert connection.execute("SELECT COUNT(*) FROM org_members WHERE organization_id=? AND account_id=?", (organization_id, member_id)).fetchone()[0] == 0
                assert connection.execute("SELECT COUNT(*) FROM repo_permissions WHERE repository_id=? AND account_id=?", (repository_id, administrator_id)).fetchone()[0] == 0
            with httpx.Client(base_url=env["BASE_URL"], timeout=20) as removed:
                assert removed.post("/api/auth/login", json={"identifier": "repo-admin", "password": "Valid-password-123!"}).is_success
                assert removed.post("/api/repos/acme-demo/acme-docs/files", json={"branch": "main", "path": "must-not-return.md", "content": "Denied", "message": "Revoked grant"}).status_code in {401, 403}
