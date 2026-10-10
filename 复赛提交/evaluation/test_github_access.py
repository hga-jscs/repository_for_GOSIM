"""Public entry points and independent browser access under the published contracts."""

import asyncio
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

import pytest
from conftest import SERVER_LOG
from playwright.async_api import async_playwright
from playwright.async_api import expect as async_expect
from playwright.sync_api import Page, expect

WORKSPACES: WeakKeyDictionary[Page, str] = WeakKeyDictionary()


def open_sign_in(page: Page) -> None:
    links = page.get_by_role("link", name="Sign in", exact=True).filter(visible=True)
    expect(links).not_to_have_count(0)
    # Early account workflows need a working entry; REQ-6 separately checks its uniqueness.
    links.first.click()
    expect(page.get_by_label("Username or email", exact=True)).to_be_visible()


def username_pattern(username: str) -> re.Pattern:
    return re.compile(rf"(?<![\w-]){re.escape(username)}(?![\w-])")


def open_account_sign_out(page: Page, *, timeout: float = 10000):
    links = page.get_by_role("link", name="Sign out", exact=True).filter(visible=True)
    organizations = page.get_by_role("link", name="Your organizations", exact=True).filter(visible=True)
    containers = page.locator("*").filter(has=organizations).filter(has=links).filter(visible=True)
    containers = containers.filter(has_not=containers)
    menus = page.get_by_role("menu").filter(visible=True).filter(has=links)
    menus = menus.filter(has_not=menus)

    def signatures(locator):
        return locator.evaluate_all(r"""elements => elements.map(element => JSON.stringify([
            element.innerText.replace(/\s+/g, ' ').trim(), element.getAttribute('href'),
            [...element.querySelectorAll('a[href], [role="link"]')].map(link => link.getAttribute('href'))
        ]))""")

    def revealed_indices(locator, previous):
        remaining = Counter(previous)
        revealed = []
        for index, signature in enumerate(signatures(locator)):
            if remaining[signature]:
                remaining[signature] -= 1
            else:
                revealed.append(index)
        return revealed

    already_visible = signatures(links)
    previous_containers = signatures(containers)
    previous_menus = signatures(menus)
    account = page.get_by_role("button", name="Account menu", exact=True)
    controlled_ids = (account.get_attribute("aria-controls") or "").split()
    account.click()
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        controlled = page.locator(
            ",".join("[id=" + json.dumps(identifier) + "]" for identifier in controlled_ids) or ":not(*)"
        ).filter(visible=True).filter(has=links)
        revealed_menus = revealed_indices(menus, previous_menus)
        scope = controlled if controlled.count() else menus.nth(revealed_menus[0]) if len(revealed_menus) == 1 else None
        if scope is not None and scope.count() == 1:
            scoped_links = scope.get_by_role("link", name="Sign out", exact=True).filter(visible=True)
            assert scoped_links.count() == 1, "Account menu must reveal exactly one Sign out link"
            return scoped_links
        for index in revealed_indices(containers, previous_containers):
            scoped_links = containers.nth(index).get_by_role("link", name="Sign out", exact=True).filter(visible=True)
            if not (set(signatures(scoped_links)) & set(already_visible)):
                assert scoped_links.count() == 1, "Account menu must reveal exactly one Sign out link"
                return scoped_links
        revealed = revealed_indices(links, already_visible)
        if revealed:
            assert len(revealed) == 1, "Account menu must reveal exactly one Sign out link"
            selected = links.nth(revealed[0])
            href = selected.get_attribute("href")
            return links.and_(page.locator("[href=" + json.dumps(href) + "]")).first if href is not None else selected
        page.wait_for_timeout(min(50, max(1, (deadline - time.monotonic()) * 1000)))
    raise AssertionError("Account menu did not reveal its Sign out link")


def repository_identity(owner: str, name: str) -> re.Pattern:
    return re.compile(r"(?<![\w.-])" + re.escape(owner) + r"\s*\/\s*" + re.escape(name) + r"(?![\w.-])")


def repository_result(page, owner: str, name: str):
    links = page.get_by_role("link", name=name, exact=True).filter(visible=True)
    metadata = page.get_by_text(repository_identity(owner, name)).filter(visible=True)
    entries = page.locator("*").filter(has=metadata).filter(has=links).filter(visible=True)
    return entries.filter(has_not=entries).get_by_role("link", name=name, exact=True).filter(visible=True).first


def repository_source_link(page: Page, source_url: str):
    links = page.get_by_role("link").filter(visible=True)
    expect(links).not_to_have_count(0)
    matches = links.evaluate_all(
        "(links, source) => links.filter(link => link.href === source).map(link => link.getAttribute('href'))",
        source_url,
    )
    assert matches, "The fork must have a visible source link to the repository opened before forking"
    return links.and_(page.locator("[href=" + json.dumps(matches[0]) + "]")).first


def remember_workspace(page: Page) -> None:
    expect(page.get_by_role("button", name="Account menu", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Sign in", exact=True)).not_to_be_visible()
    WORKSPACES[page] = page.url


def open_new_repository(page: Page) -> None:
    assert page in WORKSPACES, "Record the workspace reached through successful sign-in before creating repositories"
    page.goto(WORKSPACES[page])
    page.get_by_role("link", name="New repository", exact=True).filter(visible=True).first.click()


def server_errors(output: str) -> list[str]:
    pattern = re.compile(
        r"\bSQLITE_[A-Z_]+\b|cannot (?:start|commit|rollback) (?:a )?transaction|"
        r"database (?:is )?(?:locked|malformed)|no such (?:table|column)|"
        r"UnhandledPromiseRejection|uncaught(?:Exception| exception)|"
        r"(?:TypeError|ReferenceError|SyntaxError):",
        re.IGNORECASE,
    )
    return [line for line in output.splitlines() if pattern.search(line)]


@pytest.mark.requirements("REQ-1-1-1", "REQ-6-1")
def test_github_full_home_sign_in_is_unique_for_pull_request_workflows(page, application):
    page.goto(application)
    sign_in = page.get_by_role("link", name="Sign in", exact=True)
    expect(sign_in).to_have_count(1)
    sign_in.click()
    create_account = page.get_by_role("link", name="Create an account", exact=True)
    expect(create_account).to_have_count(1)
    create_account.click()
    expect(page.get_by_role("textbox", name="Username", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Create account", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-1-1-2", "REQ-1-2", "REQ-2-1-2", "REQ-3-1", "REQ-3-3")
def test_github_full_account_menu_reaches_organizations_and_repository(page, application):
    page.goto(application)
    open_sign_in(page)
    page.get_by_label("Username or email", exact=True).fill("alice.dev@example.test")
    page.get_by_label("Password", exact=True).fill("Valid-password-123!")
    page.get_by_role("button", name="Sign in", exact=True).click()
    account = page.get_by_role("button", name="Account menu", exact=True)
    expect(account).to_have_count(1)
    expect(account).to_be_visible()
    position = account.bounding_box()
    viewport = page.viewport_size
    assert position and viewport
    assert position["x"] + position["width"] / 2 > viewport["width"] / 2, "Account menu must be in the upper-right area"
    assert position["y"] + position["height"] / 2 < viewport["height"] / 2, (
        "Account menu must be in the upper-right area"
    )
    expect(open_account_sign_out(page)).to_be_visible()
    expect(page.get_by_text(username_pattern("alice-dev")).filter(visible=True)).not_to_have_count(0)
    organizations = page.get_by_role("link", name="Your organizations", exact=True).filter(visible=True)
    expect(organizations).not_to_have_count(0)
    organizations.first.click()
    expect(page.get_by_role("link", name="New organization", exact=True).filter(visible=True).first).to_be_visible()
    search = page.get_by_role("searchbox", name="Search", exact=True)
    search.fill("acme-docs")
    search.press("Enter")
    repository_result(page, "alice-dev", "acme-docs").click()
    expect(page.get_by_role("heading", name=repository_identity("alice-dev", "acme-docs"))).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name=repository_identity("alice-dev", "acme-docs"))).to_be_visible()
    expect(page.get_by_role("button", name="Account menu", exact=True)).to_have_count(1)


async def concurrent_visitors(application: str, channel: str | None) -> list[str]:
    failures = []
    origin = urlsplit(application).netloc
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=channel, headless=True)
        try:
            contexts = [await browser.new_context() for _ in range(4)]
            pages = [await context.new_page() for context in contexts]
            for page in pages:
                page.set_default_timeout(10000)
                page.on("pageerror", lambda error: failures.append(str(error)))
                page.on(
                    "response",
                    lambda response: (
                        failures.append(f"HTTP {response.status}: {response.url}")
                        if response.status >= 500 and urlsplit(response.url).netloc == origin
                        else None
                    ),
                )
            for _ in range(3):
                responses = await asyncio.gather(*(page.goto(application) for page in pages))
                assert all(response and response.ok for response in responses), failures
                await asyncio.gather(
                    *(
                        async_expect(page.get_by_role("link", name="Sign in", exact=True)).not_to_have_count(0)
                        for page in pages
                    )
                )
                await asyncio.gather(
                    *(
                        page.get_by_role("link", name="Sign in", exact=True).filter(visible=True).first.click()
                        for page in pages
                    )
                )
                await asyncio.gather(
                    *(
                        async_expect(page.get_by_label("Username or email", exact=True)).to_be_visible()
                        for page in pages
                    )
                )
                responses = await asyncio.gather(*(page.goto(application) for page in pages))
                assert all(response and response.ok for response in responses), failures
                await asyncio.gather(
                    *(page.get_by_role("searchbox", name="Search", exact=True).fill("acme-docs") for page in pages)
                )
                await asyncio.gather(
                    *(page.get_by_role("searchbox", name="Search", exact=True).press("Enter") for page in pages)
                )
                await asyncio.gather(*(repository_result(page, "alice-dev", "acme-docs").click() for page in pages))
                await asyncio.gather(*(page.get_by_role("link", name="Code", exact=True).click() for page in pages))
                await asyncio.gather(
                    *(
                        async_expect(page.get_by_role("link", name="README.md", exact=True)).to_be_visible()
                        for page in pages
                    )
                )
        finally:
            await browser.close()
    return failures


@pytest.mark.requirements("REQ-1-1-2", "REQ-3-1", "REQ-3-3", "REQ-4-1")
def test_github_full_independent_browsers_can_navigate_without_server_errors(application, request):
    log = request.config.stash[SERVER_LOG]
    offset = len(log.read_bytes())
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            failures = executor.submit(
                asyncio.run, concurrent_visitors(application, request.config.getoption("--browser-channel"))
            ).result()
        assert not failures, "Browser and server response errors: " + "\n".join(failures)
    finally:
        errors = server_errors(log.read_bytes()[offset:].decode("utf-8", errors="replace"))
        assert not errors, "Application server errors during independent navigation: " + "\n".join(errors)
