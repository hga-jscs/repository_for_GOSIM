"""Rank comparable evaluator outcomes by accuracy, total tokens, then elapsed time."""

import json
from pathlib import Path
from typing import Annotated

import typer


def rank_results(records: list[dict]) -> list[dict]:
    if not records:
        raise ValueError("At least one evaluated result is required")
    reference_cases = set(records[0]["outcomes"])
    if not reference_cases:
        raise ValueError("Evaluation outcomes must not be empty")
    reference_protocol = records[0]["protocol"]
    for record in records:
        if set(record["outcomes"]) != reference_cases or record["protocol"] != reference_protocol:
            raise ValueError("All candidates must use the same cases, model and evaluation protocol")
        if any(type(value) is not bool for value in record["outcomes"].values()):
            raise ValueError("Every outcome must be a boolean from an evaluator")
        if type(record["tokens"]) is not int or record["tokens"] < 0:
            raise ValueError("tokens must be a nonnegative integer measured by the API")
        if not isinstance(record["seconds"], (int, float)) or not 0 <= record["seconds"] < float("inf"):
            raise ValueError("seconds must be finite and nonnegative")
    return sorted(
        records,
        key=lambda record: (
            -sum(record["outcomes"].values()),
            record["tokens"],
            record["seconds"],
        ),
    )


def main(paths: Annotated[list[Path], typer.Argument(help="Comparable evaluation result JSON files")]) -> None:
    records = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.extend(payload if isinstance(payload, list) else [payload])
    print(json.dumps(rank_results(records), indent=2))


if __name__ == "__main__":
    typer.run(main)
