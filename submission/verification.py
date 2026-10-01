"""Rehearse the evaluator's build and startup contract on an unused port."""

import http.client
import json
import time

from server import application_server
from tools import WorkspaceTools, bounded


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


def verify_application(tools: WorkspaceTools, extra_commands: list[str]) -> list[dict]:
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
    return checks
