import hashlib
import json
import os
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from arcbench_agent_runtime import AgentRuntime

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from package import package
from scaffold import prepare_workspace
from tools import WorkspaceTools
from verification import browser_check, startup_check, verify_application


def test_official_template_integrity_and_preserve_existing_project(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[2]
    manifest = json.loads((source / "template-files.json").read_text())
    assert all(hashlib.sha256((source / name).read_bytes()).hexdigest() == digest for name, digest in manifest.items())
    (tmp_path / ".arc").mkdir()
    (tmp_path / ".arc/runner-events.jsonl").write_text("existing runner event\n")
    (tmp_path / ".gitignore").write_text("existing ignore rules\n")
    prepare_workspace(tmp_path)
    assert (tmp_path / "frontend/package.json").is_file()
    assert (tmp_path / "backend/package.json").is_file()
    assert not (tmp_path / "template.yaml").exists()
    assert (tmp_path / ".arc/runner-events.jsonl").read_text() == "existing runner event\n"
    assert (tmp_path / ".gitignore").read_text() == "existing ignore rules\n"
    page = tmp_path / "frontend/src/App.tsx"
    page.write_text("previous stage implementation")
    prepare_workspace(tmp_path)
    assert page.read_text() == "previous stage implementation"


@pytest.fixture
def runner_output(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    workspace = tmp_path / "template1"
    existing = {
        "requirements/requirements.yaml": (
            b"id: ROOT\nname: Test\nchildren:\n  - id: R1\n    name: Page\n    type: ATOMIC\n"
        ),
        "requirements/prerequisites.md": "Preserve runner prerequisites.\n保留原内容。\n".encode(),
        "requirements/reference/screen.png": b"\x89PNG\r\n\x1a\nrunner reference bytes",
        "runner-metadata.json": b'{"initialized": true}\n',
    }
    for name, content in existing.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return workspace, existing


def test_runner_requirements_and_metadata_survive_repeat_initialization(
    runner_output: tuple[Path, dict[str, bytes]],
) -> None:
    workspace, existing = runner_output
    prepare_workspace(workspace)
    assert all((workspace / part / "package.json").is_file() for part in ("frontend", "backend"))
    assert all((workspace / name).read_bytes() == content for name, content in existing.items())
    files = {path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    prepare_workspace(workspace)
    assert {path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()} == files


def test_incomplete_output_is_preserved_and_filled(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text("keep")
    (tmp_path / "frontend/src").mkdir(parents=True)
    (tmp_path / "frontend/src/App.tsx").write_text("existing frontend source")
    prepare_workspace(tmp_path)
    assert (tmp_path / "app.js").read_text() == "keep"
    assert (tmp_path / "frontend/src/App.tsx").read_text() == "existing frontend source"
    assert all((tmp_path / part / "package.json").is_file() for part in ("frontend", "backend"))
    assert (tmp_path / "frontend/src/main.tsx").is_file()


def test_existing_component_is_not_mixed_with_starter(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"scripts":{"build":"custom-build"}}')
    (frontend / "custom-entry.js").write_text("existing component")
    before = {path.name: path.read_bytes() for path in frontend.iterdir()}
    prepare_workspace(tmp_path)
    assert {path.name: path.read_bytes() for path in frontend.iterdir()} == before
    assert (tmp_path / "backend/package.json").is_file()
    assert (tmp_path / "backend/src/index.js").is_file()


def test_prepare_workspace_creates_missing_output(tmp_path: Path) -> None:
    workspace = tmp_path / "new/output"
    prepare_workspace(workspace)
    assert all((workspace / part / "package.json").is_file() for part in ("frontend", "backend"))


@pytest.mark.parametrize(
    ("name", "directory"),  # noqa: PT006 - AGENTS.md requires tuples for parameter names.
    [("frontend", False), ("frontend/src", False), ("frontend/package.json", True)],
)
def test_scaffold_conflicts_leave_output_unchanged(tmp_path: Path, name: str, directory: bool) -> None:
    conflict = tmp_path / name
    conflict.parent.mkdir(parents=True, exist_ok=True)
    if directory:
        conflict.mkdir()
    else:
        conflict.write_text("preserve conflicting file")
    (tmp_path / "runner-metadata.json").write_text('{"initialized":true}')
    before = {path.relative_to(tmp_path): path.read_bytes() if path.is_file() else None for path in tmp_path.rglob("*")}
    with pytest.raises(ValueError):
        prepare_workspace(tmp_path)
    assert {
        path.relative_to(tmp_path): path.read_bytes() if path.is_file() else None for path in tmp_path.rglob("*")
    } == before


def test_extracted_archive_starts_with_runner_files_before_model_connection(
    tmp_path: Path, runner_output: tuple[Path, dict[str, bytes]]
) -> None:
    workspace, existing = runner_output
    existing["requirements/requirements.yaml"] = (
        b"id: ROOT\nname: Test\nchildren:\n"
        b"  - id: FIRST\n    name: First module\n    children:\n"
        b"      - id: R1\n        name: First page\n        type: ATOMIC\n"
        b"  - id: SECOND\n    name: Second module\n    children:\n"
        b"      - id: R2\n        name: Second page\n        type: ATOMIC\n"
    )
    (workspace / "requirements/requirements.yaml").write_bytes(existing["requirements/requirements.yaml"])
    package(tmp_path / "submission.zip")
    with zipfile.ZipFile(tmp_path / "submission.zip") as archive:
        archive.extractall(tmp_path / "agent")
    source = tmp_path / "requirements-source"
    source.mkdir()
    (source / "requirements.yaml").write_bytes(existing["requirements/requirements.yaml"])
    (workspace / "frontend").mkdir()
    (workspace / "frontend/package.json").write_text("{}", encoding="utf-8")
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("ARCBENCH_", "ARC_BENCH_", "OPENAI_")) and "PROXY" not in name.upper()
    }
    with socket.socket() as endpoint:
        endpoint.bind(("127.0.0.1", 0))
        result = subprocess.run(
            [
                sys.executable,
                str(tmp_path / "agent/main.py"),
                str(source),
                "--output-dir",
                str(workspace),
                "--type",
                "web",
                "--max-steps",
                "1",
                "--time-limit",
                "60",
            ],
            cwd=tmp_path / "agent",
            env=environment
            | {
                "OPENAI_API_KEY": "local-startup-test-key",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{endpoint.getsockname()[1]}/v1",
                "MODEL": "startup-test",
                "NO_PROXY": "*",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    assert result.returncode == 1 and "Traceback" not in result.stderr, result.stdout + result.stderr
    assert "APIConnectionError" in result.stdout
    assert "local-startup-test-key" not in result.stdout + result.stderr
    assert all((workspace / part / "package.json").is_file() for part in ("frontend", "backend"))
    assert all((workspace / name).read_bytes() == content for name, content in existing.items())
    assert (source / "requirements.yaml").read_bytes() == existing["requirements/requirements.yaml"]
    assert (workspace / ".git/HEAD").is_file()
    assert (workspace / ".arc/traceability").is_dir()
    events = [json.loads(line) for line in (workspace / ".arc/runner-events.jsonl").read_text().splitlines()]
    assert any(event.get("type") == "runner_state" and event.get("state") == "running" for event in events)
    assert any(event.get("node_id") == "R1" and event.get("status") == "running" for event in events)
    assert all(event.get("status") not in {"completed", "passed"} for event in events)
    summary = json.loads(next((tmp_path / "template1-runs").glob("*/summary.json")).read_text(encoding="utf-8"))
    assert summary["model_requests_stopped"] and not summary["token_accounting_complete"]
    assert len(summary["results"]) == 1 and summary["results"][0]["status"] == "api_unavailable"
    assert summary["usage"]["request_attempts"] == summary["usage"]["failed_requests"] == 5
    assert summary["usage"]["calls"] == 0 and summary["usage"]["input_tokens"] == 0
    assert summary["verification"][0]["command"] == "validate evaluator scripts"
    assert not summary["delivery_ready"] and not summary["all_batches_finished"]


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
    environment_result = subprocess.run(
        [sys.executable, str(tmp_path / "agent/main.py"), "--plan-only"],
        cwd=tmp_path / "agent",
        env=os.environ
        | {
            "ARCBENCH_TASK_DIR": str(source.parent),
            "ARCBENCH_OUTPUT_DIR": str(tmp_path / "environment-output"),
            "ARCBENCH_TASK_TYPE": "web",
            "ARCBENCH_WEB_PORT": "39998",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
    )
    assert environment_result.returncode == 0, environment_result.stderr
    assert json.loads(environment_result.stdout) == json.loads(result.stdout)
    assert not (tmp_path / "environment-output").exists()
    evaluation = tmp_path / "agent/evaluation"
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(evaluation),
            "--confcutdir=" + str(evaluation),
            "--application",
            str(tmp_path / "output"),
            "--target",
            "github",
            "--full",
            "--collect-only",
            "-p",
            "no:cacheprovider",
            "-q",
        ],
        cwd=tmp_path / "agent",
        capture_output=True,
        text=True,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert "20 tests collected" in collected.stdout


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


@pytest.mark.parametrize(
    ("folder", "manifest"),  # noqa: PT006 - AGENTS.md requires tuples for parameter names.
    [
        ("frontend", '{"scripts":{"build":"vite build"},}'),
        ("frontend", "null"),
        ("frontend", "[]"),
        ("frontend", '{"scripts":null}'),
        ("backend", '{"scripts":[]}'),
        ("backend", '{"scripts":{"start":null}}'),
        ("backend", '{"scripts":{"start":true}}'),
        ("backend", '{"scripts":{"start":"  "}}'),
    ],
)
def test_invalid_generated_manifests_return_repair_feedback(tmp_path: Path, folder: str, manifest: str) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("frontend/package.json", '{"scripts":{"build":"must-not-run"}}')
    tools.write_file("backend/package.json", '{"scripts":{"start":"must-not-run"}}')
    tools.write_file(f"{folder}/package.json", manifest)
    result = verify_application(tools, [])
    assert len(result) == 1 and result[0]["returncode"] == 1
    assert folder in result[0]["output"]
    assert result[0]["command"].startswith("validate evaluator")
    assert (tmp_path / folder / "package.json").read_text(encoding="utf-8") == manifest
    assert not list(tmp_path.glob("*/package-lock.json"))


def test_startup_disconnect_returns_repair_feedback_and_releases_port(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("backend/package.json", '{"scripts":{"start":"node server.js"}}')
    tools.write_file(
        "backend/server.js",
        "require('http').createServer((req,res)=>req.socket.destroy()).listen(process.env.PORT);",
    )
    result = startup_check(tools)
    assert result["returncode"] == 1 and not result["timed_out"]
    assert "RemoteDisconnected" in result["output"]
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(("127.0.0.1", result["port"])) != 0


@pytest.mark.parametrize(("script", "expected"), [("", 0), ("throw new Error('broken context')", 1)])  # noqa: PT006
def test_real_browser_detects_frontend_runtime_errors(tmp_path: Path, script: str, expected: int) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("backend/package.json", '{"scripts":{"start":"node server.js"}}')
    page = "<html><body><h1>Application</h1><script>" + script + "</script></body></html>"
    tools.write_file(
        "backend/server.js",
        "require('http').createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end("
        + json.dumps(page)
        + ");}).listen(Number(process.env.PORT),'0.0.0.0');",
    )
    result = browser_check(tools)
    assert result["returncode"] == expected, result
    assert "Application" in result["output"]
    if script:
        assert "broken context" in result["output"]
