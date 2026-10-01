"""Build a web application from an ARC requirement tree."""

import hashlib
import json
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
        "behavior. Reuse existing tests and avoid redesigning the architecture. Build, check startup, and "
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
    agent = CodingAgent(
        OpenAI(api_key=key, base_url=endpoint, max_retries=2),
        model_name,
        tools,
        run_directory,
        max_steps=max_steps,
        deadline=started + time_limit,
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
    for number, batch in enumerate(batches[start_batch - 1 :], start_batch):
        if time.monotonic() >= started + time_limit:
            break
        task = (
            f"Implement batch {number}/{len(batches)} in the existing application. "
            "Read ARCHITECTURE.md and relevant source first. Preserve existing working features. "
            "All acceptance descriptions and scenarios below are requirements, including initial seed data. "
            "Implement behavior, persistence, validation, permissions, and exact accessible controls. "
            "Check scenarios through actual UI/API interactions, not only static source checks. "
            "Update architecture notes if interfaces change.\n\n" + render_requirements(batch)
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
        if result["status"] == "step_limit" and time.monotonic() < started + time_limit:
            previous = result
            result = agent.run(
                "The previous attempt reached its step limit. Continue from the existing files, inspect "
                "the implemented behavior, complete missing acceptance criteria, and finish this batch. "
                "Preserve working code and avoid rebuilding it.\n\n" + task,
                f"batch-{number:02d}-continuation",
            )
            result["previous_attempt"] = previous
        results.append(result | {"requirements": [item.identifier for item in batch]})
        if runtime and result["status"] == "finished":
            for item in batch:
                runtime.events.mark_implementation_done(item.identifier, result["summary"][:500])
            runtime.git.commit(f"Implement requirements: {', '.join(item.identifier for item in batch)}")
        else:
            for item in batch:
                runtime.events.mark_implementation_failed(item.identifier, result["summary"][:500])
    if integration_time_limit:
        agent.deadline = time.monotonic() + integration_time_limit
        results.append(agent.run(integration_task(items), "integration"))
        runtime.git.commit("Integrate application workflows")
    task_target = public_check_target(items) if public_checks else None
    checks = verify_application(tools, list(verification_command or []), task_target)
    agent.deadline = time.monotonic() + repair_time_limit
    for attempt in range(3):
        if all(check["returncode"] == 0 for check in checks) or time.monotonic() >= agent.deadline:
            break
        results.append(
            agent.run(
                "Repair these independently executed delivery and public workflow checks. Preserve requirements and evaluation "
                "tests. Read each failing test in full from its reported path using a command, then verify "
                "every step of that user journey, including later assertions. Never modify the test files. "
                "The frontend must build and backend npm start must serve frontend/dist using PORT.\n"
                + json.dumps(checks),
                f"repair-{attempt + 1}",
            )
        )
        checks = verify_application(tools, list(verification_command or []), task_target)
    runtime.git.commit("Verify application build and startup")
    summary = {
        "model": model_name,
        "requirements_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "variant": variant,
        "start_batch": start_batch,
        "reasoning_effort": reasoning_effort,
        "public_check_target": task_target,
        "usage": asdict(agent.usage),
        "token_accounting_complete": all(result.get("token_accounting_complete", True) for result in results),
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
        and all(result["status"] == "finished" for result in results),
    }
    (run_directory / "summary.json").write_text(tools.redact(json.dumps(summary, indent=2)), encoding="utf-8")
    if runtime:
        if summary["delivery_ready"]:
            runtime.events.mark_run_completed(
                "Application build and startup verified; official evaluation is pending. "
                f"All implementation stages finished: {summary['all_batches_finished']}"
            )
        else:
            runtime.events.mark_run_failed("Application build or startup verification failed")
    print(json.dumps({"summary": str(run_directory / "summary.json"), "usage": summary["usage"]}), flush=True)
    if not summary["delivery_ready"]:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
