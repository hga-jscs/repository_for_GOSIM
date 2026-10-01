"""Rehearse the evaluator's build and startup contract on an unused port."""

import http.client
import json
import os
import shlex
import sys
import time
from pathlib import Path

from browser_check import browser_channel
from server import application_server
from tools import WorkspaceTools, bounded


def python_command(arguments: list[str]) -> str:
    arguments = [sys.executable, *arguments]
    return (
        "& " + " ".join("'" + argument.replace("'", "''") + "'" for argument in arguments)
        if os.name == "nt"
        else shlex.join(arguments)
    )


def browser_check(tools: WorkspaceTools) -> dict:
    command = python_command([str(Path(__file__).with_name("browser_check.py"))])
    return {"command": "real browser startup and uncaught frontend errors", **tools.run_with_server(command, 180)}


def public_workflow_check(tools: WorkspaceTools, target: str) -> dict:
    source = Path(__file__).resolve().parent
    evaluation = source / "evaluation" if (source / "evaluation").is_dir() else source.parent / "evaluation"
    originals = {path: path.read_bytes() for path in evaluation.glob("*.py")}
    command = python_command(
        [
            "-m",
            "pytest",
            str(evaluation),
            "--confcutdir=" + str(evaluation),
            "--application",
            str(tools.workspace),
            "--target",
            target,
            "--full",
            "--browser-channel",
            browser_channel(),
            "-p",
            "no:cacheprovider",
            "--tb=short",
            "-q",
        ]
    )
    result = {"command": f"public {target} workflow checks", **tools.run_command(command, 180)}
    if not originals or any(not path.is_file() or path.read_bytes() != content for path, content in originals.items()):
        result.update(returncode=1, output="Independent evaluation files are absent or were modified")
    return result


def startup_check(tools: WorkspaceTools) -> dict:
    started = time.monotonic()
    with application_server(tools.workspace, tools.command_environment()) as (environment, ready, output):
        port = int(environment["PORT"])
        status = 0
        body = ""
        if ready:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                connection.request("GET", "/")
                response = connection.getresponse()
                status = response.status
                body = response.read(8192).decode("utf-8", errors="replace")
            finally:
                connection.close()
        output.seek(0)
        log = output.read().decode("utf-8", errors="replace")
    return {
        "command": "backend npm start + HTTP GET / on an unused port",
        "port": port,
        "returncode": 0 if 200 <= status < 300 and "<" in body else 1,
        "timed_out": not ready,
        "seconds": round(time.monotonic() - started, 3),
        "output": tools.redact(bounded(log + f"\nHTTP {status}\n{body[:400]}")),
    }


def verify_application(tools: WorkspaceTools, extra_commands: list[str], task_target: str | None = None) -> list[dict]:
    checks = []
    for folder in ("frontend", "backend"):
        package = tools.workspace / folder / "package.json"
        if not package.is_file():
            return [
                {
                    "command": "validate evaluator layout",
                    "returncode": 1,
                    "timed_out": False,
                    "output": f"Missing {folder}/package.json",
                }
            ]
        scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        required = "build" if folder == "frontend" else "start"
        if required not in scripts:
            return [
                {
                    "command": "validate evaluator scripts",
                    "returncode": 1,
                    "timed_out": False,
                    "output": f"Missing {folder} script: {required}",
                }
            ]
    commands = [
        "npm --prefix frontend install --no-audit --no-fund",
        "npm --prefix backend install --no-audit --no-fund",
        "npm --prefix frontend run build",
    ]
    for command in commands + extra_commands:
        check = {"command": command, **tools.run_command(command, timeout_seconds=180)}
        checks.append(check)
        if check["returncode"] != 0:
            return checks
    checks.append(startup_check(tools))
    if checks[-1]["returncode"] == 0:
        checks.append(browser_check(tools))
    if checks[-1]["returncode"] == 0 and task_target:
        checks.append(public_workflow_check(tools, task_target))
    return checks
