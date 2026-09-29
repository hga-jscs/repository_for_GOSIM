from pathlib import Path

import pytest
import yaml

from minisweagent import package_dir
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicModel, make_output
from minisweagent.run.arcbench import ArcbenchTextModel, build_task, load_requirements, ordered_requirements, trajectory_metrics


REQUIREMENTS_ROOT = Path(__file__).resolve().parents[3] / "arcbench-hackathon-requirements"


@pytest.mark.parametrize(("target", "first_id"), [("github", "REQ-1-1-1"), ("sheet", "REQ-2-1-1")])
def test_real_requirements_can_be_ordered_and_packaged(target: str, first_id: str) -> None:
    items = load_requirements(REQUIREMENTS_ROOT / f"hackathon--{target}" / "requirements.yaml")
    assert first_id in items
    ordered = ordered_requirements(items, list(items), include_dependencies=True)
    positions = {item.id: index for index, item in enumerate(ordered)}
    assert len(ordered) == len(items)
    assert all(positions[dependency] < positions[item.id] for item in ordered for dependency in item.dependencies)
    task = build_task(items[first_id], items)
    assert items[first_id].name in task
    assert items[first_id].description in task


def test_metrics_count_usage_and_tool_errors() -> None:
    messages = [
        {
            "role": "assistant",
            "extra": {
                "actions": [{"command": "echo hello"}],
                "response": {"usage": {"prompt_tokens": 100, "completion_tokens": 20}},
            },
        },
        {"role": "tool", "content": "<returncode>1</returncode>"},
    ]
    assert trajectory_metrics(messages) == {
        "input_tokens": 100,
        "output_tokens": 20,
        "tool_calls": 1,
        "tool_errors": 1,
    }


def test_arcbench_config_runs_to_submission(tmp_path: Path) -> None:
    config = yaml.safe_load((Path(package_dir) / "config" / "arcbench.yaml").read_text(encoding="utf-8"))
    agent = DefaultAgent(
        DeterministicModel(outputs=[make_output("done", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}])]),
        LocalEnvironment(cwd=str(tmp_path)),
        **(config["agent"] | {"output_path": tmp_path / "trajectory.json"}),
    )
    assert agent.run("Implement a sample requirement")["exit_status"] == "Submitted"
    assert (tmp_path / "trajectory.json").is_file()


def test_file_api_key_is_absent_from_model_serialization() -> None:
    model = ArcbenchTextModel(model_name="deepseek/deepseek-chat", api_key="test-secret-value")
    assert "test-secret-value" not in str(model.serialize())
