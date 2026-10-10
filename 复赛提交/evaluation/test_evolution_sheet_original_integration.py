"""Published original keyboard, typed sort and filtered-pivot contracts."""

import pytest
from playwright.sync_api import expect

from evolution_sheet_helpers import application, browser_errors, cell, cell_value, command, drag_range, formula_entry
from test_browser import create_workbook
from test_sheet_workflows import choose, import_csv
from test_sheet_requirements import configure_pivot, create_filter, filter_condition

pytestmark = [pytest.mark.shared, pytest.mark.full_evolution]


@pytest.mark.requirements("REQ-3-1-1", "REQ-3-2-2", "REQ-4-2-1")
def test_evolution_sheet_rapid_edits_keyboard_history_and_workbook_isolation(page, application):
    create_workbook(page, application)
    first = page.url
    for row in range(1, 7):
        formula_entry(page, f"A{row}", str(row))
    formula_entry(page, "B1", "=SUM(A1:A6)")
    cell_value(page, "B1", "21")
    cell(page, "B1").click()
    page.keyboard.press("Control+z")
    page.keyboard.press("Control+z")
    cell_value(page, "A6", "")
    cell_value(page, "B1", "")
    cell_value(page, "A5", "5")
    page.keyboard.press("Control+y")
    page.keyboard.press("Control+y")
    cell_value(page, "A6", "6")
    cell_value(page, "B1", "21")
    create_workbook(page, application)
    formula_entry(page, "A1", "999")
    formula_entry(page, "A1", "1000")
    cell_value(page, "A1", "1000")
    cell(page, "A1").click()
    page.keyboard.press("Control+z")
    cell_value(page, "A1", "999")
    page.goto(first)
    cell_value(page, "B1", "21")
    cell(page, "B1").click()
    expect(page.get_by_label("Formula bar", exact=True)).to_have_value("=SUM(A1:A6)")


@pytest.mark.requirements("REQ-5-1-1")
@pytest.mark.parametrize(("keys",), [("2026-03-01,2026-01-02,2026-03-01",), ("B,A,B",)])
def test_evolution_sheet_date_and_text_sort_are_stable_and_scoped(page, application, keys):
    first, second, third = keys.split(",")
    import_csv(page, application, f"Name,Key,Outside\nfirst,{first},a\nsecond,{second},b\nthird,{third},c\n")
    drag_range(page, "A1", "B4")
    command(page, "Data", "Sort range")
    dialog = page.get_by_role("dialog", name="Sort range", exact=True)
    dialog.get_by_role("checkbox", name="Data has header row", exact=True).check()
    choose(page, "Sort by", "Key")
    choose(page, "Order", "Ascending")
    dialog.get_by_role("button", name="Sort", exact=True).click()
    for refresh in (False, True):
        if refresh:
            page.reload()
        for address, value in {"A1": "Name", "A2": "second", "A3": "first", "A4": "third", "C2": "a", "C3": "b", "C4": "c"}.items():
            cell_value(page, address, value)


@pytest.mark.requirements("REQ-5-1-2", "REQ-5-3-1")
def test_evolution_sheet_pivot_includes_filtered_out_source_rows(page, application):
    import_csv(page, application, "Region,Sales\nEast,1200\nNorth,800\nEast,700\n")
    create_filter(page, "A1", "B4")
    filter_condition(page, "Sales", "Greater than", "1000")
    expect(cell(page, "A2")).to_be_visible()
    expect(cell(page, "A3")).not_to_be_visible()
    expect(cell(page, "A4")).not_to_be_visible()
    command(page, "Data", "Create pivot table")
    dialog = page.get_by_role("dialog", name="Create pivot table", exact=True)
    expect(dialog.get_by_text("Source range: A1:B4", exact=True)).to_be_visible()
    dialog.get_by_role("radio", name="New worksheet", exact=True).check()
    dialog.get_by_role("button", name="Create", exact=True).click()
    expect(page.get_by_role("tab", name="Pivot1", exact=True)).to_have_attribute("aria-selected", "true")
    configure_pivot(page)
    for address, value in {"A2": "East", "B2": "1900", "A3": "North", "B3": "800", "A4": "Grand Total", "B4": "2700"}.items():
        cell_value(page, address, value)
    page.reload()
    cell_value(page, "B4", "2700")
