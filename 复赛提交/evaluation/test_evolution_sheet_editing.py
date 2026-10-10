"""Three public Delete scenarios and focused shared-write regressions."""

import pytest
from playwright.sync_api import expect

import test_sheet_requirements as baseline
from test_browser import create_workbook

from evolution_sheet_helpers import application, browser_errors, open_workbook, cell, cell_value, command, drag_range, formula_entry, workbook, active_sheet


@pytest.mark.requirements("REQ-3-1-1")
@pytest.mark.scenarios("REQ-3-1-1#1")
def test_evolution_sheet_delete_text_and_formula_bar(page, application):
    open_workbook(page, application, "EVO-M03-CLEAR-TEXT", "Staging")
    cell_value(page, "H4", "obsolete tag")
    cell(page, "H4").click()
    page.keyboard.press("Delete")
    cell_value(page, "H4", "")
    expect(cell(page, "H4")).to_have_attribute("aria-selected", "true")
    expect(page.get_by_role("textbox", name="Formula bar", exact=True)).to_have_value("")
    page.reload()
    cell_value(page, "H4", "")
    cell(page, "H4").click()
    expect(page.get_by_role("textbox", name="Formula bar", exact=True)).to_have_value("")


@pytest.mark.requirements("REQ-3-1-1")
@pytest.mark.scenarios("REQ-3-1-1#2")
def test_evolution_sheet_delete_formula_recalculates_dependents(page, application):
    endpoint = open_workbook(page, application, "EVO-M03-CLEAR-FORMULA", "Calculations")
    cell_value(page, "C7", "65")
    cell_value(page, "D7", "67")
    cell(page, "C7").click()
    expect(page.get_by_role("textbox", name="Formula bar", exact=True)).to_have_value("=B7*5")
    page.keyboard.press("Delete")
    cell_value(page, "C7", "")
    cell_value(page, "D7", "2")
    expect(page.get_by_role("textbox", name="Formula bar", exact=True)).to_have_value("")
    page.reload()
    cell_value(page, "C7", "")
    cell_value(page, "D7", "2")
    current = active_sheet(workbook(page, endpoint))
    assert current["cells"].get("7,3", "") == "" and current["cells"]["7,4"] == "=C7+2"


@pytest.mark.requirements("REQ-3-1-1")
@pytest.mark.scenarios("REQ-3-1-1#3")
def test_evolution_sheet_delete_keeps_entire_rectangle_selected(page, application):
    open_workbook(page, application, "EVO-M03-CLEAR-RANGE", "Matrix")
    for address, value in {"H4": "Amber", "I4": "Delta", "H5": "Kite", "I5": "Orchid"}.items():
        cell_value(page, address, value)
    drag_range(page, "H4", "I5")
    page.keyboard.press("Delete")
    for address in ("H4", "I4", "H5", "I5"):
        cell_value(page, address, "")
        expect(cell(page, address)).to_have_attribute("aria-selected", "true")
    page.reload()
    for address in ("H4", "I4", "H5", "I5"):
        cell_value(page, address, "")


@pytest.mark.requirements("REQ-3-1-1")
def test_evolution_sheet_delete_is_undoable_and_ignores_text_inputs(page, application):
    open_workbook(page, application, "EVO-M03-CLEAR-RANGE", "Matrix")
    drag_range(page, "H4", "I5")
    page.keyboard.press("Delete")
    cell_value(page, "H4", "")
    page.keyboard.press("Control+z")
    for address, value in {"H4": "Amber", "I4": "Delta", "H5": "Kite", "I5": "Orchid"}.items():
        cell_value(page, address, value)
    page.get_by_role("button", name="Rename workbook", exact=True).click()
    field = page.get_by_role("textbox", name="Workbook name", exact=True)
    field.press("Control+a")
    field.press("Delete")
    expect(field).to_have_value("")
    page.get_by_role("button", name="Cancel", exact=True).click()
    for address, value in {"H4": "Amber", "I4": "Delta", "H5": "Kite", "I5": "Orchid"}.items():
        cell_value(page, address, value)
    cell(page, "H4").dblclick()
    editor = page.get_by_role("textbox", name="Edit H4", exact=True)
    editor.press("Control+a")
    editor.press("Delete")
    editor.press("Escape")
    cell_value(page, "H4", "Amber")


@pytest.mark.requirements("REQ-3-1-1")
def test_evolution_sheet_consecutive_edits_preserve_single_undo_steps(page, application):
    endpoint = open_workbook(page, application, "EVO-M03-CLEAR-FORMULA", "Calculations")
    # Actual browser Enter/blur transitions, without sleeps between consecutive edits.
    formula_entry(page, "B7", "14")
    formula_entry(page, "B7", "15")
    formula_entry(page, "B7", "16")
    cell_value(page, "B7", "16")
    cell_value(page, "D7", "82")
    cell(page, "E7").click()
    page.keyboard.press("Control+z")
    cell_value(page, "B7", "15")
    cell_value(page, "D7", "77")
    page.keyboard.press("Control+z")
    cell_value(page, "B7", "14")
    page.keyboard.press("Control+y")
    cell_value(page, "B7", "15")
    page.reload()
    cell_value(page, "B7", "15")
    cell_value(page, "D7", "77")
    assert active_sheet(workbook(page, endpoint))["cells"]["7,3"] == "=B7*5"


@pytest.mark.requirements("REQ-3-1-1")
def test_evolution_sheet_formula_blur_saves_original_cell_and_is_one_undo_step(page, application):
    baseline.test_sheet_full_edit_cancel_and_blur_commit_preserve_original_text(page, application)
    cell_value(page, "B3", "")
    cell(page, "D4").click()
    page.keyboard.press("Control+z")
    cell_value(page, "A3", "中文 text")
    cell_value(page, "B3", "")
    page.keyboard.press("Control+y")
    cell_value(page, "A3", "committed by blur")
    page.reload()
    for address, value in {"A1": "TRUE", "A2": "2026-10-01", "A3": "committed by blur", "B3": ""}.items():
        cell_value(page, address, value)


@pytest.mark.requirements("REQ-3-1-1")
def test_evolution_sheet_previous_save_preserves_new_formula_draft(page, application):
    create_workbook(page, application)
    formula_entry(page, "A1", "1")
    cell_value(page, "A1", "1")
    network = page.context.new_cdp_session(page)
    network.send("Network.enable")
    network.send("Network.emulateNetworkConditions", {
        "offline": False, "latency": 500,
        "downloadThroughput": -1, "uploadThroughput": -1,
    })
    try:
        entry = page.get_by_role("textbox", name="Formula bar", exact=True)
        with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/cells/1/1")) as saved:
            formula_entry(page, "A1", "=6/2")
            cell(page, "C1").click()
            entry.fill("=A1+1")
            expect(entry).to_have_value("=A1+1")
        assert saved.value.ok, saved.value.text()
        cell_value(page, "A1", "3")
        expect(entry).to_have_value("=A1+1")
        entry.press("Enter")
        cell_value(page, "C1", "4")
        page.reload()
        cell_value(page, "C1", "4")
        cell(page, "C1").click()
        expect(entry).to_have_value("=A1+1")
    finally:
        network.send("Network.emulateNetworkConditions", {
            "offline": False, "latency": 0,
            "downloadThroughput": -1, "uploadThroughput": -1,
        })
        network.detach()
