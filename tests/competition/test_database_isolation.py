import json
import socket
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server import application_server
from tools import WorkspaceTools


@pytest.fixture
def database_workspace(tmp_path: Path) -> WorkspaceTools:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("backend/package.json", '{"scripts":{"start":"node server.js"}}')
    tools.write_file(
        "backend/server.js",
        "const {DatabaseSync}=require('node:sqlite');\n"
        "const database=process.env.ARC_DB_FILE||process.env.DATABASE_FILE||'database.db';\n"
        "const db=new DatabaseSync(database);\n"
        "db.exec('PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS counter "
        "(id INTEGER PRIMARY KEY,value INTEGER); INSERT OR IGNORE INTO counter VALUES(1,0)');\n"
        "require('http').createServer((request,response)=>{\n"
        "if(request.method==='POST') db.exec('UPDATE counter SET value=value+1 WHERE id=1');\n"
        "response.end(JSON.stringify({database,value:db.prepare('SELECT value FROM counter').get().value}));\n"
        "}).listen(process.env.PORT);\n",
    )
    tools.write_file(
        "check.py",
        "import json,os,sqlite3,sys,time,urllib.request\n"
        "from pathlib import Path\n"
        "request=urllib.request.Request(os.environ['BASE_URL'],data=b'',method='POST')\n"
        "result=json.load(urllib.request.urlopen(request,timeout=5))\n"
        "assert result['database']==os.environ['ARC_DB_FILE']\n"
        "assert Path(result['database']).is_file()\n"
        "with sqlite3.connect(Path(result['database']).resolve().as_uri()+'?mode=ro',uri=True) as database:\n"
        "    assert database.execute('SELECT value FROM counter').fetchone()[0]==result['value']\n"
        "print(json.dumps(result),flush=True)\n"
        "time.sleep(float(sys.argv[1]) if len(sys.argv)>1 else 0)\n"
        "sys.exit(int(sys.argv[2]) if len(sys.argv)>2 else 0)\n",
    )
    with sqlite3.connect(tmp_path / "backend/database.db") as database:
        database.execute("CREATE TABLE sentinel (value TEXT)")
        database.execute("INSERT INTO sentinel VALUES ('original user data')")
    return tools


def assert_server_closed(port: int) -> None:
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(("127.0.0.1", port)) != 0


def test_default_managed_databases_are_independent_and_preserve_existing_data(
    database_workspace: WorkspaceTools,
) -> None:
    tools = database_workspace
    original = (tools.workspace / "backend/database.db").read_bytes()
    paths = []
    for _ in range(2):
        result = tools.run_with_server("python check.py", 10)
        assert result["returncode"] == 0 and not result["timed_out"], result
        record = json.loads(result["output"])
        assert record["value"] == 1
        path = Path(record["database"])
        assert not path.is_relative_to(tools.workspace)
        assert not path.parent.exists()
        assert (tools.workspace / "backend/database.db").read_bytes() == original
        assert_server_closed(result["port"])
        paths.append(path)
    assert paths[0] != paths[1]


@pytest.mark.parametrize(("delay", "exit_status", "timed_out"), [(0, 7, False), (30, 124, True)])  # noqa: PT006
def test_failed_checks_remove_temporary_database_after_server_shutdown(
    database_workspace: WorkspaceTools, delay: int, exit_status: int, timed_out: bool
) -> None:
    tools = database_workspace
    original = (tools.workspace / "backend/database.db").read_bytes()
    result = tools.run_with_server(f"python check.py {delay} {0 if timed_out else exit_status}", 4)
    assert result["returncode"] == exit_status and result["timed_out"] is timed_out, result
    record = json.loads(result["output"])
    assert record["value"] == 1
    assert not Path(record["database"]).parent.exists()
    assert (tools.workspace / "backend/database.db").read_bytes() == original
    assert_server_closed(result["port"])


@pytest.mark.parametrize(
    ("configuration", "relative"),  # noqa: PT006
    [("arc", False), ("database", False), ("both", False), ("arc", True), ("database", True)],
)
def test_explicit_database_survives_server_restarts(
    database_workspace: WorkspaceTools, configuration: str, relative: bool
) -> None:
    tools = database_workspace
    database_path = tools.workspace / ("backend" if relative else "") / "persistent.sqlite"
    declared_path = database_path.name if relative else str(database_path)
    default_path = tools.workspace / "backend/database.db"
    original = default_path.read_bytes()
    environment = {
        key: value for key, value in tools.command_environment().items() if key not in {"ARC_DB_FILE", "DATABASE_FILE"}
    }
    if configuration in {"arc", "both"}:
        environment["ARC_DB_FILE"] = declared_path
    if configuration in {"database", "both"}:
        environment["DATABASE_FILE"] = str(default_path) if configuration == "both" else declared_path
    original_environment = environment.copy()
    for expected in (1, 2):
        with application_server(tools.workspace, environment) as (server_environment, ready, output):
            output.seek(0)
            assert ready, output.read().decode(errors="replace")
            assert server_environment["ARC_DB_FILE"] == str(database_path)
            result = tools.run_command("python check.py", 10, server_environment)
            assert result["returncode"] == 0, result
            assert json.loads(result["output"])["value"] == expected
        assert_server_closed(int(server_environment["PORT"]))
        assert environment == original_environment
        assert database_path.is_file()
        with sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True) as database:
            assert database.execute("SELECT value FROM counter").fetchone()[0] == expected
        assert default_path.read_bytes() == original
