"""Preserve prepared projects and supply the official starter for empty outputs."""

import json
import shutil
from pathlib import Path


def prepare_workspace(workspace: Path) -> None:
    if all((workspace / part / "package.json").is_file() for part in ("frontend", "backend")):
        return
    if any(path.name not in {".git", ".arc", ".gitignore"} for path in workspace.iterdir()):
        raise ValueError("Output must be empty or contain frontend/package.json and backend/package.json")
    source = Path(__file__).resolve().parent
    for name in json.loads((source / "template-files.json").read_text(encoding="utf-8")):
        if not name.startswith("template/") or name == "template/template.yaml":
            continue
        target = workspace / Path(name).relative_to("template")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
