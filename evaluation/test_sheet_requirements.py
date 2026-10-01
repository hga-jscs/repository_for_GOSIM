"""Additional public Sheet requirements; these are not the hidden platform tests."""

import csv
import io
import re
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect
from test_browser import create_workbook
from test_sheet_workflows import cell, choose, data_command, edit, import_csv


def drag_range(page: Page, start: str, end: str) -> None:
    first = cell(page, start)
    last = cell(page, end)
    first.scroll_into_view_if_needed()
    last.scroll_into_view_if_needed()
    first_box = first.bounding_box()
    last_box = last.bounding_box()
    assert first_box and last_box
    page.mouse.move(first_box["x"] + first_box["width"] / 2, first_box["y"] + first_box["height"] / 2)
    page.mouse.down()
    page.mouse.move(last_box["x"] + last_box["width"] / 2, last_box["y"] + last_box["height"] / 2, steps=8)
    page.mouse.up()


def worksheet_command(page: Page, worksheet: str, command: str) -> None:
    tab = page.get_by_role("tab", name=worksheet, exact=True)
    name = rf"(?<!\w){re.escape(worksheet)}(?!\w)"
    named_menu = (
        page.get_by_role("button", name=re.compile(rf"(?=.*{name})(?=.*\b(?:menu|options|more actions)\b)", re.I))
        .or_(
            page.get_by_role("button", name=re.compile(name, re.I)).and_(
                page.locator('[aria-haspopup="menu"], [aria-haspopup="true"]')
            )
        )
        .filter(visible=True)
    )
    parent = tab.locator("..")
    adjacent = parent.get_by_role("button").filter(visible=True)
    if named_menu.count() == 1:
        named_menu.click()
    elif tab.get_attribute("aria-haspopup") in {"menu", "true"}:
        tab.click()
    elif tab.get_by_role("button").filter(visible=True).count() == 1:
        tab.get_by_role("button").filter(visible=True).click()
    elif (
        parent.get_attribute("role") != "tablist"
        and parent.get_by_role("tablist").count() == 0
        and parent.get_by_role("tab").count() == 1
        and parent.get_by_role("button", name="Add worksheet", exact=True).count() == 0
        and adjacent.count() == 1
    ):
        adjacent.click()
    else:
        tab.click(button="right")
    menu_command(page, command)


def menu_command(page: Page, command: str) -> None:
    control = (
        page.get_by_role("menuitem", name=command, exact=True)
        .or_(page.get_by_role("button", name=command, exact=True))
        .filter(visible=True)
    )
    if not control.count():
        control = page.get_by_text(command, exact=True).filter(visible=True)
    control.first.click()


def structure_command(page: Page, heading: str, command: str) -> None:
    grid = page.get_by_role("grid", name="Worksheet grid", exact=True)
    role = "rowheader" if heading.isdigit() else "columnheader"
    header = grid.get_by_role(role, name=heading, exact=True)
    if not header.count():
        header = grid.get_by_text(heading, exact=True)
    header.click(button="right")
    menu_command(page, command)


def assert_cells(page: Page, expected: dict[str, str]) -> None:
    for address, value in expected.items():
        expect(cell(page, address)).to_have_text(value)


def formula(page: Page, address: str, expected: str) -> None:
    cell(page, address).click()
    expect(page.get_by_label("Formula bar", exact=True)).to_have_value(expected)


def number_rule(page: Page, start: str, end: str, minimum: str = "0", maximum: str = "100") -> None:
    drag_range(page, start, end)
    data_command(page, "Data validation")
    dialog = page.get_by_role("dialog", name="Data validation", exact=True)
    choose(page, "Rule type", "Number range")
    dialog.get_by_label("Minimum", exact=True).fill(minimum)
    dialog.get_by_label("Maximum", exact=True).fill(maximum)
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).not_to_be_visible()


def external_clipboard(page: Page, content: str) -> None:
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.evaluate("text => navigator.clipboard.writeText(text)", content)


def paste(page: Page, address: str, content: str, menu: bool = False) -> None:
    external_clipboard(page, content)
    cell(page, address).click()
    if menu:
        cell(page, address).click(button="right")
        page.get_by_role("menuitem", name="Paste", exact=True).click()
    else:
        page.keyboard.press("Control+v")


def transfer(page: Page, start: str, end: str, target: str, cut: bool = False) -> None:
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    drag_range(page, start, end)
    page.keyboard.press("Control+x" if cut else "Control+c")
    cell(page, target).click()
    page.keyboard.press("Control+v")


def exported_rows(page: Page) -> list[list[str]]:
    with page.expect_download() as pending:
        page.get_by_role("button", name="Export CSV", exact=True).click()
    download = pending.value
    assert download.suggested_filename.endswith(".csv")
    return list(csv.reader(io.StringIO(Path(download.path()).read_text(encoding="utf-8-sig"))))


def create_filter(page: Page, start: str, end: str) -> None:
    drag_range(page, start, end)
    data_command(page, "Create filter")


def filter_condition(page: Page, heading: str, condition: str, value: str = "") -> None:
    page.get_by_role("button", name="Filter " + heading, exact=True).click()
    dialog = page.get_by_role("dialog", name="Filter " + heading, exact=True)
    choose(page, "Condition", condition)
    if condition not in {"Is empty", "Is not empty"}:
        dialog.get_by_label("Value", exact=True).fill(value)
    dialog.get_by_role("button", name="Apply", exact=True).click()


def create_pivot(page: Page, end: str) -> Locator:
    drag_range(page, "A1", end)
    data_command(page, "Create pivot table")
    dialog = page.get_by_role("dialog", name="Create pivot table", exact=True)
    expect(dialog.get_by_text("Source range: A1:" + end, exact=True)).to_be_visible()
    dialog.get_by_role("radio", name="New worksheet", exact=True).check()
    dialog.get_by_role("button", name="Create", exact=True).click()
    expect(page.get_by_role("tab", name="Pivot1", exact=True)).to_have_attribute("aria-selected", "true")
    return page.get_by_role("region", name="Pivot table editor", exact=True)


def configure_pivot(page: Page, method: str = "SUM", values: str = "Sales", columns: str | None = None) -> None:
    choose(page, "Rows", "Region")
    if columns:
        choose(page, "Columns", columns)
    choose(page, "Values", values)
    choose(page, "Summarize by", method)
    page.get_by_role("region", name="Pivot table editor", exact=True).get_by_role(
        "button", name="Apply", exact=True
    ).click()


@pytest.mark.requirements("REQ-1-1-1")
def test_sheet_full_seed_workbook_is_available_and_directly_reopenable(page, browser, application):
    """REQ-1-1-1: the public scenarios start from this seed, not a random blank file."""
    page.goto(application)
    expect(page.get_by_text(re.compile(r"^Last updated: .+")).first).to_be_visible()
    page.get_by_role("link", name="Q3 Sales", exact=True).click()
    expect(page.get_by_text(re.compile(r"^Last updated: .+")).first).to_be_visible()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    expect(cell(page, "A1")).to_have_text("Region")
    with browser.new_context() as context:
        reopened = context.new_page()
        reopened.goto(page.url)
        expect(reopened.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
        expect(cell(reopened, "A1")).to_have_text("Region")


@pytest.mark.requirements("REQ-2-1-3", "REQ-1-2-1", "REQ-2-1-1", "REQ-2-1-2", "REQ-3-1-1")
def test_sheet_full_worksheet_rename_rejects_empty_and_duplicate_names(page, application):
    """REQ-2-1-3."""
    create_workbook(page, application)
    edit(page, "A1", "original data")
    page.get_by_role("button", name="Add worksheet", exact=True).click()
    worksheet_command(page, "Sheet2", "Rename")
    dialog = page.get_by_role("dialog", name="Rename worksheet", exact=True)
    name = dialog.get_by_label("Worksheet name", exact=True)
    expect(name).to_have_value("Sheet2")
    for value, error in [("   ", "Worksheet name cannot be empty"), (" Sheet1 ", "Worksheet name already exists")]:
        name.fill(value)
        dialog.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text(error, exact=True).first).to_be_visible()
        expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_be_visible()
    name.fill("  Forecast  ")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role("tab", name="Forecast", exact=True)).to_have_attribute("aria-selected", "true")
    page.reload()
    expect(page.get_by_role("tab", name="Forecast", exact=True)).to_have_attribute("aria-selected", "true")
    page.get_by_role("tab", name="Sheet1", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    expect(cell(page, "A1")).to_have_text("original data")


@pytest.mark.requirements("REQ-2-1-1", "REQ-2-1-4", "REQ-1-2-1", "REQ-3-1-1")
def test_sheet_full_delete_worksheet_keeps_one_and_reuses_first_unused_name(page, application):
    """REQ-2-1-1, REQ-2-1-4."""
    create_workbook(page, application)
    edit(page, "A1", "keep")
    worksheet_command(page, "Sheet1", "Delete")
    expect(page.get_by_text("A workbook must contain at least one worksheet", exact=True).first).to_be_visible()
    expect(page.get_by_role("dialog", name="Delete worksheet", exact=True)).not_to_be_visible()
    page.get_by_role("button", name="Add worksheet", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_have_attribute("aria-selected", "true")
    edit(page, "A1", "discard")
    expect(cell(page, "A1")).to_have_text("discard")
    worksheet_command(page, "Sheet2", "Delete")
    dialog = page.get_by_role("dialog", name="Delete worksheet", exact=True)
    expect(dialog).to_contain_text("Sheet2")
    dialog.get_by_role("button", name="Delete worksheet", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_have_count(0)
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    page.reload()
    expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_have_count(0)
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    expect(cell(page, "A1")).to_have_text("keep")
    page.get_by_role("button", name="Add worksheet", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_have_attribute("aria-selected", "true")
    expect(cell(page, "A1")).to_have_text("")


@pytest.mark.requirements("REQ-3-1-3", "REQ-1-2-1", "REQ-2-1-1", "REQ-2-1-2")
def test_sheet_full_drag_rectangle_survives_switch_and_reload(page, application):
    """REQ-3-1-3: test real drag, including outside-cell ARIA state."""
    create_workbook(page, application)
    page.get_by_role("button", name="Add worksheet", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_be_visible()
    expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_have_attribute("aria-selected", "true")
    page.get_by_role("tab", name="Sheet1", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    drag_range(page, "B2", "D4")
    for reopen in [False, True]:
        if reopen:
            page.get_by_role("tab", name="Sheet2", exact=True).click()
            expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_have_attribute("aria-selected", "true")
            page.get_by_role("tab", name="Sheet1", exact=True).click()
            expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
            page.reload()
            expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("grid", name="Worksheet grid")).to_have_attribute("aria-multiselectable", "true")
        for column in "BCD":
            for row in range(2, 5):
                expect(cell(page, f"{column}{row}")).to_have_attribute("aria-selected", "true")
        for address in ["A1", "B1", "A2", "E2", "B5"]:
            expect(cell(page, address)).to_have_attribute("aria-selected", "false")
    cell(page, "A1").click()
    expect(cell(page, "A1")).to_have_attribute("aria-selected", "true")
    expect(cell(page, "B2")).to_have_attribute("aria-selected", "false")


@pytest.mark.requirements("REQ-3-1-1", "REQ-1-2-1")
def test_sheet_full_edit_cancel_and_blur_commit_preserve_original_text(page, application):
    """REQ-3-1-1."""
    create_workbook(page, application)
    for address, value in [("A1", "TRUE"), ("A2", "2026-10-01"), ("A3", "中文 text")]:
        edit(page, address, value)
        formula(page, address, value)
    page.get_by_label("Formula bar", exact=True).fill("cancel me")
    page.get_by_label("Formula bar", exact=True).press("Escape")
    formula(page, "A3", "中文 text")
    page.get_by_label("Formula bar", exact=True).fill("committed by blur")
    cell(page, "B3").click()
    assert_cells(page, {"A1": "TRUE", "A2": "2026-10-01", "A3": "committed by blur"})
    page.reload()
    formula(page, "A3", "committed by blur")


@pytest.mark.requirements("REQ-3-1-1", "REQ-1-2-1")
def test_sheet_full_grid_edit_commits_and_escape_cancels(page, application):
    """Grid editing is a separate public entry point from the formula bar."""
    create_workbook(page, application)
    cell(page, "B2").dblclick()
    page.keyboard.type("grid value")
    page.keyboard.press("Enter")
    formula(page, "B2", "grid value")
    cell(page, "B2").dblclick()
    page.keyboard.press("Control+a")
    page.keyboard.type("cancelled")
    page.keyboard.press("Escape")
    formula(page, "B2", "grid value")
    page.reload()
    expect(cell(page, "B2")).to_have_text("grid value")


@pytest.mark.parametrize("menu", [False, True])
@pytest.mark.requirements("REQ-3-1-2", "REQ-3-2-2", "REQ-4-2-1", "REQ-1-2-1", "REQ-3-1-1", "REQ-4-1-1")
def test_sheet_full_external_paste_is_rectangular_and_undoable(page, application, menu):
    """REQ-3-1-2, REQ-3-2-2, REQ-4-2-1; both specified external-paste entry points."""
    create_workbook(page, application)
    edit(page, "B2", "before")
    edit(page, "C2", "=8*8")
    edit(page, "A1", "outside")
    edit(page, "E1", "=SUM(B2:C3)")
    paste(page, "B2", "10\t\n20\t30", menu=menu)
    assert_cells(page, {"A1": "outside", "B2": "10", "C2": "", "B3": "20", "C3": "30", "E1": "60"})
    page.get_by_role("button", name="Undo", exact=True).click()
    assert_cells(page, {"B2": "before", "C2": "64", "B3": "", "C3": "", "E1": "64"})
    formula(page, "C2", "=8*8")
    page.get_by_role("button", name="Redo", exact=True).click()
    assert_cells(page, {"A1": "outside", "B2": "10", "C2": "", "B3": "20", "C3": "30", "E1": "60"})
    page.reload()
    assert_cells(page, {"A1": "outside", "B2": "10", "C2": "", "B3": "20", "C3": "30", "E1": "60"})


@pytest.mark.requirements("REQ-3-1-2", "REQ-5-2-1", "REQ-1-3-1", "REQ-3-1-3")
def test_sheet_full_invalid_bulk_paste_rejects_entire_rectangle(page, application):
    """REQ-3-1-2, REQ-5-2-1."""
    import_csv(page, application, "name,value\nfirst,50\nsecond,60\n")
    number_rule(page, "B2", "B3")
    paste(page, "A2", "changed\t70\nalso changed\t101")
    expect(page.get_by_text("Please enter a number from 0 to 100", exact=True).first).to_be_visible()
    expected = {"A2": "first", "B2": "50", "A3": "second", "B3": "60"}
    assert_cells(page, expected)
    page.reload()
    assert_cells(page, expected)


@pytest.mark.requirements("REQ-3-2-1", "REQ-3-2-2", "REQ-1-3-1", "REQ-3-1-1", "REQ-3-1-3")
def test_sheet_full_copy_and_cut_preserve_rectangle_and_outside_cells(page, application):
    """REQ-3-2-1, REQ-3-2-2."""
    import_csv(page, application, "first,second\nthird,fourth\n")
    edit(page, "E1", "outside")
    transfer(page, "A1", "B2", "C3")
    source = {"A1": "first", "B1": "second", "A2": "third", "B2": "fourth"}
    target = {"C3": "first", "D3": "second", "C4": "third", "D4": "fourth"}
    assert_cells(page, source | target | {"E1": "outside"})
    transfer(page, "A1", "B2", "A5", cut=True)
    moved = {"A5": "first", "B5": "second", "A6": "third", "B6": "fourth"}
    assert_cells(page, dict.fromkeys(source, "") | moved | target | {"E1": "outside"})
    page.get_by_role("button", name="Undo", exact=True).click()
    assert_cells(page, source | dict.fromkeys(moved, "") | target)
    page.get_by_role("button", name="Redo", exact=True).click()
    assert_cells(page, dict.fromkeys(source, "") | moved | target | {"E1": "outside"})
    page.reload()
    assert_cells(page, dict.fromkeys(source, "") | moved | target | {"E1": "outside"})


@pytest.mark.requirements("REQ-3-2-1", "REQ-1-3-1", "REQ-3-1-1", "REQ-3-1-3", "REQ-5-2-1")
def test_sheet_full_invalid_cut_preserves_source_and_target(page, application):
    """REQ-3-2-1 atomic failure: do not clear source before target validation."""
    import_csv(page, application, "150,second\nthird,fourth\n")
    edit(page, "D4", "50")
    number_rule(page, "D4", "D5")
    transfer(page, "A1", "B2", "D4", cut=True)
    expect(page.get_by_text("Please enter a number from 0 to 100", exact=True).first).to_be_visible()
    expected = {"A1": "150", "B1": "second", "A2": "third", "B2": "fourth", "D4": "50", "E4": "", "D5": "", "E5": ""}
    assert_cells(page, expected)
    page.reload()
    assert_cells(page, expected)


@pytest.mark.requirements("REQ-4-1-2", "REQ-3-2-1", "REQ-1-2-1", "REQ-3-1-1", "REQ-3-1-3", "REQ-4-1-1")
def test_sheet_full_copy_formula_adjusts_relative_and_keeps_absolute_references(page, application):
    """REQ-4-1-2, REQ-3-2-1."""
    create_workbook(page, application)
    for address, value in {"A1": "10", "A2": "20", "B1": "30", "B2": "40", "C3": "=A1+$A$1+A$1+$A1"}.items():
        edit(page, address, value)
    expect(cell(page, "C3")).to_have_text("40")
    transfer(page, "C3", "C3", "D4")
    assert_cells(page, {"C3": "40", "D4": "100"})
    formula(page, "D4", "=B2+$A$1+B$1+$A2")
    page.reload()
    formula(page, "C3", "=A1+$A$1+A$1+$A1")
    formula(page, "D4", "=B2+$A$1+B$1+$A2")
    assert_cells(page, {"C3": "40", "D4": "100"})


@pytest.mark.requirements("REQ-4-1-2", "REQ-1-2-1", "REQ-3-1-1", "REQ-3-1-3", "REQ-3-2-1", "REQ-4-1-1")
def test_sheet_full_formula_copy_outside_bounds_reports_reference_error(page, application):
    """REQ-4-1-2 explicitly specifies the rewritten original expression."""
    create_workbook(page, application)
    edit(page, "A1", "10")
    edit(page, "B2", "=A1")
    transfer(page, "B2", "B2", "A2")
    assert_cells(page, {"A1": "10", "B2": "10", "A2": "#REF!"})
    formula(page, "A2", "=#REF!")
    page.reload()
    formula(page, "A2", "=#REF!")


@pytest.mark.requirements("REQ-4-1-1", "REQ-1-3-1", "REQ-3-1-1")
def test_sheet_full_aggregate_functions_ignore_text_and_blank_cells(page, application):
    """REQ-4-1-1: blanks must not become zero for MIN or AVERAGE."""
    import_csv(page, application, "10\ntext\n\n20\n30\n")
    expressions = {
        "B1": ("=sUm(A1:A5)", "60"),
        "B2": ("=AVERAGE(A1:A5)", "20"),
        "B3": ("=COUNT(A1:A5)", "3"),
        "B4": ("=MIN(A1:A5)", "10"),
        "B5": ("=MAX(A1:A5)", "30"),
        "C1": ("=(2+3)*4-6/2", "17"),
    }
    for address, (expression, result) in expressions.items():
        edit(page, address, expression)
        expect(cell(page, address)).to_have_text(result)
    page.reload()
    for address, (expression, result) in expressions.items():
        formula(page, address, expression)
        expect(cell(page, address)).to_have_text(result)


@pytest.mark.parametrize(
    ("expression", "error"),
    [("=1/0", "#DIV/0!"), ("=MISSING(1)", "#NAME?"), ("=1+", "#ERROR!"), ("=A1", "#REF!")],
)
@pytest.mark.requirements("REQ-4-2-2", "REQ-1-2-1", "REQ-3-1-1", "REQ-4-1-1")
def test_sheet_full_formula_error_preserves_expression_and_can_be_repaired(page, application, expression, error):
    """REQ-4-2-2."""
    create_workbook(page, application)
    edit(page, "A1", expression)
    edit(page, "B1", "=3*7")
    assert_cells(page, {"A1": error, "B1": "21"})
    page.reload()
    expect(cell(page, "A1")).to_have_text(error)
    formula(page, "A1", expression)
    expect(cell(page, "B1")).to_have_text("21")
    edit(page, "A1", "=6/2")
    edit(page, "C1", "=A1+1")
    assert_cells(page, {"A1": "3", "B1": "21", "C1": "4"})
    page.reload()
    assert_cells(page, {"A1": "3", "B1": "21", "C1": "4"})
    formula(page, "A1", "=6/2")


@pytest.mark.requirements("REQ-4-2-1", "REQ-4-2-2", "REQ-1-2-1", "REQ-3-1-1", "REQ-4-1-1")
def test_sheet_full_indirect_formula_cycle_does_not_block_other_cells(page, application):
    """REQ-4-2-1, REQ-4-2-2."""
    create_workbook(page, application)
    for address, value in {"A1": "=B1", "B1": "=A1", "C1": "=5*5"}.items():
        edit(page, address, value)
    assert_cells(page, {"A1": "#REF!", "B1": "#REF!", "C1": "25"})
    edit(page, "B1", "4")
    assert_cells(page, {"A1": "4", "B1": "4", "C1": "25"})
    page.reload()
    formula(page, "A1", "=B1")
    assert_cells(page, {"A1": "4", "B1": "4", "C1": "25"})


@pytest.mark.parametrize(("heading", "command"), [("2", "Insert 1 row above"), ("1", "Insert 1 row below")])
@pytest.mark.requirements(
    "REQ-2-2-1", "REQ-3-2-2", "REQ-4-2-1", "REQ-1-3-1", "REQ-3-1-1", "REQ-3-1-3", "REQ-4-1-1", "REQ-5-2-1"
)
def test_sheet_full_rows_shift_values_rules_and_formulas_as_one_operation(page, application, heading, command):
    """REQ-2-2-1, REQ-3-2-2, REQ-4-2-1."""
    import_csv(page, application, "name,value\nfirst,50\nsecond,60\n")
    edit(page, "D1", "=B2+B3")
    number_rule(page, "B2", "B3")
    structure_command(page, heading, command)
    assert_cells(page, {"A2": "", "B2": "", "A3": "first", "B3": "50", "A4": "second", "B4": "60", "D1": "110"})
    formula(page, "D1", "=B3+B4")
    edit(page, "B3", "101")
    expect(page.get_by_text("Please enter a number from 0 to 100", exact=True).first).to_be_visible()
    expect(cell(page, "B3")).to_have_text("50")
    page.get_by_role("button", name="Undo", exact=True).click()
    assert_cells(page, {"A2": "first", "B2": "50", "A3": "second", "B3": "60", "A4": "", "D1": "110"})
    formula(page, "D1", "=B2+B3")
    page.get_by_role("button", name="Redo", exact=True).click()
    assert_cells(page, {"A2": "", "B2": "", "A3": "first", "B3": "50", "A4": "second", "B4": "60", "D1": "110"})
    formula(page, "D1", "=B3+B4")
    structure_command(page, "2", "Delete row")
    assert_cells(page, {"A2": "first", "B2": "50", "A3": "second", "B3": "60", "D1": "110"})
    formula(page, "D1", "=B2+B3")
    page.reload()
    assert_cells(page, {"A2": "first", "B2": "50", "A3": "second", "B3": "60", "D1": "110"})
    formula(page, "D1", "=B2+B3")


@pytest.mark.parametrize(("heading", "command"), [("B", "Insert 1 column left"), ("A", "Insert 1 column right")])
@pytest.mark.requirements(
    "REQ-2-2-2", "REQ-1-3-1", "REQ-3-1-1", "REQ-3-1-3", "REQ-4-1-1", "REQ-4-2-1", "REQ-4-2-2", "REQ-5-2-1"
)
def test_sheet_full_columns_shift_rules_and_deleted_references_show_error(page, application, heading, command):
    """REQ-2-2-2; deleting the source keeps the formula but invalidates its reference."""
    import_csv(page, application, "name,value,note\nfirst,50,keep\nsecond,60,also keep\n")
    edit(page, "D2", "=B2")
    number_rule(page, "B2", "B3")
    structure_command(page, heading, command)
    assert_cells(page, {"B2": "", "C2": "50", "D2": "keep", "E2": "50", "A3": "second"})
    formula(page, "E2", "=C2")
    edit(page, "C3", "101")
    expect(page.get_by_text("Please enter a number from 0 to 100", exact=True).first).to_be_visible()
    expect(cell(page, "C3")).to_have_text("60")
    structure_command(page, "C", "Delete column")
    assert_cells(page, {"A2": "first", "B2": "", "C2": "keep", "D2": "#REF!", "C3": "also keep"})
    page.reload()
    assert_cells(page, {"A2": "first", "C2": "keep", "D2": "#REF!", "C3": "also keep"})


@pytest.mark.requirements("REQ-5-2-1", "REQ-1-2-1", "REQ-3-1-1", "REQ-3-1-3")
def test_sheet_full_dropdown_trims_values_and_rule_can_be_edited_then_removed(page, application):
    """REQ-5-2-1: rule update/deletion must not alter existing values."""
    create_workbook(page, application)
    edit(page, "B2", "Open")
    drag_range(page, "B2", "B3")
    data_command(page, "Data validation")
    dialog = page.get_by_role("dialog", name="Data validation", exact=True)
    choose(page, "Rule type", "Dropdown")
    dialog.get_by_label("Allowed values", exact=True).fill(" Open , Closed ")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).not_to_be_visible()
    page.get_by_role("button", name="Open dropdown for B3", exact=True).click()
    page.get_by_role("option", name="Closed", exact=True).click()
    expect(cell(page, "B3")).to_have_text("Closed")
    page.reload()
    edit(page, "B2", "Invalid")
    expect(
        page.get_by_text(re.compile(r"Please select one of the following values: Open,\s*Closed")).first
    ).to_be_visible()
    expect(cell(page, "B2")).to_have_text("Open")
    cell(page, "B2").click()
    data_command(page, "Data validation")
    expect(dialog.get_by_label("Allowed values", exact=True)).to_have_value(re.compile(r"Open,\s*Closed"))
    expect(dialog.get_by_role("button", name="Delete rule", exact=True)).to_be_visible()
    dialog.get_by_label("Allowed values", exact=True).fill("Open,Closed,Pending")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).not_to_be_visible()
    assert_cells(page, {"B2": "Open", "B3": "Closed"})
    edit(page, "B2", "Pending")
    expect(cell(page, "B2")).to_have_text("Pending")
    cell(page, "B2").click()
    data_command(page, "Data validation")
    dialog.get_by_role("button", name="Delete rule", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(cell(page, "B2")).to_have_text("Pending")
    edit(page, "B2", "now unrestricted")
    expect(cell(page, "B2")).to_have_text("now unrestricted")
    page.reload()
    expect(cell(page, "B2")).to_have_text("now unrestricted")


@pytest.mark.requirements("REQ-5-2-1", "REQ-1-2-1", "REQ-3-1-1", "REQ-3-1-3")
def test_sheet_full_numeric_validation_includes_boundaries_and_preserves_range(page, application):
    """REQ-5-2-1 inclusive endpoints and persistent full-range enforcement."""
    create_workbook(page, application)
    number_rule(page, "B2", "B4")
    edit(page, "B2", "0")
    edit(page, "B4", "100")
    assert_cells(page, {"B2": "0", "B4": "100"})
    page.reload()
    edit(page, "B4", "101")
    expect(cell(page, "B4")).to_have_text("100")
    edit(page, "B2", "-1")
    expect(cell(page, "B2")).to_have_text("0")
    cell(page, "B3").click()
    data_command(page, "Data validation")
    dialog = page.get_by_role("dialog", name="Data validation", exact=True)
    expect(dialog.get_by_label("Minimum", exact=True)).to_have_value("0")
    expect(dialog.get_by_label("Maximum", exact=True)).to_have_value("100")
    dialog.get_by_label("Maximum", exact=True).fill("200")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).not_to_be_visible()
    edit(page, "B4", "150")
    expect(cell(page, "B4")).to_have_text("150")


@pytest.mark.requirements("REQ-5-1-2", "REQ-1-3-2", "REQ-1-3-1", "REQ-3-1-3")
def test_sheet_full_value_and_condition_filters_combine_and_export_hidden_rows(page, application):
    """REQ-5-1-2, REQ-1-3-2: filters hide records without removing them."""
    content = "Region,Sales,Status\nEast,1200,Open\nNorth,800,Closed\nSouth,700,Open\n"
    import_csv(page, application, content)
    create_filter(page, "A1", "C4")
    page.get_by_role("button", name="Filter Status", exact=True).click()
    dialog = page.get_by_role("dialog", name="Filter Status", exact=True)
    dialog.get_by_role("button", name="Clear selection", exact=True).click()
    dialog.get_by_role("checkbox", name="Open", exact=True).check()
    dialog.get_by_role("button", name="Apply", exact=True).click()
    expect(cell(page, "A2")).to_be_visible()
    expect(cell(page, "A3")).not_to_be_visible()
    expect(cell(page, "A4")).to_be_visible()
    filter_condition(page, "Sales", "Greater than", "1000")
    expect(cell(page, "A4")).not_to_be_visible()
    page.reload()
    expect(cell(page, "A2")).to_be_visible()
    expect(cell(page, "A3")).not_to_be_visible()
    expect(cell(page, "A4")).not_to_be_visible()
    assert exported_rows(page) == list(csv.reader(io.StringIO(content)))
    data_command(page, "Clear filter")
    expect(cell(page, "A3")).to_be_visible()
    expect(cell(page, "A4")).to_be_visible()
    page.reload()
    for address, value in {"A2": "East", "A3": "North", "A4": "South"}.items():
        expect(cell(page, address)).to_be_visible()
        expect(cell(page, address)).to_have_text(value)


@pytest.mark.parametrize(
    ("heading", "condition", "value", "visible", "hidden"),
    [
        ("Name", "Text contains", "alpha", ["A2", "A4"], ["A3"]),
        ("Date", "Before", "2026-02-01", ["A2"], ["A3", "A4"]),
        ("Note", "Is empty", "", ["A3"], ["A2", "A4"]),
        ("Note", "Is not empty", "", ["A2", "A4"], ["A3"]),
    ],
)
@pytest.mark.requirements("REQ-5-1-2", "REQ-1-3-1", "REQ-3-1-3")
def test_sheet_full_filter_conditions_preserve_row_order(page, application, heading, condition, value, visible, hidden):
    """REQ-5-1-2: every named condition has a concrete example."""
    import_csv(page, application, "Name,Date,Note\nalpha,2026-01-10,x\nbeta,2026-03-10,\nalphabet,2026-04-10,y\n")
    create_filter(page, "A1", "C4")
    filter_condition(page, heading, condition, value)
    for address in visible:
        expect(cell(page, address)).to_be_visible()
    for address in hidden:
        expect(cell(page, address)).not_to_be_visible()
    page.reload()
    for address in visible:
        expect(cell(page, address)).to_be_visible()
    for address in hidden:
        expect(cell(page, address)).not_to_be_visible()


@pytest.mark.requirements("REQ-5-1-1", "REQ-1-3-1", "REQ-3-1-3")
def test_sheet_full_sort_is_limited_to_selected_rectangle(page, application):
    """REQ-5-1-1 must not implicitly include neighboring data."""
    import_csv(
        page, application, "Name,Amount,Outside\nfirst,20,keep first\nsecond,30,keep second\nthird,10,keep third\n"
    )
    drag_range(page, "A1", "B4")
    data_command(page, "Sort range")
    dialog = page.get_by_role("dialog", name="Sort range", exact=True)
    dialog.get_by_role("checkbox", name="Data has header row", exact=True).check()
    choose(page, "Sort by", "Amount")
    choose(page, "Order", "Descending")
    dialog.get_by_role("button", name="Sort", exact=True).click()
    expected = {
        "A1": "Name",
        "A2": "second",
        "B2": "30",
        "A3": "first",
        "B3": "20",
        "A4": "third",
        "C2": "keep first",
        "C3": "keep second",
        "C4": "keep third",
    }
    assert_cells(page, expected)
    page.reload()
    assert_cells(page, expected)


@pytest.mark.requirements("REQ-1-3-2", "REQ-1-2-1", "REQ-3-1-1", "REQ-4-1-1")
def test_sheet_full_export_uses_formula_results_and_preserves_formula_bar(page, application):
    """REQ-1-3-2."""
    create_workbook(page, application)
    edit(page, "A1", "7")
    edit(page, "C1", "=A1*3")
    formula(page, "C1", "=A1*3")
    assert exported_rows(page) == [["7", "", "21"]]
    expect(page.get_by_label("Formula bar", exact=True)).to_have_value("=A1*3")
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    page.reload()
    formula(page, "C1", "=A1*3")


@pytest.mark.requirements("REQ-5-3-1", "REQ-2-1-4", "REQ-1-3-1", "REQ-2-1-1", "REQ-2-1-2", "REQ-3-1-1", "REQ-3-1-3")
def test_sheet_full_pivot_sum_refresh_and_source_deletion_constraint(page, application):
    """REQ-5-3-1, REQ-2-1-4."""
    import_csv(page, application, "Region,Sales,Status\nEast,1200,Open\nNorth,800,Closed\nEast,700,Open\n")
    create_pivot(page, "C4")
    configure_pivot(page)
    expected = {
        "A1": "Region",
        "B1": "SUM of Sales",
        "A2": "East",
        "B2": "1900",
        "A3": "North",
        "B3": "800",
        "A4": "Grand Total",
        "B4": "2700",
    }
    assert_cells(page, expected)
    page.reload()
    assert_cells(page, expected)
    page.get_by_role("tab", name="Sheet1", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    assert_cells(page, {"A2": "East", "B2": "1200", "A3": "North", "B3": "800", "A4": "East", "B4": "700"})
    worksheet_command(page, "Sheet1", "Delete")
    dialog = page.get_by_role("dialog", name="Delete worksheet", exact=True)
    dialog.get_by_role("button", name="Delete worksheet", exact=True).click()
    expect(page.get_by_text("Please delete or rebuild dependent pivot tables first", exact=True).first).to_be_visible()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_be_visible()
    edit(page, "B2", "1300")
    expect(cell(page, "B2")).to_have_text("1300")
    page.get_by_role("tab", name="Pivot1", exact=True).click()
    expect(page.get_by_role("tab", name="Pivot1", exact=True)).to_have_attribute("aria-selected", "true")
    expect(cell(page, "B2")).to_have_text("1900")
    page.get_by_role("button", name="Refresh pivot table", exact=True).click()
    assert_cells(page, {"B2": "2000", "B4": "2800"})
    page.reload()
    assert_cells(page, {"B2": "2000", "B4": "2800"})
    worksheet_command(page, "Pivot1", "Delete")
    page.get_by_role("dialog", name="Delete worksheet", exact=True).get_by_role(
        "button", name="Delete worksheet", exact=True
    ).click()
    expect(page.get_by_role("tab", name="Pivot1", exact=True)).to_have_count(0)
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    page.get_by_role("button", name="Add worksheet", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_have_attribute("aria-selected", "true")
    worksheet_command(page, "Sheet1", "Delete")
    page.get_by_role("dialog", name="Delete worksheet", exact=True).get_by_role(
        "button", name="Delete worksheet", exact=True
    ).click()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_count(0)
    expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_have_attribute("aria-selected", "true")
    page.reload()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_count(0)
    expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-5-3-1", "REQ-1-3-1", "REQ-2-1-1", "REQ-3-1-3")
def test_sheet_full_pivot_count_columns_includes_nonnumeric_values_and_zero_combinations(page, application):
    """REQ-5-3-1 count counts records, including text, and emits missing combinations as zero."""
    import_csv(
        page, application, "Region,Sales,Status\nEast,text,Open\nNorth,800,Closed\nEast,,Closed\nEast,700,Open\n"
    )
    create_pivot(page, "C5")
    configure_pivot(page, method="COUNT", columns="Status")
    expected = {
        "A1": "Region",
        "B1": "Open",
        "C1": "Closed",
        "D1": "Grand Total",
        "A2": "East",
        "B2": "2",
        "C2": "0",
        "D2": "2",
        "A3": "North",
        "B3": "0",
        "C3": "1",
        "D3": "1",
        "A4": "Grand Total",
        "B4": "2",
        "C4": "1",
        "D4": "3",
    }
    assert_cells(page, expected)
    page.reload()
    assert_cells(page, expected)


@pytest.mark.requirements("REQ-5-3-1", "REQ-1-3-1", "REQ-2-1-1", "REQ-2-1-2", "REQ-3-1-3")
def test_sheet_full_pivot_average_ignores_text_and_invalid_configuration_keeps_old_result(page, application):
    """REQ-5-3-1 must not turn an invalid aggregate into a destructive partial update."""
    import_csv(
        page, application, "Region,Sales,Status\nEast,1200,Open\nNorth,800,Closed\nEast,text,Open\nEast,600,Closed\n"
    )
    create_pivot(page, "C5")
    configure_pivot(page, method="AVERAGE")
    expected = {
        "A1": "Region",
        "B1": "AVERAGE of Sales",
        "A2": "East",
        "B2": "900",
        "A3": "North",
        "B3": "800",
        "A4": "Grand Total",
    }
    assert_cells(page, expected)
    previous_total = cell(page, "B4").inner_text()
    assert float(previous_total) == pytest.approx(2600 / 3, rel=0.001)
    configure_pivot(page, values="Status")
    expect(page.get_by_text("Value field requires numeric values", exact=True).first).to_be_visible()
    assert_cells(page, expected | {"B4": previous_total})
    page.get_by_role("tab", name="Sheet1", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    assert_cells(page, {"A2": "East", "B2": "1200", "B4": "text", "C3": "Closed"})


@pytest.mark.requirements("REQ-2-2-2", "REQ-5-3-1", "REQ-1-3-1", "REQ-2-1-1", "REQ-2-1-2", "REQ-3-1-3")
def test_sheet_full_pivot_header_deletion_preserves_last_successful_summary(page, application):
    """REQ-2-2-2, REQ-5-3-1."""
    import_csv(page, application, "Region,Sales,Status\nEast,1200,Open\nNorth,800,Closed\n")
    create_pivot(page, "C3")
    configure_pivot(page)
    expected = {
        "A1": "Region",
        "B1": "SUM of Sales",
        "A2": "East",
        "B2": "1200",
        "A3": "North",
        "B3": "800",
        "A4": "Grand Total",
        "B4": "2000",
    }
    assert_cells(page, expected)
    page.get_by_role("tab", name="Sheet1", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    structure_command(page, "B", "Delete column")
    assert_cells(page, {"B1": "Status", "B2": "Open", "B3": "Closed"})
    page.get_by_role("tab", name="Pivot1", exact=True).click()
    expect(page.get_by_role("tab", name="Pivot1", exact=True)).to_have_attribute("aria-selected", "true")
    page.get_by_role("button", name="Refresh pivot table", exact=True).click()
    expect(
        page.get_by_text("Pivot field is no longer available. Select a new field.", exact=True).first
    ).to_be_visible()
    assert_cells(page, expected)
    page.reload()
    assert_cells(page, expected)
    page.get_by_role("tab", name="Sheet1", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    assert_cells(page, {"A2": "East", "B2": "Open", "A3": "North", "B3": "Closed"})
