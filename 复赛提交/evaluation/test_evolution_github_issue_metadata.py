"""Five public issue metadata and state scenarios."""

import re

import pytest
from playwright.sync_api import expect

from evolution_github_baseline_helpers import application, browser_errors, choose_option, issue, issue_state, saved_mutation
from evolution_github_helpers import not_actionable


@pytest.mark.requirements("REQ-5-3-1")
@pytest.mark.scenarios("REQ-5-3-1#1")
def test_evolution_github_issue_assignment_toggles_only_relationship(page, application):
    issue(page, application, "Assignable onboarding issue", "issue-editor")
    before = issue_state(page)
    assert "bob-reviewer" not in before["assignees"]
    page.get_by_role("button", name="Assignees", exact=True).click()
    page.get_by_role("textbox", name="Search assignees", exact=True).fill("bob-reviewer")
    with saved_mutation(page) as saved:
        choose_option(page, "bob-reviewer")
    assert saved.value.ok
    assert "bob-reviewer" in issue_state(page)["assignees"]
    page.reload()
    expect(page.get_by_text("bob-reviewer", exact=True)).to_be_visible()
    page.get_by_role("button", name="Assignees", exact=True).click()
    with saved_mutation(page) as saved:
        choose_option(page, "bob-reviewer")
    assert saved.value.ok
    assert "bob-reviewer" not in issue_state(page)["assignees"]
    page.reload()
    expect(page.get_by_text("bob-reviewer", exact=True)).to_have_count(0)
    after = issue_state(page)
    for key in ("title", "description", "status", "comments", "labels", "milestone"):
        assert after[key] == before[key]


@pytest.mark.parametrize(("requirement", "title", "button", "value", "field"), [
    pytest.param("REQ-5-3-2", "Labelable onboarding issue", "Labels", "bug", "labels", marks=[pytest.mark.requirements("REQ-5-3-2"), pytest.mark.scenarios("REQ-5-3-2#1")]),
    pytest.param("REQ-5-3-3", "Milestone onboarding issue", "Milestone", "v1.0", "milestone", marks=[pytest.mark.requirements("REQ-5-3-3"), pytest.mark.scenarios("REQ-5-3-3#1")]),
])
def test_evolution_github_issue_metadata_selection_persists(page, application, requirement, title, button, value, field):
    issue(page, application, title, "issue-editor")
    before = issue_state(page)
    page.get_by_role("button", name=button, exact=True).click()
    with saved_mutation(page) as saved:
        choose_option(page, value)
    assert saved.value.ok
    page.reload()
    expect(page.get_by_text(value, exact=True)).to_be_visible()
    after = issue_state(page)
    assert after[field] != before[field]
    for key in {"title", "description", "status", "comments", "assignees", "labels", "milestone"} - {field}:
        assert after[key] == before[key]


@pytest.mark.requirements("REQ-5-4")
@pytest.mark.scenarios("REQ-5-4#1")
def test_evolution_github_issue_close_reopen_keeps_content(page, application):
    issue(page, application, "Closable onboarding issue", "issue-editor")
    before = issue_state(page)
    page.get_by_role("button", name="Close issue", exact=True).click()
    expect(page.get_by_text("Closed", exact=True)).to_be_visible()
    activity = page.get_by_role("listitem").filter(has_text=re.compile(r"closed", re.I))
    expect(activity).not_to_have_count(0)
    assert len(issue_state(page)["activity"]) == len(before["activity"]) + 1
    page.get_by_role("button", name="Reopen issue", exact=True).click()
    expect(page.get_by_text("Open", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("button", name="Close issue", exact=True)).to_be_visible()
    after = issue_state(page)
    for key in ("title", "description", "status", "comments", "assignees", "labels", "milestone"):
        assert after[key] == before[key]


@pytest.mark.requirements("REQ-5-4")
@pytest.mark.scenarios("REQ-5-4#2")
def test_evolution_github_issue_viewer_has_no_status_transition(page, application):
    issue(page, application, "Protected onboarding issue", "issue-viewer")
    not_actionable(page, "Close issue")
    not_actionable(page, "Reopen issue")
    expect(page.get_by_text("Open", exact=True)).to_be_visible()
