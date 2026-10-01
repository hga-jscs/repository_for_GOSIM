import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

MODULE_PATH = Path(__file__).resolve().parents[2] / "submission" / "requirements.py"
SPEC = importlib.util.spec_from_file_location("competition_requirements", MODULE_PATH)
requirements_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = requirements_module
SPEC.loader.exec_module(requirements_module)


def write_tree(tmp_path: Path, children: list[dict]) -> Path:
    path = tmp_path / "requirements.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "ROOT",
                "name": "Product",
                "description": "Persistence is required.",
                "children": children,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_batches_keep_dependencies_criteria_and_unique_scenarios(tmp_path: Path) -> None:
    scenario = {"steps": [{"keyword": "THEN", "content": "Refresh preserves data."}]}
    path = write_tree(
        tmp_path,
        [
            {
                "id": "MODULE",
                "name": "Account",
                "description": "Exact control names.",
                "children": [
                    {
                        "id": "B",
                        "type": "ATOMIC",
                        "name": "Login",
                        "description": "Reject bad credentials.",
                        "dependencies": ["A"],
                        "scenarios": [scenario, scenario],
                    },
                    {"id": "A", "type": "ATOMIC", "name": "Register", "description": "Store the account."},
                ],
            }
        ],
    )
    items = requirements_module.load_requirements(path)
    batches = requirements_module.make_batches(items)
    assert [[item.identifier for item in batch] for batch in batches] == [["A", "B"]]
    text = requirements_module.render_requirements(batches[0])
    assert all(item.description in text for item in items.values())
    assert text.count("Persistence is required.") == 1
    assert text.count("Refresh preserves data.") == 1
    assert len(requirements_module.make_batches(items, max_characters=1)) == 2


@pytest.mark.parametrize(
    ("children", "message"),
    [
        ([{"id": "A", "name": "A", "type": "ATOMIC", "dependencies": ["missing"]}], "Unknown dependency"),
        ([{"id": "A", "name": "A", "type": "ATOMIC", "dependencies": ["A"]}], "Cyclic dependency"),
        ([{"id": "A", "name": "A", "type": "ATOMIC"}] * 2, "Duplicate requirement"),
    ],
)
def test_invalid_graph_is_rejected(tmp_path: Path, children: list[dict], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        requirements_module.load_requirements(write_tree(tmp_path, children))
