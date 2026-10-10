"""Exact public fill colors, selected cells and live display-value rules."""

import pytest
from playwright.sync_api import expect

from evolution_sheet_helpers import application, browser_errors, active_sheet, cell, cell_value, command, drag_range, formula_entry, open_workbook, workbook

RED = "rgb(254, 226, 226)"
YELLOW = "rgb(254, 249, 195)"
GREEN = "rgb(220, 252, 231)"


def format_dialog(page):
    command(page, "Format", "Conditional formatting")
    return page.get_by_role("dialog", name="Conditional formatting", exact=True)


def save_format(dialog, condition, value, style):
    dialog.get_by_role("combobox", name="Condition", exact=True).select_option(label=condition)
    dialog.get_by_role("textbox", name="Value", exact=True).fill(value)
    dialog.get_by_role("combobox", name="Style", exact=True).select_option(label=style)
    dialog.get_by_role("button", name="Save", exact=True).click()


@pytest.mark.requirements("REQ-7-2-1")
@pytest.mark.scenarios("REQ-7-2-1#1")
def test_evolution_sheet_numeric_fill_on_selected_range_survives_refresh(page, application):
    endpoint = open_workbook(page, application, "EVO-N04-FORMAT-NUMBER", "Signals")
    original = active_sheet(workbook(page, endpoint))["cells"]
    drag_range(page, "J4", "J6")
    save_format(format_dialog(page), "Greater than", "25", "Red fill")
    for address in ("J5", "J6"):
        expect(cell(page, address)).to_have_attribute("aria-selected", "true")
        expect(cell(page, address)).to_have_css("background-color", RED)
    expect(cell(page, "J4")).not_to_have_css("background-color", RED)
    page.reload()
    for address in ("J5", "J6"):
        expect(cell(page, address)).to_have_css("background-color", RED)
    expect(cell(page, "J4")).not_to_have_css("background-color", RED)
    assert active_sheet(workbook(page, endpoint))["cells"] == original


@pytest.mark.requirements("REQ-7-2-1")
@pytest.mark.scenarios("REQ-7-2-1#2")
def test_evolution_sheet_text_fill_and_rule_persist(page, application):
    endpoint = open_workbook(page, application, "EVO-N04-FORMAT-TEXT", "Signals")
    original = active_sheet(workbook(page, endpoint))["cells"]
    drag_range(page, "K4", "K6")
    save_format(format_dialog(page), "Text contains", "Watch", "Yellow fill")
    for refresh in (False, True):
        if refresh:
            page.reload()
        expect(cell(page, "K4")).to_have_css("background-color", YELLOW)
        for address in ("K5", "K6"):
            expect(cell(page, address)).not_to_have_css("background-color", YELLOW)
    expect(format_dialog(page).get_by_role("button", name="Edit rule 1", exact=True)).to_be_visible()
    assert active_sheet(workbook(page, endpoint))["cells"] == original


@pytest.mark.requirements("REQ-7-2-1")
@pytest.mark.scenarios("REQ-7-2-1#3")
def test_evolution_sheet_edit_then_delete_rule_clears_only_its_fill(page, application):
    endpoint = open_workbook(page, application, "EVO-N04-FORMAT-EDIT", "Signals")
    original = active_sheet(workbook(page, endpoint))["cells"]
    expect(cell(page, "L4")).to_have_css("background-color", RED)
    dialog = format_dialog(page)
    dialog.get_by_role("button", name="Edit rule 1", exact=True).click()
    dialog.get_by_role("combobox", name="Style", exact=True).select_option(label="Green fill")
    dialog.get_by_role("button", name="Save", exact=True).click()
    for address in ("L4", "L5"):
        expect(cell(page, address)).to_have_css("background-color", GREEN)
    format_dialog(page).get_by_role("button", name="Delete rule 1", exact=True).click()
    for refresh in (False, True):
        if refresh:
            page.reload()
        for address in ("L3", "L4", "L5"):
            for color in (RED, YELLOW, GREEN):
                expect(cell(page, address)).not_to_have_css("background-color", color)
    assert active_sheet(workbook(page, endpoint))["cells"] == original


@pytest.mark.requirements("REQ-7-2-1")
def test_evolution_sheet_fill_tracks_formula_values_undo_and_rule_isolation(page, application):
    endpoint = open_workbook(page, application, "EVO-N04-FORMAT-NUMBER", "Signals")
    source = active_sheet(workbook(page, endpoint))
    assert page.request.put(endpoint + f'/worksheets/{source["id"]}/cells', data={"cells": {"4,10": "=J5*2", "6,10": "29x", "4,11": "Watch closely"}}).ok
    page.reload()
    drag_range(page, "J4", "J6")
    dialog = format_dialog(page)
    save_format(dialog, "Greater than", "25", "Red fill")
    expect(cell(page, "J4")).to_have_css("background-color", RED)
    expect(cell(page, "J6")).not_to_have_css("background-color", RED)
    cell(page, "K4").click()
    dialog = format_dialog(page)
    save_format(dialog, "Text contains", "Watch", "Yellow fill")
    expect(cell(page, "K4")).to_have_css("background-color", YELLOW)
    formula_entry(page, "J5", "10")
    cell_value(page, "J4", "20")
    expect(cell(page, "J4")).not_to_have_css("background-color", RED)
    page.get_by_role("button", name="Undo", exact=True).click()
    cell_value(page, "J4", "58")
    expect(cell(page, "J4")).to_have_css("background-color", RED)
    dialog = format_dialog(page)
    dialog.get_by_role("button", name="Delete rule 1", exact=True).click()
    page.reload()
    expect(cell(page, "J4")).not_to_have_css("background-color", RED)
    expect(cell(page, "K4")).to_have_css("background-color", YELLOW)
    assert active_sheet(workbook(page, endpoint))["cells"]["4,10"] == "=J5*2"


@pytest.mark.requirements("REQ-7-2-1")
def test_evolution_sheet_missing_format_value_does_not_replace_saved_rule(page, application):
    open_workbook(page, application, "EVO-N04-FORMAT-EDIT", "Signals")
    dialog = format_dialog(page)
    dialog.get_by_role("button", name="Edit rule 1", exact=True).click()
    dialog.get_by_role("textbox", name="Value", exact=True).fill("")
    dialog.get_by_role("combobox", name="Style", exact=True).select_option(label="Green fill")
    save = dialog.get_by_role("button", name="Save", exact=True)
    if save.is_enabled():
        save.click()
    else:
        expect(save).to_be_disabled()
    expect(dialog).to_be_visible()
    page.reload()
    expect(cell(page, "L4")).to_have_css("background-color", RED)
    dialog = format_dialog(page)
    dialog.get_by_role("button", name="Edit rule 1", exact=True).click()
    expect(dialog.get_by_role("textbox", name="Value", exact=True)).to_have_value("20")
