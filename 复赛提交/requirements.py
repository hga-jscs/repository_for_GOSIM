"""Lossless requirement selection and dependency-aware implementation batches."""

import re
from dataclasses import dataclass, replace
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
    external_dependencies: tuple[str, ...] = ()
    scenario_names: tuple[str, ...] = ()
    source_line: int = 0
    scenario_lines: tuple[int, ...] = ()

    @property
    def module(self) -> str:
        return self.parents[min(1, len(self.parents) - 1)][0] if self.parents else self.identifier


def stage_target(root_name: str) -> str | None:
    stage = re.fullmatch(r"(GitHub|Sheet)\s*-\s*Stage\s+[1-9][0-9]*", root_name.strip())
    return stage[1].lower() if stage else None


def load_requirements(path: Path, *, allow_external_dependencies: bool = False) -> dict[str, Requirement]:
    requirements = {}
    seen = set()
    source = path.read_text(encoding="utf-8")
    source_lines = {}

    def locate(node) -> None:
        fields = {key.value: value for key, value in node.value}
        source_lines[fields["id"].value] = (
            node.start_mark.line + 1,
            tuple(s.start_mark.line + 1 for s in fields.get("scenarios", yaml.SequenceNode("seq", [])).value),
        )
        for child in fields.get("children", yaml.SequenceNode("seq", [])).value:
            locate(child)

    locate(yaml.compose(source))

    def visit(node: dict, parents: tuple = ()) -> None:
        identifier = str(node["id"])
        if identifier in seen:
            raise ValueError(f"Duplicate requirement: {identifier}")
        seen.add(identifier)
        if node.get("type") == "ATOMIC":
            scenarios = tuple(
                "\n".join(f"{step['keyword']}: {step['content']}" for step in scenario.get("steps", []))
                for scenario in node.get("scenarios", [])
            )
            requirements[identifier] = Requirement(
                identifier,
                node["name"],
                node.get("description", ""),
                tuple(node.get("dependencies") or ()),
                parents,
                scenarios,
                scenario_names=tuple(
                    scenario.get("name", f"Scenario {number}")
                    for number, scenario in enumerate(node.get("scenarios", []), 1)
                ),
                source_line=source_lines[identifier][0],
                scenario_lines=source_lines[identifier][1],
            )
        for child in node.get("children") or ():
            visit(child, parents + ((identifier, node["name"], node.get("description", "")),))

    tree = yaml.safe_load(source)
    visit(tree)
    if not requirements:
        raise ValueError("The requirement tree contains no ATOMIC nodes")
    evolution = requirement_version(requirements) == "evolution"
    permitted = {"REQ-1-1-1", "REQ-1-3-2", "REQ-3-1-3", "REQ-4-1-1"} if evolution and public_check_target(requirements) == "sheet" else set()
    if evolution or allow_external_dependencies or stage_target(tree["name"]):
        requirements = {
            identifier: replace(
                item,
                external_dependencies=tuple(
                    dict.fromkeys(
                        dependency for dependency in item.dependencies
                        if dependency not in requirements and (not evolution or dependency in permitted)
                    )
                ),
            )
            for identifier, item in requirements.items()
        }
    ordered_requirements(requirements)
    return requirements


def external_dependency_ids(requirements: dict[str, Requirement]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(dependency for item in requirements.values() for dependency in item.external_dependencies)
    )


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
            if dependency in requirements or dependency not in requirements[identifier].external_dependencies:
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
    for item in items:
        for identifier, name, description in item.parents:
            if identifier not in seen_parents:
                sections.append(f"## {identifier}: {name}\n{description}")
                seen_parents.add(identifier)
        sections.append(
            f"### {item.identifier}: {item.name}\n"
            f"Dependencies: {', '.join(item.dependencies) or 'none'}\n{item.description}"
        )
        if item.external_dependencies:
            sections.append(
                "Dependencies outside this requirement tree (not independently verified): "
                + ", ".join(item.external_dependencies)
            )
        if include_scenarios:
            for number, scenario in enumerate(item.scenarios):
                name = item.scenario_names[number] if item.scenario_names else f"Scenario {number + 1}"
                line = item.scenario_lines[number] if item.scenario_lines else 0
                sections.append(f"Scenario for {item.identifier} [{number + 1}]: {name} (source line {line})\n{scenario}")
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


def evolution_batches(requirements: dict[str, Requirement], selected: list[str]) -> list[list[Requirement]]:
    """Visit foundational user journeys first and keep each coding task small."""
    target = public_check_target(requirements)
    priority = (
        ["REQ-1-1-1", "REQ-1-1-2", "REQ-1-2", "REQ-3-3", "REQ-4-1", "REQ-4-3-1",
         "REQ-6-2-1", "REQ-6-2-2", "REQ-6-2-3", "REQ-6-3-1"]
        if target == "github" else ["REQ-3-1-1", "REQ-1-2-2", "REQ-2-1-3"]
    )
    ordered = ordered_requirements({key: requirements[key] for key in dict.fromkeys(priority + list(requirements)) if key in requirements})
    batches = []
    for item in ordered:
        if item.identifier not in selected:
            continue
        if (not batches or len(batches[-1]) >= 3 or batches[-1][0].module != item.module
                or len(render_requirements(batches[-1] + [item])) > 12000):
            batches.append([])
        batches[-1].append(item)
    return batches


def public_check_target(requirements: dict[str, Requirement]) -> str | None:
    parents = next(iter(requirements.values())).parents
    root_name = parents[0][1] if parents else ""
    if root_name == "GitHub Collaboration Platform Core Requirements":
        return "github"
    if root_name in {
        "Core Requirements for an Online Spreadsheet Data Workspace",
        "Evolution Requirements for an Online Spreadsheet Data Workspace",
    }:
        return "sheet"
    return stage_target(root_name)


def requirement_version(requirements: dict[str, Requirement]) -> str:
    target = public_check_target(requirements)
    changed = {
        "github": {"REQ-1-1-1", "REQ-1-1-2", "REQ-2-1-2", "REQ-3-1", "REQ-4-3-1"},
        "sheet": {"REQ-1-2-2", "REQ-2-1-3", "REQ-3-1-1", "REQ-5-1-2", "REQ-5-2-1"},
    }
    introduced = {
        "github": {"REQ-1-4": "Manage Active Browser Sessions", "REQ-2-4": "View Organization Audit Log", "REQ-3-5": "Archive and Restore a Repository", "REQ-4-5": "Create and View Repository Releases", "REQ-5-5": "Add and Remove Issue Reactions"},
        "sheet": {"REQ-6-1-1": "Freeze Rows and Columns", "REQ-6-2-1": "Find and Replace Cell Text", "REQ-7-1-1": "Create and Edit Named Ranges", "REQ-7-2-1": "Create, Edit, and Delete Conditional Formatting Rules", "REQ-8-1-1": "Create, Edit, and Delete a Cell Note"},
    }
    modified = any(
        identifier in requirements
        and "Original Feature Description" in requirements[identifier].description
        and "Modified Feature Description" in requirements[identifier].description
        for identifier in changed.get(target, ())
    )
    added = any(identifier in requirements and requirements[identifier].name == name for identifier, name in introduced.get(target, {}).items())
    if target and modified and added:
        return "evolution"
    parents = next(iter(requirements.values())).parents
    if parents and stage_target(parents[0][1]):
        return "stage"
    return "baseline" if target else "custom"
