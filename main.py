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
from requirements import (
    load_requirements,
    make_batches,
    ordered_requirements,
    public_check_target,
    render_requirements,
    requirement_index,
)
from scaffold import prepare_workspace
from tools import WorkspaceTools
from verification import verify_application

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)


def batch_seconds(remaining_seconds: float, batches: list[list]) -> float:
    weights = [math.sqrt(sum(len(item.description) for item in batch) + 1000) for batch in batches]
    return max(0, remaining_seconds) * weights[0] / sum(weights)


def failed_checks(checks: list[dict]) -> str:
    return json.dumps([check for check in checks if check["returncode"] != 0], ensure_ascii=False)


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
    max_steps: Annotated[int, typer.Option(min=1)] = 60,
    start_batch: Annotated[int, typer.Option(min=1, help="Resume an existing application at this batch number")] = 1,
    reasoning_effort: Annotated[str, typer.Option(help="DeepSeek thinking effort: low, high, or max")] = "low",
    time_limit: Annotated[
        int, typer.Option(min=1, help="Implementation budget in seconds; final checks run afterward")
    ] = 7200,
    integration_time_limit: Annotated[
        int, typer.Option(min=0, help="Separate budget for checking integrated user journeys, in seconds")
    ] = 600,
    repair_time_limit: Annotated[
        int, typer.Option(min=0, help="Separate budget for repairing failed final checks, in seconds")
    ] = 900,
    public_checks: Annotated[bool, typer.Option("--public-checks/--no-public-checks")] = True,
    batch_checks: Annotated[bool, typer.Option("--batch-checks/--no-batch-checks")] = True,
    verification_command: Annotated[
        list[str] | None, typer.Option(help="External verification command; repeatable")
    ] = None,
    plan_only: Annotated[bool, typer.Option(help="Print batches without model calls or workspace edits")] = False,
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
    items = load_requirements(path)
    prerequisites = path.with_name("prerequisites.md")
    prerequisite_text = prerequisites.read_text(encoding="utf-8") if prerequisites.is_file() else ""
    batches = [[item] for item in ordered_requirements(items)] if variant == "atomic" else make_batches(items)
    if start_batch > len(batches):
        raise typer.BadParameter("start-batch exceeds the number of requirement batches")
    if plan_only:
        print(
            json.dumps(
                {
                    "variant": variant,
                    "requirements": len(items),
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
    endpoint = (
        base_url
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("ARC_BENCH_API_BASE_URL")
        or "https://api.arc-bench.com/v1"
    )
    model_name = model or os.environ.get("MODEL") or os.environ.get("ARC_BENCH_MODEL") or "deepseek-v4-flash"
    workspace = output_dir.resolve()
    if start_batch > 1 and not all((workspace / part / "package.json").is_file() for part in ("frontend", "backend")):
        raise typer.BadParameter("Resuming requires an existing frontend/backend application")
    if path.is_relative_to(workspace):
        raise typer.BadParameter("Keep input requirements outside the generated application workspace")
    if api_key_file and api_key_file.resolve().is_relative_to(workspace):
        raise typer.BadParameter("Keep the API key file outside the generated application workspace")
    tools = WorkspaceTools(workspace, (key,))
    prepare_workspace(workspace)
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
    task_target = public_check_target(items) if public_checks else None
    attempted = [item.identifier for batch in batches[: start_batch - 1] for item in batch]
    batch_verification = []
    for number, batch in enumerate(batches[start_batch - 1 :], start_batch):
        if agent.requests_stopped or time.monotonic() >= implementation_deadline:
            break
        allowance = batch_seconds(implementation_deadline - time.monotonic(), batches[number - 1 :])
        batch_deadline = min(implementation_deadline, time.monotonic() + allowance)
        generation_deadline = time.monotonic() + allowance * (0.75 if batch_checks and task_target else 1)
        agent.deadline = min(batch_deadline, generation_deadline)
        task = (
            f"Implement batch {number}/{len(batches)} in the existing application. "
            "Read ARCHITECTURE.md and relevant source first. Preserve existing working features. "
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
        result = agent.run(task, f"batch-{number:02d}")
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
        if batch_checks and task_target and result["status"] not in {"api_error", "api_unavailable"}:
            checks = verify_application(tools, [], task_target, attempted, deadline=batch_deadline)
            batch_verification.append({"batch": number, "checks": checks})
            if any(check["returncode"] != 0 for check in checks) and time.monotonic() < batch_deadline:
                agent.deadline = time.monotonic() + (batch_deadline - time.monotonic()) * 0.75
                previous = result
                result = agent.run(
                    "Independent checks found failures in implemented requirements. Fix the product behavior; "
                    "keep the test assertions and authoritative seed values unchanged. Read each failed test "
                    "and its complete workflow, then fix shared causes first. Use the reported reproduction_command "
                    "first to rerun a public check; you may append -k to select a failing test, preserving all other "
                    "arguments.\n" + failed_checks(checks),
                    f"batch-{number:02d}-repair",
                    resume_from=previous["stage"],
                )
                result["previous_attempt"] = previous
                checks = verify_application(tools, [], task_target, attempted, deadline=batch_deadline)
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
        if runtime and result["status"] == "finished" and result.get("independent_checks_passed", True):
            for item in batch:
                runtime.events.mark_implementation_done(item.identifier, result["summary"][:500])
            runtime.git.commit(f"Implement requirements: {', '.join(item.identifier for item in batch)}")
        else:
            for item in batch:
                runtime.events.mark_implementation_failed(item.identifier, result["summary"][:500])
    if integration_time_limit and not agent.requests_stopped:
        agent.deadline = time.monotonic() + integration_time_limit
        results.append(agent.run(integration_task(items), "integration"))
        runtime.git.commit("Integrate application workflows")
    checks = verify_application(tools, list(verification_command or []), task_target)
    repair_deadline = time.monotonic() + repair_time_limit
    final_repair_reasoning_effort = None
    for attempt in range(5):
        if (
            agent.requests_stopped
            or all(check["returncode"] == 0 for check in checks)
            or time.monotonic() >= repair_deadline
        ):
            break
        final_repair_reasoning_effort = "high" if reasoning_effort == "low" else reasoning_effort
        agent.reasoning_effort = final_repair_reasoning_effort
        agent.deadline = time.monotonic() + (repair_deadline - time.monotonic()) * 0.75
        results.append(
            agent.run(
                "Repair these independently executed delivery and public workflow checks. Preserve requirements and evaluation "
                "tests. Read each failing test in full from its reported path using a command, then verify "
                "every step of that user journey, including later assertions. Never modify the test files. "
                "Use the reported reproduction_command first to rerun a public check; you may append -k to select "
                "a failing test, preserving all other arguments. "
                "The frontend must build and backend npm start must serve frontend/dist using PORT.\n"
                + failed_checks(checks),
                f"repair-{attempt + 1}",
                resume_from=f"repair-{attempt}" if attempt else None,
            )
        )
        checks = verify_application(tools, list(verification_command or []), task_target, deadline=repair_deadline)
    if any(check.get("timed_out") for check in checks):
        workflow_checks = [check for check in checks if check["command"] == f"public {task_target} workflow checks"]
        checks = verify_application(tools, list(verification_command or []), deadline=time.monotonic() + 180)
        checks.extend(workflow_checks)
    runtime.git.commit("Verify application build and startup")
    summary = {
        "model": model_name,
        "requirements_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "variant": variant,
        "start_batch": start_batch,
        "reasoning_effort": reasoning_effort,
        "final_repair_reasoning_effort": final_repair_reasoning_effort,
        "public_check_target": task_target,
        "batch_verification": batch_verification,
        "usage": asdict(agent.usage),
        "token_accounting_complete": agent.token_accounting_complete,
        "model_requests_stopped": agent.requests_stopped,
        "results": results,
        "verification": checks,
        "official_pass_rate": None,
        "delivery_ready": bool(checks)
        and all(
            check["returncode"] == 0 for check in checks if check["command"] != f"public {task_target} workflow checks"
        ),
        "public_workflow_passed": next(
            (
                check["returncode"] == 0
                for check in checks
                if check["command"] == f"public {task_target} workflow checks"
            ),
            None,
        ),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "all_batches_finished": len([result for result in results if "requirements" in result]) == len(batches)
        and all(result["status"] == "finished" and result.get("independent_checks_passed", True) for result in results),
    }
    (run_directory / "summary.json").write_text(tools.redact(json.dumps(summary, indent=2)), encoding="utf-8")
    if runtime:
        if summary["delivery_ready"]:
            runtime.events.mark_run_completed(
                "Application build and startup verified; official evaluation is pending. "
                f"All implementation stages finished: {summary['all_batches_finished']}. "
                f"Model requests stopped after an API error: {agent.requests_stopped}"
            )
        else:
            runtime.events.mark_run_failed("Application build or startup verification failed")
    print(json.dumps({"summary": str(run_directory / "summary.json"), "usage": summary["usage"]}), flush=True)
    if not summary["delivery_ready"]:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
