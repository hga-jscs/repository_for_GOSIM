import json
import os
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from processes import ProcessGroup
from tools import WorkspaceTools

SERVER_LOG = pytest.StashKey[Path]()
PROGRESS = pytest.StashKey[dict]()
REPORTS = pytest.StashKey[dict]()


def save_progress(config):
    path = Path(config.getoption("--progress-json"))
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config.stash[PROGRESS], ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    path = item.config.stash.get(SERVER_LOG, None)
    if report.failed and path and path.is_file():
        report.sections.append(("Application server log", path.read_text(encoding="utf-8", errors="replace")[-4000:]))
    if PROGRESS not in item.config.stash:
        return
    reports = item.stash.setdefault(REPORTS, {})
    reports[report.when] = report
    if report.when == "teardown":
        phases = {phase: entry.outcome for phase, entry in reports.items()}
        status = (
            "failed"
            if "failed" in phases.values()
            else "skipped"
            if "skipped" in phases.values()
            else "passed"
            if phases.get("call") == "passed"
            else None
        )
        if status:
            item.config.stash[PROGRESS]["cases"].append(
                {
                    "nodeid": item.nodeid,
                    "name": item.name,
                    "file": str(item.path),
                    "requirements": next(
                        (value.split(",") for name, value in item.user_properties if name == "requirements"), []
                    ),
                    "scenarios": next(
                        (value.split(",") for name, value in item.user_properties if name == "scenarios"), []
                    ),
                    "status": status,
                    "phases": phases,
                    "failure": "\n\n".join(
                        f"{phase}:\n{entry.longreprtext}" for phase, entry in reports.items() if entry.failed
                    )[-3000:],
                }
            )
            save_progress(item.config)


def pytest_addoption(parser):
    parser.addoption("--requirement-version", choices=["baseline", "evolution"], default="baseline")
    parser.addoption("--requirements-file", default=None)
    parser.addoption("--application", required=True)
    parser.addoption("--target", choices=["github", "sheet"], required=True)
    parser.addoption("--browser-channel", default=None)
    parser.addoption("--full", action="store_true", help="Include cross-module checks for full applications")
    parser.addoption("--implemented", default="", help="Comma-separated requirement IDs already attempted")
    parser.addoption("--progress-json", default=None, help="Optional atomic report of completed test cases")
    parser.addoption("--progress-id", default=None, help="Identifier of this independent verification invocation")


def pytest_configure(config):
    config.addinivalue_line("markers", "scenarios(*ids): explicit public requirement scenario IDs (REQ-x#n)")
    config.addinivalue_line("markers", "shared: incremental data preservation required in every evolution batch")
    config.addinivalue_line("markers", "full_evolution: preserved workflows required when all current requirements are attempted")
    config.addinivalue_line("markers", "requirements(*ids): public requirements checked by this workflow")
    if config.getoption("--progress-json"):
        if not config.getoption("--progress-id"):
            raise pytest.UsageError("--progress-json requires --progress-id")
        config.stash[PROGRESS] = {"schema_version": 1, "run_id": config.getoption("--progress-id"), "cases": []}
        save_progress(config)


@pytest.fixture(scope="session")
def application(request, tmp_path_factory):
    workspace = Path(request.config.getoption("--application")).resolve()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    tools = WorkspaceTools(workspace)
    environment = tools.command_environment() | {
        "PORT": str(port),
        "ARC_DB_FILE": str(tmp_path_factory.mktemp("database") / "application.db"),
    }
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


@dataclass
class Sessions:
    browser: Browser
    resources: ExitStack
    pages: dict[str, Page]

    def open(self, name: str) -> Page:
        assert name not in self.pages
        context = self.browser.new_context()
        self.resources.callback(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        self.pages[name] = page
        return page


@pytest.fixture
def sessions(page, browser):
    with ExitStack() as resources:
        yield Sessions(browser, resources, {"primary": page})


def pytest_collection_modifyitems(config, items):
    target = config.getoption("--target")
    current_scenarios = None
    current_ids = set()
    if config.getoption("--requirement-version") == "evolution":
        from requirements import load_requirements

        path = config.getoption("--requirements-file")
        if not path:
            raise pytest.UsageError("Evolution checks require their actual public requirements file")
        current = load_requirements(Path(path))
        current_ids = set(current)
        current_scenarios = {f"{key}#{number}" for key, requirement in current.items() for number in range(1, len(requirement.scenarios) + 1)}
    implemented = set(filter(None, config.getoption("--implemented").split(",")))
    for item in items:
        evolution = item.path.name.startswith("test_evolution_")
        if evolution != (config.getoption("--requirement-version") == "evolution"):
            item.add_marker(pytest.mark.skip(reason="Different public requirement version"))
        if not item.name.startswith((f"test_{target}_", f"test_evolution_{target}_")):
            item.add_marker(pytest.mark.skip(reason="Different application task"))
        if "_full_" in item.name and not config.getoption("--full"):
            item.add_marker(pytest.mark.skip(reason="Requires a full application"))
        if item.get_closest_marker("full_evolution") and current_ids and implemented and not current_ids <= implemented:
            item.add_marker(pytest.mark.skip(reason="Preserved full workflow waits for all current requirements"))
        marker = item.get_closest_marker("requirements")
        scenarios = item.get_closest_marker("scenarios")
        if scenarios:
            if evolution and current_scenarios is not None and item.path.name.startswith(f"test_evolution_{target}") and not set(scenarios.args) <= current_scenarios:
                raise pytest.UsageError(f"Scenario mapping outside current public requirements: {item.nodeid}")
            item.user_properties.append(("scenarios", ",".join(scenarios.args)))
        if marker:
            item.user_properties.append(("requirements", ",".join(marker.args)))
        if implemented and (marker is None or not set(marker.args) <= implemented) and not (evolution and item.get_closest_marker("shared")):
            item.add_marker(pytest.mark.skip(reason="Requirements belong to a later implementation batch"))
