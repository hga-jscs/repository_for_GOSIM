"""Preserved public baseline prerequisites used by the five changed workflows."""

import pytest
from playwright.sync_api import expect

import test_sheet_requirements as baseline
from test_browser import create_workbook
from evolution_sheet_helpers import application, browser_errors, active_sheet, cell, cell_value, formula_entry, workbook


@pytest.mark.shared
def test_evolution_sheet_fresh_seed_matches_public_baseline(page, browser, application):
    baseline.test_sheet_full_seed_workbook_is_available_and_directly_reopenable(page, browser, application)


@pytest.mark.shared
def test_evolution_sheet_repeated_blank_creation_preserves_unique_names_and_independent_data(page, application):
    names = []
    entries = []
    for number in range(3):
        create_workbook(page, application)
        endpoint = application + "/api/workbooks/" + page.url.rstrip("/").split("/")[-1]
        data = workbook(page, endpoint)
        names.append(data["name"])
        assert len(data["worksheets"]) == 1
        assert active_sheet(data)["name"] == "Sheet1"
        assert all(value == "" for value in active_sheet(data)["cells"].values())
        expect(cell(page, "A1")).to_have_attribute("aria-selected", "true")
        value = f"Independent workbook {number}"
        formula_entry(page, "B2", value)
        cell_value(page, "B2", value)
        entries.append((page.url, data["name"], value))
    assert len({name.casefold() for name in names}) == 3
    for url, name, value in entries:
        page.goto(url)
        expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
        cell_value(page, "B2", value)
        page.reload()
        cell_value(page, "B2", value)
    page.goto(application)
    for name in names:
        expect(page.get_by_role("link", name=name, exact=True)).to_have_count(1)
