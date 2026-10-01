"""Small file tools and bounded process execution for an isolated build workspace."""

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from processes import ProcessGroup
from server import application_server


def function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TEXT = {"type": "string"}
TOOLS = [
    function(
        "read_files",
        "Read workspace text files. Large files should use line limits.",
        {
            "paths": {"type": "array", "items": TEXT},
            "first_line": {"type": "integer", "minimum": 1},
            "last_line": {"type": "integer", "minimum": 1},
        },
        ["paths"],
    ),
    function(
        "write_file",
        "Create or replace a complete UTF-8 workspace file.",
        {"path": TEXT, "content": TEXT},
        ["path", "content"],
    ),
    function(
        "replace_text",
        "Replace one exact unique text match; ambiguity leaves the file unchanged.",
        {"path": TEXT, "old": TEXT, "new": TEXT},
        ["path", "old", "new"],
    ),
    function(
        "run_command",
        "Run a command in the workspace. Use for search, builds and tests. Output is bounded. "
        "Bash pipelines preserve nonzero upstream exit codes.",
        {"command": TEXT, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 180}},
        ["command"],
    ),
    function(
        "run_with_server",
        "Run a test command while backend npm start serves the built app on an unused port. "
        "The test gets BASE_URL and PORT environment variables. Server and test processes are cleaned up. "
        "Unless the calling environment explicitly sets ARC_DB_FILE or DATABASE_FILE, each invocation shares "
        "a fresh temporary ARC_DB_FILE between backend and test and removes it after the server stops. "
        "Initialize seeds on app startup and create test objects in this script; do not depend on earlier debug data. "
        "Explicit database paths are preserved for same-database restarts. "
        "Install dependencies and build frontend first. Do not start another server in the test.",
        {"command": TEXT, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 180}},
        ["command"],
    ),
    function(
        "finish",
        "Finish this stage. State implemented behavior and actual checks, including failures.",
        {"summary": TEXT},
        ["summary"],
    ),
]


def bounded(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + f"\n[omitted {len(text) - limit} characters]\n" + text[-limit // 2 :]


class WorkspaceTools:
    def __init__(self, workspace: Path, secrets: tuple[str, ...] = ()):
        self.workspace = workspace.resolve()
        self.secrets = tuple(value for value in secrets if value)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def resolve(self, path: str) -> Path | None:
        resolved = (self.workspace / path).resolve()
        if not resolved.is_relative_to(self.workspace):
            return None
        if any(part in {".git", ".env", ".secrets"} for part in resolved.relative_to(self.workspace).parts):
            return None
        return resolved

    def read_files(self, paths: list[str], first_line: int = 1, last_line: int = 250) -> dict:
        if first_line < 1 or last_line < first_line or len(paths) > 12:
            return {"error": "Use 1-12 paths and a valid line range"}
        result = {}
        for name in paths:
            path = self.resolve(name)
            if path is None or not path.is_file():
                result[name] = "File is absent or outside the editable workspace"
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            result[name] = {
                "lines": len(lines),
                "content": bounded(
                    "\n".join(
                        f"{index + 1}: {line}"
                        for index, line in enumerate(lines)
                        if first_line <= index + 1 <= last_line
                    )
                ),
            }
        return result

    def write_file(self, path: str, content: str) -> dict:
        resolved = self.resolve(path)
        if resolved is None:
            return {"error": "Path is outside the editable workspace"}
        if any(secret in content for secret in self.secrets):
            return {"error": "Credential content must not be written to the workspace"}
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return {"written": path, "characters": len(content)}

    def replace_text(self, path: str, old: str, new: str) -> dict:
        resolved = self.resolve(path)
        if resolved is None or not resolved.is_file():
            return {"error": "File is absent or outside the editable workspace"}
        text = resolved.read_text(encoding="utf-8")
        if not old or text.count(old) != 1:
            return {"error": "old must match exactly once; read the relevant lines and retry"}
        return self.write_file(path, text.replace(old, new, 1))

    def command_environment(self) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE"))
            and not any(secret in value for secret in self.secrets)
        }
        environment.update({"CI": "1", "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"})
        return environment

    def run_command(
        self,
        command: str,
        timeout_seconds: int = 120,
        environment: dict | None = None,
        deadline: float | None = None,
    ) -> dict:
        if not 1 <= timeout_seconds <= 180:
            return {"error": "timeout_seconds must be between 1 and 180"}
        started = time.monotonic()
        deadline = min(started + timeout_seconds, deadline if deadline is not None else float("inf"))
        if started >= deadline:
            return {
                "returncode": 124,
                "timed_out": True,
                "seconds": 0,
                "output": "Deadline reached; command not executed",
            }
        arguments = (
            [
                shutil.which("pwsh") or "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference = 'Stop'\n"
                "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()\n"
                + command
                + "\nexit $LASTEXITCODE",
            ]
            if os.name == "nt"
            else ["/bin/bash", "-o", "pipefail", "-lc", command]
        )
        with tempfile.TemporaryFile() as output, ProcessGroup() as group:
            process = subprocess.Popen(
                arguments,
                cwd=self.workspace,
                env=self.command_environment() | (environment or {}),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            group.attach(process)
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(max(0, min(0.05, deadline - time.monotonic())))
            timed_out = process.poll() is None
            if timed_out:
                group.close()
                process.wait()
            group.close()
            output.seek(0)
            text = output.read().decode("utf-8", errors="replace")
        return {
            "returncode": 124 if timed_out else process.returncode,
            "timed_out": timed_out,
            "seconds": round(time.monotonic() - started, 3),
            "output": self.redact(bounded(text)),
        }

    def run_with_server(self, command: str, timeout_seconds: int = 120, deadline: float | None = None) -> dict:
        if not 1 <= timeout_seconds <= 180:
            return {"error": "timeout_seconds must be between 1 and 180"}
        deadline = min(time.monotonic() + timeout_seconds, deadline if deadline is not None else float("inf"))
        if time.monotonic() >= deadline:
            return {"returncode": 124, "timed_out": True, "output": "Deadline reached; command not executed"}
        if not (self.workspace / "backend/package.json").is_file():
            return {"error": "backend/package.json is required"}
        with application_server(self.workspace, self.command_environment(), deadline) as (environment, ready, output):
            result = (
                self.run_command(command, timeout_seconds, environment, deadline)
                if ready
                else {"returncode": 124, "timed_out": True, "output": "Deadline reached before application startup"}
                if time.monotonic() >= deadline
                else {"returncode": 1, "timed_out": False, "output": "Application server did not start"}
            )
            output.seek(0)
            return result | {
                "server_output": self.redact(bounded(output.read().decode("utf-8", errors="replace"))),
                "port": int(environment["PORT"]),
            }

    def dispatch(self, name: str, arguments: dict, deadline: float | None = None) -> str:
        if deadline is not None and time.monotonic() >= deadline:
            return json.dumps({"error": "Stage deadline reached; tool was not executed", "executed": False})
        functions = {
            key: getattr(self, key)
            for key in ("read_files", "write_file", "replace_text", "run_command", "run_with_server")
        }
        schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in TOOLS}
        if name not in functions or not isinstance(arguments, dict):
            return json.dumps({"error": "Unknown tool or invalid arguments object"})
        schema = schemas[name]
        missing = set(schema["required"]) - set(arguments)
        unknown = set(arguments) - set(schema["properties"])
        if missing or unknown:
            return json.dumps({"error": "Invalid arguments", "missing": sorted(missing), "unknown": sorted(unknown)})
        types = {"string": str, "integer": int, "array": list}
        for key, value in arguments.items():
            expected = schema["properties"][key]["type"]
            if type(value) is not types[expected] or (
                expected == "array" and any(not isinstance(item, str) for item in value)
            ):
                return json.dumps({"error": f"Argument {key} must have type {expected}"})
        if name in {"run_command", "run_with_server"}:
            arguments = arguments | {"deadline": deadline}
        result = functions[name](**arguments)
        return bounded(self.redact(json.dumps(result, ensure_ascii=False)), 18000)

    def index(self) -> str:
        ignored = {".git", "node_modules", "__pycache__", ".venv", ".arc", "dist", ".next", "build"}
        paths = []
        for folder, directories, files in os.walk(self.workspace):
            directories[:] = sorted(name for name in directories if name not in ignored)
            for name in sorted(files):
                path = Path(folder) / name
                if name.startswith(".env") or path.is_symlink():
                    continue
                paths.append(f"{path.relative_to(self.workspace).as_posix()} ({path.stat().st_size} bytes)")
                if len(paths) >= 180:
                    return "\n".join(paths) + "\n[more files omitted]"
        return "\n".join(paths) or "Empty workspace"
