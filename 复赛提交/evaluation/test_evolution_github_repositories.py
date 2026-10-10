"""Ten current public repository creation, fork, clone and visibility scenarios."""

import re
import uuid
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from evolution_github_baseline_helpers import application, browser_errors, login, public_repository, signed_repository
from evolution_github_helpers import not_actionable
from test_github_requirements import create_file


def repository_state(page):
    path = "/" + "/".join(urlsplit(page.url).path.strip("/").split("/")[:2])
    response = page.request.get(urlsplit(page.url).scheme + "://" + urlsplit(page.url).netloc + "/api/repos" + path)
    assert response.ok, response.text()
    return response.json()


@pytest.mark.requirements("REQ-3-2-1")
@pytest.mark.scenarios("REQ-3-2-1#1")
def test_evolution_github_create_initialized_private_repository(page, application):
    login(page, application, "repo-owner")
    workspace = page.url
    page.get_by_role("link", name="New repository", exact=True).click()
    expect(page.get_by_label("Owner", exact=True).locator("option:checked")).to_have_text("repo-owner")
    name = "public-check-" + uuid.uuid4().hex[:10]
    page.get_by_label("Repository name", exact=True).fill(name)
    page.get_by_label("Description", exact=True).fill("Repository created by Playwright")
    page.get_by_role("radio", name="Private", exact=True).check()
    page.get_by_role("checkbox", name="Add a README file", exact=True).check()
    page.get_by_role("button", name="Create repository", exact=True).click()
    for refresh in (False, True):
        if refresh:
            page.reload()
        expect(page.get_by_role("heading", name="repo-owner/" + name, exact=True)).to_be_visible()
        expect(page.get_by_text("Private", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="README.md", exact=True)).to_be_visible()
        expect(page.get_by_text("Repository created by Playwright", exact=True)).to_be_visible()
        state = repository_state(page)
        assert len(state["commits"]) == 1
    page.get_by_role("link", name="Commits", exact=True).click()
    expect(page.get_by_role("main").get_by_role("listitem")).to_have_count(1)
    page.goto(workspace)
    expect(page.get_by_role("link", name=name, exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-3-2-1")
@pytest.mark.parametrize(("name", "message"), [
    pytest.param("acme-docs", r"(already exists|name.*(?:conflict|taken))", marks=pytest.mark.scenarios("REQ-3-2-1#2")),
    pytest.param("", r"(repository name.*(?:required|empty)|name is required)", marks=pytest.mark.scenarios("REQ-3-2-1#3")),
])
def test_evolution_github_create_repository_rejects_name(page, application, name, message):
    login(page, application, "repo-owner")
    before = page.request.get(application + "/api/repos", params={"owner": "repo-owner"}).json()
    page.get_by_role("link", name="New repository", exact=True).click()
    destination = page.url
    page.get_by_label("Repository name", exact=True).fill(name)
    page.get_by_role("button", name="Create repository", exact=True).click()
    expect(page.get_by_role("main")).to_contain_text(re.compile(message, re.I))
    assert page.url == destination
    expect(page.get_by_role("heading", name="repo-owner/acme-docs", exact=True)).to_have_count(0)
    assert page.request.get(application + "/api/repos", params={"owner": "repo-owner"}).json() == before


@pytest.mark.requirements("REQ-3-2-2")
@pytest.mark.scenarios("REQ-3-2-2#1")
def test_evolution_github_fork_preserves_history_and_source(page, application):
    source_url = signed_repository(page, application, "fork-user")
    source = repository_state(page)
    page.get_by_role("button", name="Fork", exact=True).click()
    name = "fork-check-" + uuid.uuid4().hex[:10]
    page.get_by_label("Repository name", exact=True).fill(name)
    page.get_by_role("button", name="Create fork", exact=True).click()
    for refresh in (False, True):
        if refresh:
            page.reload()
        expect(page.get_by_role("heading", name="fork-user/" + name, exact=True)).to_be_visible()
        expect(page.get_by_role("main")).to_contain_text(re.compile(r"Forked from\s+(?:[^\s/]+/)?acme-docs"))
    fork_url = page.url
    fork = repository_state(page)
    assert fork["files"] == source["files"]
    assert [commit["message"] for commit in fork["commits"]] == [commit["message"] for commit in source["commits"]]
    source_path = "/" + "/".join(urlsplit(source_url).path.strip("/").split("/")[:2])
    source_link = page.get_by_role("link", name=re.compile(r"acme-docs$")).and_(page.locator(f'a[href="{source_path}"],a[href^="{source_path}/"]'))
    source_link.click()
    assert "/".join(urlsplit(page.url).path.split("/")[:3]) == "/".join(urlsplit(source_url).path.split("/")[:3])
    page.goto(fork_url)
    create_file(page, "fork-independent.md", "A fork-only addition.", "Add independent fork file")
    page.goto(source_url)
    assert repository_state(page)["files"] == source["files"]
    assert repository_state(page)["commits"] == source["commits"]
    page.goto(application)
    expect(page.get_by_role("link", name=name, exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-3-2-2")
@pytest.mark.scenarios("REQ-3-2-2#2")
def test_evolution_github_fork_duplicate_is_rejected(page, application):
    signed_repository(page, application, "fork-user")
    before = page.request.get(application + "/api/repos", params={"owner": "fork-user"}).json()
    page.get_by_role("button", name="Fork", exact=True).click()
    page.get_by_label("Repository name", exact=True).fill("acme-docs-fork")
    page.get_by_role("button", name="Create fork", exact=True).click()
    expect(page.get_by_role("main")).to_contain_text(re.compile(r"(already exists|name.*(?:conflict|taken))", re.I))
    expect(page.get_by_label("Repository name", exact=True)).to_have_value("acme-docs-fork")
    assert page.request.get(application + "/api/repos", params={"owner": "fork-user"}).json() == before


@pytest.mark.requirements("REQ-3-2-3")
@pytest.mark.parametrize(("protocol",), [
    pytest.param("HTTPS", marks=pytest.mark.scenarios("REQ-3-2-3#1")),
    pytest.param("SSH", marks=pytest.mark.scenarios("REQ-3-2-3#2")),
])
def test_evolution_github_clone_copies_real_protocol_value(page, application, protocol):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=application)
    public_repository(page, application)
    original = repository_state(page)
    owner = original["repository"]["owner"]
    page.get_by_role("button", name="Code", exact=True).click()
    page.get_by_role("tab", name=protocol, exact=True).click()
    page.get_by_role("button", name=re.compile(r"^Copy(?: clone value)?$", re.I)).click()
    expect(page.get_by_text("Copied", exact=True)).to_be_visible()
    value = page.evaluate("navigator.clipboard.readText()")
    if protocol == "HTTPS":
        assert re.fullmatch(r"https://[^/\s]+/" + re.escape(owner) + r"/acme-docs\.git", value)
    else:
        assert re.fullmatch(r"(?:[^@:\s/]+@)?[^:\s/]+:" + re.escape(owner) + r"/acme-docs\.git|ssh://(?:[^@:\s/]+@)?[^/\s]+/" + re.escape(owner) + r"/acme-docs\.git", value)
    expect(page.get_by_role("heading", name=owner + "/acme-docs", exact=True)).to_be_visible()
    assert repository_state(page) == original


@pytest.mark.requirements("REQ-3-3")
@pytest.mark.scenarios("REQ-3-3#1")
def test_evolution_github_public_overview_reloads_without_login(page, application):
    public_repository(page, application)
    expect(page.get_by_text("Public", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Code", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name=re.compile(r"acme-docs$"))).to_be_visible()


@pytest.mark.requirements("REQ-3-4")
@pytest.mark.scenarios("REQ-3-4#1")
def test_evolution_github_visibility_change_persists_for_visitor(page, application):
    destination = signed_repository(page, application, "visibility-admin", "visibility-demo")
    page.get_by_role("link", name="Settings", exact=True).click()
    page.get_by_role("link", name="General", exact=True).click()
    page.get_by_role("button", name="Change visibility", exact=True).click()
    page.get_by_role("radio", name="Public", exact=True).check()
    page.get_by_role("button", name="Confirm visibility", exact=True).click()
    expect(page.get_by_text("Public", exact=True)).to_be_visible()
    page.context.clear_cookies()
    page.goto(destination)
    expect(page.get_by_role("heading", name=re.compile("visibility-demo"))).to_be_visible()
    expect(page.get_by_text("Public", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-3-4")
@pytest.mark.scenarios("REQ-3-4#2")
def test_evolution_github_nonadmin_cannot_change_visibility(page, application):
    signed_repository(page, application, "collaborator", "visibility-demo")
    settings = page.get_by_role("link", name="Settings", exact=True)
    if settings.is_visible():
        settings.click()
    not_actionable(page, "Change visibility")
