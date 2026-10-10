"""Isolated public seed scenarios from the unchanged evolution requirements."""

import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from server import application_server
from tools import WorkspaceTools
from test_github_requirements import sign_in
from test_evolution_github_discovery import open_repository, search


@pytest.fixture
def application(request, tmp_path):
    workspace = Path(request.config.getoption("--application")).resolve()
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(tmp_path / "github.db")}
    with application_server(workspace, environment, time.monotonic() + 180) as (env, ready, output):
        assert ready, output
        yield env["BASE_URL"]


@pytest.fixture(autouse=True)
def browser_errors(page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield
    assert not errors, errors


def login(page, application, username):
    sign_in(page, application, username)
    expect(page.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
    expect(page.get_by_text(username.split("@")[0], exact=True)).to_be_visible()


def organization(page, application, username=None):
    if username:
        login(page, application, username)
        page.get_by_role("button", name="Account menu", exact=True).click()
        page.get_by_role("link", name="Your organizations", exact=True).click()
    else:
        page.goto(application)
    page.get_by_role("link", name="Acme Demo", exact=True).click()
    expect(page.get_by_role("link", name="Repositories", exact=True)).to_be_visible()
    return page.url


def public_repository(page, application):
    # Disambiguate the public organization's repository from same-named personal ones.
    organization(page, application)
    page.get_by_role("link", name="Repositories", exact=True).click()
    page.get_by_role("textbox", name="Find a repository", exact=True).fill("acme-docs")
    page.get_by_role("link", name="acme-docs", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile(r"acme-docs$"))).to_be_visible()
    return page.url


def signed_repository(page, application, username, name="acme-docs"):
    public_path = None
    if name == "acme-docs":
        public_path = "/" + "/".join(urlsplit(public_repository(page, application)).path.strip("/").split("/")[:2])
    login(page, application, username)
    if public_path:
        search(page, application, name)
        page.get_by_role("link", name=name, exact=True).and_(page.locator(f'a[href="{public_path}"]')).click()
        expect(page.get_by_role("heading", name=re.compile(r"acme-docs$"))).to_be_visible()
    else:
        open_repository(page, application, name)
    return page.url


def issue(page, application, title, username=None):
    if username:
        signed_repository(page, application, username)
    else:
        public_repository(page, application)
    page.get_by_role("link", name="Issues", exact=True).click()
    page.get_by_role("link", name=title, exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    return page.url


def form_with(page, label):
    form = page.locator("form").filter(has=page.get_by_role("textbox", name=label, exact=True))
    expect(form).to_have_count(1)
    return form


def displayed_text(page, text):
    return page.get_by_role("main").get_by_text(text, exact=True).and_(page.locator(":not(textarea):not(input)"))


def choose_option(page, name):
    option = page.get_by_role("option", name=name, exact=True)
    expect(option).to_have_count(1)
    select = option.locator("xpath=ancestor::select")
    if select.count() == 1:
        select.select_option(label=name)
    else:
        option.click()


def issue_state(page):
    response = page.request.get(urlsplit(page.url).scheme + "://" + urlsplit(page.url).netloc + "/api/repos" + urlsplit(page.url).path)
    assert response.ok, response.text()
    return response.json()["issue"]


def saved_mutation(page):
    return page.expect_response(lambda response: response.request.method in {"POST", "PUT", "PATCH", "DELETE"} and "/api/" in response.url)
