from pathlib import Path

import pytest

from trajectory_maker.synthesize import (
    build_synthesize_prompt,
    validate_task_dir,
    TaskDirValidationError,
    finalize_task_dir,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_prompt_mentions_input_path():
    prompt = build_synthesize_prompt("/tmp/myinput", "/tmp/out")
    assert "/tmp/myinput" in prompt
    assert "/tmp/out" in prompt
    assert "task.yaml" in prompt


def test_validate_valid_task_dir():
    spec = validate_task_dir(FIXTURES / "synth_valid")
    assert spec.task_id == "synth-demo-task"


def test_validate_missing_rubric_script_raises():
    with pytest.raises(TaskDirValidationError):
        validate_task_dir(FIXTURES / "synth_missing_rubric_script")


def test_validate_missing_dockerfile_raises(tmp_path):
    import shutil

    d = tmp_path / "no_df"
    shutil.copytree(FIXTURES / "synth_valid", d)
    (d / "Dockerfile").unlink()
    with pytest.raises(TaskDirValidationError):
        validate_task_dir(d)


def test_finalize_renames_to_task_id_and_checks_unique(tmp_path):
    import shutil

    temp = tmp_path / "_synth_abc"
    shutil.copytree(FIXTURES / "synth_valid", temp)
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    final_path = finalize_task_dir(temp, tasks_root)
    assert final_path.name == "synth-demo-task"
    assert (tasks_root / "synth-demo-task" / "task.yaml").exists()
    assert not temp.exists()


def test_finalize_conflict_raises(tmp_path):
    import shutil

    temp = tmp_path / "_synth_abc"
    shutil.copytree(FIXTURES / "synth_valid", temp)
    tasks_root = tmp_path / "tasks"
    (tasks_root / "synth-demo-task").mkdir(parents=True)
    with pytest.raises(Exception):
        finalize_task_dir(temp, tasks_root)
