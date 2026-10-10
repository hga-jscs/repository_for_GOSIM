"""Six public issue creation, editing and discussion scenarios."""

import re
import uuid
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from evolution_github_baseline_helpers import application, browser_errors, displayed_text, issue, issue_state, saved_mutation, signed_repository


@pytest.mark.requirements("REQ-5-2-1")
@pytest.mark.parametrize(("valid",), [
    pytest.param(True, marks=pytest.mark.scenarios("REQ-5-2-1#1")),
    pytest.param(False, marks=pytest.mark.scenarios("REQ-5-2-1#2")),
])
def test_evolution_github_issue_create_or_reject_empty_title(page, application, valid):
    signed_repository(page, application, "issue-author")
    path = "/" + "/".join(urlsplit(page.url).path.strip("/").split("/")[:2])
    endpoint = application + "/api/repos" + path + "/issues"
    before = page.request.get(endpoint).json()
    page.get_by_role("link", name="Issues", exact=True).click()
    page.get_by_role("link", name="New issue", exact=True).click()
    title = "New public issue " + uuid.uuid4().hex[:10] if valid else "   "
    body = "Full issue description " + uuid.uuid4().hex[:10]
    page.get_by_label("Title", exact=True).fill(title)
    page.get_by_label("Description", exact=True).fill(body)
    page.get_by_role("button", name="Submit new issue", exact=True).click()
    if valid:
        expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
        expect(displayed_text(page, body)).to_be_visible()
        expect(page.get_by_text("Open", exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
        expect(displayed_text(page, body)).to_be_visible()
        page.get_by_role("link", name="Issues", exact=True).click()
        expect(page.get_by_role("link", name=title, exact=True)).to_be_visible()
    else:
        expect(page.get_by_role("main")).to_contain_text(re.compile(r"title.*(?:required|empty)", re.I))
        expect(page.get_by_label("Title", exact=True)).to_be_visible()
        assert page.request.get(endpoint).json() == before


@pytest.mark.requirements("REQ-5-2-2")
@pytest.mark.parametrize(("valid",), [
    pytest.param(True, marks=pytest.mark.scenarios("REQ-5-2-2#1")),
    pytest.param(False, marks=pytest.mark.scenarios("REQ-5-2-2#2")),
])
def test_evolution_github_issue_edit_preserves_untouched_fields(page, application, valid):
    original = "Editable onboarding issue" if valid else "Original issue title"
    issue(page, application, original, "issue-editor")
    before = issue_state(page)
    title = "Edited title " + uuid.uuid4().hex[:10] if valid else "   "
    page.get_by_role("button", name="Edit issue title", exact=True).click()
    page.get_by_label("Issue title", exact=True).fill(title)
    page.get_by_role("button", name="Save issue title", exact=True).click()
    if valid:
        expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
        assert issue_state(page)["description"] == before["description"]
        body = "Edited description " + uuid.uuid4().hex[:10]
        page.get_by_role("button", name="Edit issue description", exact=True).click()
        page.get_by_label("Issue description", exact=True).fill(body)
        page.get_by_role("button", name="Save issue description", exact=True).click()
        expect(displayed_text(page, body)).to_be_visible()
        page.reload()
        expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
        expect(displayed_text(page, body)).to_be_visible()
        after = issue_state(page)
        for key in ("status", "comments", "assignees", "labels", "milestone"):
            assert after[key] == before[key]
    else:
        expect(page.get_by_role("main")).to_contain_text(re.compile(r"title.*(?:required|empty)", re.I))
        page.reload()
        expect(page.get_by_role("heading", name=original, exact=True)).to_be_visible()
        assert issue_state(page) == before


@pytest.mark.requirements("REQ-5-2-3")
@pytest.mark.parametrize(("valid",), [
    pytest.param(True, marks=pytest.mark.scenarios("REQ-5-2-3#1")),
    pytest.param(False, marks=pytest.mark.scenarios("REQ-5-2-3#2")),
])
def test_evolution_github_comment_appends_exact_text_or_rejects_blank(page, application, valid):
    issue(page, application, "Commentable onboarding issue" if valid else "Comment validation issue", "issue-commenter")
    before = issue_state(page)
    count = page.get_by_role("article").count()
    text = "Comment body " + uuid.uuid4().hex[:10] if valid else "  \n  "
    page.get_by_label("Comment", exact=True).fill(text)
    submit = page.get_by_role("button", name="Comment", exact=True)
    if valid or submit.is_enabled():
        submit.click()
        if not valid:
            expect(page.get_by_role("main")).to_contain_text(re.compile(r"comment.*(?:required|empty)", re.I))
    else:
        expect(submit).to_be_disabled()
    if valid:
        article = page.get_by_role("article").filter(has=page.get_by_text(text, exact=True))
        expect(article).to_have_count(1)
        expect(article.get_by_text("issue-commenter", exact=True)).to_be_visible()
        expect(page.get_by_role("article")).to_have_count(count + 1)
        page.reload()
        expect(article).to_have_count(1)
        expect(article.get_by_text("issue-commenter", exact=True)).to_be_visible()
        after = issue_state(page)
        for key in ("title", "description", "status", "assignees", "labels", "milestone"):
            assert after[key] == before[key]
    else:
        expect(page.get_by_role("article")).to_have_count(count)
        page.reload()
        expect(page.get_by_role("article")).to_have_count(count)
        assert issue_state(page) == before
