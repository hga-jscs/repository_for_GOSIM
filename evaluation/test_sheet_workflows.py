import csv
import io
import uuid
from pathlib import Path

from playwright.sync_api import expect
from test_browser import create_workbook


def cell(page, address):
    return page.get_by_role("gridcell", name=address, exact=True)


def edit(page, address, value):
    cell(page, address).click()
    page.get_by_label("Formula bar", exact=True).fill(value)
    page.get_by_label("Formula bar", exact=True).press("Enter")


def import_csv(page, application, content):
    page.goto(application)
    page.get_by_role("button", name="Import CSV", exact=True).click()
    dialog = page.get_by_role("dialog", name="Import CSV", exact=True)
    filename = "Import-" + uuid.uuid4().hex[:10] + ".csv"
    dialog.get_by_label("CSV file", exact=True).set_input_files(
        {"name": filename, "mimeType": "text/csv", "buffer": content.encode("utf-8")}
    )
    dialog.get_by_role("button", name="Confirm import", exact=True).click()
    return filename[:-4]


def select_range(page, start, end):
    cell(page, start).click()
    cell(page, end).click(modifiers=["Shift"])


def data_command(page, name):
    page.get_by_role("button", name="Data", exact=True).click()
    page.get_by_role("menuitem", name=name, exact=True).click()


def test_sheet_full_csv_roundtrip_preserves_quoted_unicode_and_empty_cells(page, application):
    content = 'Name,Value,Note\r\n中文,12,"hello,world"\r\n"a""b",,"line1\nline2"\r\n'
    import_csv(page, application, content)
    expect(cell(page, "A2")).to_have_text("中文")
    expect(cell(page, "C2")).to_have_text("hello,world")
    expect(cell(page, "A3")).to_have_text('a"b')
    expect(cell(page, "B3")).to_have_text("")
    expect(cell(page, "C3")).to_have_text("line1\nline2")
    with page.expect_download() as pending:
        page.get_by_role("button", name="Export CSV", exact=True).click()
    download = pending.value
    assert download.suggested_filename.endswith(".csv")
    exported = Path(download.path()).read_text(encoding="utf-8-sig")
    assert list(csv.reader(io.StringIO(exported))) == list(csv.reader(io.StringIO(content)))
    page.reload()
    expect(cell(page, "A3")).to_have_text('a"b')


def test_sheet_full_invalid_csv_does_not_create_a_workbook(page, application):
    name = import_csv(page, application, 'Name,Value\n"unclosed,12')
    expect(page.get_by_text("Invalid CSV file format. Import failed.", exact=True).first).to_be_visible()
    page.goto(application)
    expect(page.get_by_role("link", name=name, exact=True)).to_have_count(0)


def test_sheet_full_worksheet_switch_keeps_data_and_selection(page, application):
    create_workbook(page, application)
    edit(page, "B2", "original")
    expect(cell(page, "B2")).to_have_text("original")
    cell(page, "B2").click()
    page.get_by_role("button", name="Add worksheet", exact=True).click()
    expect(page.get_by_role("tab", name="Sheet2", exact=True)).to_have_attribute("aria-selected", "true")
    expect(cell(page, "A1")).to_have_attribute("aria-selected", "true")
    expect(cell(page, "B2")).to_have_text("")
    page.get_by_role("tab", name="Sheet1", exact=True).click()
    expect(cell(page, "B2")).to_have_text("original")
    expect(cell(page, "B2")).to_have_attribute("aria-selected", "true")
    page.reload()
    expect(page.get_by_role("tab", name="Sheet1", exact=True)).to_have_attribute("aria-selected", "true")
    expect(cell(page, "B2")).to_have_attribute("aria-selected", "true")


def test_sheet_full_formula_chain_recalculates_and_persists(page, application):
    create_workbook(page, application)
    edit(page, "A1", "10")
    edit(page, "A2", "20")
    edit(page, "B1", "=SUM(A1:A3)")
    expect(cell(page, "B1")).to_have_text("30")
    edit(page, "C1", "=B1*2")
    expect(cell(page, "C1")).to_have_text("60")
    edit(page, "A1", "15")
    expect(cell(page, "C1")).to_have_text("70")
    page.reload()
    expect(cell(page, "C1")).to_have_text("70")
    cell(page, "B1").click()
    expect(page.get_by_label("Formula bar", exact=True)).to_have_value("=SUM(A1:A3)")


def test_sheet_full_formula_errors_can_be_corrected(page, application):
    create_workbook(page, application)
    edit(page, "A1", "=1/0")
    expect(cell(page, "A1")).to_have_text("#DIV/0!")
    edit(page, "B1", "=B1")
    expect(cell(page, "B1")).to_have_text("#REF!")
    edit(page, "A1", "=6/2")
    expect(cell(page, "A1")).to_have_text("3")
    page.reload()
    expect(cell(page, "A1")).to_have_text("3")
    expect(cell(page, "B1")).to_have_text("#REF!")


def test_sheet_full_undo_redo_and_new_branch(page, application):
    create_workbook(page, application)
    edit(page, "A1", "before")
    expect(cell(page, "A1")).to_have_text("before")
    edit(page, "A1", "after")
    expect(cell(page, "A1")).to_have_text("after")
    page.get_by_role("button", name="Undo", exact=True).click()
    expect(cell(page, "A1")).to_have_text("before")
    page.get_by_role("button", name="Redo", exact=True).click()
    expect(cell(page, "A1")).to_have_text("after")
    page.get_by_role("button", name="Undo", exact=True).click()
    expect(cell(page, "A1")).to_have_text("before")
    edit(page, "A1", "new branch")
    expect(page.get_by_role("button", name="Redo", exact=True)).to_be_disabled()
    page.reload()
    expect(cell(page, "A1")).to_have_text("new branch")


def test_sheet_full_stable_sort_moves_whole_rows(page, application):
    import_csv(page, application, "Name,Amount\nfirst,2\nsecond,1\nthird,2\n")
    expect(cell(page, "A4")).to_have_text("third")
    select_range(page, "A1", "B4")
    data_command(page, "Sort range")
    dialog = page.get_by_role("dialog", name="Sort range", exact=True)
    dialog.get_by_role("checkbox", name="Data has header row", exact=True).check()
    dialog.get_by_role("combobox", name="Sort by", exact=True).select_option(label="Amount")
    dialog.get_by_role("combobox", name="Order", exact=True).select_option(label="Ascending")
    dialog.get_by_role("button", name="Sort", exact=True).click()
    expect(cell(page, "A2")).to_have_text("second")
    expect(cell(page, "A3")).to_have_text("first")
    expect(cell(page, "A4")).to_have_text("third")
    page.reload()
    expect(cell(page, "A2")).to_have_text("second")


def test_sheet_full_number_validation_persists(page, application):
    create_workbook(page, application)
    edit(page, "B3", "50")
    expect(cell(page, "B3")).to_have_text("50")
    select_range(page, "B2", "B4")
    data_command(page, "Data validation")
    dialog = page.get_by_role("dialog", name="Data validation", exact=True)
    dialog.get_by_role("combobox", name="Rule type", exact=True).select_option(label="Number range")
    dialog.get_by_label("Minimum", exact=True).fill("0")
    dialog.get_by_label("Maximum", exact=True).fill("100")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).not_to_be_visible()
    page.reload()
    edit(page, "B3", "101")
    expect(page.get_by_text("Please enter a number from 0 to 100", exact=True).first).to_be_visible()
    expect(cell(page, "B3")).to_have_text("50")
