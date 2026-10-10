"""Atomic records with bounded retries for Windows and WSL reader sharing."""

import time
from pathlib import Path


def replace_record(source: Path, destination: Path) -> None:
    for attempt in range(8):
        try:
            source.replace(destination)
            return
        except PermissionError as error:
            if (error.errno != 13 and getattr(error, "winerror", None) not in {5, 32}) or attempt == 7:
                raise
            time.sleep(0.05 * 2 ** attempt)
