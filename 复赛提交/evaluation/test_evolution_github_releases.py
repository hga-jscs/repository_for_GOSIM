"""REQ-4-5 public release journeys and ordinary repository release API boundaries."""

import time
import uuid
import sqlite3
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from evolution_github_helpers import signed_repository, repository_path, not_actionable, data
from test_evolution_github_discovery import open_repository
from test_evolution_github_storage import create_user
from server import application_server
from tools import WorkspaceTools


@pytest.mark.requirements("REQ-4-5")
@pytest.mark.scenarios("REQ-4-5#1")
def test_evolution_github_publish_release_and_reload(page, application):
    signed_repository(page, application, "evo-release-owner", "evo-release-repository-s1")
    page.get_by_role("link", name="Releases", exact=True).click()
    page.get_by_role("link", name="New release", exact=True).click()
    page.get_by_label("Tag name", exact=True).fill("evo-v0-1-s1")
    page.get_by_label("Release title", exact=True).fill("Evolution Preview")
    page.get_by_label("Description", exact=True).fill("Evolution release description")
    page.get_by_role("combobox", name="Target branch", exact=True).select_option(label="evo-main-s1")
    page.get_by_role("button", name="Publish release", exact=True).click()
    for _ in range(2):
        for text in ("evo-v0-1-s1", "Evolution Preview", "Evolution release description", "evo-main-s1"):
            expect(page.get_by_text(text, exact=True)).to_be_visible()
        page.reload()


@pytest.mark.requirements("REQ-4-5")
@pytest.mark.scenarios("REQ-4-5#2")
def test_evolution_github_guest_reads_release(page, application):
    open_repository(page, application, "evo-release-repository-s2")
    path = repository_path(page)
    page.get_by_role("link", name="Releases", exact=True).click()
    not_actionable(page, "New release")
    response = page.request.get(application + "/api/repos" + path + "/releases/evo-v0-1-s2")
    assert response.ok
    release = response.json()["release"]
    page.get_by_role("link", name="evo-v0-1-s2", exact=True).click()
    for text in ("evo-v0-1-s2", "Existing Evolution Release", release["description"], "evo-main-s2"):
        expect(page.get_by_text(text, exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_text("Existing Evolution Release", exact=True)).to_be_visible()
    not_actionable(page, "Publish release")


@pytest.mark.requirements("REQ-4-5")
@pytest.mark.scenarios("REQ-4-5#3")
def test_evolution_github_duplicate_release_preserves_first(page, application):
    path = signed_repository(page, application, "evo-release-owner", "evo-release-repository-s3")
    endpoint = application + "/api/repos" + path + "/releases"
    before = page.request.get(endpoint).json()["releases"]
    page.get_by_role("link", name="Releases", exact=True).click()
    page.get_by_role("link", name="New release", exact=True).click()
    page.get_by_label("Tag name", exact=True).fill("evo-v0-1-s3")
    page.get_by_label("Release title", exact=True).fill("Rejected duplicate title")
    page.get_by_label("Description", exact=True).fill("Rejected duplicate description")
    page.get_by_role("button", name="Publish release", exact=True).click()
    expect(page.get_by_text("Tag already exists", exact=True)).to_be_visible()
    assert page.request.get(endpoint).json()["releases"] == before


@pytest.mark.requirements("REQ-4-5")
def test_evolution_github_release_roles_targets_and_private_reads(request, tmp_path):
    workspace = Path(request.config.getoption("--application"))
    database = tmp_path / "releases.db"
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    username = "release-" + uuid.uuid4().hex[:8]
    with application_server(workspace, environment, time.monotonic() + 120) as (env, ready, output):
        assert ready
        with httpx.Client(base_url=env["BASE_URL"], timeout=20) as owner, httpx.Client(base_url=env["BASE_URL"], timeout=20) as guest:
            create_user(owner, username)
            data(owner.post("/api/repos", json={"owner": username, "name": "private-releases", "visibility": "private", "with_readme": True}))
            path = f"/api/repos/{username}/private-releases"
            with sqlite3.connect(database) as connection:
                repository = connection.execute("SELECT r.id FROM repositories r JOIN accounts a ON a.id=r.owner_account_id WHERE a.username=? AND r.name='private-releases'", [username]).fetchone()[0]
            for role in ("Read", "Triage", "Write", "Maintain", "Admin"):
                with httpx.Client(base_url=env["BASE_URL"], timeout=20) as member:
                    name = username + "-" + role.lower()
                    create_user(member, name)
                    with sqlite3.connect(database) as connection:
                        account = connection.execute("SELECT id FROM accounts WHERE username=?", [name]).fetchone()[0]
                        connection.execute("INSERT INTO repo_permissions(repository_id,account_id,role) VALUES (?,?,?)", [repository, account, role])
                    response = member.post(path + "/releases", json={"tag": role.lower(), "title": role, "description": "Role boundary", "target_branch": "main"})
                    assert response.status_code in ({200, 201} if role in {"Write", "Maintain", "Admin"} else {403}), (role, response.text)
            before = data(owner.get(path + "/releases"))["releases"]
            assert len(before) == 3
            response = owner.post(path + "/releases", json={"tag": "bad-branch", "title": "Invalid target", "target_branch": "absent"})
            assert response.status_code in {400, 422}
            assert data(owner.get(path + "/releases"))["releases"] == before
            assert guest.get(path + "/releases").status_code in {401, 403, 404}
            assert guest.get(path + "/releases/write").status_code in {401, 403, 404}
            assert guest.post(path + "/releases", json={"tag": "guest", "title": "No permission", "target_branch": "main"}).status_code in {401, 403, 404}
