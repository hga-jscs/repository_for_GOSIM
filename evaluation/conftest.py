import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

agent_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(agent_root / "submission" if (agent_root / "submission").is_dir() else agent_root))
from processes import ProcessGroup
from tools import WorkspaceTools

SERVER_LOG = pytest.StashKey[Path]()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    path = item.config.stash.get(SERVER_LOG, None)
    if report.failed and path and path.is_file():
        report.sections.append(("Application server log", path.read_text(encoding="utf-8", errors="replace")[-4000:]))


def pytest_addoption(parser):
    parser.addoption("--application", required=True)
    parser.addoption("--target", choices=["github", "sheet"], required=True)
    parser.addoption("--browser-channel", default=None)
    parser.addoption("--full", action="store_true", help="Include cross-module checks for full applications")


@pytest.fixture(scope="session")
def application(request, tmp_path_factory):
    workspace = Path(request.config.getoption("--application")).resolve()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    tools = WorkspaceTools(workspace)
    environment = tools.command_environment() | {"PORT": str(port)}
    arguments = ["cmd.exe", "/d", "/c", "npm start"] if os.name == "nt" else ["npm", "start"]
    log_path = tmp_path_factory.mktemp("server") / "output.log"
    request.config.stash[SERVER_LOG] = log_path
    with log_path.open("wb") as output, ProcessGroup() as group:
        process = subprocess.Popen(
            arguments,
            cwd=workspace / "backend",
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        group.attach(process)
        deadline = time.monotonic() + 30
        ready = False
        while process.poll() is None and time.monotonic() < deadline:
            with socket.socket() as connection:
                connection.settimeout(0.2)
                ready = connection.connect_ex(("127.0.0.1", port)) == 0
            if ready:
                break
            time.sleep(0.2)
        try:
            assert ready, "Application failed to start; inspect the server log in pytest's temporary directory"
            yield f"http://127.0.0.1:{port}"
        finally:
            group.close()
            process.wait()


@pytest.fixture(scope="session")
def browser(request):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=request.config.getoption("--browser-channel"),
            headless=True,
            args=[
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
            ],
        )
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(10000)
    expect.set_options(timeout=10000)
    yield page
    context.close()


def pytest_collection_modifyitems(config, items):
    target = config.getoption("--target")
    for item in items:
        if not item.name.startswith(f"test_{target}_"):
            item.add_marker(pytest.mark.skip(reason="Different application task"))
        if "_full_" in item.name and not config.getoption("--full"):
            item.add_marker(pytest.mark.skip(reason="Requires a full application"))
