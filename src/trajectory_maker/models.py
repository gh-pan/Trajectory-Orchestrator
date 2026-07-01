"""TaskSpec data model and yaml loading."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class TaskSource(BaseModel):
    type: Literal["github", "local-folder"]
    ref: str
    commit: str | None = None


class WorkspaceSpec(BaseModel):
    path: str
    init_script: str | None = None


class InputEnv(BaseModel):
    dockerfile: str
    workspace: WorkspaceSpec
    base_image: str | None = None


class ExpectedFinalEnv(BaseModel):
    description: str
    reference_patch: str | None = None


class Rubric(BaseModel):
    id: str
    type: Literal["checklist", "script"]
    description: str
    severity: Literal["required", "preferred"] = "required"

    # checklist 字段
    criterion: str | None = None
    target_files: list[str] | None = None

    # script 字段
    run: str | None = None
    interpreter: Literal["bash", "python", "sh"] | None = None
    pass_condition: str | None = None
    pass_value: str = ""
    timeout_seconds: int = 120

    @field_validator("pass_condition")
    @classmethod
    def _validate_pass_condition(cls, v):
        if v is None:
            return v
        allowed = {"exit_zero", "output_contains", "output_matches"}
        head = v.split(":", 1)[0]
        if head not in allowed:
            raise ValueError(
                f"pass_condition must start with one of {allowed}, got '{head}'"
            )
        return v


class TaskSpec(BaseModel):
    task_id: str
    category: str
    source: TaskSource
    initial_instruction: str
    objective: str
    input_env: InputEnv
    expected_final_env: ExpectedFinalEnv
    rubrics: list[Rubric] = Field(default_factory=list)

    @field_validator("task_id")
    @classmethod
    def _validate_task_id(cls, v):
        import re

        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", v):
            raise ValueError("task_id must be kebab-case (lowercase, digits, hyphens)")
        return v

    def check_id_unique(self, parent_dir: Path) -> None:
        if (parent_dir / self.task_id).exists():
            raise TaskIdConflictError(self.task_id, parent_dir)


class TaskIdConflictError(Exception):
    def __init__(self, task_id: str, parent_dir: Path):
        self.task_id = task_id
        self.parent_dir = parent_dir
        super().__init__(
            f"task_id '{task_id}' already exists in {parent_dir}"
        )


def load_task_spec(yaml_path: Path) -> TaskSpec:
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return TaskSpec.model_validate(data)
