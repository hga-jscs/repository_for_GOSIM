"""Public issue reaction journeys and independent account ownership checks."""

import re
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from evolution_github_helpers import PASSWORD, signed_repository, repository_path, not_actionable, data
from test_evolution_github_discovery import open_repository
from test_evolution_github_storage import create_user
from server import application_server
from tools import WorkspaceTools


def open_issue(page, application, number, username=None):
    name = f"evo-reaction-repository-s{number}"
    if username:
        path = signed_repository(page, application, username, name)
    else:
        open_repository(page, application, name)
        path = repository_path(page)
    page.get_by_role("link", name="Issues", exact=True).click()
    title = f"Evo reaction issue s{number}"
    page.get_by_role("link", name=title, exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    issues = page.request.get(application + "/api/repos" + path + "/issues").json()["issues"]
    issue = next(item for item in issues if item["title"] == title)
    return application + "/api/repos" + path + "/issues/" + str(issue["number"])


def add_reaction(page):
    page.get_by_role("button", name="Add reaction", exact=True).click()
    page.get_by_role("menu").get_by_role("menuitem", name="+1", exact=True).click()


def reaction(issue):
    return next((item for item in issue.get("reactions", []) if item["type"] == "+1"), {"count": 0, "reacted": False})


def preserved(issue):
    return {key: issue[key] for key in ("title", "description", "comments", "labels", "assignees", "milestone")}


@pytest.mark.requirements("REQ-5-5")
@pytest.mark.scenarios("REQ-5-5#1")
def test_evolution_github_add_reaction_persists(page, application):
    endpoint = open_issue(page, application, 1, "evo-reaction-author")
    before = page.request.get(endpoint).json()["issue"]
    add_reaction(page)
    expect(page.get_by_role("button", name="Remove +1 reaction", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("button", name="Remove +1 reaction", exact=True)).to_be_visible()
    after = page.request.get(endpoint).json()["issue"]
    assert reaction(after)["count"] == reaction(before)["count"] + 1
    assert reaction(after)["reacted"]
    assert preserved(after) == preserved(before)


@pytest.mark.requirements("REQ-5-5")
@pytest.mark.scenarios("REQ-5-5#2")
def test_evolution_github_remove_only_own_reaction(page, application):
    endpoint = open_issue(page, application, 2, "evo-reaction-user")
    before = page.request.get(endpoint).json()["issue"]
    assert reaction(before)["count"] == 0
    add_reaction(page)
    page.get_by_role("button", name="Remove +1 reaction", exact=True).click()
    expect(page.get_by_role("button", name="Remove +1 reaction", exact=True)).to_have_count(0)
    page.reload()
    after = page.request.get(endpoint).json()["issue"]
    assert reaction(after)["count"] == 0 and not reaction(after)["reacted"]
    assert preserved(after) == preserved(before)


@pytest.mark.requirements("REQ-5-5")
@pytest.mark.scenarios("REQ-5-5#3")
def test_evolution_github_guest_reads_count_without_control(page, application):
    endpoint = open_issue(page, application, 3)
    count = reaction(page.request.get(endpoint).json()["issue"])["count"]
    assert count > 0
    expect(page.get_by_text(re.compile(r"^\+1\s*[·:×]?\s*" + str(count) + r"$"))).to_be_visible()
    not_actionable(page, "Add reaction")
    not_actionable(page, "Remove +1 reaction")


@pytest.mark.requirements("REQ-5-5")
def test_evolution_github_reactions_reader_identity_and_restarts(request, tmp_path):
    workspace = Path(request.config.getoption("--application"))
    database = tmp_path / "reactions.db"
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    username = "reaction-" + uuid.uuid4().hex[:8]
    with application_server(workspace, environment, time.monotonic() + 120) as (env, ready, output):
        assert ready
        with httpx.Client(base_url=env["BASE_URL"], timeout=20) as owner, httpx.Client(base_url=env["BASE_URL"], timeout=20) as reader, httpx.Client(base_url=env["BASE_URL"], timeout=20) as guest:
            create_user(owner, username)
            create_user(reader, username + "-reader")
            data(owner.post("/api/repos", json={"owner": username, "name": "public-reactions", "visibility": "public", "with_readme": True}))
            path = f"/api/repos/{username}/public-reactions/issues"
            issue = data(owner.post(path, json={"title": "Preserved issue", "description": "Keep description"}))["issue"]
            endpoint = path + "/" + str(issue["number"])
            data(owner.post(endpoint + "/comments", json={"body": "Preserved comment"}))
            with sqlite3.connect(database) as connection:
                repository, issue_id, account = connection.execute("SELECT i.repository_id,i.id,a.id FROM issues i JOIN repositories r ON r.id=i.repository_id JOIN accounts a ON a.id=r.owner_account_id WHERE a.username=? AND i.number=?", [username, issue["number"]]).fetchone()
                label = connection.execute("INSERT INTO labels(repository_id,name,color) VALUES (?,'Preserved label','ededed')", [repository]).lastrowid
                milestone = connection.execute("INSERT INTO milestones(repository_id,title) VALUES (?,'Preserved milestone')", [repository]).lastrowid
                connection.execute("INSERT INTO issue_labels(issue_id,label_id) VALUES (?,?)", [issue_id, label])
                connection.execute("INSERT INTO issue_assignees(issue_id,account_id) VALUES (?,?)", [issue_id, account])
                connection.execute("UPDATE issues SET milestone_id=? WHERE id=?", [milestone, issue_id])
            before = data(owner.get(endpoint))["issue"]
            assert before["comments"] and before["labels"] and before["assignees"] and before["milestone"]
            data(reader.get(endpoint))
            for client in (owner, reader):
                for _ in range(2):
                    response = client.post(endpoint + "/reactions", json={"type": "+1"})
                    assert response.status_code in {200, 201, 204, 409}
            assert reaction(data(owner.get(endpoint))["issue"])["count"] == 2
            assert reaction(data(reader.get(endpoint))["issue"])["reacted"]
            assert guest.post(endpoint + "/reactions", json={"type": "+1"}).status_code in {401, 403}
            assert guest.delete(endpoint + "/reactions/+1").status_code in {401, 403}
            assert reader.post(endpoint + "/reactions", json={"type": "unsupported"}).status_code in {400, 422}
            for _ in range(2):
                assert reader.delete(endpoint + "/reactions/+1").status_code in {200, 204, 404}
            current = data(owner.get(endpoint))["issue"]
            assert reaction(current)["count"] == 1 and reaction(current)["reacted"]
            assert not reaction(data(reader.get(endpoint))["issue"])["reacted"]
            assert preserved(current) == preserved(before)
            owner_cookies, reader_cookies = dict(owner.cookies), dict(reader.cookies)
    for _ in range(2):
        with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
            assert ready
            with httpx.Client(base_url=env["BASE_URL"], cookies=owner_cookies, timeout=20) as owner, httpx.Client(base_url=env["BASE_URL"], cookies=reader_cookies, timeout=20) as reader:
                for client, name in ((owner, username), (reader, username + "-reader")):
                    client.cookies.clear()
                    data(client.post("/api/auth/login", json={"identifier": name, "password": PASSWORD}))
                current = data(owner.get(endpoint))["issue"]
                assert reaction(current)["count"] == 1 and reaction(current)["reacted"]
                assert not reaction(data(reader.get(endpoint))["issue"])["reacted"]
                assert preserved(current) == preserved(before)
                owner_cookies, reader_cookies = dict(owner.cookies), dict(reader.cookies)
