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
import uuid
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


def browser_check(tools: WorkspaceTools, deadline: float | None = None, *, target: str | None = None) -> dict:
    label = "real browser startup and uncaught frontend errors"
    command = python_command([str(Path(__file__).with_name("browser_check.py")), *([target] if target else [])])
    deadline = deadline if deadline is not None else time.monotonic() + 180
    if not remaining_timeout(deadline):
        return incomplete_check(label)
    with application_server(tools.workspace, tools.command_environment(), deadline, copy_existing_database=True) as (environment, ready, output):
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


def read_workflow_progress(path: Path, run_id: str) -> tuple[list[dict], str]:
    if not path.is_file():
        return [], "No completed test progress report is available."
    try:
        progress = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [], f"The partial progress report is unreadable: {type(error).__name__}."
    if (
        not isinstance(progress, dict)
        or progress.get("schema_version") != 1
        or progress.get("run_id") != run_id
        or not isinstance(progress.get("cases"), list)
    ):
        return [], "The partial progress report has an invalid schema or stale invocation identifier."
    seen = set()
    for case in progress["cases"]:
        if (
            not isinstance(case, dict)
            or any(not isinstance(case.get(key), str) or not case[key] for key in ("nodeid", "name", "file"))
            or not isinstance(case.get("failure"), str)
            or not isinstance(case.get("requirements"), list)
            or any(not isinstance(identifier, str) for identifier in case["requirements"])
            or not isinstance(case.get("scenarios", []), list)
            or any(not isinstance(identifier, str) for identifier in case.get("scenarios", []))
            or not isinstance(case.get("phases"), dict)
        ):
            return [], "The partial progress report contains a malformed case."
        phases = case["phases"]
        expected = (
            "failed"
            if "failed" in phases.values()
            else "skipped"
            if "skipped" in phases.values()
            else "passed"
            if phases.get("call") == "passed"
            else None
        )
        if (
            not {"setup", "teardown"} <= phases.keys() <= {"setup", "call", "teardown"}
            or any(
                not isinstance(value, str) or value not in {"passed", "failed", "skipped"} for value in phases.values()
            )
            or ("call" in phases and phases["setup"] != "passed")
            or expected is None
            or case.get("status") != expected
            or case["nodeid"] in seen
        ):
            return [], "The partial progress report contains an incomplete or inconsistent case."
        seen.add(case["nodeid"])
    return [case | {"source": "partial_progress"} for case in progress["cases"]], (
        f"Recovered {len(progress['cases'])} teardown-complete cases from partial progress; "
        "the full workflow verification is incomplete."
    )


def junit_case_identity(case: ElementTree.Element, scope: Path) -> dict:
    parts = case.get("classname", "").split(".")
    directory = scope.parent if scope.is_file() else scope
    matches = []
    for start in range(len(parts)):
        for end in range(start + 1, len(parts) + 1):
            candidate = directory.joinpath(*parts[start:end]).with_suffix(".py").resolve()
            if (
                candidate.is_relative_to(directory.resolve())
                and candidate.is_file()
                and (not scope.is_file() or candidate == scope.resolve())
            ):
                matches.append((candidate, parts[end:]))
    if len(matches) != 1:
        return {"file": "", "nodeid": "", "classname": case.get("classname", "")}
    path, classes = matches[0]
    return {"file": str(path), "nodeid": "::".join([str(path), *classes, case.get("name", "")])}


def public_workflow_check(
    tools: WorkspaceTools,
    target: str,
    requirement_ids: list[str] | None = None,
    deadline: float | None = None,
    *,
    max_failures: int | None = 5,
    requirement_version: str = "baseline",
    requirement_path: Path | None = None,
    test_files: list[str] | None = None,
) -> dict:
    source = Path(__file__).resolve().parent
    evaluation = source / "evaluation"
    originals = {path: path.read_bytes() for path in evaluation.glob("*.py")}
    arguments = [
        "-m",
        "pytest",
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
    failure_limit = max_failures if requirement_ids or test_files is not None else None
    if failure_limit is not None and failure_limit < 1:
        raise ValueError("max_failures must be positive or None")
    if requirement_ids:
        arguments.extend(["--implemented", ",".join(requirement_ids)])
    if failure_limit is not None:
        arguments.append(f"--maxfail={failure_limit}")
    if requirement_version == "evolution":
        arguments.extend(["--requirement-version", "evolution", "--requirements-file", str(requirement_path)])
    paths = (
        [evaluation]
        if requirement_ids and max_failures is not None
        else [evaluation / "test_browser.py", *sorted(evaluation.glob(f"test_{target}_*.py"))]
    )
    if requirement_version == "evolution":
        paths = sorted(evaluation.glob(f"test_evolution_{target}*.py"))
    if test_files is not None:
        paths = list(dict.fromkeys((evaluation / name).resolve() for name in test_files))
        if any(not path.is_relative_to(evaluation) or not path.is_file() or not path.name.startswith("test_") or path.suffix != ".py" for path in paths):
            raise ValueError("test_files must name existing public test_*.py files inside evaluation")
    result = {
        "command": f"public {target} workflow checks",
        "requirement_version": requirement_version,
        "requirements_sha256": hashlib.sha256(requirement_path.read_bytes()).hexdigest() if requirement_path else None,
        "verification_scope": "all" if not requirement_ids else "final_stage" if max_failures is None else "batch",
        "requirement_ids": requirement_ids,
        "test_directory": str(evaluation),
        "reproduction_command": python_command([*arguments, *map(str, paths)]),
        "returncode": 0,
        "timed_out": False,
        "completed": True,
        "cases": [],
        "parts": [],
        "output": "",
    }
    with tempfile.TemporaryDirectory(prefix="agent-public-checks-") as temporary:
        for number, path in enumerate(paths):
            failure_count = sum(case["status"] == "failed" for case in result["cases"])
            expired = not remaining_timeout(deadline)
            limited = failure_limit is not None and failure_count >= failure_limit
            if expired or limited:
                result.update(completed=False, returncode=result["returncode"] or (124 if expired else 1))
                result["timed_out"] = result["timed_out"] or expired
                reason = "Verification deadline reached" if expired else "Verification failure limit reached"
                result["output"] += f"\n{reason}; verification was not completed and remaining test files were not executed."
                for pending in paths[number:]:
                    result["parts"].append({"test_file": str(pending), "returncode": 124 if expired else 1,
                                            "timed_out": expired, "executed": False, "report_source": "unavailable",
                                            "reproduction_command": python_command([*arguments, str(pending)])})
                break
            part_arguments = [argument for argument in arguments if not argument.startswith("--maxfail=")]
            if failure_limit is not None:
                part_arguments.append(f"--maxfail={failure_limit - failure_count}")
            report = Path(temporary) / f"results-{number}.xml"
            progress = Path(temporary) / f"progress-{number}.json"
            run_id = uuid.uuid4().hex
            part = run_check(
                tools,
                python_command(
                    [
                        *part_arguments,
                        str(path),
                        "--junitxml=" + str(report),
                        "--progress-json=" + str(progress),
                        "--progress-id=" + run_id,
                    ]
                ),
                deadline,
            )
            result["parts"].append(
                {key: value for key, value in part.items() if key != "output"}
                | {"test_file": str(path), "executed": True, "reproduction_command": python_command([*part_arguments, str(path)])}
            )
            result["returncode"] = result["returncode"] or part["returncode"]
            result["timed_out"] = result["timed_out"] or part["timed_out"]
            result["output"] += f"\n{path.name}:\n{part['output']}"
            cases = []
            junit_available = False
            if report.is_file() and report.stat().st_size:
                try:
                    cases = list(ElementTree.parse(report).iter("testcase"))
                    junit_available = True
                except ElementTree.ParseError:
                    result["output"] += "\nThe workflow report is incomplete; verification did not finish."
            if not cases or part["timed_out"] or part["returncode"] not in {0, 1}:
                result["completed"] = False
                result["returncode"] = result["returncode"] or 1
            for case in cases:
                failure = case.find("failure")
                if failure is None:
                    failure = case.find("error")
                result["cases"].append(
                    {
                        "name": case.get("name"),
                        **junit_case_identity(case, path),
                        "requirements": next(
                            (
                                entry.get("value", "").split(",")
                                for entry in case.findall("properties/property")
                                if entry.get("name") == "requirements"
                            ),
                            [],
                        ),
                        "scenarios": next((entry.get("value", "").split(",") for entry in case.findall("properties/property") if entry.get("name") == "scenarios"), []),
                        "status": "failed"
                        if failure is not None
                        else "skipped"
                        if case.find("skipped") is not None
                        else "passed",
                        "failure": (failure.text or "")[-3000:] if failure is not None else "",
                        "source": "junit",
                    }
                )
            result["parts"][-1]["report_source"] = "junit" if junit_available else "unavailable"
            if not junit_available:
                partial, message = read_workflow_progress(progress, run_id)
                result["cases"].extend(partial)
                result["output"] += "\n" + message
                if partial:
                    result["parts"][-1]["report_source"] = "partial_progress"
            if failure_limit is not None and sum(case["status"] == "failed" for case in result["cases"]) >= failure_limit:
                result["completed"] = False
    result["passed"] = sum(case["status"] == "passed" for case in result["cases"])
    result["failed"] = sum(case["status"] == "failed" for case in result["cases"])
    result["skipped"] = sum(case["status"] == "skipped" for case in result["cases"])
    result["reproduction_command"] = next(
        (part["reproduction_command"] for part in result["parts"] if part["returncode"]),
        result["reproduction_command"],
    )
    if not result["passed"] + result["failed"]:
        result.update(returncode=result["returncode"] or 1, completed=False)
        result["output"] += "\nNo business test executed; skipped tests do not establish readiness."
    if not originals or any(not path.is_file() or path.read_bytes() != content for path, content in originals.items()):
        result.update(returncode=1, output="Independent evaluation files are absent or were modified")
    return result


def startup_check(tools: WorkspaceTools, deadline: float | None = None) -> dict:
    label = "backend npm start + HTTP GET / on an unused port"
    if not remaining_timeout(deadline):
        return incomplete_check(label)
    started = time.monotonic()
    startup_state = {}
    with application_server(tools.workspace, tools.command_environment(), deadline,
                            copy_existing_database=True, startup_state=startup_state) as (environment, ready, output):
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
        "timed_out": expired or startup_state.get("timed_out", False),
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
    *,
    max_failures: int | None = 5,
    requirement_version: str = "baseline",
    requirement_path: Path | None = None,
    public_test_files: list[str] | None = None,
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
    checks.append(run_check(tools, "npm --prefix frontend run build", deadline))
    if checks[-1]["returncode"] != 0:
        return checks
    checks.append(startup_check(tools, deadline))
    if checks[-1]["returncode"] == 0:
        checks.append(browser_check(tools, deadline, target=task_target))
    if checks[-1]["returncode"] == 0 and task_target:
        checks.append(public_workflow_check(tools, task_target, requirement_ids, deadline, max_failures=max_failures, requirement_version=requirement_version, requirement_path=requirement_path, test_files=public_test_files))
    for command in extra_commands:
        checks.append(run_check(tools, command, deadline))
    return checks
