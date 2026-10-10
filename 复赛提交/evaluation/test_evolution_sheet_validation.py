"""Public custom validation messages and their existing atomic write paths."""

import pytest
from playwright.sync_api import expect

import test_sheet_requirements as baseline

from evolution_sheet_helpers import application, browser_errors, open_workbook, cell, cell_value, formula_entry, validation, command, drag_range, external_clipboard, workbook, active_sheet


@pytest.mark.requirements("REQ-5-2-1")
@pytest.mark.scenarios("REQ-5-2-1#1")
def test_evolution_sheet_custom_numeric_message_from_formula_bar(page, application):
    open_workbook(page, application, "EVO-M05-VALIDATION-FORMULA", "Thresholds")
    for _ in range(2):
        formula_entry(page, "J6", "88")
        expect(page.get_by_text("Capacity must be from 25 to 75", exact=True)).to_be_visible()
        cell_value(page, "J6", "37")
        expect(page.get_by_role("textbox", name="Formula bar", exact=True)).to_have_value("37")
        page.reload()


@pytest.mark.requirements("REQ-5-2-1")
@pytest.mark.scenarios("REQ-5-2-1#2")
def test_evolution_sheet_custom_dropdown_message_from_grid(page, application):
    open_workbook(page, application, "EVO-M05-VALIDATION-GRID", "Thresholds")
    for _ in range(2):
        cell(page, "K8").dblclick()
        editor = page.get_by_role("textbox", name="Edit K8", exact=True)
        editor.fill("Paused")
        editor.press("Enter")
        expect(page.get_by_text("Choose a queue state", exact=True)).to_be_visible()
        expect(editor).not_to_be_visible()
        cell_value(page, "K8", "Ready")
        page.reload()


@pytest.mark.requirements("REQ-5-2-1")
@pytest.mark.scenarios("REQ-5-2-1#3")
def test_evolution_sheet_custom_message_update_and_reload(page, application):
    open_workbook(page, application, "EVO-M05-VALIDATION-EDIT", "Thresholds")
    dialog = validation(page, "L4")
    expect(dialog.get_by_role("textbox", name="Error message", exact=True)).to_have_value("Capacity must be from 25 to 75")
    dialog.get_by_role("textbox", name="Error message", exact=True).fill("Allocate between 25 and 75")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).not_to_be_visible()
    formula_entry(page, "L4", "24")
    expect(page.get_by_text("Allocate between 25 and 75", exact=True)).to_be_visible()
    cell_value(page, "L4", "42")
    page.reload()
    expect(validation(page, "L4").get_by_role("textbox", name="Error message", exact=True)).to_have_value("Allocate between 25 and 75")


@pytest.mark.requirements("REQ-5-2-1")
def test_evolution_sheet_create_clear_and_delete_validation_message(page, application):
    open_workbook(page, application, "EVO-M05-VALIDATION-EDIT", "Thresholds")
    dialog = validation(page, "L4")
    dialog.get_by_role("textbox", name="Error message", exact=True).fill("   ")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).not_to_be_visible()
    formula_entry(page, "L4", "24")
    expect(page.get_by_text("Please enter a number between 25 and 75", exact=True)).to_be_visible()
    cell_value(page, "L4", "42")
    page.reload()
    dialog = validation(page, "L4")
    expect(dialog.get_by_role("textbox", name="Error message", exact=True)).to_have_value("")
    dialog.get_by_role("button", name="Delete rule", exact=True).click()
    expect(dialog).not_to_be_visible()
    formula_entry(page, "L4", "24")
    cell_value(page, "L4", "24")
    dialog = validation(page, "L4")
    expect(dialog).to_be_visible()
    dialog.get_by_role("combobox", name="Rule type", exact=True).select_option(label="Number range")
    dialog.get_by_role("textbox", name="Minimum", exact=True).fill("10")
    dialog.get_by_role("textbox", name="Maximum", exact=True).fill("30")
    dialog.get_by_role("textbox", name="Error message", exact=True).fill("  Stay within capacity  ")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).not_to_be_visible()
    formula_entry(page, "L4", "31")
    expect(page.get_by_text("Stay within capacity", exact=True)).to_be_visible()
    cell_value(page, "L4", "24")
    page.reload()
    expect(validation(page, "L4").get_by_role("textbox", name="Error message", exact=True)).to_have_value("Stay within capacity")


@pytest.mark.requirements("REQ-5-2-1", "REQ-3-1-1")
def test_evolution_sheet_invalid_paste_and_move_preserve_all_cells(page, application):
    endpoint = open_workbook(page, application, "EVO-M05-VALIDATION-FORMULA", "Thresholds")
    source = active_sheet(workbook(page, endpoint))
    response = page.request.put(endpoint + f'/worksheets/{source["id"]}/cells', data={"cells": {"3,3": "88", "3,4": "90", "6,11": "untouched"}})
    assert response.ok, response.text()
    page.reload()
    external_clipboard(page, "88\tchanged")
    cell(page, "J6").click()
    page.keyboard.press("Control+v")
    expect(page.get_by_text("Capacity must be from 25 to 75", exact=True)).to_be_visible()
    cell_value(page, "J6", "37")
    cell_value(page, "K6", "untouched")
    drag_range(page, "C3", "D3")
    page.keyboard.press("Control+x")
    cell(page, "J6").click()
    page.keyboard.press("Control+v")
    expect(page.get_by_text("Capacity must be from 25 to 75", exact=True)).to_be_visible()
    for address, value in {"C3": "88", "D3": "90", "J6": "37", "K6": "untouched"}.items():
        cell_value(page, address, value)
    page.reload()
    for address, value in {"C3": "88", "D3": "90", "J6": "37", "K6": "untouched"}.items():
        cell_value(page, address, value)


@pytest.mark.requirements("REQ-5-2-1")
def test_evolution_sheet_custom_validation_survives_structure_and_undo(page, application):
    endpoint = open_workbook(page, application, "EVO-M05-VALIDATION-EDIT", "Thresholds")
    source = active_sheet(workbook(page, endpoint))
    sheet_path = endpoint + f'/worksheets/{source["id"]}'
    response = page.request.post(sheet_path + "/rows", data={"row": 4, "position": "above"})
    assert response.ok, response.text()
    response = page.request.put(sheet_path + "/cells/5/12", data={"value": "90"})
    assert not response.ok and response.json()["error"] == "Capacity must be from 25 to 75"
    assert page.request.post(endpoint + "/undo").ok
    response = page.request.put(sheet_path + "/cells/4/12", data={"value": "90"})
    assert not response.ok and response.json()["error"] == "Capacity must be from 25 to 75"
    page.reload()
    cell_value(page, "L4", "42")
    expect(validation(page, "L4").get_by_role("textbox", name="Error message", exact=True)).to_have_value("Capacity must be from 25 to 75")


@pytest.mark.requirements("REQ-5-2-1")
def test_evolution_sheet_default_dropdown_message_and_edit_delete_remain_available(page, application):
    baseline.test_sheet_full_dropdown_trims_values_and_rule_can_be_edited_then_removed(page, application)
