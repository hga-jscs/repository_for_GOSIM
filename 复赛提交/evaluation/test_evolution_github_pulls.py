"""The 25 REQ-6 public scenarios, each mapped to its own real browser journey."""

import re
import time
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_evolution_github_discovery import search, open_repository
from evolution_github_helpers import repository_path, not_actionable
from test_github_access import repository_result
from test_github_requirements import sign_in, navigation_control
from server import application_server
from tools import WorkspaceTools


@pytest.fixture
def application(request, tmp_path):
    workspace = Path(request.config.getoption("--application"))
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(tmp_path / "pulls.db")}
    with application_server(workspace, environment, time.monotonic() + 120) as (env, ready, output):
        assert ready, output
        yield env["BASE_URL"]


@pytest.fixture(autouse=True)
def browser_errors(page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield
    assert errors == [], errors


def repository(page, application, username=None, name="acme-docs"):
    if username:
        sign_in(page, application, username)
        expect(page.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
    if name == "acme-docs":
        search(page, application, name)
        repository_result(page, "acme-demo", name).click()
        expect(page.get_by_role("heading", name=re.compile("acme-docs"))).to_be_visible()
    else:
        open_repository(page, application, name)
    return application + "/api/repos" + repository_path(page)


def pull(page, application, title, username=None, name="acme-docs"):
    endpoint = repository(page, application, username, name)
    page.get_by_role("link", name="Pull requests", exact=True).click()
    page.get_by_role("link", name=title, exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    response = page.request.get(endpoint + "/pulls")
    assert response.ok
    number = next(item["number"] for item in response.json()["pulls"] if item["title"] == title)
    return endpoint + "/pulls/" + str(number)


def compare(page, application, same=False):
    endpoint = repository(page, application, "pr-contributor")
    page.get_by_role("link", name="Pull requests", exact=True).click()
    navigation_control(page, "New pull request").click()
    page.get_by_role("combobox", name="Base", exact=True).select_option(label="main")
    page.get_by_role("combobox", name="Compare", exact=True).select_option(label="main" if same else "feature-search")
    if not same:
        page.get_by_role("button", name="Compare changes", exact=True).click()
        expect(page.get_by_text("src/search.ts", exact=True)).to_be_visible()
    return endpoint


def detail(page, endpoint):
    response = page.request.get(endpoint)
    assert response.ok, response.text()
    return response.json()


def shown_diff(page):
    expect(page.get_by_text("src/search.ts", exact=True)).to_be_visible()
    expect(page.get_by_text(re.compile(r"\d+\s+additions?", re.I)).first).to_be_visible()
    expect(page.get_by_text(re.compile(r"\d+\s+deletions?", re.I)).first).to_be_visible()


def shown_commits(page, endpoint):
    commits = detail(page, endpoint)["commits"]
    assert commits
    expect(page.get_by_text(commits[0]["message"], exact=True).first).to_be_visible()


@pytest.mark.requirements("REQ-6-1")
@pytest.mark.scenarios("REQ-6-1#1")
def test_evolution_github_protection_create_reload(page, application):
    endpoint = repository(page, application, "protection-admin", "branch-protection-demo")
    page.get_by_role("link", name="Settings", exact=True).click()
    page.get_by_role("link", name="Branches", exact=True).click()
    page.get_by_role("button", name="Add branch protection rule", exact=True).click()
    page.get_by_label("Branch name pattern", exact=True).fill("main")
    page.get_by_role("checkbox", name="Require 1 approval", exact=True).check()
    page.get_by_role("checkbox", name="Require status check test", exact=True).check()
    page.get_by_role("button", name=re.compile(r"^(Create|Save changes)$")).click()
    expect(page.get_by_text("1 approval", exact=True)).to_be_visible()
    page.reload()
    for text in ("main", "1 approval", "test"):
        expect(page.get_by_text(text, exact=True)).to_be_visible()
    rules = detail(page, endpoint + "/settings/branch-protection")["rules"]
    assert any(item["pattern"] == "main" and item["require_approval"] and "test" in item["status_checks"] for item in rules)


@pytest.mark.requirements("REQ-6-1")
@pytest.mark.scenarios("REQ-6-1#2")
def test_evolution_github_protection_viewer_cannot_create(page, application):
    endpoint = repository(page, application, "protection-viewer", "branch-protection-demo")
    for name in ("Settings", "Branches"):
        link = page.get_by_role("link", name=name, exact=True)
        if link.is_visible():
            link.click()
    expect(page.get_by_role("button", name="Add branch protection rule", exact=True)).to_have_count(0)
    response = page.request.post(endpoint + "/settings/branch-protection", data={"pattern": "main", "require_approval": False, "status_checks": []})
    assert response.status in {401, 403}


@pytest.mark.requirements("REQ-6-1")
@pytest.mark.scenarios("REQ-6-1#3")
def test_evolution_github_check_update_reload(page, application):
    endpoint = pull(page, application, "Protection status onboarding PR", "protection-admin", "branch-protection-demo")
    page.get_by_role("link", name="Checks", exact=True).click()
    page.get_by_role("combobox", name="test", exact=True).select_option(label="success")
    page.get_by_role("button", name=re.compile(r"^(Save|Update)$")).click()
    expect(page.get_by_role("combobox", name="test", exact=True)).to_have_value("success")
    page.reload()
    expect(page.get_by_role("combobox", name="test", exact=True)).to_have_value("success")
    assert any(item["name"] == "test" and item["status"] == "success" and item["setter"] == "protection-admin" for item in detail(page, endpoint)["checks"])
    expect(page.get_by_text("protection-admin", exact=True).filter(visible=True).first).to_be_visible()


@pytest.mark.requirements("REQ-6-2-1")
@pytest.mark.parametrize(("scenario",), [pytest.param(n, marks=pytest.mark.scenarios(f"REQ-6-2-1#{n}")) for n in (1, 2, 3)])
def test_evolution_github_pull_list_read_reload_return(page, application, scenario):
    endpoint = repository(page, application)
    before = detail(page, endpoint + "/pulls")
    page.get_by_role("link", name="Pull requests", exact=True).click()
    page.get_by_role("link", name="Open", exact=True).click()
    expect(page.get_by_role("link", name="Improve onboarding", exact=True)).to_be_visible()
    if scenario == 1:
        page.get_by_role("link", name="Improve onboarding", exact=True).click()
        expect(page.get_by_role("heading", name="Improve onboarding", exact=True)).to_be_visible()
    elif scenario == 2:
        page.reload()
        expect(page.get_by_role("link", name="Improve onboarding", exact=True)).to_be_visible()
    else:
        repository(page, application)
        page.get_by_role("link", name="Pull requests", exact=True).click()
        page.get_by_role("link", name="Open", exact=True).click()
        expect(page.get_by_role("link", name="Improve onboarding", exact=True)).to_be_visible()
    assert detail(page, endpoint + "/pulls") == before


@pytest.mark.requirements("REQ-6-2-2")
@pytest.mark.scenarios("REQ-6-2-2#1")
def test_evolution_github_compare_changed_files_commits(page, application):
    endpoint = compare(page, application)
    response = detail(page, endpoint + "/pulls/compare?base=main&compare=feature-search")
    assert response["commits"]
    expect(page.get_by_text(response["commits"][0]["message"], exact=True).first).to_be_visible()


@pytest.mark.requirements("REQ-6-2-2")
@pytest.mark.scenarios("REQ-6-2-2#2")
def test_evolution_github_identical_branches_disable_create(page, application):
    compare(page, application, same=True)
    expect(page.get_by_text(re.compile(r"identical|no (changes|differences)", re.I)).first).to_be_visible()
    for control in page.get_by_role("button", name="Create pull request", exact=True).all():
        expect(control).to_be_disabled()


@pytest.mark.requirements("REQ-6-2-3")
@pytest.mark.scenarios("REQ-6-2-3#1")
def test_evolution_github_create_open_pull_reload(page, application):
    compare(page, application)
    page.get_by_role("button", name="Create pull request", exact=True).click()
    title = "Public create " + uuid.uuid4().hex[:8]
    page.get_by_label("Title", exact=True).fill(title)
    page.get_by_label("Description", exact=True).fill("Preserved proposal description")
    page.get_by_role("button", name="Create pull request", exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.get_by_text("Open", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.get_by_text("Preserved proposal description", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-6-2-3")
@pytest.mark.scenarios("REQ-6-2-3#2")
def test_evolution_github_whitespace_title_no_created_pull(page, application):
    endpoint = compare(page, application)
    before = detail(page, endpoint + "/pulls")["pulls"]
    page.get_by_role("button", name="Create pull request", exact=True).click()
    page.get_by_label("Title", exact=True).fill("   ")
    page.get_by_role("button", name="Create pull request", exact=True).click()
    expect(page.get_by_text(re.compile(r"title.*required", re.I))).to_be_visible()
    assert detail(page, endpoint + "/pulls")["pulls"] == before


@pytest.mark.requirements("REQ-6-2-4")
@pytest.mark.scenarios("REQ-6-2-4#1")
def test_evolution_github_create_draft_disables_merge(page, application):
    compare(page, application)
    page.get_by_role("button", name="Create draft pull request", exact=True).click()
    title = "Public draft " + uuid.uuid4().hex[:8]
    page.get_by_label("Title", exact=True).fill(title)
    page.get_by_role("button", name="Create draft pull request", exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.get_by_text("Draft", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Merge pull request", exact=True)).to_be_disabled()


@pytest.mark.requirements("REQ-6-2-4")
@pytest.mark.scenarios("REQ-6-2-4#2")
def test_evolution_github_draft_ready_preserves_branches(page, application):
    endpoint = pull(page, application, "Draft onboarding update", "draft-author")
    before = detail(page, endpoint)["pr"]
    assert before["status"] == "Draft"
    page.get_by_role("button", name="Ready for review", exact=True).click()
    confirm = page.get_by_role("button", name="Confirm", exact=True)
    if confirm.is_visible():
        confirm.click()
    expect(page.get_by_text("Open", exact=True)).to_be_visible()
    expect(page.get_by_text("Draft", exact=True)).to_have_count(0)
    expect(page.get_by_text(re.compile(r"ready[ -]for[ -]review", re.I)).first).to_be_visible()
    page.reload()
    expect(page.get_by_text("Open", exact=True)).to_be_visible()
    after = detail(page, endpoint)["pr"]
    assert (after["title"], after["base_branch"], after["compare_branch"]) == (before["title"], "main", "draft-feature")

