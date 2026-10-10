"""Public navigation and ordinary API helpers for repository evolution checks."""

from urllib.parse import urlsplit

from playwright.sync_api import expect

from test_github_requirements import sign_in
from test_evolution_github_discovery import open_repository

PASSWORD = "Evo-Password-987!"


def signed_repository(page, application, username, name):
    sign_in(page, application, username, PASSWORD)
    expect(page.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
    open_repository(page, application, name)
    return repository_path(page)


def repository_path(page):
    return "/" + "/".join(urlsplit(page.url).path.strip("/").split("/")[:2])


def api_path(page):
    return "/api/repos" + repository_path(page)


def not_actionable(page, name):
    for role in ("button", "link", "menuitem"):
        for control in page.get_by_role(role, name=name, exact=True).all():
            assert not control.is_visible() or not control.is_enabled(), f"{name} must be unavailable"


def data(response):
    assert response.is_success, (response.status_code, response.text)
    return response.json()
