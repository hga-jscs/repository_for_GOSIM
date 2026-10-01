"""Create a deterministic submission archive from an explicit source allowlist."""

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Annotated

import typer

FILES = (
    "main.py",
    "agent.py",
    "model_requests.py",
    "tools.py",
    "processes.py",
    "requirements.py",
    "requirements.txt",
    "LICENSE.md",
    "scaffold.py",
    "verification.py",
    "browser_check.py",
    "server.py",
    "template-files.json",
    "SOURCES.md",
)


def package(destination: Path) -> dict:
    source = Path(__file__).resolve().parent
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite an existing archive: {destination}")
    files = FILES + tuple(json.loads((source / "template-files.json").read_text(encoding="utf-8")))
    contents = {name: (source / name).read_bytes() for name in files}
    for name in ("conftest.py", "test_browser.py", "test_github_workflows.py", "test_sheet_workflows.py"):
        contents["evaluation/" + name] = (source / "evaluation" / name).read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(contents.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
    return {
        "path": str(destination.resolve()),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "files": sorted(contents),
    }


def main(destination: Annotated[Path, typer.Argument(help="Path for a new agent ZIP archive")]) -> None:
    print(json.dumps(package(destination), indent=2))


if __name__ == "__main__":
    typer.run(main)
