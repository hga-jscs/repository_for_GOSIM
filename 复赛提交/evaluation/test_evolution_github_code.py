"""Thirteen current public file, history, search and branch scenarios."""

import re
import uuid

import pytest
from playwright.sync_api import expect

from evolution_github_baseline_helpers import application, browser_errors, displayed_text, public_repository, signed_repository
from evolution_github_helpers import not_actionable
from test_evolution_github_discovery import open_repository
from test_evolution_github_repositories import repository_state


@pytest.mark.requirements("REQ-4-1")
@pytest.mark.scenarios("REQ-4-1#1")
def test_evolution_github_browse_directory_file_and_reload(page, application):
    public_repository(page, application)
    page.get_by_role("link", name="Code", exact=True).click()
    original = repository_state(page)
    page.get_by_role("link", name="src", exact=True).click()
    page.get_by_role("link", name="README.md", exact=True).click()
    for refresh in (False, True):
        if refresh:
            page.reload()
        expect(page.get_by_role("main")).to_contain_text("Document search flow")
    assert repository_state(page) == original


@pytest.mark.requirements("REQ-4-2-1")
@pytest.mark.scenarios("REQ-4-2-1#1")
def test_evolution_github_history_displays_message_author_and_time(page, application):
    public_repository(page, application)
    page.get_by_role("link", name="Commits", exact=True).click()
    row = page.get_by_role("listitem").filter(has=page.get_by_role("link", name="Document search flow", exact=True))
    expect(row).to_have_count(1)
    expect(row).to_contain_text("alice-dev")
    expect(row).to_contain_text("ago")
    page.reload()
    expect(row).to_contain_text("Document search flow")


@pytest.mark.requirements("REQ-4-2-2")
@pytest.mark.scenarios("REQ-4-2-2#1")
def test_evolution_github_commit_diff_read_only_and_deep_reload(page, application):
    public_repository(page, application)
    original = repository_state(page)
    page.get_by_role("link", name="Commits", exact=True).click()
    page.get_by_role("link", name="Document search flow", exact=True).click()
    for refresh in (False, True):
        if refresh:
            page.reload()
        expect(page.get_by_role("main")).to_contain_text("src/search.ts")
        expect(page.get_by_role("main")).to_contain_text("Changed files")
        changes = page.get_by_role("listitem").filter(has_text="src/search.ts")
        expect(changes).to_have_count(1)
        expect(changes).to_contain_text(re.compile(r"(?:\d+ additions|\+\d+)"))
        expect(changes).to_contain_text(re.compile(r"(?:\d+ deletions|[−-]\d+)"))
    assert repository_state(page) == original


def code_search(page, query):
    search = page.get_by_role("main").get_by_role("searchbox", name="Search", exact=True)
    search.fill(query)
    search.press("Enter")
    # Code results are distinct from the persistent Code navigation link.
    results = page.get_by_text("Code", exact=True).and_(page.locator(":not(a)"))
    expect(results).to_have_count(1)
    results.click()


@pytest.mark.requirements("REQ-4-2-3")
@pytest.mark.parametrize(("scenario",), [
    pytest.param(1, marks=pytest.mark.scenarios("REQ-4-2-3#1")),
    pytest.param(3, marks=pytest.mark.scenarios("REQ-4-2-3#3")),
])
def test_evolution_github_code_search_opens_matching_file(page, application, scenario):
    public_repository(page, application)
    original = repository_state(page)
    code_search(page, "search flow")
    page.get_by_role("link", name="README.md", exact=True).click()
    expect(page.get_by_role("main")).to_contain_text("search flow")
    if scenario == 3:
        page.reload()
        expect(page.get_by_role("main")).to_contain_text("search flow")
        expect(page.get_by_role("link", name="README.md", exact=True)).to_be_visible()
    assert repository_state(page) == original


@pytest.mark.requirements("REQ-4-2-3")
@pytest.mark.parametrize(("repeat",), [
    pytest.param(False, marks=pytest.mark.scenarios("REQ-4-2-3#2")),
    pytest.param(True, marks=pytest.mark.scenarios("REQ-4-2-3#4")),
])
def test_evolution_github_code_search_absent_query_is_stable(page, application, repeat):
    destination = public_repository(page, application)
    original = repository_state(page)
    for _ in range(2 if repeat else 1):
        page.goto(destination)
        code_search(page, "no-such-token")
        expect(page.get_by_role("main")).to_contain_text(re.compile(r"(no code|no results|couldn.t find any code)", re.I))
        expect(page.get_by_role("main").get_by_role("searchbox", name="Search", exact=True)).to_have_value("no-such-token")
    assert repository_state(page) == original


@pytest.mark.requirements("REQ-3-1", "REQ-2-3", "REQ-4-2-3")
@pytest.mark.full_evolution
def test_evolution_github_global_search_survives_local_search_pages(page, application):
    destination = signed_repository(page, application, "repo-admin")
    for context in ("repository", "access"):
        page.goto(destination)
        if context == "access":
            page.get_by_role("link", name="Settings", exact=True).click()
            page.get_by_role("link", name="Manage access", exact=True).click()
            page.get_by_role("button", name="Add people or teams", exact=True).click()
        local = page.get_by_role("main").get_by_role(
            "textbox" if context == "access" else "searchbox", name="Search", exact=True
        )
        expect(local).to_have_count(1)
        expect(local).to_be_visible()
        search = page.get_by_role("banner").get_by_role("searchbox", name="Search", exact=True)
        expect(search).to_have_count(1)
        expect(search).to_be_visible()
        search.fill("evo-search-catalog-s1")
        search.press("Enter")
        page.get_by_role("link", name="evo-search-catalog-s1", exact=True).click()
        expect(page.get_by_role("heading", name=re.compile(r"evo-search-catalog-s1$"))).to_be_visible()


@pytest.mark.requirements("REQ-4-3-2")
@pytest.mark.scenarios("REQ-4-3-2#1")
def test_evolution_github_create_branch_from_existing_head(page, application):
    signed_repository(page, application, "branch-contributor", "branch-switch-demo")
    original = repository_state(page)
    name = "pw-branch-" + uuid.uuid4().hex[:10]
    page.get_by_role("button", name="Branch main", exact=True).click()
    page.get_by_role("textbox", name="Find branch", exact=True).fill(name)
    page.get_by_role("option", name="Create branch: " + name, exact=True).click()
    expect(page.get_by_role("button", name="Branch " + name, exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("button", name="Branch " + name, exact=True)).to_be_visible()
    assert repository_state(page)["commits"] == original["commits"]
    assert repository_state(page)["files"] == original["files"]


@pytest.mark.requirements("REQ-4-3-2")
@pytest.mark.scenarios("REQ-4-3-2#2")
def test_evolution_github_invalid_branch_is_rejected_before_creation(page, application):
    signed_repository(page, application, "branch-contributor", "branch-switch-demo")
    original = repository_state(page)
    page.get_by_role("button", name="Branch main", exact=True).click()
    page.get_by_role("textbox", name="Find branch", exact=True).fill("invalid..branch")
    expect(page.get_by_text(re.compile(r"^Invalid branch"))).to_be_visible()
    expect(page.get_by_role("option", name="Create branch: invalid..branch", exact=True)).to_have_count(0)
    assert repository_state(page) == original


@pytest.mark.requirements("REQ-4-3-3")
@pytest.mark.scenarios("REQ-4-3-3#1")
def test_evolution_github_admin_changes_default_without_removing_main(page, application):
    signed_repository(page, application, "default-branch-admin", "default-branch-demo")
    page.get_by_role("link", name="Settings", exact=True).click()
    page.get_by_role("link", name="Branches", exact=True).click()
    page.get_by_role("combobox", name="Default branch", exact=True).select_option(label="release")
    page.get_by_role("button", name="Update", exact=True).click()
    page.get_by_role("button", name="Confirm", exact=True).click()
    expect(page.get_by_role("button", name="Confirm", exact=True)).not_to_be_visible()
    open_repository(page, application, "default-branch-demo")
    page.get_by_role("button", name="Branch release", exact=True).click()
    expect(page.get_by_role("option", name="main", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    page.reload()
    expect(page.get_by_role("button", name="Branch release", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-4-3-3")
@pytest.mark.scenarios("REQ-4-3-3#2")
def test_evolution_github_viewer_cannot_change_default_branch(page, application):
    signed_repository(page, application, "default-branch-viewer", "default-branch-demo")
    for name in ("Settings", "Branches"):
        control = page.get_by_role("link", name=name, exact=True)
        if control.is_visible():
            control.click()
    expect(page.get_by_role("combobox", name="Default branch", exact=True)).to_have_count(0)
    not_actionable(page, "Update default branch")
    not_actionable(page, "Update")


@pytest.mark.requirements("REQ-4-4")
@pytest.mark.parametrize(("valid",), [
    pytest.param(True, marks=pytest.mark.scenarios("REQ-4-4#1")),
    pytest.param(False, marks=pytest.mark.scenarios("REQ-4-4#2")),
])
def test_evolution_github_file_creation_commit_or_atomic_rejection(page, application, valid):
    signed_repository(page, application, "file-contributor", "file-management-demo")
    original = repository_state(page)
    filename = "pw-file-" + uuid.uuid4().hex[:10] + ".md" if valid else "../invalid.md"
    content = "Persist this newly authored file." if valid else "must not be saved"
    message = "Add " + filename if valid else ""
    page.get_by_role("button", name="Add file", exact=True).click()
    page.get_by_role("menuitem", name="Create new file", exact=True).or_(page.get_by_role("link", name="Create new file", exact=True)).click()
    page.get_by_label("File name", exact=True).fill(filename)
    page.get_by_role("textbox", name="File contents", exact=True).fill(content)
    page.get_by_label("Commit message", exact=True).fill(message)
    page.get_by_role("button", name="Commit changes", exact=True).click()
    if valid:
        saved_content = displayed_text(page, content)
        expect(saved_content).to_be_visible()
        page.reload()
        expect(saved_content).to_be_visible()
        page.get_by_role("link", name="Commits", exact=True).click()
        expect(page.get_by_role("link", name=message, exact=True)).to_be_visible()
        assert len(repository_state(page)["commits"]) == len(original["commits"]) + 1
    else:
        expect(page.get_by_text(re.compile(r"^(Invalid file path|Commit message is required)$"))).to_be_visible()
        expect(page.get_by_label("File name", exact=True)).to_have_value(filename)
        assert repository_state(page)["files"] == original["files"]
        assert repository_state(page)["commits"] == original["commits"]
