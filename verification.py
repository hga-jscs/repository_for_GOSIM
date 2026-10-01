"""Rehearse the evaluator's build and startup contract on an unused port."""

import hashlib
import http.client
import json
import math
import os
import shlex
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from browser_check import browser_channel
from server import application_server
from tools import WorkspaceTools, bounded

INSTALLED_MANIFESTS: dict[Path, str] = {}


def remaining_timeout(deadline: float | None, maximum: int = 180) -> int:
    return maximum if deadline is None else math.ceil(max(0, min(maximum, deadline - time.monotonic())))


def incomplete_check(command: str) -> dict:
    return {
        "command": command,
        "returncode": 124,
        "timed_out": True,
        "output": "Verification deadline reached; remaining checks were not completed.",
    }


def run_check(tools: WorkspaceTools, command: str, deadline: float | None, environment: dict | None = None) -> dict:
    timeout = remaining_timeout(deadline)
    if not timeout:
        return incomplete_check(command)
    result = {"command": command, **tools.run_command(command, timeout, environment, deadline)}
    if result["timed_out"]:
        result["output"] += "\nVerification command timed out; checks were not completed."
    return result


def dependency_hash(folder: Path) -> str:
    return hashlib.sha256(
        b"".join(
            path.read_bytes() for path in (folder / "package.json", folder / "package-lock.json") if path.is_file()
        )
    ).hexdigest()


def python_command(arguments: list[str]) -> str:
    arguments = [sys.executable, *arguments]
    return (
        "& " + " ".join("'" + argument.replace("'", "''") + "'" for argument in arguments)
        if os.name == "nt"
        else shlex.join(arguments)
    )


def browser_check(tools: WorkspaceTools, deadline: float | None = None) -> dict:
    label = "real browser startup and uncaught frontend errors"
    command = python_command([str(Path(__file__).with_name("browser_check.py"))])
    if deadline is None:
        return {"command": label, **tools.run_with_server(command, 180)}
    if not remaining_timeout(deadline):
        return incomplete_check(label)
    with application_server(tools.workspace, tools.command_environment(), deadline) as (environment, ready, output):
        result = (
            run_check(tools, command, deadline, environment)
            if ready
            else incomplete_check(label)
            if not remaining_timeout(deadline)
            else {"returncode": 1, "timed_out": False, "output": "Application server did not start"}
        )
        output.seek(0)
        return result | {
            "command": label,
            "server_output": tools.redact(bounded(output.read().decode("utf-8", errors="replace"))),
            "port": int(environment["PORT"]),
        }


def public_workflow_check(
    tools: WorkspaceTools,
    target: str,
    requirement_ids: list[str] | None = None,
    deadline: float | None = None,
) -> dict:
    source = Path(__file__).resolve().parent
    evaluation = source / "evaluation"
    originals = {path: path.read_bytes() for path in evaluation.glob("*.py")}
    with tempfile.TemporaryDirectory(prefix="agent-public-checks-") as temporary:
        report = Path(temporary) / "results.xml"
        arguments = [
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
            "--maxfail=5",
            "-q",
        ]
        if requirement_ids:
            arguments.extend(["--implemented", ",".join(requirement_ids)])
        result = run_check(tools, python_command([*arguments, "--junitxml=" + str(report)]), deadline) | {
            "command": f"public {target} workflow checks",
            "test_directory": str(evaluation),
            "reproduction_command": python_command(arguments),
        }
        result["cases"] = []
        if report.is_file() and report.stat().st_size:
            try:
                cases = list(ElementTree.parse(report).iter("testcase"))
            except ElementTree.ParseError:
                cases = []
                result.update(returncode=result["returncode"] or 1)
                result["output"] += "\nThe workflow report is incomplete; verification did not finish."
            for case in cases:
                failure = case.find("failure")
                if failure is None:
                    failure = case.find("error")
                result["cases"].append(
                    {
                        "name": case.get("name"),
                        "requirements": next(
                            (
                                entry.get("value", "").split(",")
                                for entry in case.findall("properties/property")
                                if entry.get("name") == "requirements"
                            ),
                            [],
                        ),
                        "status": "failed"
                        if failure is not None
                        else "skipped"
                        if case.find("skipped") is not None
                        else "passed",
                        "failure": (failure.text or "")[-3000:] if failure is not None else "",
                    }
                )
        result["passed"] = sum(case["status"] == "passed" for case in result["cases"])
        result["failed"] = sum(case["status"] == "failed" for case in result["cases"])
    if not originals or any(not path.is_file() or path.read_bytes() != content for path, content in originals.items()):
        result.update(returncode=1, output="Independent evaluation files are absent or were modified")
    return result


def startup_check(tools: WorkspaceTools, deadline: float | None = None) -> dict:
    label = "backend npm start + HTTP GET / on an unused port"
    if not remaining_timeout(deadline):
        return incomplete_check(label)
    started = time.monotonic()
    with application_server(tools.workspace, tools.command_environment(), deadline) as (environment, ready, output):
        port = int(environment["PORT"])
        status = 0
        body = ""
        error = ""
        if ready and remaining_timeout(deadline):
            timeout = 10 if deadline is None else min(10, max(0.001, deadline - time.monotonic()))
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
            try:
                connection.request("GET", "/")
                response = connection.getresponse()
                status = response.status
                body = response.read(8192).decode("utf-8", errors="replace")
            except (http.client.HTTPException, OSError) as failure:
                error = f"{type(failure).__name__}: {failure}"
            finally:
                connection.close()
        output.seek(0)
        log = output.read().decode("utf-8", errors="replace")
    expired = deadline is not None and time.monotonic() >= deadline
    return {
        "command": label,
        "port": port,
        "returncode": 124 if expired else 0 if not error and 200 <= status < 300 and "<" in body else 1,
        "timed_out": expired or not ready,
        "seconds": round(time.monotonic() - started, 3),
        "output": tools.redact(
            bounded(log + f"\nHTTP {status}\n{body[:400]}\n{error}")
            + ("\nVerification deadline reached; startup verification did not finish." if expired else "")
        ),
    }


def verify_application(
    tools: WorkspaceTools,
    extra_commands: list[str],
    task_target: str | None = None,
    requirement_ids: list[str] | None = None,
    deadline: float | None = None,
) -> list[dict]:
    checks = []
    if not remaining_timeout(deadline):
        return [incomplete_check("validate evaluator layout")]
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
        try:
            manifest = json.loads(package.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            return [
                {
                    "command": "validate evaluator layout",
                    "returncode": 1,
                    "timed_out": False,
                    "output": f"Invalid {folder}/package.json: {error}",
                }
            ]
        scripts = manifest.get("scripts") if isinstance(manifest, dict) else None
        required = "build" if folder == "frontend" else "start"
        if not isinstance(scripts, dict) or not isinstance(scripts.get(required), str) or not scripts[required].strip():
            return [
                {
                    "command": "validate evaluator scripts",
                    "returncode": 1,
                    "timed_out": False,
                    "output": f"Missing or invalid {folder} script: {required}; expected a nonempty command string",
                }
            ]
    for name in ("frontend", "backend"):
        folder = tools.workspace / name
        if INSTALLED_MANIFESTS.get(folder) == dependency_hash(folder) and (folder / "node_modules").is_dir():
            continue
        command = f"npm --prefix {name} install --no-audit --no-fund"
        checks.append(run_check(tools, command, deadline))
        if checks[-1]["returncode"] != 0:
            return checks
        INSTALLED_MANIFESTS[folder] = dependency_hash(folder)
    commands = ["npm --prefix frontend run build"]
    for command in commands + extra_commands:
        check = run_check(tools, command, deadline)
        checks.append(check)
        if check["returncode"] != 0:
            return checks
    checks.append(startup_check(tools, deadline))
    if checks[-1]["returncode"] == 0:
        checks.append(browser_check(tools, deadline))
    if checks[-1]["returncode"] == 0 and task_target:
        checks.append(public_workflow_check(tools, task_target, requirement_ids, deadline))
    return checks
