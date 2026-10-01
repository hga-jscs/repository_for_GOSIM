import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "submission"))
from compare import rank_results


def test_accuracy_precedes_tokens_and_time() -> None:
    common = {"protocol": "same-model-and-case-hash", "outcomes": {"reload": True, "permission": True}}
    records = [
        common | {"candidate": "fast", "tokens": 10, "seconds": 1, "outcomes": {"reload": True, "permission": False}},
        common | {"candidate": "correct", "tokens": 100, "seconds": 10},
        common | {"candidate": "efficient", "tokens": 50, "seconds": 20},
        common | {"candidate": "winner", "tokens": 50, "seconds": 15},
    ]
    assert [record["candidate"] for record in rank_results(records)] == ["winner", "efficient", "correct", "fast"]


@pytest.mark.parametrize(
    ("change",),  # noqa: PT006 - AGENTS.md requires tuples for parameter names.
    [
        ({"outcomes": {"different": True}},),
        ({"outcomes": {"case": "false"}},),
        ({"protocol": "different-model"},),
        ({"tokens": -1},),
        ({"seconds": float("nan")},),
        ({"token_accounting_complete": False},),
    ],
)
def test_incomparable_or_invalid_measurements_are_rejected(change: dict) -> None:
    record = {"candidate": "a", "protocol": "p", "outcomes": {"case": True}, "tokens": 100, "seconds": 1}
    with pytest.raises(ValueError):
        rank_results([record, record | change])
