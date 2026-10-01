"""Lossless requirement selection and dependency-aware implementation batches."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Requirement:
    identifier: str
    name: str
    description: str
    dependencies: tuple[str, ...]
    parents: tuple[tuple[str, str, str], ...]
    scenarios: tuple[str, ...]

    @property
    def module(self) -> str:
        return self.parents[min(1, len(self.parents) - 1)][0] if self.parents else self.identifier


def load_requirements(path: Path) -> dict[str, Requirement]:
    requirements = {}

    def visit(node: dict, parents: tuple = ()) -> None:
        identifier = str(node["id"])
        if node.get("type") == "ATOMIC":
            if identifier in requirements:
                raise ValueError(f"Duplicate requirement: {identifier}")
            scenarios = tuple(
                dict.fromkeys(
                    "\n".join(f"{step['keyword']}: {step['content']}" for step in scenario.get("steps", []))
                    for scenario in node.get("scenarios", [])
                )
            )
            requirements[identifier] = Requirement(
                identifier,
                node["name"],
                node.get("description", ""),
                tuple(node.get("dependencies") or ()),
                parents,
                scenarios,
            )
        for child in node.get("children") or ():
            visit(child, parents + ((identifier, node["name"], node.get("description", "")),))

    visit(yaml.safe_load(path.read_text(encoding="utf-8")))
    if not requirements:
        raise ValueError("The requirement tree contains no ATOMIC nodes")
    ordered_requirements(requirements)
    return requirements


def ordered_requirements(requirements: dict[str, Requirement]) -> list[Requirement]:
    ordered = []
    visiting = set()
    visited = set()

    def visit(identifier: str) -> None:
        if identifier not in requirements:
            raise ValueError(f"Unknown dependency: {identifier}")
        if identifier in visiting:
            raise ValueError(f"Cyclic dependency: {identifier}")
        if identifier in visited:
            return
        visiting.add(identifier)
        for dependency in requirements[identifier].dependencies:
            visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)
        ordered.append(requirements[identifier])

    for identifier in requirements:
        visit(identifier)
    return ordered


def render_requirements(items: list[Requirement], include_scenarios: bool = True) -> str:
    sections = []
    seen_parents = set()
    seen_scenarios = set()
    for item in items:
        for identifier, name, description in item.parents:
            if identifier not in seen_parents:
                sections.append(f"## {identifier}: {name}\n{description}")
                seen_parents.add(identifier)
        sections.append(
            f"### {item.identifier}: {item.name}\n"
            f"Dependencies: {', '.join(item.dependencies) or 'none'}\n{item.description}"
        )
        if include_scenarios:
            for scenario in item.scenarios:
                if scenario and scenario not in seen_scenarios:
                    sections.append(f"Scenario for {item.identifier}:\n{scenario}")
                    seen_scenarios.add(scenario)
    return "\n\n".join(sections)


def make_batches(requirements: dict[str, Requirement], max_characters: int = 28000) -> list[list[Requirement]]:
    """Keep topological order; combine neighboring requirements sharing a module."""
    if max_characters < 1:
        raise ValueError("max_characters must be positive")
    batches = []
    current = []
    for item in ordered_requirements(requirements):
        if current and (
            item.module != current[0].module or len(render_requirements(current + [item])) > max_characters
        ):
            batches.append(current)
            current = []
        current.append(item)
    if current:
        batches.append(current)
    return batches


def requirement_index(requirements: dict[str, Requirement]) -> str:
    return "\n".join(
        f"{item.identifier}: {item.name}; depends on {', '.join(item.dependencies) or 'none'}"
        for item in ordered_requirements(requirements)
    )
