"""Shared browser helpers for public evolution Sheet scenarios and real databases."""

import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from server import application_server
from tools import WorkspaceTools
from test_sheet_requirements import drag_range, external_clipboard


@pytest.fixture
def application(request, tmp_path):
    workspace = Path(request.config.getoption("--application")).resolve()
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(tmp_path / "sheet.db")}
    with application_server(workspace, environment, time.monotonic() + 120) as (env, ready, output):
        assert ready, output
        yield env["BASE_URL"]


@pytest.fixture(autouse=True)
def browser_errors(page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield
    assert errors == [], errors


def cell(page, address):
    return page.get_by_role("gridcell", name=address, exact=True)


def cell_value(page, address, value):
    locator = cell(page, address)
    expect(locator).to_be_visible()
    deadline = time.monotonic() + 10
    actual = None
    while time.monotonic() < deadline:
        actual = locator.evaluate("""element => {
            const copy = element.cloneNode(true);
            copy.querySelectorAll('button,[role="listbox"],[aria-hidden="true"]').forEach(node => node.remove());
            return (copy.textContent || '').trim();
        }""")
        if actual == value:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"{address}: expected {value!r}, received {actual!r}")


def command(page, menu, name):
    page.get_by_role("button", name=menu, exact=True).click()
    page.get_by_role("menuitem", name=name, exact=True).or_(page.get_by_role("button", name=name, exact=True)).click()


def open_workbook(page, application, name, worksheet=None):
    page.goto(application)
    page.get_by_role("link", name=name, exact=True).click()
    expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
    if worksheet:
        expect(page.get_by_role("tab", name=worksheet, exact=True)).to_have_attribute("aria-selected", "true")
    return application + "/api/workbooks/" + page.url.rstrip("/").split("/")[-1]


def workbook(page, endpoint):
    response = page.request.get(endpoint)
    assert response.ok, response.text()
    return response.json()["workbook"]


def active_sheet(data):
    return next(sheet for sheet in data["worksheets"] if sheet["is_active"])


def formula_entry(page, address, value):
    cell(page, address).click()
    entry = page.get_by_role("textbox", name="Formula bar", exact=True)
    entry.fill(value)
    entry.press("Enter")


def rename_sheet(page, name):
    page.get_by_role("button", name="Worksheet options for " + name, exact=True).click()
    page.get_by_role("menuitem", name="Rename", exact=True).click()
    return page.get_by_role("dialog", name="Rename worksheet", exact=True)


def validation(page, address):
    cell(page, address).click()
    command(page, "Data", "Data validation")
    return page.get_by_role("dialog", name="Data validation", exact=True)
