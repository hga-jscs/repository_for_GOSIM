"""Preserve every row in the supplied database, including legacy duplicate names."""

import sqlite3
import time
from pathlib import Path

import httpx
import pytest

from server import application_server
from tools import WorkspaceTools


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


def snapshot(connection):
    tables = {}
    for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        columns = connection.execute(f"PRAGMA table_info({identifier(name)})").fetchall()
        primary = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
        names = [row[1] for row in columns]
        rows = connection.execute(f'SELECT {",".join(map(identifier, names))} FROM {identifier(name)}').fetchall()
        tables[name] = {"columns": names, "primary": primary, "rows": sorted(rows)}
    return tables


@pytest.mark.shared
@pytest.mark.parametrize(("renamed_before_upgrade",), [(False,), (True,)])
def test_evolution_sheet_existing_database_survives_first_upgrade_and_restarts(request, tmp_path, renamed_before_upgrade):
    workspace = Path(request.config.getoption("--application")).resolve()
    original = workspace / "backend/database.db"
    assert original.is_file(), "This input baseline contains a real database; do not silently replace it with a blank database"
    database = tmp_path / "existing-database.db"
    with sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(database) as destination:
        source.backup(destination)
    with sqlite3.connect(database) as connection:
        # Simulate the first upgrade, even if a prior local startup added the registry.
        connection.execute("DROP TABLE IF EXISTS seed_versions")
        if renamed_before_upgrade:
            assert connection.execute("UPDATE workbooks SET name='User renamed original workbook' WHERE name='Q3 Sales'").rowcount == 1
        before = snapshot(connection)
    counts = {name: len(data["rows"]) for name, data in before.items()}
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    first = None
    for restart in range(3):
        with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
            assert ready, "The actual injected database must remain startable"
            with httpx.Client(base_url=env["BASE_URL"], timeout=20) as client:
                response = client.get("/api/workbooks")
                assert response.is_success, response.text
                if renamed_before_upgrade:
                    assert not any(row["name"] == "Q3 Sales" for row in response.json()["workbooks"]), "First migration must not recreate an original workbook already renamed before seed versions existed"
            with sqlite3.connect(database) as connection:
                after = snapshot(connection)
                for name, data in before.items():
                    assert name in after, name
                    assert data["primary"], f"Expected a stable existing primary key for {name}"
                    indexes = [data["columns"].index(column) for column in data["primary"]]
                    for row in data["rows"]:
                        where = " AND ".join(identifier(column) + "=?" for column in data["primary"])
                        current = connection.execute(f'SELECT {",".join(map(identifier, data["columns"]))} FROM {identifier(name)} WHERE {where}', [row[index] for index in indexes]).fetchone()
                        assert current == row, (name, row, current)
                if restart == 0:
                    first = after
                else:
                    assert after == first, "A later startup must not recreate, duplicate or rewrite records"
    print({"existing_row_counts": counts, "restarts_after_upgrade": 2, "renamed_before_upgrade": renamed_before_upgrade})
