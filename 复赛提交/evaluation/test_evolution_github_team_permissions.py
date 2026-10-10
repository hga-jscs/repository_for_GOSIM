"""Public team grants must affect actual writes and remain revoked after restarts."""

import time
import uuid
from pathlib import Path

import httpx
import pytest

from server import application_server
from tools import WorkspaceTools
from test_evolution_github_storage import create_user


def data(response):
    assert response.is_success, (response.status_code, response.text)
    return response.json()


@pytest.mark.requirements("REQ-2-3", "REQ-2-2-4", "REQ-4-4", "REQ-5-2-1")
@pytest.mark.full_evolution
def test_evolution_github_team_role_applies_to_files_and_issues_and_revocation_survives_restarts(request, tmp_path):
    workspace = Path(request.config.getoption("--application"))
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(tmp_path / "team-grants.db")}
    name = "grant-" + uuid.uuid4().hex[:8]
    organization = name + "-org"
    path = f"/api/repos/{organization}/team-repository"
    with application_server(workspace, environment, time.monotonic() + 120) as (env, ready, output):
        assert ready, output
        with httpx.Client(base_url=env["BASE_URL"], timeout=20) as owner, httpx.Client(base_url=env["BASE_URL"], timeout=20) as member:
            create_user(owner, name)
            create_user(member, name + "-member")
            data(owner.post("/api/orgs", json={"name": organization, "displayName": "Team permissions"}))
            data(owner.post(f"/api/orgs/{organization}/people", json={"identifier": name + "-member", "role": "Member"}))
            data(owner.post(f"/api/orgs/{organization}/teams", json={"name": "contributors"}))
            data(owner.post(f"/api/orgs/{organization}/teams/contributors/members", json={"username": name + "-member"}))
            data(owner.post("/api/repos", json={"owner": organization, "name": "team-repository", "visibility": "private", "with_readme": True}))
            assert member.get(path).status_code in {401, 403, 404}
            grants = data(owner.post(path + "/access", json={"kind": "team", "name": "contributors", "role": "Write"}))["grants"]
            granted = [grant for grant in grants if grant["kind"] == "team" and grant["name"] == "contributors"]
            assert len(granted) == 1 and granted[0]["role"] == "Write"
            data(member.get(path))
            data(member.post(path + "/files", json={"branch": "main", "path": "team.md", "content": "Actual team write", "message": "Team contribution"}))
            issue = data(member.post(path + "/issues", json={"title": "Team discussion", "description": "Preserve this discussion"}))["issue"]
            issue_path = path + "/issues/" + str(issue["number"])
            data(owner.post(path + "/access/" + str(granted[0]["id"]), json={"role": "Read"}))
            before = data(member.get(path))
            assert member.post(path + "/files", json={"branch": "main", "path": "denied.md", "content": "Denied", "message": "Denied"}).status_code in {401, 403}
            assert member.post(issue_path + "/comments", json={"body": "Must be denied after downgrade"}).status_code in {401, 403}
            assert data(member.get(path))["files"] == before["files"]
            data(owner.delete(f"/api/orgs/{organization}/people/{name}-member"))
            assert member.get(path).status_code in {401, 403, 404}
            cookies = dict(member.cookies)
    for _ in range(2):
        with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
            assert ready, output
            with httpx.Client(base_url=env["BASE_URL"], cookies=cookies, timeout=20) as removed:
                assert removed.get("/api/auth/me").is_success
                assert removed.get(path).status_code in {401, 403, 404}
                assert removed.get(issue_path).status_code in {401, 403, 404}
                assert not any(row["name"] == "team-repository" and row["owner"] == organization for row in data(removed.get("/api/repos/search", params={"q": "team-repository"}))["results"])
