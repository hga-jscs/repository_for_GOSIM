"""Build a web application from an ARC requirement tree."""

import hashlib
import json
import math
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from arcbench_agent_runtime import AgentRuntime
from openai import OpenAI

from agent import CodingAgent
from model_config import resolve_model
from requirements import (
    evolution_batches,
    external_dependency_ids,
    load_requirements,
    make_batches,
    ordered_requirements,
    public_check_target,
    render_requirements,
    requirement_index,
    requirement_version,
)
from scaffold import prepare_workspace
from tools import WorkspaceTools, bounded
from verification import incomplete_check, verify_application

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)
FINAL_VERIFICATION_SECONDS = 480
DIAGNOSTIC_SECONDS = 120
MINIMUM_REPAIR_SECONDS = 120


def previous_requirement_ids(workspace: Path, target: str | None) -> list[str]:
    path = workspace / "agent-requirements.json"
    if not target or not path.is_file():
        return []
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("Invalid Agent requirement history in agent-requirements.json")
    if record.get("target") != target:
        return []
    identifiers = record.get("attempted_requirement_ids")
    if record.get("schema_version") != 1 or not isinstance(identifiers, list) or not all(
        isinstance(identifier, str) and identifier for identifier in identifiers
    ):
        raise ValueError("Invalid Agent requirement history in agent-requirements.json")
    return sorted(set(identifiers))


def save_requirement_ids(workspace: Path, target: str | None, identifiers: list[str]) -> None:
    if target:
        path = workspace / "agent-requirements.json"
        pending = path.with_suffix(".tmp")
        pending.write_text(
            json.dumps({"schema_version": 1, "target": target, "attempted_requirement_ids": sorted(set(identifiers))}),
            encoding="utf-8",
        )
        pending.replace(path)


def batch_seconds(remaining_seconds: float, batches: list[list]) -> float:
    weights = [math.sqrt(sum(len(item.description) for item in batch) + 1000) for batch in batches]
    return max(0, remaining_seconds) * weights[0] / sum(weights)


def failed_checks(checks: list[dict]) -> str:
    return json.dumps([check for check in checks if check["returncode"] != 0], ensure_ascii=False)


def verification_feedback(history: list[dict]) -> str:
    if not history:
        return ""
    previous = history[-1]
    latest = previous.get("checks_after_repair", previous["checks"])
    failures = [
        check
        for check in latest
        if check["returncode"] != 0 or check.get("timed_out") or check.get("completed") is False
    ]
    pending = {}
    latest_checks = {id(check) for check in latest}
    for record in history:
        for checks in [record["checks"], *([record["checks_after_repair"]] if "checks_after_repair" in record else [])]:
            for check in checks:
                for index, case in enumerate(check.get("cases", [])):
                    file = case.get("file", "").replace("\\", "/")
                    test = case.get("nodeid", "").partition("::")[2] or case.get("name")
                    identity = (file, test) if file and test else (id(check), index)
                    if case.get("status") == "passed":
                        pending.pop(identity, None)
                    elif case.get("status") == "failed":
                        pending[identity] = (record["batch"], check, case)
    historical = {}
    for batch, check, case in pending.values():
        if id(check) in latest_checks:
            continue
        part = next(
            (part for part in check.get("parts", []) if part.get("test_file") == case.get("file")),
            {},
        )
        reproduction = part.get("reproduction_command", check.get("reproduction_command", ""))
        group = historical.setdefault(
            (batch, reproduction),
            {
                "observation": "Earlier case failures, NOT reverified; current status unknown",
                "batch": batch,
                "failed_cases": [],
                "reproduction_command": reproduction,
            },
        )
        group["failed_cases"].append(
            {
                "file": case.get("file", ""),
                "name": case.get("name"),
                **({"test": case["nodeid"].partition("::")[2]} if "::" in case.get("nodeid", "").partition("::")[2] else {}),
                "requirements": case.get("requirements", []),
                "failure": bounded(case.get("failure", ""), 240),
            }
        )
    if not failures and not historical:
        return ""
    earlier = []
    if failures and "checks_after_repair" in previous and all(check == incomplete_check(check["command"]) for check in failures):
        passed_commands = {check["command"] for check in latest if check not in failures}
        earlier = [
            check for check in previous["checks"]
            if check["returncode"] != 0 and check["command"] not in passed_commands and not check.get("cases")
        ]
    observations = []
    for label, checks in (("Latest verification", failures), ("Earlier failure, NOT reverified after repair", earlier)):
        for check in sorted(checks, key=lambda check: check["command"].startswith("public ")):
            observations.append(
                {
                    "observation": label,
                    **{
                        key: check[key]
                        for key in ("command", "returncode", "timed_out", "completed", "reproduction_command")
                        if key in check
                    },
                    "output": bounded(check.get("output", ""), 2400),
                    "server_output": bounded(check.get("server_output", ""), 1600),
                    "failed_cases": [
                        {
                            "name": case.get("name"),
                            "requirements": case.get("requirements", []),
                            "failure": bounded(case.get("failure", ""), 600),
                        }
                        for case in check.get("cases", [])
                        if case.get("status") == "failed"
                    ][:3],
                }
            )
    startup = [item for item in observations if not item["command"].startswith("public ")]
    workflows = [item for item in observations if item["command"].startswith("public ")]
    observations = startup + list(historical.values()) + workflows
    return (
        f"Independent verification from batch {previous['batch']} needs attention before new features. "
        "Reproduce and repair dependency, build, server startup or browser startup failures first. "
        "Use the actual error logs even when timed_out is true. A deadline-only result means verification "
        "was incomplete; an earlier error is historical evidence, not proof it remains after repair. "
        "Inspect current source rather than relying on outdated architecture notes. Preserve working behavior, "
        "test assertions and required seed data; rerun the reported check before continuing.\n"
        + bounded(json.dumps(observations, ensure_ascii=False), 7300)
        + "\n\n"
    )


def verification_state(checks: list[dict], task_target: str | None, extra_commands: list[str]) -> dict:
    public = next((check for check in checks if check["command"] == f"public {task_target} workflow checks"), None)
    build_checks = [
        check
        for check in checks
        if not check["command"].startswith("public ") and check["command"] not in extra_commands
    ]
    required = {
        "npm --prefix frontend run build",
        "backend npm start + HTTP GET / on an unused port",
        "real browser startup and uncaught frontend errors",
    }
    build_ready = required <= {check["command"] for check in build_checks} and all(
        check["returncode"] == 0 and not check.get("timed_out") for check in build_checks
    )
    public_passed = (
        public["returncode"] == 0
        and not public.get("timed_out")
        and public.get("completed", False)
        and public.get("passed", 0) > 0
        if public is not None
        else None
    )
    business_ready = public_passed
    if task_target and public is None:
        business_ready = False
    if extra_commands:
        external_passed = all(
            any(
                check["command"] == command and check["returncode"] == 0 and not check.get("timed_out")
                for check in checks
            )
            for command in extra_commands
        )
        business_ready = business_ready is True and external_passed if task_target else external_passed
    return {
        "build_ready": build_ready,
        "business_ready": business_ready,
        "public_workflow_passed": public_passed,
        "delivery_ready": build_ready and business_ready is True,
        "verification_status": "unverified"
        if business_ready is None and build_ready
        else "passed"
        if build_ready and business_ready is True
        else "failed",
    }


def report_checks(stage: str, checks: list[dict]) -> None:
    print(
        json.dumps(
            {
                "event": "verification_completed",
                "stage": stage,
                "checks": [
                    {
                        key: check[key]
                        for key in ("command", "returncode", "timed_out", "passed", "failed", "skipped", "completed")
                        if key in check
                    }
                    | {
                        "failures": [
                            {"name": case["name"], "reason": case.get("failure", "")[-700:]}
                            for case in check.get("cases", [])
                            if case["status"] == "failed"
                        ][:5],
                        "reason": check.get("output", "")[-1000:] if check["returncode"] else "",
                    }
                    for check in checks
                ],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def repair_budget(
    remaining_seconds: float,
    checks: list[dict] | None = None,
    full_verification_seconds: float | None = None,
) -> dict:
    checks = checks or []
    public = next((check for check in checks if check["command"].startswith("public ")), {})
    parts = public.get("parts", [])
    complete = (
        full_verification_seconds is not None
        and math.isfinite(full_verification_seconds)
        and full_verification_seconds >= 0
        and public.get("completed") is True
        and public.get("passed", 0) + public.get("failed", 0) > 0
        and parts
        and all(part.get("report_source") == "junit" for part in parts)
        and all(
            not check.get("timed_out")
            and check.get("completed") is not False
            and check.get("returncode") in {0, 1}
            and not any(
                flag in check["command"] or flag in check.get("reproduction_command", "")
                for flag in (
                    ("--maxfail",)
                    if public.get("verification_scope") == "final_stage"
                    else ("--maxfail", "--implemented")
                )
            )
            for check in [*checks, *parts]
        )
        and {
            "npm --prefix frontend run build",
            "backend npm start + HTTP GET / on an unused port",
            "real browser startup and uncaught frontend errors",
        } <= {check["command"] for check in checks if check["returncode"] == 0}
    )
    reserve = FINAL_VERIFICATION_SECONDS
    if complete:
        reserve = min(FINAL_VERIFICATION_SECONDS, max(120, full_verification_seconds * 2 + 30))
    available = remaining_seconds - reserve
    return {
        "remaining_seconds": remaining_seconds,
        "reserved_seconds": reserve,
        "repair_seconds": available if available >= MINIMUM_REPAIR_SECONDS else 0,
        "observed_full_seconds": full_verification_seconds if complete else None,
    }


def repair_seconds(
    remaining_seconds: float,
    checks: list[dict] | None = None,
    full_verification_seconds: float | None = None,
) -> float:
    return repair_budget(remaining_seconds, checks, full_verification_seconds)["repair_seconds"]


def integration_task(items: dict) -> str:
    return (
        "Integrate the completed application against the full feature contract below. "
        "Start with the home page and route table. Within eight inspection calls, choose the first missing "
        "core user journey, implement it, and test it before inspecting another module. "
        "Inspect only the screens and server endpoints needed for that journey. Later batches may have "
        "removed earlier routes or left implemented pages unreachable. Restore missing required user journeys "
        "and controls, including creating fresh data from the home page. Check early features still work after "
        "later changes; do not rely only on seeded URLs or direct API tests. Use real browser smoke checks "
        "for creation, mutation, validation and reload. Fix integration failures while preserving working "
        "behavior. Seed initialization must be idempotent across backend restarts, preserve subsequent user "
        "edits/deletions, and never duplicate or recreate revoked grants. "
        "Exercise consecutive UI mutations while earlier writes are pending, including an immediate "
        "edit/paste/undo sequence when supported. Earlier values and formulas must survive; prevent "
        "duplicate blur/keyboard commits, stale responses and undo snapshots captured from old state. "
        "Reuse existing tests and avoid redesigning the architecture. Build, check startup, and "
        "report unresolved requirements honestly.\n\n"
        + render_requirements(ordered_requirements(items), include_scenarios=False)
    )


@app.command()
def main(
    requirement_path: Annotated[
        Path, typer.Argument(help="requirements.yaml or its containing directory", envvar="ARCBENCH_TASK_DIR")
    ] = Path("requirements"),
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir", "-o", help="Application workspace", envvar=["ARCBENCH_OUTPUT_DIR", "ARCBENCH_TEMPLATE_DIR"]
        ),
    ] = Path("."),
    app_type: Annotated[str, typer.Option("--type", "-t", envvar="ARCBENCH_TASK_TYPE")] = "web",
    port: Annotated[int, typer.Option("--web-port", "--port", min=1, max=65535, envvar="ARCBENCH_WEB_PORT")] = 3000,
    model: Annotated[str | None, typer.Option()] = None,
    base_url: Annotated[str | None, typer.Option()] = None,
    api_key_file: Annotated[Path | None, typer.Option()] = None,
    variant: Annotated[str, typer.Option(help="atomic, grouped, or compact")] = "compact",
    max_steps: Annotated[int, typer.Option(min=1)] = 70,
    start_batch: Annotated[int, typer.Option(min=1, help="Resume an existing application at this batch number")] = 1,
    reasoning_effort: Annotated[str, typer.Option(help="DeepSeek thinking effort: low, high, or max")] = "high",
    time_limit: Annotated[
        int, typer.Option(min=1, help="Implementation budget in seconds; final checks run afterward")
    ] = 10800,
    integration_time_limit: Annotated[
        int, typer.Option(min=0, help="Separate budget for checking integrated user journeys, in seconds")
    ] = 1800,
    repair_time_limit: Annotated[
        int, typer.Option(min=0, help="Separate budget for repairing failed final checks, in seconds")
    ] = 3600,
    public_checks: Annotated[bool, typer.Option("--public-checks/--no-public-checks")] = True,
    batch_checks: Annotated[bool, typer.Option("--batch-checks/--no-batch-checks")] = True,
    verification_command: Annotated[
        list[str] | None, typer.Option(help="External verification command; repeatable")
    ] = None,
    plan_only: Annotated[bool, typer.Option(help="Print batches without model calls or workspace edits")] = False,
    only_requirements: Annotated[str, typer.Option(help="Local evolution scope: comma-separated IDs; omitted means all")] = "",
) -> None:
    if app_type != "web":
        raise typer.BadParameter("This agent implements web tasks only")
    if variant not in {"atomic", "grouped", "compact"}:
        raise typer.BadParameter("variant must be atomic, grouped, or compact")
    if reasoning_effort not in {"low", "high", "max"}:
        raise typer.BadParameter("reasoning-effort must be low, high, or max")
    path = requirement_path.resolve()
    if path.is_dir():
        path /= "requirements.yaml"
    prerequisites = path.with_name("prerequisites.md")
    prerequisite_text = prerequisites.read_text(encoding="utf-8") if prerequisites.is_file() else ""
    items = load_requirements(path, allow_external_dependencies=bool(prerequisite_text.strip()))
    external_dependencies = external_dependency_ids(items)
    workspace = output_dir.resolve()
    task_target = public_check_target(items)
    check_target = task_target if public_checks else None
    task_version = requirement_version(items)
    requirement_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    previous_ids = [] if task_version == "evolution" else previous_requirement_ids(workspace, task_target)
    selected = sorted(set(filter(None, only_requirements.split(",")))) or list(items)
    if not set(selected) <= set(items):
        raise typer.BadParameter("only-requirements contains an unknown requirement ID")
    if only_requirements and task_version != "evolution":
        raise typer.BadParameter("only-requirements applies only to evolution tasks")
    verification_ids = sorted(set(items) | set(external_dependencies) | set(previous_ids))
    batches = evolution_batches(items, selected) if task_version == "evolution" else (
        [[item] for item in ordered_requirements(items)] if variant == "atomic" else make_batches(items)
    )
    if start_batch > len(batches):
        raise typer.BadParameter("start-batch exceeds the number of requirement batches")
    endpoint = (
        base_url
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("ARC_BENCH_API_BASE_URL")
        or "https://api.arc-bench.com/v1"
    )
    model_name = resolve_model(
        endpoint, model or os.environ.get("MODEL") or os.environ.get("ARC_BENCH_MODEL"),
        evolution=task_version == "evolution",
    )
    if plan_only:
        print(
            json.dumps(
                {
                    "variant": variant,
                    "model": model_name,
                    "base_url": endpoint,
                    "execution_settings": {
                        "max_steps": max_steps,
                        "reasoning_effort": reasoning_effort,
                        "time_limit": time_limit,
                        "integration_time_limit": integration_time_limit,
                        "repair_time_limit": repair_time_limit,
                    },
                    "task": task_target,
                    "requirement_version": task_version,
                    "requirements_sha256": requirement_hash,
                    "scenario_count": sum(len(item.scenarios) for item in items.values()),
                    "requirements": len(items),
                    "selected_requirements": selected,
                    "external_dependencies": external_dependencies,
                    "previously_attempted_requirements": previous_ids,
                    "public_check_target": task_target,
                    "verification_requirement_ids": verification_ids,
                    "batches": [[item.identifier for item in batch] for batch in batches],
                    "requirement_characters": sum(len(render_requirements(batch)) for batch in batches),
                },
                indent=2,
            )
        )
        return
    key = (
        api_key_file.read_text(encoding="utf-8").strip()
        if api_key_file
        else os.environ.get("OPENAI_API_KEY") or os.environ.get("ARC_BENCH_API_KEY")
    )
    if not key:
        raise typer.BadParameter("Supply OPENAI_API_KEY or --api-key-file outside the application workspace")
    if start_batch > 1 and not all((workspace / part / "package.json").is_file() for part in ("frontend", "backend")):
        raise typer.BadParameter("Resuming requires an existing frontend/backend application")
    if path.is_relative_to(workspace):
        raise typer.BadParameter("Keep input requirements outside the generated application workspace")
    if api_key_file and api_key_file.resolve().is_relative_to(workspace):
        raise typer.BadParameter("Keep the API key file outside the generated application workspace")
    prepare_workspace(workspace, incremental=task_version == "evolution")
    if task_version == "evolution":
        from evolution import run_evolution

        summary = run_evolution(workspace=workspace, requirement_path=path, items=items, target=task_target,
                                key=key, endpoint=endpoint, model=model_name, variant=variant, port=port,
                                max_steps=max_steps, start_batch=start_batch, reasoning_effort=reasoning_effort,
                                time_limit=time_limit, integration_time_limit=integration_time_limit,
                                repair_time_limit=repair_time_limit, public_checks=public_checks,
                                batch_checks=batch_checks, extra_commands=verification_command or [], selected=selected)
        if not summary["generation_completed"]:
            raise typer.Exit(1)
        return
    tools = WorkspaceTools(workspace, (key,))
    run_directory = workspace.parent / f"{workspace.name}-runs" / str(time.time_ns())
    started = time.monotonic()
    implementation_deadline = started + time_limit
    agent = CodingAgent(
        OpenAI(api_key=key, base_url=endpoint, max_retries=0),
        model_name,
        tools,
        run_directory,
        max_steps=max_steps,
        deadline=implementation_deadline,
        compact=variant == "compact",
        reasoning_effort=reasoning_effort,
    )
    runtime = AgentRuntime.from_env(project_dir=str(workspace))
    runtime.git.ensure_repo()
    runtime.events.mark_run_started("Requirement implementation started")
    index = requirement_index(items)
    if external_dependencies:
        prerequisite_text += (
            "\nExternal requirement dependencies: " + ", ".join(external_dependencies) + ". "
            "These IDs are outside the current stage, not completed or verified by this run. "
            "Inspect their existing implementation in the supplied application and reuse its data, routes and "
            "authentication. Preserve earlier features; repair missing prerequisite behavior when needed."
        )
        print(json.dumps({"event": "external_dependencies", "ids": external_dependencies, "verified": False}), flush=True)
    context = "\n\n".join(description for _, _, description in next(iter(items.values())).parents[:1])
    task = (
        f"Build the application described below. Evaluation uses PORT={port}.\n{context}\n\n"
        f"All planned features:\n{index}\n\n"
        "This first stage establishes the shared architecture and a working application skeleton. "
        "Preserve the supplied frontend/backend template and its startup/build conventions. "
        "Document commands, routes, data model, state transitions "
        "and component ownership in ARCHITECTURE.md. Include a reusable test setup. "
        "Implement the scaffold and run a startup smoke check; later stages implement the listed features."
    )
    results = [agent.run(task, "foundation")] if variant == "atomic" and start_batch == 1 else []
    attempted = [
        *previous_ids, *external_dependencies,
        *(item.identifier for batch in batches[: start_batch - 1] for item in batch),
    ]
    batch_verification = []
    for number, batch in enumerate(batches[start_batch - 1 :], start_batch):
        if agent.requests_stopped or time.monotonic() >= implementation_deadline:
            break
        allowance = batch_seconds(implementation_deadline - time.monotonic(), batches[number - 1 :])
        batch_deadline = min(implementation_deadline, time.monotonic() + allowance)
        generation_deadline = time.monotonic() + allowance * (0.75 if batch_checks and check_target else 1)
        agent.deadline = min(batch_deadline, generation_deadline)
        task = (
            f"Implement batch {number}/{len(batches)} in the existing application. "
            "Read ARCHITECTURE.md and relevant source first. Preserve existing working features. "
            "Keep the controller-owned agent-requirements.json history intact. "
            "All acceptance descriptions and scenarios below are requirements, including initial seed data. "
            "Implement behavior, persistence, validation, permissions, and exact accessible controls. "
            "Check scenarios through actual UI/API interactions, not only static source checks. "
            "Do not change required seed records to make smoke tests pass; create separate test objects. "
            "Implement all acceptance behavior within this stage's allocated time. "
            "Record unfinished requirement IDs and reproducible failures in ARCHITECTURE.md before finish. "
            "Update architecture notes if interfaces change.\n\n"
            + ("Task prerequisites:\n" + prerequisite_text + "\n\n" if prerequisite_text else "")
            + render_requirements(batch)
        )
        print(
            json.dumps(
                {
                    "event": "batch_started",
                    "batch": number,
                    "total_batches": len(batches),
                    "budget_seconds": round(allowance, 1),
                    "requirements": [item.identifier for item in batch],
                }
            ),
            flush=True,
        )
        if number == 1 and variant != "atomic":
            task = (
                f"Create or extend the supplied frontend/backend web application. Evaluation uses PORT={port}. "
                "Preserve its startup/build conventions and server-side persistence. "
                "Implement this first batch immediately, including its acceptance criteria. "
                "Keep the architecture small: avoid speculative frameworks and unused abstractions. "
                "Document shared routes, data schema and test commands in ARCHITECTURE.md for later batches.\n"
                f"Planned feature index (later batches will implement remaining features):\n{index}\n\n" + task
            )
        for item in batch:
            runtime.events.mark_implementation_started(item.identifier)
        result = agent.run(verification_feedback(batch_verification) + task, f"batch-{number:02d}")
        if result["status"] in {"step_limit", "api_unavailable"} and time.monotonic() < agent.deadline:
            previous = result
            result = agent.run(
                "Continue the unfinished stage using the existing conversation and files. "
                "Complete missing acceptance criteria and finish this batch. "
                "Preserve working code and avoid rebuilding it.",
                f"batch-{number:02d}-continuation",
                resume_from=previous["stage"],
            )
            result["previous_attempt"] = previous
        attempted.extend(item.identifier for item in batch)
        if batch_checks and check_target and result["status"] not in {"api_error", "api_unavailable"}:
            checks = verify_application(tools, [], check_target, attempted, deadline=batch_deadline)
            report_checks(f"batch-{number:02d}", checks)
            batch_verification.append({"batch": number, "checks": checks})
            if any(check["returncode"] != 0 for check in checks) and time.monotonic() < batch_deadline:
                agent.deadline = time.monotonic() + (batch_deadline - time.monotonic()) * 0.75
                previous = result
                result = agent.run(
                    "Independent checks found failures in implemented requirements. Fix the product behavior; "
                    "preserve requirements, test assertions and authoritative seed values. Select one reported "
                    "failure at a time. Read that test's complete workflow and the directly related source. "
                    "Once the evidence explains the failure, make the smallest justified product change and "
                    "rerun that same test before investigating another failure. Do not expand reading to "
                    "hypothetical unrelated regressions; the controller will run independent checks for the "
                    "implemented requirements after this repair. Use the reported reproduction_command "
                    "first to rerun a public check; you may append -k to select a failing test, preserving all other "
                    "arguments.\n" + failed_checks(checks),
                    f"batch-{number:02d}-repair",
                    resume_from=previous["stage"],
                    repair_checks=checks,
                )
                result["previous_attempt"] = previous
                checks = verify_application(tools, [], check_target, attempted, deadline=batch_deadline)
                report_checks(f"batch-{number:02d}-repair", checks)
                batch_verification[-1]["checks_after_repair"] = checks
            result["independent_checks_passed"] = all(check["returncode"] == 0 for check in checks)
            result["business_checks_executed"] = sum(
                check.get("passed", 0) + check.get("failed", 0) for check in checks
            )
            checked_ids = {
                identifier
                for check in checks
                for case in check.get("cases", [])
                if case["status"] != "skipped"
                for identifier in case.get("requirements", [])
            }
            result["requirements_without_independent_checks"] = [
                item.identifier for item in batch if item.identifier not in checked_ids
            ]
        results.append(result | {"requirements": [item.identifier for item in batch]})
        save_requirement_ids(workspace, task_target, sorted(set(attempted) & (set(items) | set(previous_ids))))
        if runtime and result["status"] == "finished" and result.get("independent_checks_passed", True):
            for item in batch:
                runtime.events.mark_implementation_done(item.identifier, result["summary"][:500])
            runtime.git.commit(f"Implement requirements: {', '.join(item.identifier for item in batch)}")
        else:
            for item in batch:
                runtime.events.mark_implementation_failed(item.identifier, result["summary"][:500])
    if integration_time_limit and not agent.requests_stopped:
        agent.deadline = time.monotonic() + integration_time_limit
        results.append(agent.run(verification_feedback(batch_verification) + integration_task(items), "integration"))
        runtime.git.commit("Integrate application workflows")
    repair_deadline = time.monotonic() + repair_time_limit
    final_deadline = repair_deadline if repair_time_limit else time.monotonic() + FINAL_VERIFICATION_SECONDS
    extra_commands = list(verification_command or [])
    checks = []
    final_checks_run = False
    if repair_time_limit:
        checks = verify_application(
            tools,
            extra_commands,
            check_target,
            verification_ids if check_target else None,
            deadline=min(final_deadline, time.monotonic() + DIAGNOSTIC_SECONDS),
        )
        report_checks("final-diagnostic", checks)
    final_repair_reasoning_effort = None
    full_verification_seconds = None
    for attempt in range(5):
        budget = repair_budget(repair_deadline - time.monotonic(), checks, full_verification_seconds)
        print(json.dumps({"event": "final_repair_budget", "attempt": attempt + 1, **budget}), flush=True)
        allowance = budget["repair_seconds"]
        if agent.requests_stopped or all(check["returncode"] == 0 for check in checks) or not allowance:
            break
        final_repair_reasoning_effort = reasoning_effort
        agent.reasoning_effort = final_repair_reasoning_effort
        agent.deadline = time.monotonic() + allowance
        results.append(
            agent.run(
                "Repair these independently executed delivery and public workflow checks. Preserve requirements, "
                "evaluation tests and authoritative seed values. Select one reported failure at a time. Read "
                "that test's complete workflow, including later assertions, and the directly related source. "
                "Once the evidence explains the failure, make the smallest justified product change and rerun "
                "that same test before investigating another failure. Do not expand reading to hypothetical "
                "unrelated regressions; the controller will run the full independent checks after this repair. "
                "Never modify the test files. "
                "Use the reported reproduction_command first to rerun a public check; you may append -k to select "
                "a failing test, preserving all other arguments. "
                "The frontend must build and backend npm start must serve frontend/dist using PORT.\n"
                + failed_checks(checks),
                f"repair-{attempt + 1}",
                resume_from=f"repair-{attempt}" if attempt else None,
                repair_checks=checks,
            )
        )
        verification_started = time.monotonic()
        checks = verify_application(
            tools, extra_commands, check_target, verification_ids if check_target else None,
            deadline=repair_deadline, max_failures=None,
        )
        full_verification_seconds = time.monotonic() - verification_started
        final_checks_run = True
        report_checks(f"repair-{attempt + 1}", checks)
    if not final_checks_run:
        checks = verify_application(
            tools, extra_commands, check_target, verification_ids if check_target else None,
            deadline=final_deadline, max_failures=None,
        )
        report_checks("final", checks)
    runtime.git.commit("Verify application build and startup")
    checked_ids = {
        identifier
        for check in checks
        for case in check.get("cases", [])
        if case["status"] != "skipped"
        for identifier in case.get("requirements", [])
    }
    unchecked_ids = sorted(set(items) - checked_ids)
    summary = {
        "model": model_name,
        "requirements_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "variant": variant,
        "start_batch": start_batch,
        "reasoning_effort": reasoning_effort,
        "final_repair_reasoning_effort": final_repair_reasoning_effort,
        "public_check_target": task_target,
        "external_dependencies": external_dependencies,
        "previously_attempted_requirements": previous_ids,
        "verification_requirement_ids": verification_ids,
        "batch_verification": batch_verification,
        "usage": asdict(agent.usage),
        "token_accounting_complete": agent.token_accounting_complete,
        "model_requests_stopped": agent.requests_stopped,
        "results": results,
        "verification": checks,
        "official_pass_rate": None,
        **verification_state(checks, check_target, extra_commands),
        "business_check_scope": "local_partial"
        if task_target and unchecked_ids
        else "local_atomic_coverage"
        if task_target
        else "custom_commands"
        if extra_commands
        else "unverified",
        "requirements_without_independent_checks": unchecked_ids,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "all_batches_finished": len([result for result in results if "requirements" in result]) == len(batches)
        and all(result["status"] == "finished" and result.get("independent_checks_passed", True) for result in results),
    }
    (run_directory / "summary.json").write_text(tools.redact(json.dumps(summary, indent=2)), encoding="utf-8")
    if runtime:
        if summary["build_ready"]:
            business_status = (
                "passed"
                if summary["business_ready"] is True
                else "failed"
                if summary["business_ready"] is False
                else "unverified"
            )
            runtime.events.mark_run_completed(
                "Application generation finished; build and startup passed. "
                f"Local business checks: {business_status}; official evaluation is pending. "
                f"Local validation scope: {summary['business_check_scope']}. "
                f"All implementation stages finished: {summary['all_batches_finished']}. "
                f"Model requests stopped after an API error: {agent.requests_stopped}"
            )
        else:
            runtime.events.mark_run_failed(
                "Application build or startup failed: "
                f"build_ready={summary['build_ready']}, business_ready={summary['business_ready']}. "
                "See independent verification results for failures or missing checks."
            )
    print(
        json.dumps(
            {
                "summary": str(run_directory / "summary.json"),
                "usage": summary["usage"],
                **verification_state(checks, check_target, extra_commands),
            }
        ),
        flush=True,
    )
    if not summary["build_ready"]:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
