import hashlib
import json
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from arcbench_agent_runtime import AgentRuntime

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "submission"))
from package import package
from scaffold import prepare_workspace
from tools import WorkspaceTools
from verification import startup_check


def test_official_template_integrity_and_preserve_existing_project(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[2] / "submission"
    manifest = json.loads((source / "template-files.json").read_text())
    assert all(hashlib.sha256((source / name).read_bytes()).hexdigest() == digest for name, digest in manifest.items())
    prepare_workspace(tmp_path)
    assert (tmp_path / "frontend/package.json").is_file()
    assert (tmp_path / "backend/package.json").is_file()
    assert not (tmp_path / "template.yaml").exists()
    page = tmp_path / "frontend/src/App.tsx"
    page.write_text("previous stage implementation")
    prepare_workspace(tmp_path)
    assert page.read_text() == "previous stage implementation"


def test_incomplete_output_is_not_overwritten(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        prepare_workspace(tmp_path)
    assert list(tmp_path.iterdir()) == [tmp_path / "app.js"]


def test_archive_is_deterministic_and_entrypoint_accepts_official_arguments(tmp_path: Path) -> None:
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    assert package(first)["sha256"] == package(second)["sha256"]
    with zipfile.ZipFile(first) as archive:
        assert "main.py" in archive.namelist()
        assert "template/frontend/package.json" in archive.namelist()
        assert all("__pycache__" not in name and ".egg-info" not in name for name in archive.namelist())
        archive.extractall(tmp_path / "agent")
    source = tmp_path / "requirements.yaml"
    source.write_text("id: ROOT\nname: Test\nchildren:\n  - id: R1\n    name: Page\n    type: ATOMIC\n")
    result = subprocess.run(
        [
            sys.executable,
            str(tmp_path / "agent/main.py"),
            str(source),
            "--output-dir",
            str(tmp_path / "output"),
            "--type",
            "web",
            "--web-port",
            "39999",
            "--plan-only",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["requirements"] == 1
    assert not (tmp_path / "output").exists()


def test_official_runtime_records_implementation_without_claiming_test_pass(tmp_path: Path) -> None:
    runtime = AgentRuntime.from_env(
        project_dir=str(tmp_path),
        runner_events_path=str(tmp_path / ".arc/events.jsonl"),
        traceability_dir=str(tmp_path / ".arc/traceability"),
    )
    runtime.git.ensure_repo()
    runtime.events.mark_run_started()
    runtime.events.mark_implementation_started("REQ-1")
    runtime.events.mark_implementation_done("REQ-1", "Implementation finished; official tests pending")
    (tmp_path / "application.txt").write_text("application")
    assert runtime.git.commit("Implement application")
    assert runtime.git.status_porcelain() == ""
    events = [json.loads(line) for line in (tmp_path / ".arc/events.jsonl").read_text().splitlines()]
    assert any(event.get("phase") == "implement" and event.get("status") == "completed" for event in events)
    assert all(event.get("phase") != "test" for event in events)


@pytest.mark.parametrize(
    ("status", "expected"),  # noqa: PT006 - AGENTS.md requires tuples for parameter names.
    [(200, 0), (503, 1)],
)
def test_real_startup_probe_checks_http_and_releases_port(tmp_path: Path, status: int, expected: int) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("backend/package.json", '{"scripts":{"start":"node server.js"}}')
    tools.write_file(
        "backend/server.js",
        "require('http').createServer((req,res)=>{res.writeHead("
        + str(status)
        + ");res.end('<html>hello</html>')}).listen(Number(process.env.PORT),'0.0.0.0');",
    )
    result = startup_check(tools)
    assert result["returncode"] == expected
    assert f"HTTP {status}" in result["output"]
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(("127.0.0.1", result["port"])) != 0
