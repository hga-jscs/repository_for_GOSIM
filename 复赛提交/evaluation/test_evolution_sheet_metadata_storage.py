"""Persist three metadata features and deliberate deletions over two restarts."""

import sqlite3
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from server import application_server
from tools import WorkspaceTools
from evolution_sheet_helpers import browser_errors, cell, cell_value, formula_entry, open_workbook
from test_evolution_sheet_named_ranges import named_dialog
from test_evolution_sheet_formatting import format_dialog, GREEN
from test_evolution_sheet_notes import note_text, open_note


@pytest.mark.requirements("REQ-7-1-1", "REQ-7-2-1", "REQ-8-1-1")
def test_evolution_sheet_metadata_and_deletions_survive_two_restarts(page, request, tmp_path):
    workspace = Path(request.config.getoption("--application")).resolve()
    database = tmp_path / "metadata.db"
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    for restart in range(3):
        with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
            assert ready, output
            application = env["BASE_URL"]
            endpoint = open_workbook(page, application, "EVO-N03-NAMED-UPDATE" if restart == 0 else "User forecast", "ForecastModel")
            if restart == 0:
                dialog = named_dialog(page)
                dialog.get_by_role("button", name="Edit MarginBase", exact=True).click()
                dialog.get_by_role("textbox", name="Range", exact=True).fill("ForecastModel!K2:K4")
                dialog.get_by_role("button", name="Save", exact=True).click()
                formula_entry(page, "K4", "22")
                cell_value(page, "M2", "35")
                assert page.request.patch(endpoint, data={"name": "User forecast"}).ok
            else:
                cell_value(page, "M2", "35")
            open_workbook(page, application, "EVO-N04-FORMAT-EDIT", "Signals")
            if restart == 0:
                dialog = format_dialog(page)
                dialog.get_by_role("button", name="Edit rule 1", exact=True).click()
                dialog.get_by_role("combobox", name="Style", exact=True).select_option(label="Green fill")
                dialog.get_by_role("button", name="Save", exact=True).click()
            expect(cell(page, "L4")).to_have_css("background-color", GREEN)
            open_workbook(page, application, "EVO-N05-NOTE-EDIT", "ReviewQueue")
            if restart == 0:
                dialog = open_note(page, "F6")
                dialog.get_by_role("button", name="Edit note", exact=True).click()
                dialog.get_by_role("textbox", name="Note", exact=True).fill("Saved user note")
                dialog.get_by_role("button", name="Save note", exact=True).click()
            note_text(page, "F6", "Saved user note")
            open_workbook(page, application, "EVO-N05-NOTE-DELETE", "ReviewQueue")
            if restart == 0:
                open_note(page, "J3").get_by_role("button", name="Delete note", exact=True).click()
            expect(page.get_by_role("button", name="Open note for J3", exact=True)).to_have_count(0)
            cell_value(page, "J3", "Route Zeta")
            if restart:
                page.goto(application)
                for name in ("EVO-N03-NAMED-UPDATE", "EVO-N04-FORMAT-TEXT"):
                    expect(page.get_by_role("link", name=name, exact=True)).to_have_count(0)
        if restart == 0:
            with sqlite3.connect(database) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                assert connection.execute("DELETE FROM workbooks WHERE name='EVO-N04-FORMAT-TEXT'").rowcount == 1
