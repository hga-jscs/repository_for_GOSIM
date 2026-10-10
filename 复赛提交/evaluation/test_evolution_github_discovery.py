"""Public evolution YAML: case-insensitive literal search and branch addresses."""

import re
from urllib.parse import unquote

import pytest
from playwright.sync_api import expect


def search(page, application, query):
    page.goto(application)
    box = page.get_by_role("searchbox", name="Search", exact=True)
    box.fill(query)
    box.press("Enter")


def open_repository(page, application, name):
    search(page, application, name)
    page.get_by_role("link", name=name, exact=True).click()
    expect(page.get_by_role("heading", name=re.compile(re.escape(name)))).to_be_visible()


@pytest.mark.requirements("REQ-3-1")
@pytest.mark.scenarios("REQ-3-1#1")
def test_evolution_github_search_name_case(page, application):
    search(page, application, "EVO-SEARCH-CATALOG-S1")
    page.get_by_role("link", name="evo-search-catalog-s1", exact=True).click()
    expect(page.get_by_role("heading", name=re.compile("evo-search-catalog-s1"))).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name=re.compile("evo-search-catalog-s1"))).to_be_visible()


@pytest.mark.requirements("REQ-3-1")
@pytest.mark.scenarios("REQ-3-1#2")
def test_evolution_github_search_persisted_description(page, application):
    search(page, application, "evolution-notebook")
    expect(page.get_by_role("link", name="evo-search-notebook-s2", exact=True)).to_be_visible()
    search(page, application, "EVOLUTION-NOTEBOOK")
    expect(page.get_by_role("link", name="evo-search-notebook-s2", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-3-1")
@pytest.mark.scenarios("REQ-3-1#3")
def test_evolution_github_search_no_results(page, application):
    for _ in range(2):
        search(page, application, "evo-search-empty-s3")
        expect(page.get_by_text(re.compile("^No (results|repositories)$"))).to_be_visible()


@pytest.mark.requirements("REQ-4-3-1")
@pytest.mark.parametrize(("number",), [
    pytest.param(1, marks=pytest.mark.scenarios("REQ-4-3-1#1")),
    pytest.param(3, marks=pytest.mark.scenarios("REQ-4-3-1#3")),
])
def test_evolution_github_branch_snapshot_and_reload(page, application, number):
    open_repository(page, application, f"evo-branch-switch-s{number}")
    page.get_by_role("button", name=f"Branch evo-main-s{number}", exact=True).click()
    page.get_by_role("textbox", name="Find branch", exact=True).fill(f"evo-feature-s{number}")
    page.get_by_role("option", name=f"evo-feature-s{number}", exact=True).click()
    expect(page.get_by_role("button", name=f"Branch evo-feature-s{number}", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=f"evo-target-s{number}.md", exact=True)).to_be_visible()
    assert f"evo-feature-s{number}" in unquote(page.url)
    page.reload()
    expect(page.get_by_role("button", name=f"Branch evo-feature-s{number}", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=f"evo-target-s{number}.md", exact=True)).to_be_visible()
    assert f"evo-feature-s{number}" in unquote(page.url)


@pytest.mark.requirements("REQ-4-3-1")
@pytest.mark.scenarios("REQ-4-3-1#2")
def test_evolution_github_unmatched_branch_preserves_initial_address(page, application):
    open_repository(page, application, "evo-branch-switch-s2")
    button = page.get_by_role("button", name="Branch evo-main-s2", exact=True)
    expect(button).to_be_visible()
    expect(page).to_have_url(re.compile("evo-main-s2"))
    original = page.url
    button.click()
    expect(page.get_by_role("option", name="evo-main-s2", exact=True)).to_be_visible()
    page.get_by_role("textbox", name="Find branch", exact=True).fill("evo-missing-s2")
    expect(page.get_by_text("No matching branch", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("textbox", name="Find branch", exact=True)).not_to_be_visible()
    assert page.url == original
    page.reload()
    expect(button).to_be_visible()
    assert page.url == original
