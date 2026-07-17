import json
from pathlib import Path

import pytest

from trajectory_maker import workflow as workflow_module
from trajectory_maker.workflow import (
    WorkflowValidationError,
    build_workflow_task_spec,
    load_workflow,
    prepare_build_context,
)


def _turn(task_id: str, instruction: str, *, workspace: str = "workspace", base_image: str = "node:22-bookworm") -> dict:
    return {
        "task_id": task_id,
        "category": "test",
        "source": {"type": "local-folder", "ref": "./workspace"},
        "initial_instruction": instruction,
        "objective": f"objective for {task_id}",
        "input_env": {
            "dockerfile": "Dockerfile",
            "workspace": {"path": workspace},
            "base_image": base_image,
        },
        "expected_final_env": {"description": f"expected for {task_id}"},
    }


def _make_case(tmp_path: Path, turns: list[dict] | None = None) -> Path:
    case_dir = tmp_path / "case_1"
    workspace = case_dir / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("original", encoding="utf-8")
    payload = turns or [
        _turn("stage-one", "第一条\n继续"),
        _turn("stage-two", "第二条"),
        _turn("stage-three", "第三条"),
    ]
    (case_dir / "workflow.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return case_dir


def test_load_workflow_preserves_turn_order_and_resolves_workspace(tmp_path, monkeypatch):
    case_dir = _make_case(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    workflow = load_workflow(case_dir / "workflow.json")

    assert workflow.instructions == ["第一条\n继续", "第二条", "第三条"]
    assert workflow.workspace_dir == (case_dir / "workspace").resolve()
    assert [turn.task_id for turn in workflow.turns] == [
        "stage-one", "stage-two", "stage-three"
    ]


def test_load_workflow_rejects_empty_array(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "workflow.json").write_text("[]", encoding="utf-8")

    with pytest.raises(WorkflowValidationError, match="non-empty JSON array"):
        load_workflow(case_dir)


def test_load_workflow_rejects_environment_mismatch(tmp_path):
    turns = [
        _turn("stage-one", "one"),
        _turn("stage-two", "two", base_image="node:20-bookworm"),
    ]
    case_dir = _make_case(tmp_path, turns)

    with pytest.raises(WorkflowValidationError, match="different workspace or Docker"):
        load_workflow(case_dir)


def test_load_workflow_allows_repeated_stage_task_ids(tmp_path):
    turns = [
        _turn("shared-task", "one"),
        _turn("shared-task", "two"),
    ]

    workflow = load_workflow(_make_case(tmp_path, turns))

    assert [turn.task_id for turn in workflow.turns] == ["shared-task", "shared-task"]


def test_prepare_build_context_generates_dockerfile_without_touching_case(tmp_path):
    case_dir = _make_case(tmp_path)
    workflow = load_workflow(case_dir)
    destination = tmp_path / "build-context"

    prepare_build_context(workflow, destination)

    dockerfile = (destination / "Dockerfile").read_text(encoding="utf-8")
    assert 'COPY ["workspace", "/workspace"]' in dockerfile
    assert "@anthropic-ai/claude-code" in dockerfile
    assert (destination / "workspace" / "input.txt").read_text() == "original"
    assert not (case_dir / "Dockerfile").exists()
    assert (case_dir / "workspace" / "input.txt").read_text() == "original"


def test_automatic_dockerfile_rejects_alpine_base(tmp_path):
    case_dir = _make_case(tmp_path, [
        _turn("stage-one", "one", base_image="node:22-alpine"),
    ])
    workflow = load_workflow(case_dir)

    with pytest.raises(WorkflowValidationError, match="Debian-based node image"):
        prepare_build_context(workflow, tmp_path / "build-context")


def test_aggregate_spec_uses_case_id_and_first_instruction(tmp_path):
    workflow = load_workflow(_make_case(tmp_path))

    spec = build_workflow_task_spec(workflow)

    assert spec.task_id == "case-1"
    assert spec.initial_instruction == "第一条\n继续"
    assert "stage-one" in spec.objective
    assert "stage-three" in spec.objective
    assert "expected for stage-three" in spec.expected_final_env.description


def test_run_workflow_delegates_fixed_turns_to_existing_runner(tmp_path, monkeypatch):
    case_dir = _make_case(tmp_path)
    captured = {}
    expected_out = tmp_path / "dataset" / "case-1" / "run"

    def fake_run_prepared_task(**kwargs):
        captured.update(kwargs)
        assert kwargs["task_dir"].is_dir()
        assert (kwargs["task_dir"] / "Dockerfile").is_file()
        return expected_out

    monkeypatch.setattr(workflow_module, "run_prepared_task", fake_run_prepared_task)

    out = workflow_module.run_workflow(
        case_dir,
        endpoint="https://api.example.com",
        apikey="secret",
        model="model-x",
        output=tmp_path / "dataset",
    )

    assert out == expected_out
    assert captured["scripted_instructions"] == ["第一条\n继续", "第二条", "第三条"]
    assert captured["task_spec"].task_id == "case-1"
    assert captured["endpoint"] == "https://api.example.com"
    assert not (case_dir / "Dockerfile").exists()


def test_run_workflow_local_delegates_without_generating_dockerfile(tmp_path, monkeypatch):
    case_dir = _make_case(tmp_path)
    captured = {}
    expected_out = tmp_path / "dataset" / "case-1" / "run"

    def fake_local(**kwargs):
        captured.update(kwargs)
        return expected_out

    monkeypatch.setattr(workflow_module, "run_prepared_task_local", fake_local)
    monkeypatch.setattr(
        workflow_module,
        "run_prepared_task",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("local workflow must not use Docker runner")
        ),
    )

    out = workflow_module.run_workflow(
        case_dir,
        endpoint="https://aihubmix.example",
        apikey="secret",
        output=tmp_path / "dataset",
        runtime="local",
    )

    assert out == expected_out
    assert captured["task_dir"] == case_dir.resolve()
    assert captured["workspace_dir"] == (case_dir / "workspace").resolve()
    assert captured["model"] == "claude-opus-4-8"
    assert captured["scripted_instructions"] == ["第一条\n继续", "第二条", "第三条"]
    assert not (case_dir / "Dockerfile").exists()
