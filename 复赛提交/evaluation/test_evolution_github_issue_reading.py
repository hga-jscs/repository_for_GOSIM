"""Five exact public issue list and detail scenarios."""

import pytest
from playwright.sync_api import expect

from evolution_github_baseline_helpers import application, browser_errors, issue, issue_state, public_repository


@pytest.mark.requirements("REQ-5-1-1")
@pytest.mark.parametrize(("status", "title"), [
    pytest.param("Open", "Improve onboarding", marks=pytest.mark.scenarios("REQ-5-1-1#1")),
    pytest.param("Closed", "Legacy welcome text", marks=pytest.mark.scenarios("REQ-5-1-1#2")),
])
def test_evolution_github_issue_filter_is_live_and_status_scoped(page, application, status, title):
    public_repository(page, application)
    page.get_by_role("link", name="Issues", exact=True).click()
    page.get_by_role("link", name=status, exact=True).click()
    page.get_by_role("searchbox", name="Search issues", exact=True).fill(title)
    expect(page.get_by_role("link", name=title, exact=True)).to_be_visible()
    other = "Legacy welcome text" if status == "Open" else "Improve onboarding"
    expect(page.get_by_role("link", name=other, exact=True)).to_have_count(0)
    page.reload()
    expect(page.get_by_role("link", name=title, exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=other, exact=True)).to_have_count(0)


@pytest.mark.requirements("REQ-5-1-2")
@pytest.mark.parametrize(("scenario",), [
    pytest.param(1, marks=pytest.mark.scenarios("REQ-5-1-2#1")),
    pytest.param(2, marks=pytest.mark.scenarios("REQ-5-1-2#2")),
    pytest.param(3, marks=pytest.mark.scenarios("REQ-5-1-2#3")),
])
def test_evolution_github_issue_detail_reopens_consistently(page, application, scenario):
    destination = issue(page, application, "Improve onboarding")
    before = issue_state(page)
    if scenario == 2:
        page.reload()
    elif scenario == 3:
        page.goto(application)
        assert issue(page, application, "Improve onboarding") == destination
    expect(page.get_by_role("heading", name="Improve onboarding", exact=True)).to_be_visible()
    expect(page.get_by_text("Describe the onboarding improvement.", exact=True)).to_be_visible()
    expect(page.get_by_text("Open", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Activity", exact=True)).to_be_visible()
    assert issue_state(page) == before
