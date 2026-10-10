"""Public whole-displayed-cell search and atomic replacement behavior."""

import pytest
from playwright.sync_api import expect

from test_browser import create_workbook
from evolution_sheet_helpers import application, browser_errors, active_sheet, cell, cell_value, command, formula_entry, open_workbook, workbook


def find_dialog(page):
    command(page, "Edit", "Find and replace")
    dialog = page.get_by_role("dialog", name="Find and replace", exact=True)
    expect(dialog.get_by_role("textbox", name="Find", exact=True)).to_be_visible()
    expect(dialog.get_by_role("textbox", name="Replace with", exact=True)).to_be_visible()
    expect(dialog.get_by_role("checkbox", name="Match case", exact=True)).not_to_be_checked()
    return dialog


def replace(dialog, search, replacement, count, match_case=False):
    dialog.get_by_role("textbox", name="Find", exact=True).fill(search)
    dialog.get_by_role("textbox", name="Replace with", exact=True).fill(replacement)
    dialog.get_by_role("checkbox", name="Match case", exact=True).set_checked(match_case)
    dialog.get_by_role("button", name="Replace all", exact=True).click()
    expect(dialog.get_by_text(f"Replaced {count} cells", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-6-2-1")
@pytest.mark.scenarios("REQ-6-2-1#1")
def test_evolution_sheet_find_next_selects_second_match(page, application):
    endpoint = open_workbook(page, application, "EVO-N02-FIND-NEXT", "Narrative")
    original = active_sheet(workbook(page, endpoint))["cells"]
    cell(page, "A1").click()
    dialog = find_dialog(page)
    dialog.get_by_role("textbox", name="Find", exact=True).fill("Cobalt")
    dialog.get_by_role("button", name="Find next", exact=True).click()
    expect(cell(page, "E4")).to_have_attribute("aria-selected", "true")
    dialog.get_by_role("button", name="Find next", exact=True).click()
    expect(cell(page, "E7")).to_have_attribute("aria-selected", "true")
    expect(cell(page, "E4")).to_have_attribute("aria-selected", "false")
    expect(dialog.get_by_text("Match 2 of 3", exact=True)).to_be_visible()
    assert active_sheet(workbook(page, endpoint))["cells"] == original


@pytest.mark.requirements("REQ-6-2-1")
@pytest.mark.scenarios("REQ-6-2-1#2")
def test_evolution_sheet_replace_all_persists_exact_count_and_values(page, application):
    open_workbook(page, application, "EVO-N02-REPLACE-ALL", "Narrative")
    dialog = find_dialog(page)
    replace(dialog, "Cobalt", "Indigo", 3)
    for address, value in {"F3": "Indigo", "F6": "Indigo", "F9": "Indigo", "F12": "Copper"}.items():
        cell_value(page, address, value)
    page.reload()
    for address, value in {"F3": "Indigo", "F6": "Indigo", "F9": "Indigo", "F12": "Copper"}.items():
        cell_value(page, address, value)


@pytest.mark.requirements("REQ-6-2-1")
@pytest.mark.scenarios("REQ-6-2-1#3")
def test_evolution_sheet_case_sensitive_replacement_keeps_other_text(page, application):
    open_workbook(page, application, "EVO-N02-CASE-SENSITIVE", "Narrative")
    replace(find_dialog(page), "Cobalt", "Azure", 1, match_case=True)
    for address, value in {"G3": "Azure", "G4": "cobalt", "G5": "Cobalt-7"}.items():
        cell_value(page, address, value)
    page.reload()
    for address, value in {"G3": "Azure", "G4": "cobalt", "G5": "Cobalt-7"}.items():
        cell_value(page, address, value)


@pytest.mark.requirements("REQ-6-2-1")
def test_evolution_sheet_case_insensitive_whole_cell_match_is_sheet_local(page, application):
    endpoint = open_workbook(page, application, "EVO-N02-CASE-SENSITIVE", "Narrative")
    first = active_sheet(workbook(page, endpoint))["id"]
    response = page.request.post(endpoint + "/worksheets")
    assert response.ok, response.text()
    second = active_sheet(response.json()["workbook"])["id"]
    assert page.request.put(endpoint + f"/worksheets/{second}/cells/3/7", data={"value": "Cobalt"}).ok
    assert page.request.post(endpoint + f"/worksheets/{first}/activate").ok
    page.reload()
    replace(find_dialog(page), "cObAlT", "Teal", 2)
    page.reload()
    for address, value in {"G3": "Teal", "G4": "Teal", "G5": "Cobalt-7"}.items():
        cell_value(page, address, value)
    other = next(sheet for sheet in workbook(page, endpoint)["worksheets"] if sheet["id"] == second)
    assert other["cells"]["3,7"] == "Cobalt"


@pytest.mark.requirements("REQ-6-2-1")
def test_evolution_sheet_find_wraps_in_row_major_order_and_reports_no_match(page, application):
    endpoint = open_workbook(page, application, "EVO-N02-FIND-NEXT", "Narrative")
    original = active_sheet(workbook(page, endpoint))["cells"]
    cell(page, "A1").click()
    dialog = find_dialog(page)
    dialog.get_by_role("textbox", name="Find", exact=True).fill("cObAlT")
    for address, count in [("E4", 1), ("E7", 2), ("E11", 3), ("E4", 1)]:
        dialog.get_by_role("button", name="Find next", exact=True).click()
        expect(cell(page, address)).to_have_attribute("aria-selected", "true")
        expect(dialog.get_by_text(f"Match {count} of 3", exact=True)).to_be_visible()
    dialog.get_by_role("textbox", name="Find", exact=True).fill("No existing cell has this value")
    dialog.get_by_role("button", name="Find next", exact=True).click()
    expect(dialog.get_by_text("No matches", exact=True)).to_be_visible()
    expect(cell(page, "E4")).to_have_attribute("aria-selected", "true")
    assert active_sheet(workbook(page, endpoint))["cells"] == original


@pytest.mark.requirements("REQ-6-2-1", "REQ-3-1-1")
def test_evolution_sheet_replace_uses_formula_display_snapshot_and_one_undo_step(page, application):
    create_workbook(page, application)
    endpoint = application + "/api/workbooks/" + page.url.rstrip("/").split("/")[-1]
    source = active_sheet(workbook(page, endpoint))
    original = {"1,1": "2", "2,1": "=A1", "3,1": "=A2+1", "4,1": "12"}
    assert page.request.put(endpoint + f'/worksheets/{source["id"]}/cells', data={"cells": original}).ok
    page.reload()
    cell_value(page, "A2", "2")
    cell_value(page, "A3", "3")
    dialog = find_dialog(page)
    dialog.get_by_role("textbox", name="Find", exact=True).fill("=A1")
    dialog.get_by_role("button", name="Find next", exact=True).click()
    expect(dialog.get_by_text("No matches", exact=True)).to_be_visible()
    replace(dialog, "2", "3", 2)
    dialog.get_by_role("button", name="Close", exact=True).click()
    for address, value in {"A1": "3", "A2": "3", "A3": "4", "A4": "12"}.items():
        cell_value(page, address, value)
    data = active_sheet(workbook(page, endpoint))
    assert data["cells"]["2,1"] == "3" and data["cells"]["3,1"] == "=A2+1"
    page.get_by_role("button", name="Undo", exact=True).click()
    cell_value(page, "A1", "2")
    cell_value(page, "A2", "2")
    cell_value(page, "A3", "3")
    assert active_sheet(workbook(page, endpoint))["cells"] == original
    page.get_by_role("button", name="Redo", exact=True).click()
    cell_value(page, "A3", "4")
    page.reload()
    for address, value in {"A1": "3", "A2": "3", "A3": "4", "A4": "12"}.items():
        cell_value(page, address, value)


@pytest.mark.requirements("REQ-6-2-1", "REQ-5-2-1")
def test_evolution_sheet_replace_all_rejects_every_change_on_validation_failure(page, application):
    create_workbook(page, application)
    endpoint = application + "/api/workbooks/" + page.url.rstrip("/").split("/")[-1]
    source = active_sheet(workbook(page, endpoint))
    sheet_path = endpoint + f'/worksheets/{source["id"]}'
    assert page.request.put(sheet_path + "/cells", data={"cells": {"1,1": "Cobalt", "1,2": "Cobalt", "1,4": "previous"}}).ok
    assert page.request.post(sheet_path + "/validations", data={"row_start": 1, "col_start": 1, "row_end": 1, "col_end": 1, "type": "dropdown", "allowed_values": "Cobalt", "error_message": "Keep Cobalt for reviewed cell"}).ok
    page.reload()
    formula_entry(page, "D1", "saved")
    cell_value(page, "D1", "saved")
    before = active_sheet(workbook(page, endpoint))["cells"]
    dialog = find_dialog(page)
    dialog.get_by_role("textbox", name="Find", exact=True).fill("Cobalt")
    dialog.get_by_role("textbox", name="Replace with", exact=True).fill("Indigo")
    dialog.get_by_role("button", name="Replace all", exact=True).click()
    expect(page.get_by_text("Keep Cobalt for reviewed cell", exact=True)).to_be_visible()
    expect(dialog.get_by_text("Replaced 2 cells", exact=True)).to_have_count(0)
    assert active_sheet(workbook(page, endpoint))["cells"] == before
    dialog.get_by_role("button", name="Close", exact=True).click()
    page.get_by_role("button", name="Undo", exact=True).click()
    cell_value(page, "D1", "previous")
    page.reload()
    cell_value(page, "A1", "Cobalt")
    cell_value(page, "B1", "Cobalt")
    cell_value(page, "D1", "previous")
