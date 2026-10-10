"""Public PR scenarios with independent real databases and browser sessions."""

import re
import uuid
import pytest
from playwright.sync_api import expect
from evolution_github_helpers import not_actionable
from test_evolution_github_pulls import application, browser_errors, repository, pull, compare, detail, shown_diff, shown_commits

@pytest.mark.requirements("REQ-6-5")
@pytest.mark.scenarios("REQ-6-5#1")
def test_evolution_github_merge_writes_branch_and_terminal_pr(page, application):
    endpoint = pull(page, application, "Mergeable onboarding PR", "pr-maintainer")
    expect(page.get_by_text("Create a merge commit", exact=True).filter(visible=True)).to_be_visible()
    not_actionable(page, "Squash and merge")
    not_actionable(page, "Rebase and merge")
    before = detail(page, endpoint)
    assert before["merge"]["canMerge"]
    page.get_by_role("button", name="Merge pull request", exact=True).click()
    page.get_by_role("button", name="Confirm merge", exact=True).click()
    expect(page.get_by_text("Merged", exact=True)).to_be_visible()
    current = detail(page, endpoint)
    assert current["pr"]["merged_by"] == "pr-maintainer" and current["pr"]["merged_at"]
    commit = current["pr"]["merge_commit_id"]
    assert commit and commit != before["merge"]["baseHead"]["id"]
    expect(page.get_by_text(re.compile(r"\b" + str(commit) + r"\b")).filter(visible=True).first).to_be_visible()
    page.reload()
    expect(page.get_by_text("Merged", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Close pull request", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Reopen pull request", exact=True)).to_have_count(0)
    repo = endpoint.rsplit("/pulls/", 1)[0]
    commits = detail(page, repo + "/commits?branch=main")["commits"]
    assert commits[0]["id"] == commit
    compare_state = detail(page, repo + "/pulls/compare?base=main&compare=feature-search")
    assert compare_state["identical"]
    for action in ("close", "reopen", "merge"):
        assert page.request.post(endpoint + "/" + action).status in {403, 409}
    assert detail(page, endpoint)["pr"] == current["pr"]


@pytest.mark.requirements("REQ-6-5")
@pytest.mark.scenarios("REQ-6-5#2")
def test_evolution_github_blocked_merge_has_reason_without_writes(page, application):
    endpoint = pull(page, application, "Blocked onboarding PR", "pr-maintainer")
    before = detail(page, endpoint)
    repo = endpoint.rsplit("/pulls/", 1)[0]
    branch = detail(page, repo + "/tree?branch=main")
    expect(page.get_by_role("button", name="Merge pull request", exact=True)).to_be_disabled()
    expect(page.get_by_text("Review required by branch protection", exact=True)).to_be_visible()
    assert detail(page, endpoint) == before
    assert detail(page, repo + "/tree?branch=main") == branch


@pytest.mark.requirements("REQ-6-6")
@pytest.mark.scenarios("REQ-6-6#1")
def test_evolution_github_close_reopen_keeps_branches_discussion(page, application):
    endpoint = pull(page, application, "Closable onboarding PR", "pr-author")
    before = detail(page, endpoint)
    repo = endpoint.rsplit("/pulls/", 1)[0]
    branch = detail(page, repo + "/commits?branch=main")
    page.get_by_role("button", name="Close pull request", exact=True).click()
    expect(page.get_by_text("Closed", exact=True)).to_be_visible()
    page.get_by_role("button", name="Reopen pull request", exact=True).click()
    expect(page.get_by_text("Open", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("button", name="Close pull request", exact=True)).to_be_visible()
    after = detail(page, endpoint)
    for key in ("title", "base_branch", "compare_branch", "description"):
        assert after["pr"][key] == before["pr"][key]
    for key in ("comments", "reviews"):
        assert after["conversation"][key] == before["conversation"][key]
    assert detail(page, repo + "/commits?branch=main") == branch


@pytest.mark.requirements("REQ-6-6")
@pytest.mark.scenarios("REQ-6-6#2")
def test_evolution_github_read_viewer_cannot_close_reopen(page, application):
    endpoint = pull(page, application, "Protected onboarding PR", "pr-viewer")
    before = detail(page, endpoint)
    for label, action in (("Close pull request", "close"), ("Reopen pull request", "reopen")):
        expect(page.get_by_role("button", name=label, exact=True)).to_have_count(0)
        assert page.request.post(endpoint + "/" + action).status in {401, 403, 409}
    assert detail(page, endpoint) == before

