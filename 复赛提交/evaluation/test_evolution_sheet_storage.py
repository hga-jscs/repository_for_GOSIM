"""Public incremental preservation requirements on a real pre-versioned Sheet database."""

import sqlite3
import time
from pathlib import Path

import httpx
import pytest

from server import application_server
from tools import WorkspaceTools
from test_evolution_github_database import test_evolution_github_transaction_isolation_preserves_independent_writes as verify_transaction_isolation


@pytest.mark.shared
def test_evolution_sheet_transaction_isolation_preserves_independent_writes(request, tmp_path):
    # Both supplied applications expose the same db_runtime contract. Execute the
    # existing independent rollback probe against THIS Sheet application's module.
    verify_transaction_isolation(request, tmp_path)


@pytest.mark.shared
def test_evolution_sheet_legacy_upgrade_and_two_restarts_preserve_edits_and_deletions(request, tmp_path):
    workspace = Path(request.config.getoption("--application")).resolve()
    database = tmp_path / "legacy-sheet.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')));
            CREATE TABLE worksheets (id INTEGER PRIMARY KEY AUTOINCREMENT, workbook_id INTEGER NOT NULL
              REFERENCES workbooks(id) ON DELETE CASCADE, name TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
              is_active INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE cells (id INTEGER PRIMARY KEY AUTOINCREMENT, worksheet_id INTEGER NOT NULL
              REFERENCES worksheets(id) ON DELETE CASCADE, row INTEGER NOT NULL, col INTEGER NOT NULL,
              value TEXT NOT NULL DEFAULT '');
            INSERT INTO workbooks (id, name) VALUES (41, 'Q3 Sales');
            INSERT INTO worksheets (id, workbook_id, name, position, is_active) VALUES (42, 41, 'User worksheet', 0, 1);
            INSERT INTO cells (id, worksheet_id, row, col, value) VALUES
              (44,42,1,1,'31'),(45,42,1,2,'3'),(46,42,1,3,'=A1+B1');
        """)
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    renamed_evolution = None
    deleted_evolution = None
    for restart in range(3):
        with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
            assert ready, output
            with httpx.Client(base_url=env["BASE_URL"], timeout=20) as client:
                response = client.get("/api/workbooks/41")
                assert response.is_success, response.text
                data = response.json()["workbook"]
                assert data["name"] == ("Q3 Sales" if restart == 0 else "User preserved sales")
                assert len(data["worksheets"]) == 1
                sheet = data["worksheets"][0]
                assert sheet["id"] == 42 and sheet["name"] == "User worksheet"
                assert sheet["cells"] == {"1,1": "31", "1,2": "3", "1,3": "=A1+B1"}, "Migration must not reinsert deleted original seed cells"
                assert sheet["display"]["1,3"] == "34"
                if restart:
                    names = [item["name"] for item in client.get("/api/workbooks").json()["workbooks"]]
                    assert names.count("User preserved sales") == 1 and "Q3 Sales" not in names
                    if renamed_evolution:
                        assert "User edited evolution workbook" in names and renamed_evolution not in names
                    if deleted_evolution:
                        assert deleted_evolution not in names
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            assert connection.execute("SELECT id,row,col,value FROM cells WHERE worksheet_id=42 ORDER BY id").fetchall() == [
                (44, 1, 1, "31"), (45, 1, 2, "3"), (46, 1, 3, "=A1+B1")]
            if restart == 0:
                connection.execute("UPDATE workbooks SET name='User preserved sales' WHERE id=41")
                if connection.execute("SELECT id FROM workbooks WHERE name='EVO-M03-CLEAR-TEXT'").fetchone():
                    renamed_evolution = "EVO-M03-CLEAR-TEXT"
                    connection.execute("UPDATE workbooks SET name='User edited evolution workbook' WHERE name=?", (renamed_evolution,))
                if connection.execute("SELECT id FROM workbooks WHERE name='EVO-M01-ARCHIVE-RESERVED'").fetchone():
                    deleted_evolution = "EVO-M01-ARCHIVE-RESERVED"
                    connection.execute("DELETE FROM workbooks WHERE name=?", (deleted_evolution,))
