"""Fill missing starter files while preserving the runner's prepared workspace."""

import json
import shutil
from pathlib import Path


def prepare_workspace(workspace: Path) -> None:
    workspace = workspace.resolve()
    source = Path(__file__).resolve().parent
    if source.is_relative_to(workspace):
        raise ValueError("The output directory must not contain the Agent source")
    if workspace.exists() and not workspace.is_dir():
        raise ValueError("The output path must be a directory")
    existing = {part for part in ("frontend", "backend") if (workspace / part / "package.json").is_file()}
    if len(existing) == 2:
        return
    pending = []
    for name in json.loads((source / "template-files.json").read_text(encoding="utf-8")):
        if not name.startswith("template/") or name == "template/template.yaml":
            continue
        relative = Path(name).relative_to("template")
        if relative.parts[0] in existing:
            continue
        target = workspace / relative
        if not target.resolve().is_relative_to(workspace):
            raise ValueError(f"Template file escapes the output directory: {relative}")
        if target.exists() or target.is_symlink():
            if not target.is_file():
                raise ValueError(f"Template file conflicts with an existing path: {relative}")
            continue
        for parent in target.parents:
            if parent == workspace:
                break
            if parent.exists() and not parent.is_dir():
                raise ValueError(f"Template directory conflicts with an existing file: {parent.relative_to(workspace)}")
        pending.append((source / name, target))
    for original, target in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
