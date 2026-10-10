"""Direct release form permissions, concurrent unique tags and real restart retention."""

import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from evolution_github_helpers import signed_repository, repository_path, not_actionable, data
from test_evolution_github_discovery import open_repository
from test_evolution_github_storage import create_user
from server import application_server
from tools import WorkspaceTools


def release_fields(release):
    return {key: release[key] for key in ("tag", "title", "description", "target_branch")}


@pytest.mark.requirements("REQ-3-5", "REQ-4-5")
@pytest.mark.parametrize(("name", "username"), [
    ("evo-release-repository-s2", None),
    ("evo-archive-repository-s2", "evo-archive-admin"),
])
def test_evolution_github_direct_publish_form_obeys_permission_and_archive(page, application, name, username):
    if username:
        path = signed_repository(page, application, username, name)
    else:
        open_repository(page, application, name)
        path = repository_path(page)
    page.goto(application + path + "/releases/new")
    page.wait_for_load_state("networkidle")
    not_actionable(page, "Publish release")


@pytest.mark.requirements("REQ-4-5")
def test_evolution_github_release_concurrency_and_seed_changes_survive_restarts(request, tmp_path):
    workspace = Path(request.config.getoption("--application"))
    database = tmp_path / "release-retention.db"
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    username = "release-" + uuid.uuid4().hex[:8]
    with application_server(workspace, environment, time.monotonic() + 120) as (env, ready, output):
        assert ready, output
        with httpx.Client(base_url=env["BASE_URL"], timeout=20) as owner:
            create_user(owner, username)
            data(owner.post("/api/repos", json={"owner": username, "name": "release-retention", "visibility": "public", "with_readme": True}))
            path = f"/api/repos/{username}/release-retention/releases"
            cookies = dict(owner.cookies)
            def publish(title):
                with httpx.Client(base_url=env["BASE_URL"], cookies=cookies, timeout=20) as client:
                    return client.post(path, json={"tag": "same-tag", "title": title, "description": "Preserved text", "target_branch": "main"})
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(publish, ["First attempt", "Second attempt"]))
            assert sorted(item.status_code for item in results) == [201, 409]
            winner = release_fields(next(item.json()["release"] for item in results if item.status_code == 201))
            assert next(item.json()["error"] for item in results if item.status_code == 409) == "Tag already exists"
            assert [release_fields(item) for item in data(owner.get(path))["releases"]] == [winner]
        with sqlite3.connect(database) as connection:
            assert connection.execute("DELETE FROM releases WHERE tag='evo-v0-1-s2'").rowcount == 1
            assert connection.execute("UPDATE releases SET description='User edited seeded release' WHERE tag='evo-v0-1-s3'").rowcount == 1
    for _ in range(2):
        with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
            assert ready, output
            with httpx.Client(base_url=env["BASE_URL"], timeout=20) as guest:
                assert [release_fields(item) for item in data(guest.get(path))["releases"]] == [winner]
                assert release_fields(data(guest.get(path + "/same-tag"))["release"]) == winner
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM releases WHERE tag='evo-v0-1-s2'").fetchone()[0] == 0
                assert connection.execute("SELECT description FROM releases WHERE tag='evo-v0-1-s3'").fetchone()[0] == "User edited seeded release"
