"""Three public saved-filter scenarios plus hidden-row export/pivot preservation."""

import csv
import io

import pytest
from playwright.sync_api import expect

from evolution_sheet_helpers import application, browser_errors, open_workbook, command, cell, cell_value, drag_range, workbook, active_sheet


def create_filter(page):
    drag_range(page, "D3", "F7")
    command(page, "Data", "Create filter")


def condition(page, heading, kind, value):
    page.get_by_role("button", name="Filter " + heading, exact=True).click()
    dialog = page.get_by_role("dialog", name="Filter " + heading, exact=True)
    dialog.get_by_role("combobox", name="Condition", exact=True).select_option(label=kind)
    dialog.get_by_role("textbox", name="Value", exact=True).fill(value)
    dialog.get_by_role("button", name="Apply", exact=True).click()


def filter_views(page):
    command(page, "Data", "Filter views")
    return page.get_by_role("dialog", name="Filter views", exact=True)


def view_control(dialog, name):
    return dialog.get_by_role("button", name=name, exact=True).or_(dialog.get_by_role("option", name=name, exact=True))


def queued_rows(page):
    for row, workstream, load in ((4, "Atlas", "17"), (6, "Beacon", "22"), (7, "Cirrus", "9")):
        cell_value(page, f"D{row}", workstream)
        cell_value(page, f"E{row}", "Queued")
        cell_value(page, f"F{row}", load)
    expect(cell(page, "D5")).not_to_be_visible()


def source_rows(page):
    for row, values in enumerate((("Atlas", "Queued", "17"), ("Atlas", "Active", "31"),
                                  ("Beacon", "Queued", "22"), ("Cirrus", "Queued", "9")), start=4):
        for column, value in zip("DEF", values):
            cell_value(page, f"{column}{row}", value)


@pytest.mark.requirements("REQ-5-1-2")
@pytest.mark.scenarios("REQ-5-1-2#1")
def test_evolution_sheet_save_two_condition_filter(page, application):
    endpoint = open_workbook(page, application, "EVO-M04-FILTER-SAVE", "Workload")
    before = active_sheet(workbook(page, endpoint))["cells"]
    source_rows(page)
    create_filter(page)
    condition(page, "Workstream", "Text contains", "Atlas")
    condition(page, "Load", "Greater than", "20")
    command(page, "Data", "Save filter view")
    page.get_by_role("textbox", name="Filter view name", exact=True).fill("Atlas active load")
    page.get_by_role("button", name="Save view", exact=True).click()
    for row in (4, 6, 7):
        expect(cell(page, f"D{row}")).not_to_be_visible()
    for column, value in zip("DEF", ("Atlas", "Active", "31")):
        cell_value(page, column + "5", value)
    page.reload()
    dialog = filter_views(page)
    expect(view_control(dialog, "Atlas active load")).to_have_count(1)
    expect(cell(page, "D4")).not_to_be_visible()
    cell_value(page, "F5", "31")
    assert active_sheet(workbook(page, endpoint))["cells"] == before


@pytest.mark.requirements("REQ-5-1-2")
@pytest.mark.scenarios("REQ-5-1-2#2")
def test_evolution_sheet_apply_saved_filter_replaces_current(page, application):
    open_workbook(page, application, "EVO-M04-FILTER-APPLY", "Workload")
    create_filter(page)
    condition(page, "Workstream", "Text contains", "Atlas")
    dialog = filter_views(page)
    choice = view_control(dialog, "Queued lanes")
    choice.click()
    expect(dialog).to_be_visible()
    assert choice.get_attribute("aria-selected") == "true" or choice.get_attribute("aria-pressed") == "true"
    queued_rows(page)
    page.reload()
    queued_rows(page)
    expect(view_control(filter_views(page), "Queued lanes")).to_have_count(1)


@pytest.mark.requirements("REQ-5-1-2")
@pytest.mark.scenarios("REQ-5-1-2#3")
def test_evolution_sheet_filter_duplicate_and_persistent_deletion(page, application):
    endpoint = open_workbook(page, application, "EVO-M04-FILTER-DELETE", "Workload")
    before = active_sheet(workbook(page, endpoint))["cells"]
    create_filter(page)
    command(page, "Data", "Save filter view")
    entry = page.get_by_role("textbox", name="Filter view name", exact=True)
    entry.fill(" queued lanes ")
    page.get_by_role("button", name="Save view", exact=True).click()
    expect(page.get_by_text("Filter view name already exists", exact=True)).to_be_visible()
    expect(entry).to_be_visible()
    page.get_by_role("button", name="Cancel", exact=True).click()
    dialog = filter_views(page)
    view_control(dialog, "Queued lanes").click()
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Delete filter view", exact=True).click()
    expect(view_control(dialog, "Queued lanes")).to_have_count(0)
    source_rows(page)
    page.reload()
    source_rows(page)
    expect(view_control(filter_views(page), "Queued lanes")).to_have_count(0)
    assert active_sheet(workbook(page, endpoint))["cells"] == before


@pytest.mark.requirements("REQ-5-1-2")
def test_evolution_sheet_hidden_rows_remain_in_export_and_pivot(page, application):
    endpoint = open_workbook(page, application, "EVO-M04-FILTER-APPLY", "Workload")
    source = active_sheet(workbook(page, endpoint))
    dialog = filter_views(page)
    view_control(dialog, "Queued lanes").click()
    queued_rows(page)
    response = page.request.get(endpoint + "/export")
    assert response.ok
    rows = list(csv.reader(io.StringIO(response.text())))
    assert [row[3:6] for row in rows[3:7]] == [["Atlas", "Queued", "17"], ["Atlas", "Active", "31"],
                                                            ["Beacon", "Queued", "22"], ["Cirrus", "Queued", "9"]]
    response = page.request.post(endpoint + f'/worksheets/{source["id"]}/pivot', data={"range": {"rowStart": 3, "colStart": 4, "rowEnd": 7, "colEnd": 6}})
    assert response.ok, response.text()
    pivot_sheet = next(sheet for sheet in response.json()["workbook"]["worksheets"] if sheet["pivot"])
    response = page.request.put(endpoint + f'/pivots/{pivot_sheet["pivot"]["id"]}', data={"rowField": "Workstream", "valueField": "Load", "summarize": "SUM"})
    assert response.ok, response.text()
    data = response.json()["workbook"]
    pivot = next(sheet for sheet in data["worksheets"] if sheet["id"] == pivot_sheet["id"])
    assert [pivot["cells"].get(f"{row},1") for row in range(2, 5)] == ["Atlas", "Beacon", "Cirrus"]
    assert [pivot["cells"].get(f"{row},2") for row in range(2, 5)] == ["48", "22", "9"]
    assert next(sheet for sheet in data["worksheets"] if sheet["id"] == source["id"])["cells"] == source["cells"]
