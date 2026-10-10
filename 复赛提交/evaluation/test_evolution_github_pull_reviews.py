"""Public PR scenarios with independent real databases and browser sessions."""

import re
import uuid
import pytest
from playwright.sync_api import expect
from evolution_github_helpers import not_actionable
from test_evolution_github_pulls import application, browser_errors, repository, pull, compare, detail, shown_diff, shown_commits

@pytest.mark.requirements("REQ-6-3-1")
@pytest.mark.parametrize(("scenario",), [pytest.param(n, marks=pytest.mark.scenarios(f"REQ-6-3-1#{n}")) for n in (1, 2, 3)])
def test_evolution_github_pull_overview_navigation(page, application, scenario):
    endpoint = pull(page, application, "Overview onboarding PR")
    before = detail(page, endpoint)
    if scenario == 1:
        page.get_by_role("link", name="Commits", exact=True).click()
        shown_commits(page, endpoint)
        page.get_by_role("link", name="Files changed", exact=True).click()
        shown_diff(page)
    elif scenario == 2:
        page.reload()
        page.get_by_role("link", name="Commits", exact=True).click()
        shown_commits(page, endpoint)
    else:
        pull(page, application, "Overview onboarding PR")
        page.get_by_role("link", name="Files changed", exact=True).click()
        shown_diff(page)
    expect(page.get_by_role("heading", name="Overview onboarding PR", exact=True)).to_be_visible()
    assert detail(page, endpoint) == before


@pytest.mark.requirements("REQ-6-3-2")
@pytest.mark.scenarios("REQ-6-3-2#1")
def test_evolution_github_public_diff_reads_without_mutations(page, application):
    endpoint = pull(page, application, "Public onboarding PR")
    before = detail(page, endpoint)
    page.get_by_role("link", name="Files changed", exact=True).click()
    shown_diff(page)
    assert detail(page, endpoint) == before
    not_actionable(page, "Add comment")
    not_actionable(page, "Review changes")


@pytest.mark.requirements("REQ-6-3-3")
@pytest.mark.parametrize(("title", "action", "pending"), [
    pytest.param("Reviewable onboarding PR", "Add single comment", False, marks=pytest.mark.scenarios("REQ-6-3-3#1")),
    pytest.param("Pending review onboarding PR", "Start a review", True, marks=pytest.mark.scenarios("REQ-6-3-3#2")),
])
def test_evolution_github_line_comments_persist(page, application, title, action, pending):
    pull(page, application, title, "pr-reviewer")
    page.get_by_role("link", name="Files changed", exact=True).click()
    page.get_by_role("button", name="Add comment", exact=True).first.click()
    body = "Line review " + uuid.uuid4().hex[:8]
    page.get_by_label("Comment", exact=True).fill(body)
    page.get_by_role("button", name=action, exact=True).click()
    expect(page.get_by_text(body, exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_text(body, exact=True)).to_be_visible()
    if pending:
        expect(page.get_by_text("Pending review", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-6-3-4")
@pytest.mark.parametrize(("title", "decision", "status"), [
    pytest.param("Reviewable onboarding PR", "Approve", "Approved", marks=pytest.mark.scenarios("REQ-6-3-4#1")),
    pytest.param("Change request onboarding PR", "Request changes", "Changes requested", marks=pytest.mark.scenarios("REQ-6-3-4#2")),
])
def test_evolution_github_submit_review(page, application, title, decision, status):
    pull(page, application, title, "pr-reviewer")
    page.get_by_role("link", name="Files changed", exact=True).click()
    page.get_by_role("button", name="Review changes", exact=True).click()
    summary = "Review summary " + uuid.uuid4().hex[:8]
    if decision == "Request changes":
        page.get_by_role("textbox", name=re.compile(r"^(Summary|Comment)$")).fill(summary)
    page.get_by_role("radio", name=decision, exact=True).check()
    page.get_by_role("button", name="Submit review", exact=True).click()
    expect(page.get_by_text(status, exact=True).filter(visible=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_text(status, exact=True).filter(visible=True).first).to_be_visible()
    if decision == "Request changes":
        expect(page.get_by_text(summary, exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-6-4")
@pytest.mark.scenarios("REQ-6-4#1")
def test_evolution_github_reviewer_picker_reload_remove(page, application):
    endpoint = pull(page, application, "Reviewer request onboarding PR", "pr-author")
    before = detail(page, endpoint)["conversation"]["reviews"]
    page.get_by_role("button", name="Reviewers", exact=True).click()
    page.get_by_role("textbox", name="Search", exact=True).fill("bob-reviewer")
    page.get_by_role("option", name="bob-reviewer", exact=True).click()
    expect(page.get_by_text("bob-reviewer", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_text("bob-reviewer", exact=True)).to_be_visible()
    page.get_by_role("button", name="Remove bob-reviewer", exact=True).click()
    expect(page.get_by_text("bob-reviewer", exact=True)).to_have_count(0)
    assert detail(page, endpoint)["conversation"]["reviews"] == before

