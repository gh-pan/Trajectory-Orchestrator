from pathlib import Path

import pytest

from trajectory_maker.models import (
    TaskSpec,
    load_task_spec,
    TaskIdConflictError,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_valid_task():
    spec = load_task_spec(FIXTURES / "task_valid.yaml")
    assert isinstance(spec, TaskSpec)
    assert spec.task_id == "repo-cleanup-unused-imports"
    assert spec.category == "code-refactor"
    assert spec.source.type == "github"
    assert spec.source.commit == "abc123def"
    assert spec.rubrics[0].type == "checklist"
    assert spec.rubrics[0].severity == "required"
    assert spec.rubrics[1].type == "script"
    assert spec.rubrics[1].pass_condition == "exit_zero"


def test_missing_field_raises_validation_error():
    with pytest.raises(Exception):
        load_task_spec(FIXTURES / "task_invalid_missing_field.yaml")


def test_bad_pass_condition_raises_validation_error():
    with pytest.raises(Exception):
        load_task_spec(FIXTURES / "task_invalid_bad_pass_condition.yaml")


def test_reference_patch_optional():
    spec = load_task_spec(FIXTURES / "task_valid.yaml")
    assert spec.expected_final_env.reference_patch is not None


def test_init_script_optional(tmp_path):
    yaml_text = (FIXTURES / "task_valid.yaml").read_text()
    yaml_text = yaml_text.replace("    init_script: setup.sh\n", "")
    p = tmp_path / "no_init.yaml"
    p.write_text(yaml_text)
    spec = load_task_spec(p)
    assert spec.input_env.workspace.init_script is None


def test_check_task_id_unique_raises_on_conflict(tmp_path):
    existing = tmp_path / "repo-cleanup-unused-imports"
    existing.mkdir()
    spec = load_task_spec(FIXTURES / "task_valid.yaml")
    with pytest.raises(TaskIdConflictError):
        spec.check_id_unique(tmp_path)


def test_check_task_id_unique_passes_when_absent(tmp_path):
    spec = load_task_spec(FIXTURES / "task_valid.yaml")
    spec.check_id_unique(tmp_path)
