import json
import os
import socket
import sys
import time
from pathlib import Path
from threading import Thread

import pytest
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent import CodingAgent, Usage, compact_messages, parse_arguments
from tools import WorkspaceTools


@pytest.mark.skipif(os.name != "nt", reason="Windows denies replacement while readers hold the destination")
def test_progress_retries_a_real_windows_reader_lock(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path / "application")
    logs = tmp_path / "logs"
    with OpenAI(api_key="unused-local-test") as client:
        agent = CodingAgent(client, "unused", tools, logs)
        agent.save_progress("batch", 0, Usage(), [], {"status": "running"}, time.monotonic())
        with (logs / "batch.json").open("rb") as reader:

            def release_reader():
                time.sleep(0.2)
                reader.close()

            release = Thread(target=release_reader)
            release.start()
            try:
                agent.save_progress("batch", 1, Usage(), [], {"status": "finished"}, time.monotonic())
            finally:
                release.join()
    assert json.loads((logs / "batch.json").read_text())["result"]["status"] == "finished"
    assert not (logs / "batch.tmp").exists()


def test_file_edits_reject_escape_ambiguity_and_secret(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path / "workspace", ("private-model-key",))
    assert "error" in tools.write_file("../outside.txt", "data")
    assert "error" in tools.write_file(".git/config", "data")
    assert "error" in tools.write_file("config.txt", "private-model-key")
    assert tools.write_file("source/app.txt", "value\nvalue\n") == {"written": "source/app.txt", "characters": 12}
    assert "error" in tools.replace_text("source/app.txt", "value", "other")
    assert tools.replace_text("source/app.txt", "value\nvalue\n", "done\n")["written"] == "source/app.txt"
    assert tools.read_files(["source/app.txt"])["source/app.txt"]["content"] == "1: done"
    assert not (tmp_path / "outside.txt").exists()


def test_command_returns_real_exit_status_and_redacts_output(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path, ("private-model-key",))
    tools.write_file("check.py", "import sys\nprint('private-' + 'model-key')\nsys.exit(7)\n")
    result = tools.run_command("python check.py", timeout_seconds=10)
    assert result["returncode"] == 7
    assert not result["timed_out"]
    assert "[REDACTED]" in result["output"] and "private-model-key" not in result["output"]


@pytest.mark.skipif(sys.platform != "linux", reason="Tests the Linux Bash pipeline executor")
@pytest.mark.parametrize(
    ("managed", "exit_status"),  # noqa: PT006 - AGENTS.md requires tuples.
    [(False, 7), (False, 0), (True, 7), (True, 0)],
)
def test_linux_pipeline_preserves_upstream_status_with_and_without_server(
    tmp_path: Path, managed: bool, exit_status: int
) -> None:
    tools = WorkspaceTools(tmp_path)
    script = "import sys\n"
    if managed:
        tools.write_file("backend/package.json", '{"scripts":{"start":"node server.js"}}')
        tools.write_file(
            "backend/server.js", "require('http').createServer((q,r)=>r.end('application')).listen(process.env.PORT);"
        )
        script += (
            "import os,urllib.request\nassert urllib.request.urlopen(os.environ['BASE_URL']).read() == b'application'\n"
        )
    tools.write_file("check.py", script + f"print('pipeline checked')\nsys.exit({exit_status})\n")
    execute = tools.run_with_server if managed else tools.run_command
    result = execute("python check.py | tail -n 1", timeout_seconds=10)
    assert result["returncode"] == exit_status
    assert not result["timed_out"] and result["output"].strip() == "pipeline checked"
    if managed:
        with socket.socket() as connection:
            connection.settimeout(1)
            assert connection.connect_ex(("127.0.0.1", result["port"])) != 0


def test_invalid_model_arguments_return_feedback_without_mutation(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("app.txt", "original")
    assert '"missing": ["old"]' in tools.dispatch("replace_text", {"path": "app.txt", "new": "changed"})
    assert "error" in tools.dispatch("write_file", {"path": "app.txt", "content": 123})
    assert "error" in tools.dispatch("read_files", {"paths": "app.txt"})
    assert (tmp_path / "app.txt").read_text() == "original"
    arguments, error = parse_arguments('{"path":"app.txt", broken: "data"}')
    assert arguments is None and "Resend valid JSON" in error
    arguments, error = parse_arguments('{"path":"app.txt","content":"repaired"}')
    assert error is None
    assert "written" in tools.dispatch("write_file", arguments)
    assert (tmp_path / "app.txt").read_text() == "repaired"


def test_timeout_and_background_children_are_cleaned_up(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("wait.py", "import time\ntime.sleep(15)\n")
    result = tools.run_command("python wait.py", timeout_seconds=1)
    assert result["timed_out"] and result["returncode"] != 0
    assert result["seconds"] < 5
    tools.write_file(
        "spawn.py",
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', "
        "\"import time; from pathlib import Path; time.sleep(1); Path('leaked.txt').write_text('leak')\"], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n",
    )
    assert tools.run_command("python spawn.py")["returncode"] == 0
    time.sleep(1.3)
    assert not (tmp_path / "leaked.txt").exists()


def test_compaction_preserves_requirements_actions_and_recent_observations() -> None:
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "full requirement"}]
    for index in range(10):
        messages.extend(
            [
                {"role": "assistant", "tool_calls": [{"id": str(index)}]},
                {"role": "tool", "tool_call_id": str(index), "content": str(index) * 2000},
            ]
        )
    compacted = compact_messages(messages, keep_rounds=2, character_limit=1)
    assert compacted[:2] == messages[:2]
    assert compacted[-4:] == messages[-4:]
    assert [m.get("tool_calls") for m in compacted] == [m.get("tool_calls") for m in messages]
    assert [m.get("tool_call_id") for m in compacted] == [m.get("tool_call_id") for m in messages]
    assert len(compacted[3]["content"]) < 500
    assert len(messages[3]["content"]) == 2000


def test_managed_server_is_reachable_during_test_and_closed_afterward(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("backend/package.json", '{"scripts":{"start":"node server.js"}}')
    tools.write_file(
        "backend/server.js", "require('http').createServer((q,r)=>r.end('application')).listen(process.env.PORT);"
    )
    tools.write_file(
        "check.py", "import os,urllib.request\nprint(urllib.request.urlopen(os.environ['BASE_URL']).read().decode())\n"
    )
    result = tools.run_with_server("python check.py", timeout_seconds=10)
    assert result["returncode"] == 0 and "application" in result["output"]
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(("127.0.0.1", result["port"])) != 0
