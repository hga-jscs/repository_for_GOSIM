"""Public named ranges and real formula, rename and export integration."""

import csv
import io

import pytest
from playwright.sync_api import expect

from evolution_sheet_helpers import application, browser_errors, active_sheet, cell, cell_value, command, formula_entry, open_workbook, rename_sheet, workbook


def named_dialog(page):
    command(page, "Data", "Named ranges")
    return page.get_by_role("dialog", name="Named ranges", exact=True)


def add_named(page, name, reference):
    dialog = named_dialog(page)
    dialog.get_by_role("button", name="Add named range", exact=True).click()
    dialog.get_by_role("textbox", name="Name", exact=True).fill(name)
    dialog.get_by_role("textbox", name="Range", exact=True).fill(reference)
    dialog.get_by_role("button", name="Save", exact=True).click()
    return dialog


def close_dialog(dialog):
    if dialog.is_visible():
        dialog.get_by_role("button", name="Close", exact=True).click()


@pytest.mark.requirements("REQ-7-1-1")
def test_evolution_sheet_named_metadata_redo_matches_original_structural_delete(page, application):
    endpoint = open_workbook(page, application, "EVO-N03-NAMED-UPDATE", "ForecastModel")
    before = workbook(page, endpoint)
    sheet_path = endpoint + f'/worksheets/{active_sheet(before)["id"]}'
    assert before["namedRanges"], "The public MarginBase definition must exist"
    assert page.request.delete(sheet_path + "/columns/11").ok
    deleted = workbook(page, endpoint)
    assert page.request.post(endpoint + "/undo").ok
    restored = workbook(page, endpoint)
    assert restored["namedRanges"] == before["namedRanges"]
    assert active_sheet(restored)["cells"] == active_sheet(before)["cells"]
    assert page.request.post(endpoint + "/redo").ok
    redone = workbook(page, endpoint)
    assert redone["namedRanges"] == deleted["namedRanges"]
    assert active_sheet(redone)["cells"] == active_sheet(deleted)["cells"]


@pytest.mark.requirements("REQ-7-1-1")
@pytest.mark.scenarios("REQ-7-1-1#1")
def test_evolution_sheet_named_create_and_formula_persist(page, application):
    endpoint = open_workbook(page, application, "EVO-N03-NAMED-CREATE", "ForecastModel")
    for address, value in {"J3": "18", "J4": "24", "J5": "31", "L3": ""}.items():
        cell_value(page, address, value)
    add_named(page, "CapacityPlan", "ForecastModel!J3:J5")
    formula_entry(page, "L3", "=SUM(CapacityPlan)")
    cell_value(page, "L3", "73")
    assert active_sheet(workbook(page, endpoint))["cells"]["3,12"] == "=SUM(CapacityPlan)"
    page.reload()
    cell_value(page, "L3", "73")
    cell(page, "L3").click()
    expect(page.get_by_role("textbox", name="Formula bar", exact=True)).to_have_value("=SUM(CapacityPlan)")
    expect(named_dialog(page).get_by_role("button", name="Edit CapacityPlan", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-7-1-1")
@pytest.mark.scenarios("REQ-7-1-1#2")
def test_evolution_sheet_invalid_named_range_is_not_saved(page, application):
    endpoint = open_workbook(page, application, "EVO-N03-NAMED-INVALID", "ForecastModel")
    original = active_sheet(workbook(page, endpoint))["cells"]
    dialog = add_named(page, "1stBatch", "ForecastModel!K2:K3")
    expect(dialog.get_by_text("Named range must start with a letter", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="Edit 1stBatch", exact=True)).to_have_count(0)
    page.reload()
    expect(named_dialog(page).get_by_role("button", name="Edit 1stBatch", exact=True)).to_have_count(0)
    assert active_sheet(workbook(page, endpoint))["cells"] == original


@pytest.mark.requirements("REQ-7-1-1")
@pytest.mark.scenarios("REQ-7-1-1#3")
def test_evolution_sheet_edit_named_range_recalculates_and_persists(page, application):
    endpoint = open_workbook(page, application, "EVO-N03-NAMED-UPDATE", "ForecastModel")
    cell_value(page, "M2", "13")
    dialog = named_dialog(page)
    dialog.get_by_role("button", name="Edit MarginBase", exact=True).click()
    expect(dialog.get_by_role("textbox", name="Range", exact=True)).to_have_value("ForecastModel!K2:K3")
    dialog.get_by_role("textbox", name="Range", exact=True).fill("ForecastModel!K2:K4")
    dialog.get_by_role("button", name="Save", exact=True).click()
    cell_value(page, "M2", "25")
    page.reload()
    cell_value(page, "M2", "25")
    dialog = named_dialog(page)
    dialog.get_by_role("button", name="Edit MarginBase", exact=True).click()
    expect(dialog.get_by_role("textbox", name="Range", exact=True)).to_have_value("ForecastModel!K2:K4")
    assert active_sheet(workbook(page, endpoint))["cells"]["2,13"] == "=SUM(MarginBase)"


@pytest.mark.requirements("REQ-7-1-1")
def test_evolution_sheet_named_live_dependencies_copy_and_csv_match_server(page, application):
    endpoint = open_workbook(page, application, "EVO-N03-NAMED-CREATE", "ForecastModel")
    add_named(page, "CapacityPlan", "ForecastModel!J3:J5")
    formula_entry(page, "L3", "=SUM(CapacityPlan)")
    cell_value(page, "L3", "73")
    formula_entry(page, "M3", "=L3*2")
    cell_value(page, "M3", "146")
    formula_entry(page, "J3", "20")
    cell_value(page, "L3", "75")
    cell_value(page, "M3", "150")
    cell(page, "L3").click()
    page.keyboard.press("Control+c")
    cell(page, "L4").click()
    page.keyboard.press("Control+v")
    cell_value(page, "L4", "75")
    data = active_sheet(workbook(page, endpoint))
    assert data["cells"]["4,12"] == "=SUM(CapacityPlan)"
    assert {key: data["display"][key] for key in ("3,12", "3,13", "4,12")} == {"3,12": "75", "3,13": "150", "4,12": "75"}
    response = page.request.get(endpoint + "/export")
    assert response.ok
    rows = list(csv.reader(io.StringIO(response.text())))
    assert rows[2][11:13] == ["75", "150"] and rows[3][11] == "75"
    assert active_sheet(workbook(page, endpoint))["cells"] == data["cells"]
    formula_entry(page, "J3", "=J4+J5")
    cell_value(page, "L3", "110")
    cell_value(page, "L4", "110")
    cell_value(page, "M3", "220")
    response = page.request.get(endpoint + "/export")
    assert response.ok and list(csv.reader(io.StringIO(response.text())))[2][11:13] == ["110", "220"]
    page.reload()
    cell_value(page, "M3", "220")


@pytest.mark.requirements("REQ-7-1-1")
def test_evolution_sheet_named_reference_follows_sheet_identity(page, application):
    endpoint = open_workbook(page, application, "EVO-N03-NAMED-CREATE", "ForecastModel")
    source_id = active_sheet(workbook(page, endpoint))["id"]
    add_named(page, "CapacityPlan", "ForecastModel!J3:J5")
    dialog = rename_sheet(page, "ForecastModel")
    dialog.get_by_role("textbox", name="Worksheet name", exact=True).fill("Forecast Revised")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("tab", name="Forecast Revised", exact=True)).to_have_attribute("aria-selected", "true")
    page.get_by_role("button", name="Add worksheet", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    formula_entry(page, "A1", "=SUM(CapacityPlan)")
    cell_value(page, "A1", "73")
    assert page.request.put(endpoint + f"/worksheets/{source_id}/cells/3/10", data={"value": "30"}).ok
    page.reload()
    cell_value(page, "A1", "85")
    dialog = named_dialog(page)
    dialog.get_by_role("button", name="Edit CapacityPlan", exact=True).click()
    expect(dialog.get_by_role("textbox", name="Range", exact=True)).to_have_value("Forecast Revised!J3:J5")
    assert next(sheet for sheet in workbook(page, endpoint)["worksheets"] if sheet["id"] == source_id)["name"] == "Forecast Revised"


@pytest.mark.requirements("REQ-7-1-1")
def test_evolution_sheet_named_duplicate_and_invalid_reference_keep_old_definition(page, application):
    open_workbook(page, application, "EVO-N03-NAMED-UPDATE", "ForecastModel")
    dialog = add_named(page, " marginbase ", "ForecastModel!K2:K4")
    expect(dialog.get_by_text("Named range already exists", exact=True)).to_be_visible()
    close_dialog(dialog)
    cell_value(page, "M2", "13")
    dialog = named_dialog(page)
    dialog.get_by_role("button", name="Edit MarginBase", exact=True).click()
    dialog.get_by_role("textbox", name="Range", exact=True).fill("MissingWorksheet!K2:K4")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog.get_by_text("Invalid named range", exact=True)).to_be_visible()
    page.reload()
    cell_value(page, "M2", "13")
    dialog = named_dialog(page)
    dialog.get_by_role("button", name="Edit MarginBase", exact=True).click()
    expect(dialog.get_by_role("textbox", name="Range", exact=True)).to_have_value("ForecastModel!K2:K3")
