import json
import socket
import sys
import time
from pathlib import Path

import pytest
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageFunctionToolCall

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent import CodingAgent, Usage
from main import batch_seconds
from requirements import Requirement
from tools import WorkspaceTools
from verification import (
    browser_check,
    public_workflow_check,
    python_command,
    run_check,
    startup_check,
    verify_application,
)


@pytest.mark.parametrize(("budget",), [(0.0,), (1.0,), (7200.0,)])  # noqa: PT006
def test_weighted_batch_allocations_reserve_time_for_later_short_requirements(budget: float) -> None:
    batches = [
        [Requirement(str(index), "Feature", "x" * length, (), (), ())]
        for index, length in enumerate([60000, 0, 100, 1000, 20000, 0, 300])
    ]
    remaining = budget
    allocations = []
    for index in range(len(batches)):
        allocation = batch_seconds(remaining, batches[index:])
        assert 0 <= allocation <= remaining
        allocations.append(allocation)
        remaining -= allocation
    assert remaining == pytest.approx(0)
    assert sum(allocations) == pytest.approx(budget)
    if budget:
        assert all(allocation > 0 for allocation in allocations)
        assert allocations[0] > allocations[1]
        assert allocations[1] == pytest.approx(allocations[5])
        assert allocations[0] < budget * 0.75
    assert batch_seconds(-1, batches) == 0


def test_expired_verification_deadline_does_not_start_commands_or_create_files(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    deadline = time.monotonic() - 1
    results = [
        *verify_application(tools, ["must-not-run"], deadline=deadline),
        startup_check(tools, deadline),
        browser_check(tools, deadline),
        public_workflow_check(tools, "sheet", deadline=deadline),
    ]
    assert all(result["returncode"] == 124 and result["timed_out"] for result in results)
    assert all("not completed" in result["output"] for result in results)
    assert list(tmp_path.iterdir()) == []


def test_public_check_reproduction_command_collects_tests_from_workspace_with_spaces(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path / "application with spaces")
    result = public_workflow_check(tools, "github", ["REQ-1-1-1", "REQ-1-1-2"], deadline=time.monotonic() - 1)
    assert result["returncode"] == 124 and result["timed_out"]
    assert result["command"] == "public github workflow checks"
    assert Path(result["test_directory"]).is_absolute()
    command = result["reproduction_command"]
    assert sys.executable in command and result["test_directory"] in command
    assert str(tools.workspace) in command
    assert all(
        argument in command
        for argument in ["--application", "--target", "github", "--full", "--implemented", "REQ-1-1-1,REQ-1-1-2"]
    )
    assert "--junitxml" not in command
    collected = tools.run_command(command + " --collect-only", 30)
    assert collected["returncode"] == 0 and not collected["timed_out"], collected["output"]
    assert "test_github_registration_controls" in collected["output"]
    assert "tests collected" in collected["output"]
    assert list(tools.workspace.iterdir()) == []


@pytest.mark.parametrize(("check",), [(startup_check,), (browser_check,)])  # noqa: PT006
def test_verification_startup_deadline_cleans_up_delayed_server(tmp_path: Path, check) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("backend/package.json", '{"scripts":{"start":"node server.js"}}')
    tools.write_file(
        "backend/server.js",
        "setTimeout(()=>{require('fs').writeFileSync('leaked.txt','leaked');"
        "require('http').createServer((q,r)=>r.end('<html>started</html>')).listen(process.env.PORT)},2000);",
    )
    started = time.monotonic()
    result = check(tools, started + 0.6)
    assert result["returncode"] == 124 and result["timed_out"]
    assert time.monotonic() - started < 3
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(("127.0.0.1", result["port"])) != 0
    time.sleep(2)
    assert not (tmp_path / "backend/leaked.txt").exists()


def test_command_uses_remaining_verification_time_and_cleans_up_on_timeout(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("delayed.py", "import time\nfrom pathlib import Path\ntime.sleep(2)\nPath('leaked.txt').touch()\n")
    started = time.monotonic()
    result = run_check(tools, python_command([str(tmp_path / "delayed.py")]), started + 0.3)
    assert result["returncode"] == 124 and result["timed_out"]
    assert "not completed" in result["output"]
    assert time.monotonic() - started < 3
    time.sleep(1.5)
    assert not (tmp_path / "leaked.txt").exists()


def test_full_verification_stops_after_dependency_install_exhausts_budget(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("frontend/package.json", '{"scripts":{"preinstall":"node wait.js","build":"node build.js"}}')
    tools.write_file("frontend/wait.js", "setTimeout(()=>{},10000);")
    tools.write_file("backend/package.json", '{"scripts":{"start":"node server.js"}}')
    started = time.monotonic()
    checks = verify_application(tools, [], deadline=started + 0.3)
    assert time.monotonic() - started < 3
    assert len(checks) == 1 and checks[0]["command"].startswith("npm --prefix frontend install")
    assert checks[0]["returncode"] == 124 and checks[0]["timed_out"]
    assert "not completed" in checks[0]["output"]
    assert not (tmp_path / "backend/package-lock.json").exists()


def test_agent_deadline_prevents_later_writes_and_keeps_all_tool_results_paired(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path / "application")
    tools.write_file("wait.py", "import time\ntime.sleep(10)\n")
    calls = [
        ChatCompletionMessageFunctionToolCall(
            id=str(index), type="function", function={"name": name, "arguments": json.dumps(arguments)}
        )
        for index, (name, arguments) in enumerate(
            [
                (
                    "run_command",
                    {"command": python_command([str(tools.workspace / "wait.py")]), "timeout_seconds": 180},
                ),
                ("write_file", {"path": "must-not-write.txt", "content": "missed deadline"}),
                ("finish", {"summary": "must not report success"}),
            ]
        )
    ]
    messages = [{"role": "assistant", "tool_calls": [call.model_dump() for call in calls]}]
    with OpenAI(api_key="unused-tool-data-test") as client:
        agent = CodingAgent(client, "unused", tools, tmp_path / "logs")
        started = time.monotonic()
        agent.deadline = started + 0.4
        result = {"status": "running"}
        usage = Usage()
        agent.execute_tools(calls, messages, usage, result)
        assert time.monotonic() - started < 2
    assert usage.tool_calls == 3 and result["status"] == "time_limit"
    assert json.loads(messages[1]["content"])["returncode"] == 124
    assert all(json.loads(message["content"])["executed"] is False for message in messages[2:])
    assert [message["tool_call_id"] for message in messages[1:]] == [call.id for call in calls]
    assert not (tools.workspace / "must-not-write.txt").exists()


def test_managed_server_startup_and_command_share_one_deadline(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("backend/package.json", '{"scripts":{"start":"node server.js"}}')
    tools.write_file(
        "backend/server.js", "require('http').createServer((q,r)=>r.end('ready')).listen(process.env.PORT);"
    )
    tools.write_file(
        "delayed.py",
        "import time\nfrom pathlib import Path\nprint('checking',flush=True)\ntime.sleep(3)\nPath('leaked.txt').touch()\n",
    )
    started = time.monotonic()
    result = tools.run_with_server(python_command([str(tmp_path / "delayed.py")]), 3, deadline=started + 1.6)
    assert result["returncode"] == 124 and result["timed_out"]
    assert "checking" in result["output"]
    assert time.monotonic() - started < 2.8
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(("127.0.0.1", result["port"])) != 0
    time.sleep(2)
    assert not (tmp_path / "leaked.txt").exists()
