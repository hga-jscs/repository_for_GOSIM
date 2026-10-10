"""Six public workbook/worksheet naming scenarios; no unpublished test input."""

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from playwright.sync_api import expect

from evolution_sheet_helpers import application, browser_errors, open_workbook, cell_value, rename_sheet, workbook, active_sheet


@pytest.mark.requirements("REQ-1-2-2")
@pytest.mark.scenarios("REQ-1-2-2#1")
def test_evolution_sheet_workbook_rename_trim_and_persist(page, application):
    endpoint = open_workbook(page, application, "EVO-M01-RENAME-OK", "IdentityLog")
    before = workbook(page, endpoint)
    page.get_by_role("button", name="Rename workbook", exact=True).click()
    page.get_by_role("textbox", name="Workbook name", exact=True).fill("  FY26 Procurement Ledger  ")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("heading", name="FY26 Procurement Ledger", exact=True)).to_be_visible()
    page.reload()
    cell_value(page, "F3", "unreviewed")
    page.get_by_role("button", name="Rename workbook", exact=True).click()
    expect(page.get_by_role("textbox", name="Workbook name", exact=True)).to_have_value("FY26 Procurement Ledger")
    after = workbook(page, endpoint)
    assert [{key: value for key, value in sheet.items() if key != "selection"} for sheet in after["worksheets"]] == [
        {key: value for key, value in sheet.items() if key != "selection"} for sheet in before["worksheets"]]
    page.goto(application)
    expect(page.get_by_role("link", name="FY26 Procurement Ledger", exact=True)).to_have_count(1)
    expect(page.get_by_role("link", name="EVO-M01-RENAME-OK", exact=True)).to_have_count(0)


@pytest.mark.requirements("REQ-1-2-2")
@pytest.mark.scenarios("REQ-1-2-2#2")
def test_evolution_sheet_workbook_duplicate_rolls_back(page, application):
    name = "EVO-M01-RENAME-DUP"
    endpoint = open_workbook(page, application, name, "IdentityLog")
    page.get_by_role("button", name="Rename workbook", exact=True).click()
    page.get_by_role("textbox", name="Workbook name", exact=True).fill("evo-m01-archive-reserved")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Workbook name already exists", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="Workbook name", exact=True)).to_have_value(name)
    page.reload()
    page.get_by_role("button", name="Rename workbook", exact=True).click()
    expect(page.get_by_role("textbox", name="Workbook name", exact=True)).to_have_value(name)
    assert workbook(page, endpoint)["name"] == name
    page.goto(application)
    expect(page.get_by_role("link", name=name, exact=True)).to_have_count(1)
    expect(page.get_by_role("link", name="EVO-M01-ARCHIVE-RESERVED", exact=True)).to_have_count(1)


@pytest.mark.requirements("REQ-1-2-2")
@pytest.mark.scenarios("REQ-1-2-2#3")
def test_evolution_sheet_workbook_length_limit(page, application):
    name = "EVO-M01-RENAME-LIMIT"
    open_workbook(page, application, name, "LimitProbe")
    page.get_by_role("button", name="Rename workbook", exact=True).click()
    page.get_by_role("textbox", name="Workbook name", exact=True).fill("EVO-" + "A" * 77)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Workbook name must be 80 characters or fewer", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="Workbook name", exact=True)).to_have_value(name)
    page.reload()
    expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
    cell_value(page, "C2", "limit sentinel")


@pytest.mark.requirements("REQ-2-1-3")
@pytest.mark.scenarios("REQ-2-1-3#1")
def test_evolution_sheet_worksheet_rename_keeps_active_identity(page, application):
    endpoint = open_workbook(page, application, "EVO-M02-SHEET-OK", "HarborDraft")
    before = active_sheet(workbook(page, endpoint))
    dialog = rename_sheet(page, "HarborDraft")
    dialog.get_by_role("textbox", name="Worksheet name", exact=True).fill("  Dispatch Register  ")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("tab", name="Dispatch Register", exact=True)).to_have_attribute("aria-selected", "true")
    page.reload()
    expect(page.get_by_role("tab", name="Dispatch Register", exact=True)).to_have_attribute("aria-selected", "true")
    cell_value(page, "D5", "dock marker")
    after = active_sheet(workbook(page, endpoint))
    assert after["id"] == before["id"] and after["cells"] == before["cells"]
    expect(rename_sheet(page, "Dispatch Register").get_by_role("textbox", name="Worksheet name", exact=True)).to_have_value("Dispatch Register")


@pytest.mark.requirements("REQ-2-1-3")
@pytest.mark.scenarios("REQ-2-1-3#2")
def test_evolution_sheet_worksheet_duplicate_rolls_back(page, application):
    open_workbook(page, application, "EVO-M02-SHEET-DUP", "ArchiveBay")
    dialog = rename_sheet(page, "ArchiveBay")
    dialog.get_by_role("textbox", name="Worksheet name", exact=True).fill("meridian")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog.get_by_text("Worksheet name already exists", exact=True)).to_be_visible()
    expect(dialog.get_by_role("textbox", name="Worksheet name", exact=True)).to_have_value("ArchiveBay")
    expect(page.get_by_role("tab", name="ArchiveBay", exact=True)).to_have_attribute("aria-selected", "true")
    page.reload()
    expect(page.get_by_role("tab", name="ArchiveBay", exact=True)).to_have_attribute("aria-selected", "true")
    expect(rename_sheet(page, "ArchiveBay").get_by_role("textbox", name="Worksheet name", exact=True)).to_have_value("ArchiveBay")


@pytest.mark.requirements("REQ-2-1-3")
@pytest.mark.scenarios("REQ-2-1-3#3")
def test_evolution_sheet_worksheet_length_limit(page, application):
    open_workbook(page, application, "EVO-M02-SHEET-LIMIT", "LengthGauge")
    dialog = rename_sheet(page, "LengthGauge")
    dialog.get_by_role("textbox", name="Worksheet name", exact=True).fill("B" * 51)
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog.get_by_text("Worksheet name must be 50 characters or fewer", exact=True)).to_be_visible()
    expect(dialog.get_by_role("textbox", name="Worksheet name", exact=True)).to_have_value("LengthGauge")
    page.reload()
    expect(page.get_by_role("tab", name="LengthGauge", exact=True)).to_have_attribute("aria-selected", "true")
    cell_value(page, "G4", "sheet sentinel")


@pytest.mark.requirements("REQ-1-2-2", "REQ-2-1-3")
def test_evolution_sheet_name_boundaries_and_empty_rollbacks(page, application):
    endpoint = open_workbook(page, application, "EVO-M02-SHEET-OK", "HarborDraft")
    page.get_by_role("button", name="Rename workbook", exact=True).click()
    entry = page.get_by_role("textbox", name="Workbook name", exact=True)
    entry.fill("   ")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Workbook name cannot be empty", exact=True)).to_be_visible()
    expect(entry).to_have_value("EVO-M02-SHEET-OK")
    entry.fill("A" * 80)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("heading", name="A" * 80, exact=True)).to_be_visible()
    dialog = rename_sheet(page, "HarborDraft")
    dialog.get_by_role("textbox", name="Worksheet name", exact=True).fill("  ")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog.get_by_text("Worksheet name cannot be empty", exact=True)).to_be_visible()
    expect(dialog.get_by_role("textbox", name="Worksheet name", exact=True)).to_have_value("HarborDraft")
    dialog.get_by_role("textbox", name="Worksheet name", exact=True).fill("B" * 50)
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("tab", name="B" * 50, exact=True)).to_have_attribute("aria-selected", "true")
    assert active_sheet(workbook(page, endpoint))["cells"]["5,4"] == "dock marker"
    page.reload()
    cell_value(page, "D5", "dock marker")


@pytest.mark.requirements("REQ-1-2-2", "REQ-2-1-3")
def test_evolution_sheet_naming_is_atomic_and_shared_by_creation_paths(application):
    with httpx.Client(base_url=application, timeout=20) as client:
        ids = []
        for name in ("Concurrent name left", "Concurrent name right"):
            response = client.post("/api/workbooks", json={"name": name})
            assert response.is_success, response.text
            ids.append(response.json()["workbook"]["id"])
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda identifier: client.patch(f"/api/workbooks/{identifier}", json={"name": "Shared destination"}), ids))
        assert sum(response.is_success for response in outcomes) == 1, [response.text for response in outcomes]
        assert all(response.is_success or response.status_code in {400, 409, 422} for response in outcomes)
        for response in (client.post("/api/workbooks", json={"name": "shared DESTINATION"}),
                         client.post("/api/workbooks/import", json={"fileName": "SHARED destination.csv", "content": "sentinel,23\n"})):
            assert response.is_success or response.status_code in {400, 409, 422}, response.text
        names = [row["name"].casefold() for row in client.get("/api/workbooks").json()["workbooks"]]
        assert len(names) == len(set(names)), "Create/import must preserve the global uniqueness invariant"
        winner = next(response.json()["workbook"] for response in outcomes if response.is_success)
        assert client.patch(f'/api/workbooks/{winner["id"]}', json={"name": "  shared destination  "}).is_success
        for identifier in ids:
            data = client.get(f"/api/workbooks/{identifier}").json()["workbook"]
            worksheet_id = data["worksheets"][0]["id"]
            assert client.patch(f"/api/workbooks/{identifier}/worksheets/{worksheet_id}", json={"name": "Same tab across separate workbooks"}).is_success
