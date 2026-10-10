"""Exact cell note text without contaminating values, formulas or history."""

import csv
import io

import pytest
from playwright.sync_api import expect

from evolution_sheet_helpers import application, browser_errors, active_sheet, cell, cell_value, command, formula_entry, open_workbook, workbook
from test_evolution_sheet_find import find_dialog
from test_evolution_sheet_named_ranges import close_dialog


def open_note(page, address):
    cell(page, address).get_by_role("button", name="Open note for " + address, exact=True).click()
    return page.get_by_role("dialog", name="Note for " + address, exact=True)


def create_note(page, address, value):
    cell(page, address).click()
    command(page, "Insert", "Add note")
    dialog = page.get_by_role("dialog", name="Note for " + address, exact=True)
    dialog.get_by_role("textbox", name="Note", exact=True).fill(value)
    dialog.get_by_role("button", name="Save note", exact=True).click()
    # Follow the public Save -> Open sequence; an extra Close races async save.
    note_text(page, address, value)


def note_text(page, address, value):
    dialog = open_note(page, address)
    expect(dialog.get_by_role("textbox", name="Note", exact=True)).to_have_value(value)
    close_dialog(dialog)


@pytest.mark.requirements("REQ-8-1-1")
@pytest.mark.scenarios("REQ-8-1-1#1")
def test_evolution_sheet_note_create_keeps_cell_and_persists(page, application):
    endpoint = open_workbook(page, application, "EVO-N05-NOTE-CREATE", "ReviewQueue")
    original = active_sheet(workbook(page, endpoint))["cells"]
    create_note(page, "D8", "Confirm the harbor reference before release")
    for refresh in (False, True):
        if refresh:
            page.reload()
        cell_value(page, "D8", "Manifest R41")
        note_text(page, "D8", "Confirm the harbor reference before release")
        assert active_sheet(workbook(page, endpoint))["cells"] == original


@pytest.mark.requirements("REQ-8-1-1")
@pytest.mark.scenarios("REQ-8-1-1#2")
def test_evolution_sheet_note_edit_replaces_exact_text(page, application):
    endpoint = open_workbook(page, application, "EVO-N05-NOTE-EDIT", "ReviewQueue")
    original = active_sheet(workbook(page, endpoint))["cells"]
    dialog = open_note(page, "F6")
    expect(dialog.get_by_role("textbox", name="Note", exact=True)).to_have_value("Awaiting controller sign-off")
    dialog.get_by_role("button", name="Edit note", exact=True).click()
    dialog.get_by_role("textbox", name="Note", exact=True).fill("Controller signed at 14:20")
    dialog.get_by_role("button", name="Save note", exact=True).click()
    for refresh in (False, True):
        if refresh:
            page.reload()
        note_text(page, "F6", "Controller signed at 14:20")
        cell_value(page, "F6", "Gate Rho")
        assert active_sheet(workbook(page, endpoint))["cells"] == original


@pytest.mark.requirements("REQ-8-1-1")
@pytest.mark.scenarios("REQ-8-1-1#3")
def test_evolution_sheet_note_delete_keeps_value_and_remains_deleted(page, application):
    endpoint = open_workbook(page, application, "EVO-N05-NOTE-DELETE", "ReviewQueue")
    original = active_sheet(workbook(page, endpoint))["cells"]
    dialog = open_note(page, "J3")
    expect(dialog.get_by_role("textbox", name="Note", exact=True)).to_have_value("Retire after audit")
    dialog.get_by_role("button", name="Delete note", exact=True).click()
    for refresh in (False, True):
        if refresh:
            page.reload()
        expect(page.get_by_role("button", name="Open note for J3", exact=True)).to_have_count(0)
        cell_value(page, "J3", "Route Zeta")
        assert active_sheet(workbook(page, endpoint))["cells"] == original


@pytest.mark.requirements("REQ-8-1-1", "REQ-6-2-1")
def test_evolution_sheet_note_does_not_pollute_formula_find_export_or_value_undo(page, application):
    endpoint = open_workbook(page, application, "EVO-N05-NOTE-CREATE", "ReviewQueue")
    formula_entry(page, "E8", "=2+3")
    cell_value(page, "E8", "5")
    text = "  First line\nsecond & <label>  "
    create_note(page, "E8", text)
    note_text(page, "E8", text)
    formula_entry(page, "E8", "=2+4")
    cell_value(page, "E8", "6")
    page.get_by_role("button", name="Undo", exact=True).click()
    cell_value(page, "E8", "5")
    note_text(page, "E8", text)
    cell(page, "E8").click()
    expect(page.get_by_role("textbox", name="Formula bar", exact=True)).to_have_value("=2+3")
    dialog = find_dialog(page)
    dialog.get_by_role("textbox", name="Find", exact=True).fill("5")
    dialog.get_by_role("button", name="Find next", exact=True).click()
    expect(cell(page, "E8")).to_have_attribute("aria-selected", "true")
    expect(dialog.get_by_text("Match 1 of 1", exact=True)).to_be_visible()
    close_dialog(dialog)
    response = page.request.get(endpoint + "/export")
    assert response.ok
    assert list(csv.reader(io.StringIO(response.text())))[7][4] == "5"
    assert active_sheet(workbook(page, endpoint))["cells"]["8,5"] == "=2+3"
    page.get_by_role("button", name="Add worksheet", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    expect(page.get_by_role("button", name="Open note for E8", exact=True)).to_have_count(0)
    page.get_by_role("tab", name="ReviewQueue", exact=True).click()
    page.reload()
    note_text(page, "E8", text)


@pytest.mark.parametrize(("axis", "payload", "moved", "remove"), [
    ("rows", {"row": 6, "position": "above"}, "F7", "rows/7"),
    ("columns", {"col": 6, "position": "left"}, "G6", "columns/7"),
])
@pytest.mark.requirements("REQ-8-1-1")
def test_evolution_sheet_notes_follow_structure_delete_and_undo(page, application, axis, payload, moved, remove):
    endpoint = open_workbook(page, application, "EVO-N05-NOTE-EDIT", "ReviewQueue")
    source = active_sheet(workbook(page, endpoint))
    sheet_path = endpoint + f'/worksheets/{source["id"]}'
    response = page.request.post(sheet_path + "/" + axis, data=payload)
    assert response.ok, response.text()
    page.reload()
    note_text(page, moved, "Awaiting controller sign-off")
    cell_value(page, moved, "Gate Rho")
    expect(page.get_by_role("button", name="Open note for F6", exact=True)).to_have_count(0)
    assert page.request.delete(sheet_path + "/" + remove).ok
    page.reload()
    expect(page.get_by_role("button", name="Open note for " + moved, exact=True)).to_have_count(0)
    assert page.request.post(endpoint + "/undo").ok
    page.reload()
    note_text(page, moved, "Awaiting controller sign-off")
    assert page.request.post(endpoint + "/undo").ok
    page.reload()
    note_text(page, "F6", "Awaiting controller sign-off")
    assert active_sheet(workbook(page, endpoint))["cells"] == source["cells"]
    assert page.request.post(endpoint + "/redo").ok
    page.reload()
    note_text(page, moved, "Awaiting controller sign-off")
