"""Run mini-SWE-agent against ArcBench's atomic product requirements."""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
import yaml
import litellm

from minisweagent import package_dir
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

app = typer.Typer(help="ArcBench requirement runner")
DEFAULT_REQUIREMENTS = Path(package_dir).parents[2] / "arcbench-hackathon-requirements"


class ArcbenchTextModel(LitellmTextbasedModel):
    def __init__(self, *, api_key: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self._api_key = api_key

    def _query(self, messages: list[dict[str, str]], **kwargs):
        options = self.config.model_kwargs | kwargs
        if self._api_key:
            options["api_key"] = self._api_key
        return litellm.completion(model=self.config.model_name, messages=messages, **options)


def read_api_key(path: Path | None) -> str | None:
    if path is None:
        return None
    if not path.is_file():
        raise typer.BadParameter(f"API key file not found: {path}")
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise typer.BadParameter("API key file is empty")
    return key


@dataclass(frozen=True)
class Requirement:
    id: str
    name: str
    description: str
    dependencies: tuple[str, ...]
    scenarios: tuple[dict, ...]
    parents: tuple[tuple[str, str], ...]


def load_requirements(path: Path) -> dict[str, Requirement]:
    root = yaml.safe_load(path.read_text(encoding="utf-8"))
    requirements: dict[str, Requirement] = {}

    def visit(node: dict, parents: tuple[tuple[str, str], ...] = ()) -> None:
        if node.get("type") == "ATOMIC":
            item = Requirement(
                id=node["id"],
                name=node["name"],
                description=node.get("description", "").strip(),
                dependencies=tuple(node.get("dependencies") or ()),
                scenarios=tuple(node.get("scenarios") or ()),
                parents=parents,
            )
            if item.id in requirements:
                raise ValueError(f"Duplicate requirement ID: {item.id}")
            requirements[item.id] = item
        for child in node.get("children") or ():
            visit(child, parents + ((node["name"], node.get("description", "").strip()),))

    visit(root)
    return requirements


def ordered_requirements(items: dict[str, Requirement], selected: list[str], include_dependencies: bool) -> list[Requirement]:
    order: list[Requirement] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(requirement_id: str) -> None:
        if requirement_id not in items:
            raise ValueError(f"Unknown requirement ID: {requirement_id}")
        if requirement_id in visiting:
            raise ValueError(f"Cyclic dependency at {requirement_id}")
        if requirement_id in visited:
            return
        visiting.add(requirement_id)
        if include_dependencies:
            for dependency in items[requirement_id].dependencies:
                visit(dependency)
        visiting.remove(requirement_id)
        visited.add(requirement_id)
        order.append(items[requirement_id])

    for requirement_id in selected:
        visit(requirement_id)
    return order


def build_task(item: Requirement, items: dict[str, Requirement], include_scenarios: bool = False) -> str:
    context = "\n".join(f"- {name}: {description}" for name, description in item.parents)
    dependencies = "\n".join(
        f"- {dependency}: {items[dependency].name}" for dependency in item.dependencies if dependency in items
    )
    task = (
        f"Implement ArcBench requirement {item.id}: {item.name}\n\n"
        f"Product context:\n{context}\n\n"
        f"Prerequisite features:\n{dependencies or 'None'}\n\n"
        f"Authoritative acceptance criteria:\n{item.description}\n"
    )
    if include_scenarios:
        task += "\nEvaluation scenarios:\n" + yaml.safe_dump(
            list(item.scenarios), allow_unicode=True, sort_keys=False
        )
    return task


def requirement_file(root: Path, target: str) -> Path:
    path = root / f"hackathon--{target}" / "requirements.yaml"
    if not path.is_file():
        raise typer.BadParameter(f"Requirement file not found: {path}")
    return path


def trajectory_metrics(messages: list[dict]) -> dict[str, int]:
    usage = [message.get("extra", {}).get("response", {}).get("usage") or {} for message in messages]
    outputs = [
        str(message.get("content", ""))
        for message in messages
        if "<returncode>" in str(message.get("content", ""))
    ]
    return {
        "input_tokens": sum(item.get("prompt_tokens") or 0 for item in usage),
        "output_tokens": sum(item.get("completion_tokens") or 0 for item in usage),
        "tool_calls": sum(len(message.get("extra", {}).get("actions", [])) for message in messages),
        "tool_errors": sum("<returncode>0</returncode>" not in str(output) for output in outputs),
    }


@app.command("list")
def list_requirements(
    target: Annotated[str, typer.Option(help="github or sheet")],
    requirements_root: Annotated[Path, typer.Option()] = DEFAULT_REQUIREMENTS,
) -> None:
    for item in load_requirements(requirement_file(requirements_root, target)).values():
        typer.echo(f"{item.id}\t{item.name}\tdeps={','.join(item.dependencies)}")


@app.command()
def prepare(
    target: Annotated[str, typer.Option(help="github or sheet")],
    requirement_id: Annotated[str, typer.Option("--id")],
    requirements_root: Annotated[Path, typer.Option()] = DEFAULT_REQUIREMENTS,
    include_scenarios: Annotated[bool, typer.Option()] = False,
) -> None:
    items = load_requirements(requirement_file(requirements_root, target))
    if requirement_id not in items:
        raise typer.BadParameter(f"Unknown requirement ID: {requirement_id}")
    typer.echo(build_task(items[requirement_id], items, include_scenarios))


@app.command()
def run(
    target: Annotated[str, typer.Option(help="github or sheet")],
    workspace: Annotated[Path, typer.Option(help="Existing application repository to edit")],
    model: Annotated[str | None, typer.Option(help="LiteLLM model name")] = None,
    api_key_file: Annotated[Path | None, typer.Option(help="Plaintext API key file outside the app repository")] = None,
    ids: Annotated[list[str] | None, typer.Option("--id", help="Repeat to select requirements")] = None,
    all_requirements: Annotated[bool, typer.Option("--all", help="Run every atomic requirement")] = False,
    requirements_root: Annotated[Path, typer.Option()] = DEFAULT_REQUIREMENTS,
    output_dir: Annotated[Path, typer.Option()] = Path("arcbench-runs"),
    variant: Annotated[str, typer.Option(help="baseline or requirements")] = "baseline",
    include_scenarios: Annotated[bool, typer.Option()] = False,
    step_limit: Annotated[int, typer.Option()] = 80,
    wall_time_limit: Annotated[int, typer.Option(help="Seconds per requirement")] = 1800,
    cost_limit: Annotated[float, typer.Option(help="USD per requirement; 0 disables")] = 0.0,
    command_timeout: Annotated[int, typer.Option(help="Seconds per shell command")] = 120,
    include_dependencies: Annotated[bool, typer.Option(help="Include prerequisite requirements")] = True,
) -> None:
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise typer.BadParameter(f"Workspace directory not found: {workspace}")
    if not (workspace / ".git").exists():
        raise typer.BadParameter("Workspace must be a Git repository so changes can be reviewed")
    if variant not in {"baseline", "requirements"}:
        raise typer.BadParameter("Variant must be baseline or requirements")
    model = model or os.getenv("MSWEA_MODEL_NAME") or "deepseek/deepseek-chat"
    if not ids and not all_requirements:
        raise typer.BadParameter("Select --id or --all")
    if ids and all_requirements:
        raise typer.BadParameter("Use --id or --all, not both")

    items = load_requirements(requirement_file(requirements_root, target))
    selected = list(items) if all_requirements else ids or []
    queue = ordered_requirements(items, selected, include_dependencies)
    config_path = Path(package_dir) / "config" / ("arcbench.yaml" if variant == "requirements" else "default.yaml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    api_key = read_api_key(api_key_file)
    run_dir = output_dir.resolve() / f"{target}-{variant}-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}"
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "target": target,
        "variant": variant,
        "model": model,
        "workspace": str(workspace),
        "requirements": str(requirement_file(requirements_root, target).resolve()),
        "ids": [item.id for item in queue],
        "step_limit": step_limit,
        "wall_time_limit": wall_time_limit,
        "cost_limit": cost_limit,
        "include_scenarios": include_scenarios,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for item in queue:
        agent = DefaultAgent(
            ArcbenchTextModel(api_key=api_key, model_name=model, cost_tracking="ignore_errors", **config.get("model", {})),
            LocalEnvironment(cwd=str(workspace), timeout=command_timeout),
            **config["agent"] | {
                "step_limit": step_limit,
                "wall_time_limit_seconds": wall_time_limit,
                "cost_limit": cost_limit,
                "output_path": run_dir / f"{item.id}.traj.json",
            },
        )
        started = time.monotonic()
        result = agent.run(build_task(item, items, include_scenarios))
        record = {
            "id": item.id,
            "exit_status": result.get("exit_status"),
            "model_calls": agent.n_calls,
            "cost_usd": agent.cost,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "trajectory": f"{item.id}.traj.json",
            **trajectory_metrics(agent.messages),
        }
        with (run_dir / "results.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        typer.echo(json.dumps(record, ensure_ascii=False))


@app.command()
def probe(
    model: Annotated[str, typer.Option(help="LiteLLM model name")] = "deepseek/deepseek-chat",
    api_key_file: Annotated[Path | None, typer.Option(help="Plaintext API key file")] = None,
) -> None:
    response = ArcbenchTextModel(model_name=model, api_key=read_api_key(api_key_file))._query(
        [{"role": "user", "content": "Reply with exactly READY."}], max_tokens=16
    )
    typer.echo(
        json.dumps(
            {
                "model": model,
                "finish_reason": response.choices[0].finish_reason,
                "input_tokens": response.usage.prompt_tokens if response.usage else None,
                "output_tokens": response.usage.completion_tokens if response.usage else None,
            }
        )
    )


@app.command()
def report(
    results: Annotated[Path, typer.Argument(help="A run directory or its results.jsonl")],
    outcomes: Annotated[Path | None, typer.Option(help="Optional JSON mapping requirement IDs to pass/fail booleans")] = None,
) -> None:
    path = results / "results.jsonl" if results.is_dir() else results
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    labels = json.loads(outcomes.read_text(encoding="utf-8")) if outcomes else {}
    evaluated = [item for item in records if item["id"] in labels]
    summary = {
        "tasks": len(records),
        "submitted": sum(item["exit_status"] == "Submitted" for item in records),
        "model_calls": sum(item["model_calls"] for item in records),
        "input_tokens": sum(item["input_tokens"] for item in records),
        "output_tokens": sum(item["output_tokens"] for item in records),
        "cost_usd": round(sum(item["cost_usd"] for item in records), 6),
        "elapsed_seconds": round(sum(item["elapsed_seconds"] for item in records), 2),
        "evaluated": len(evaluated),
        "pass_rate": sum(bool(labels[item["id"]]) for item in evaluated) / len(evaluated) if evaluated else None,
    }
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
