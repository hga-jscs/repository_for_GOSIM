"""Freeze and replace persistence with the same database through two restarts."""

import sqlite3
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from server import application_server
from tools import WorkspaceTools
from evolution_sheet_helpers import browser_errors, cell_value, formula_entry, open_workbook
from test_evolution_sheet_freeze import freeze
from test_evolution_sheet_find import find_dialog, replace


@pytest.mark.requirements("REQ-6-1-1", "REQ-6-2-1")
def test_evolution_sheet_navigation_state_and_seed_edits_survive_two_restarts(page, request, tmp_path):
    workspace = Path(request.config.getoption("--application")).resolve()
    database = tmp_path / "navigation.db"
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    for restart in range(3):
        with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
            assert ready
            application = env["BASE_URL"]
            open_workbook(page, application, "EVO-N01-FREEZE-BOTH", "ScrollLedger")
            if restart == 0:
                formula_entry(page, "A1", "User heading retained")
                cell_value(page, "A1", "User heading retained")
                freeze(page, "C4", "Freeze panes at C4", 3, 2)
            else:
                cell_value(page, "A1", "User heading retained")
                expect(page.get_by_role("button", name="Frozen rows: 3; columns: 2", exact=True)).to_be_visible()
            endpoint = open_workbook(page, application, "EVO-N02-REPLACE-ALL" if restart == 0 else "Saved replacement result", "Narrative")
            if restart == 0:
                replace(find_dialog(page), "Cobalt", "Indigo", 3)
                assert page.request.patch(endpoint, data={"name": "Saved replacement result"}).ok
            else:
                for address, value in {"F3": "Indigo", "F6": "Indigo", "F9": "Indigo", "F12": "Copper"}.items():
                    cell_value(page, address, value)
                page.goto(application)
                expect(page.get_by_role("link", name="EVO-N02-REPLACE-ALL", exact=True)).to_have_count(0)
                expect(page.get_by_role("link", name="EVO-N02-FIND-NEXT", exact=True)).to_have_count(0)
        if restart == 0:
            with sqlite3.connect(database) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                assert connection.execute("DELETE FROM workbooks WHERE name='EVO-N02-FIND-NEXT'").rowcount == 1
