"""Incremental orchestration with versioned evidence and conservative readiness."""

import hashlib
import json
import os
import re
import time
import zipfile
from dataclasses import asdict
from pathlib import Path

from arcbench_agent_runtime import AgentRuntime
from openai import OpenAI

from agent import CodingAgent, source_fingerprint
from budget import BalanceBudget
from file_records import replace_record
from requirements import evolution_batches, external_dependency_ids, ordered_requirements, render_requirements
from tools import WorkspaceTools, bounded
from verification import python_command, verify_application

VERIFICATION_SECONDS = 1200
INCREMENTAL = """This is an EVOLUTION task on the existing platform-injected project.
Inspect the current implementation and its callers, then make local incremental changes.
Never empty/reset/replace the whole output directory, copy a baseline, or create a new skeleton.
Preserve existing files, SQLite data, IDs, metadata and working behavior. Use versioned,
idempotent migrations/seeds; restarting must preserve edits, deletions and revoked access.
First unblock startup/routes, authentication/permissions, database compatibility and the
Sheet shared write/recalculation/undo path. Then implement the requested differences.
Only public requirements and our public-source local checks are available; never discover
or access private evaluator tests. Do not change independent checks to obtain a pass.
Finish means an implementation attempt; only independent coverage proves local acceptance.
"""


def source_version() -> str:
    source = Path(__file__).resolve().parent
    files = [path for path in sorted(source.glob("*.py")) if path.name not in {"package.py", "compare.py"}] + sorted((source / "evaluation").glob("*.py")) + sorted(source.glob("evolution_*.md")) + [source / "requirements.txt"]
    files += [source / name for name in sorted(json.loads((source / "template-files.json").read_text(encoding="utf-8")))]
    return hashlib.sha256(b"".join(path.relative_to(source).as_posix().encode() + path.read_bytes() for path in files)).hexdigest()


def identity(target: str, requirement_hash: str, workspace: Path) -> dict:
    databases = {}
    for folder, directories, files in os.walk(workspace):
        directories[:] = [name for name in directories if name not in {"node_modules", ".git", "__pycache__", "dist"}]
        for name in files:
            path = Path(folder) / name
            if path.suffix in {".db", ".sqlite", ".sqlite3"} or name.endswith((".db-wal", ".sqlite-wal", ".sqlite3-wal")):
                databases[path.relative_to(workspace).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"task": target, "requirements_sha256": requirement_hash, "agent_sha256": source_version(), "application_checkpoint": source_fingerprint(workspace), "database_checkpoint": databases}


def check_resume(saved: dict, current: dict, *, selected: list[str] | None = None, variant: str | None = None) -> bool:
    return (saved.get("schema_version") == 2 and all(saved.get(key) == value for key, value in current.items())
            and (selected is None or saved.get("selected") == selected)
            and (variant is None or (saved.get("variant") == variant
                 and "failed_requirement_ids" in saved and "deferred_requirement_ids" in saved)))


def regression_scope(saved: dict, current: dict, items: dict) -> list[str]:
    if saved.get("schema_version") != 2 or any(saved.get(key) != current[key] for key in ("task", "requirements_sha256")):
        return []
    return [key for key in saved.get("attempted_requirement_ids", saved.get("selected", [])) if key in items]


def check_feedback(checks: list[dict]) -> str:
    failure_count = sum(case.get("status") == "failed" for check in checks for case in check.get("cases", []))
    detail_limit = max(160, min(2000, 18000 // max(1, failure_count)))
    summaries = []
    for check in checks:
        summary = {key: check[key] for key in ("command", "reproduction_command", "requirement_version", "returncode", "timed_out", "completed", "passed", "failed", "skipped") if key in check}
        failures = []
        for case in check.get("cases", []):
            if case.get("status") == "failed":
                failure = {key: case[key] for key in ("name", "nodeid", "file", "requirements", "scenarios") if key in case}
                failures.append(failure | {"failure": bounded(case.get("failure", ""), detail_limit)})
        if failures:
            summary["failures"] = failures
        elif check.get("returncode") or check.get("timed_out") or check.get("completed") is False:
            summary["output"] = bounded(check.get("output", ""), 5000)
        summaries.append(summary)
    return json.dumps(summaries, ensure_ascii=False)


def coverage_state(checks: list[dict], items: dict, selected: list[str]) -> dict:
    expected = {f"{identifier}#{number}" for identifier in selected for number in range(1, len(items[identifier].scenarios) + 1)}
    cases = [case for check in checks if check.get("requirement_version") == "evolution"
             for case in check.get("cases", []) if (check.get("completed") and not check.get("timed_out"))
             or case.get("source") in {"junit", "partial_progress"}]
    observed = {scenario for case in cases if case["status"] == "passed" for scenario in case.get("scenarios", [])}
    failed = {scenario for case in cases if case["status"] == "failed" for scenario in case.get("scenarios", [])}
    passed = expected & observed - failed
    required_build = {"npm --prefix frontend run build", "backend npm start + HTTP GET / on an unused port", "real browser startup and uncaught frontend errors"}
    successful = {check["command"] for check in checks if check["returncode"] == 0 and not check.get("timed_out")}
    business_checks = [check for check in checks if check.get("requirement_version") == "evolution"]
    business_ready = bool(expected) and not (expected - passed) and bool(business_checks) and all(check["returncode"] == 0 and check.get("completed") for check in business_checks) and all(check["returncode"] == 0 and not check.get("timed_out") for check in checks)
    full_scope = set(selected) == set(items)
    build_ready = required_build <= successful
    return {"generation_completed": build_ready, "generation_status": "complete" if build_ready and business_ready else "partial" if build_ready else "failed",
            "build_ready": build_ready, "business_ready": business_ready, "scope": "full" if full_scope else "selected",
            "expected_scenarios": sorted(expected), "passed_scenarios": sorted(passed), "failed_scenarios": sorted(expected & failed),
            "unverified_scenarios": sorted(expected - passed - failed), "delivery_ready": build_ready and business_ready and full_scope,
            "official_pass_rate": None}


def save(path: Path, record: dict) -> None:
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    replace_record(pending, path)


def partition_batch(batch: list, unavailable: set[str]) -> tuple[list, dict[str, list[str]]]:
    """Keep independent work while propagating blocked dependencies in topological order."""
    blocked_ids = set(unavailable)
    runnable, blocked = [], {}
    for item in batch:
        dependencies = sorted(set(item.dependencies) & blocked_ids)
        if dependencies:
            blocked[item.identifier] = dependencies
            blocked_ids.add(item.identifier)
        else:
            runnable.append(item)
    return runnable, blocked


def related_tests(target: str, identifiers: list[str]) -> list[str]:
    evaluation = Path(__file__).with_name("evaluation")
    related = []
    for path in sorted(evaluation.glob(f"test_evolution_{target}*.py")):
        content = path.read_text(encoding="utf-8")
        if any(re.search(r'["\x27]' + re.escape(identifier) + r'["\x27]', content) for identifier in identifiers):
            related.append(path.name)
    return related


def focused_notes(path: Path, batch: list) -> str:
    if not path.is_file():
        return ""
    paragraphs = path.read_text(encoding="utf-8").split("\n\n")
    words = {word.lower() for item in batch for word in item.name.split() if len(word) > 4}
    ranked = sorted(paragraphs[4:], key=lambda part: (
        sum(item.identifier in part for item in batch) * 10 + sum(word in part.lower() for word in words)
    ), reverse=True)
    return bounded("\n\n".join(paragraphs[:4] + ranked[:3]), 5000)


def case_identity(case: dict) -> str:
    return case.get("nodeid") or case.get("file", "") + "::" + case.get("name", "")


def passed_cases(checks: list[dict]) -> set[str]:
    return {case_identity(case) for check in checks for case in check.get("cases", []) if case.get("status") == "passed"}


def primary_failure(checks: list[dict]) -> list[dict]:
    for check in checks:
        if not check.get("command", "").startswith("public ") and check.get("returncode") and not check.get("timed_out"):
            return [check]
    for check in checks:
        case = next((case for case in check.get("cases", []) if case.get("status") == "failed"), None)
        if case:
            return [check | {"cases": [case]}]
    return []


def checkpoint_sources(workspace: Path, label: str) -> dict[str, bytes]:
    files = {name: (workspace / name).read_bytes() for name in source_fingerprint(workspace)}
    directory = workspace / ".agent-checkpoints"
    directory.mkdir(exist_ok=True)
    with zipfile.ZipFile(directory / f"{label}.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return files


def restore_sources(workspace: Path, files: dict[str, bytes]) -> None:
    root = workspace.resolve()
    for name in set(source_fingerprint(workspace)) | set(files):
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Checkpoint file escapes application workspace")
        if name in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[name])
        elif path.is_file():
            path.unlink()


def run_evolution(*, workspace: Path, requirement_path: Path, items: dict, target: str, key: str, endpoint: str,
                  model: str, variant: str, port: int, max_steps: int, start_batch: int, reasoning_effort: str,
                  time_limit: int, integration_time_limit: int, repair_time_limit: int, public_checks: bool,
                  batch_checks: bool, extra_commands: list[str], selected: list[str]) -> dict:
    requirement_hash = hashlib.sha256(requirement_path.read_bytes()).hexdigest()
    current = identity(target, requirement_hash, workspace)
    checkpoint = workspace / "agent-evolution.json"
    previous = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.is_file() else {}
    compatible = check_resume(previous, current, selected=selected, variant=variant)
    attempted = regression_scope(previous, current, items)
    final_scope = list(dict.fromkeys(attempted + selected))
    if start_batch > 1 and not compatible:
        raise ValueError("Resume identity or batch plan differs; restart from batch 1 to reassess the preserved application")
    evaluation = Path(__file__).with_name("evaluation")
    hints = Path(__file__).with_name(f"evolution_{target}.md")
    tools = WorkspaceTools(workspace, (key,), incremental=True, public_read_roots=(evaluation, requirement_path.parent, hints))
    directory = workspace.parent / f"{workspace.name}-runs" / str(time.time_ns())
    started = time.monotonic()
    final_deadline = started + min(23 * 3600, time_limit + integration_time_limit + repair_time_limit + VERIFICATION_SECONDS + 240)
    work_deadline = final_deadline - VERIFICATION_SECONDS - 240
    implementation_deadline = min(work_deadline, started + time_limit)
    agent = CodingAgent(OpenAI(api_key=key, base_url=endpoint, max_retries=0), model, tools, directory,
                        max_steps=max_steps, max_tokens=16384, compact=True, reasoning_effort=reasoning_effort,
                        budget=BalanceBudget.from_environment(key, endpoint, model), context_character_limit=90000)
    runtime = AgentRuntime.from_env(project_dir=str(workspace))
    runtime.git.ensure_repo()
    runtime.events.mark_run_started("Incremental generation started; local business acceptance is recorded separately")
    batches = evolution_batches(items, selected)
    if variant == "atomic":
        batches = [[item] for batch in batches for item in batch]
    if start_batch > len(batches):
        raise ValueError("start-batch exceeds selected batches")
    context = INCREMENTAL + f"\nPlatform port: {port}. Request model: {model}.\n"
    context += (f"Read-only public requirements: {requirement_path}; engineering notes (historical, inspect actual source): {hints}. "
                f"Read-only public checks: {evaluation}. Read only relevant files/line ranges. "
                "Locate the component and API handler, make a small patch, then reproduce the current scenario. "
                "A navigation link must have a working matching route and backend; check entry, action and reload. "
                "Do not run the full suite yourself; the controller reserves one final full regression. "
                "A timeout means unverified, not a demonstrated business defect. Never weaken assertions.\n")
    if target == "github":
        context += "Preserve populated databases and make seeds idempotent. Prioritize auth/permissions and reachable repository/PR list, compare, detail and mutation routes.\n"
    else:
        context += "Preserve workbooks and cells. Share the write/recalculation/undo path; verify save followed immediately by another action and reload. SPA deep links must work even in hidden output directories (sendFile dotfiles option).\n"
    results, checks, baseline_checks = [], [], []
    cache = {}
    deferred = list(previous["deferred_requirement_ids"]) if start_batch > 1 else []
    failed_requirements = set(previous["failed_requirement_ids"]) if start_batch > 1 else set()
    last_good = None
    before = source_fingerprint(workspace)
    save(directory / "input.json", current | {"selected": selected, "variant": variant, "request_model": model,
         "base_url": endpoint, "resume_compatible": compatible, "batches": [[item.identifier for item in batch] for batch in batches]})

    def verify(identifiers, *, seconds=180, files=None, full=False, startup_only=False):
        scope_key = (tuple(identifiers), tuple(files or ()), full, startup_only)
        version = identity(target, requirement_hash, workspace)
        if scope_key in cache and cache[scope_key][0] == version and all(
            not check.get("timed_out") and check.get("completed") is not False for check in cache[scope_key][1]
        ):
            print(json.dumps({"event": "verification_reused", "requirements": identifiers}), flush=True)
            return cache[scope_key][1]
        print(json.dumps({"event": "verification_started", "requirements": identifiers, "full": full, "test_files": files}), flush=True)
        checked = verify_application(tools, extra_commands if full else [], target if public_checks and not startup_only else None,
                                     identifiers, min(final_deadline, time.monotonic() + seconds), max_failures=None if full else 2,
                                     requirement_version="evolution", requirement_path=requirement_path, public_test_files=files)
        version = identity(target, requirement_hash, workspace)
        for check in checked:
            check["application_checkpoint"] = version["application_checkpoint"]
        cache[scope_key] = (version, checked)
        print(json.dumps({"event": "verification_completed", "checks": [{name: check[name] for name in
              ("command", "returncode", "passed", "failed", "skipped", "completed", "timed_out") if name in check}
              for check in checked]}, ensure_ascii=False), flush=True)
        return checked

    def record(*, final=False):
        after = source_fingerprint(workspace)
        summary = {"schema_version": 2, **identity(target, requirement_hash, workspace), "selected": selected, "variant": variant,
                   "attempted_requirement_ids": attempted, "deferred_requirement_ids": deferred,
                   "failed_requirement_ids": sorted(failed_requirements),
                   "results": results, "checks": checks, "baseline_checks": baseline_checks,
                   "request_model": model, "response_models": agent.response_models, "base_url": endpoint,
                   "usage": asdict(agent.usage), "token_accounting_complete": agent.token_accounting_complete,
                   "elapsed_seconds": round(time.monotonic() - started, 3),
                   "application_changes": [name for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)],
                   **coverage_state(checks, items, final_scope)}
        if not final:
            summary.update(generation_completed=False, generation_status="running", delivery_ready=False)
        save(directory / "summary.json", summary)
        save(workspace / "agent-summary.json", summary)
        save(checkpoint, {name: summary[name] for name in ("schema_version", *current, "selected", "variant", "attempted_requirement_ids", "failed_requirement_ids", "deferred_requirement_ids", "passed_scenarios", "unverified_scenarios")})
        return summary

    def repair_once(failures, label, deadline, batch):
        focus = primary_failure(failures)
        if not focus or agent.requests_stopped or time.monotonic() + 15 >= deadline:
            return None
        agent.deadline = deadline
        return agent.run(context + "\nFix only this first observed failure; retain other working behavior.\n"
                         + render_requirements(batch) + "\n" + focused_notes(hints, batch)
                         + "\nIndependent failure:\n" + check_feedback(focus), label, repair_checks=focus)

    # One initial snapshot and one baseline run; never repeat a cumulative pre-batch suite.
    checkpoint_sources(workspace, "initial")
    if batch_checks:
        baseline_checks = verify(final_scope, seconds=min(600, max(1, time_limit / 6)), full=True)
        checks = baseline_checks
        if coverage_state(checks, items, final_scope)["build_ready"]:
            last_good = checkpoint_sources(workspace, "working")
        record()
        foundation_files = [path.name for path in sorted(evaluation.glob(f"test_evolution_{target}*.py"))
                            if "@pytest.mark.shared" in path.read_text(encoding="utf-8")]
        foundation = [check | {"cases": [case for case in check.get("cases", [])
                      if Path(case.get("file", "")).name in foundation_files]}
                      for check in checks]
        if primary_failure(foundation):
            result = repair_once(foundation, "foundation", min(implementation_deadline, time.monotonic() + 600), [])
            if result:
                results.append({"stage": "foundation", **result})
                checks = verify(final_scope, files=foundation_files, seconds=180)
                if coverage_state(checks, items, final_scope)["build_ready"]:
                    last_good = checkpoint_sources(workspace, "working")
                record()
    for number, batch in enumerate(batches[start_batch - 1:], start_batch):
        if agent.requests_stopped or time.monotonic() + 15 >= implementation_deadline:
            break
        batch, blocked = partition_batch(batch, failed_requirements | set(deferred))
        if blocked:
            deferred.extend(identifier for identifier in blocked if identifier not in deferred)
            results.append({"batch": number, "requirements": list(blocked), "status": "deferred", "failed_dependencies": blocked})
            record()
        if not batch:
            continue
        identifiers = [item.identifier for item in batch]
        attempted = list(dict.fromkeys(attempted + identifiers))
        files = related_tests(target, identifiers)
        available = max(0, implementation_deadline - time.monotonic())
        deadline = min(implementation_deadline, time.monotonic() + max(60, available / (len(batches) - number + 1)))
        original = {name: (workspace / name).read_bytes() for name in source_fingerprint(workspace)}
        previous_passes = passed_cases(checks) | passed_cases(baseline_checks)
        for identifier in identifiers:
            runtime.events.mark_implementation_started(identifier)
        check_reserve = min(60, max(0, deadline - time.monotonic()) * 0.2) if batch_checks else 0
        agent.deadline = max(time.monotonic(), deadline - check_reserve)
        command = python_command(["-m", "pytest", "--confcutdir=" + str(evaluation),
                  *[str(evaluation / name) for name in files[:1]], "--application", str(workspace), "--target", target,
                  "--full", "--requirement-version", "evolution", "--requirements-file", str(requirement_path),
                  "--implemented", ",".join(attempted), "--browser-channel", "chromium", "--maxfail=1", "-q"])
        failures = primary_failure(checks)
        relevant_failures = primary_failure([
            check | {"cases": [case for case in check.get("cases", [])
                               if set(case.get("requirements", [])) & set(identifiers)]}
            for check in baseline_checks + checks if check.get("command", "").startswith("public ")
        ])
        task = (context + "\nComplete only these requirements, including all Given/When/Then steps:\n"
                + render_requirements(batch) + "\nRelevant engineering cautions:\n" + focused_notes(hints, batch)
                + "\nRelevant public check files: " + ", ".join(files)
                + "\nFor a focused reproduction append -k <one-test-name> to: " + command)
        if failures and not coverage_state(checks, items, final_scope)["build_ready"]:
            task += "\nResolve the startup failure before implementing features:\n" + check_feedback(failures)
        elif relevant_failures:
            task += "\nFirst related observation; reproduce on the current source before fixing:\n" + check_feedback(relevant_failures)
        result = agent.run(task, f"batch-{number}")
        entry = {"batch": number, "requirements": identifiers, **result}
        results.append(entry)
        if batch_checks:
            checks = verify(attempted, files=files, seconds=min(180, max(1, implementation_deadline - time.monotonic())))
            regressed = lambda: {case_identity(case) for check in checks for case in check.get("cases", [])
                                if case.get("status") == "failed"} & previous_passes
            state = coverage_state(checks, items, attempted)
            prerequisite_failure = any(
                set(case.get("requirements", [])) & set(identifiers)
                for check in checks for case in check.get("cases", []) if case.get("status") == "failed"
            ) and any(set(item.dependencies) & set(identifiers) for item in items.values())
            if not state["build_ready"] or regressed() or prerequisite_failure:
                repair = repair_once(checks, f"batch-{number}-recovery", min(implementation_deadline, time.monotonic() + 120), batch)
                if repair:
                    entry["recovery"] = repair
                    checks = verify(attempted, files=files, seconds=120)
                if (not coverage_state(checks, items, attempted)["build_ready"] or regressed()) and last_good is not None:
                    restore_sources(workspace, original)
                    entry["rolled_back"] = True
                    checks = verify(attempted, files=files, seconds=120)
            state = coverage_state(checks, items, attempted)
            if state["build_ready"]:
                last_good = checkpoint_sources(workspace, "working")
            entry["checks"] = checks
            passed = set(state["passed_scenarios"])
            failed = {identifier for check in checks for case in check.get("cases", []) if case.get("status") == "failed"
                      for identifier in case.get("requirements", []) if identifier in identifiers}
            for identifier in identifiers:
                expected = {f"{identifier}#{n}" for n in range(1, len(items[identifier].scenarios) + 1)}
                if expected and expected <= passed:
                    failed_requirements.discard(identifier)
                    runtime.events.mark_implementation_done(identifier, "Related public scenarios passed on this source revision")
                else:
                    if identifier in failed:
                        failed_requirements.add(identifier)
                    runtime.events.mark_implementation_failed(identifier, "Local business acceptance incomplete; candidate preserved")
        record()
    # Spend remaining work time on a concrete failure, never inject the entire backlog into one prompt.
    repair_deadline = min(work_deadline, time.monotonic() + integration_time_limit + repair_time_limit)
    for attempt in range(2):
        focus = primary_failure(checks)
        if not focus:
            break
        identifiers = list(dict.fromkeys(identifier for check in focus for case in check.get("cases", [])
                           for identifier in case.get("requirements", []) if identifier in items))[:3]
        batch = [items[identifier] for identifier in identifiers]
        prior = source_fingerprint(workspace)
        previous_source = {name: (workspace / name).read_bytes() for name in prior}
        previous_passes = passed_cases(checks)
        result = repair_once(checks, f"repair-{attempt + 1}", min(repair_deadline, time.monotonic() + 600), batch)
        if not result:
            break
        results.append({"stage": "repair", "attempt": attempt + 1, **result})
        if prior == source_fingerprint(workspace):
            break
        checks = verify(final_scope, files=related_tests(target, identifiers) if identifiers else [], seconds=240)
        regression = {case_identity(case) for check in checks for case in check.get("cases", [])
                      if case.get("status") == "failed"} & previous_passes
        if regression and last_good is not None:
            restore_sources(workspace, previous_source)
            results[-1]["rolled_back"] = True
            checks = verify(final_scope, files=related_tests(target, identifiers) if identifiers else [], seconds=240)
        if coverage_state(checks, items, final_scope)["build_ready"]:
            last_good = checkpoint_sources(workspace, "working")
        record()
    # Always reserve a genuine final build/startup and one full independent workflow pass.
    checks = verify(final_scope, full=True, seconds=max(1, min(VERIFICATION_SECONDS, final_deadline - time.monotonic() - 240)))
    if not coverage_state(checks, items, final_scope)["build_ready"] and last_good is not None:
        results.append({"stage": "restore_working_source", "rejected_checks": checks})
        restore_sources(workspace, last_good)
        checks = verify(final_scope, startup_only=True, seconds=max(1, final_deadline - time.monotonic()))
    summary = record(final=True)
    if summary["generation_completed"]:
        runtime.events.mark_run_completed("Generation completed; local business status: " + summary["generation_status"] + "; see agent-summary.json. Official score unknown")
    else:
        runtime.events.mark_run_failed("Build or startup not verified; see agent-summary.json in the output directory")
    print(json.dumps({"event": "evolution_completed", "directory": str(directory), **{name: summary[name] for name in
          ("generation_completed", "generation_status", "build_ready", "business_ready", "delivery_ready", "request_model", "response_models", "usage", "elapsed_seconds", "passed_scenarios", "failed_scenarios", "unverified_scenarios")}}, ensure_ascii=False), flush=True)
    return summary
