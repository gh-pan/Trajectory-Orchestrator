# 01 · Models — TaskSpec pydantic 模型

**Goal:** 定义 TaskSpec 数据模型（对齐 spec 01-task-schema.md），实现 yaml 加载与校验、task_id 唯一性检查。

**Files:**
- Create: `src/trajectory_maker/models.py`
- Create: `tests/test_models.py`
- Create: `tests/fixtures/task_valid.yaml`
- Create: `tests/fixtures/task_invalid_missing_field.yaml`
- Create: `tests/fixtures/task_invalid_bad_pass_condition.yaml`

**Depends on:** 00-bootstrap

---

- [ ] **Step 1: 写合法 fixture task_valid.yaml**

Create `tests/fixtures/task_valid.yaml`：

```yaml
task_id: repo-cleanup-unused-imports
category: code-refactor
source:
  type: github
  ref: https://github.com/acme/widget
  commit: abc123def

initial_instruction: |
  请清理 src/ 下所有 Python 文件中未使用的 import，并移除因此产生的空行。
objective: |
  全部 src/*.py 文件中无未使用 import；不改变任何运行逻辑；py_compile 通过。

input_env:
  dockerfile: Dockerfile
  workspace:
    path: workspace
    init_script: setup.sh
  base_image: node:22

expected_final_env:
  description: |
    src/ 下所有 .py 经 lint 检查无 unused-import；py_compile 全通过；无逻辑改动。
  reference_patch: |
    diff --git a/src/app.py b/src/app.py

rubrics:
  - id: r1
    type: checklist
    description: 不存在未使用的 import
    criterion: "src/**/*.py 中任意文件经 pyflakes 检查无 'unused import' 报错"
    target_files: ["src/**/*.py"]
    severity: required
  - id: r2
    type: script
    description: py_compile 全通过
    run: rubrics/py_compile_check.sh
    interpreter: bash
    pass_condition: exit_zero
    pass_value: ""
    timeout_seconds: 120
```

- [ ] **Step 2: 写非法 fixture（缺字段）**

Create `tests/fixtures/task_invalid_missing_field.yaml`：

```yaml
task_id: bad-no-objective
category: bugfix
source:
  type: local-folder
  ref: /tmp/somefolder
initial_instruction: |
  做点事。
# 缺 objective
input_env:
  dockerfile: Dockerfile
  workspace:
    path: workspace
rubrics:
  - id: r1
    type: script
    run: rubrics/check.sh
    interpreter: bash
    pass_condition: exit_zero
    pass_value: ""
    timeout_seconds: 60
```

- [ ] **Step 3: 写非法 fixture（坏 pass_condition）**

Create `tests/fixtures/task_invalid_bad_pass_condition.yaml`：

```yaml
task_id: bad-pass-condition
category: bugfix
source:
  type: local-folder
  ref: /tmp/x
initial_instruction: x
objective: x
input_env:
  dockerfile: Dockerfile
  workspace:
    path: workspace
rubrics:
  - id: r1
    type: script
    run: rubrics/check.sh
    interpreter: bash
    pass_condition: invalid_condition
    pass_value: ""
    timeout_seconds: 60
```

- [ ] **Step 4: 写失败测试 test_models.py**

Create `tests/test_models.py`：

```python
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
```

- [ ] **Step 5: 运行测试验证失败**

Run:
```bash
uv run pytest tests/test_models.py -v
```
Expected: FAIL（`ModuleNotFoundError: No module named 'trajectory_maker.models'`）。

- [ ] **Step 6: 实现 models.py**

Create `src/trajectory_maker/models.py`：

```python
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
```

- [ ] **Step 7: 运行测试验证通过**

Run:
```bash
uv run pytest tests/test_models.py -v
```
Expected: PASS（7 passed）。

- [ ] **Step 8: 提交**

```bash
git add src/trajectory_maker/models.py tests/test_models.py tests/fixtures/task_valid.yaml tests/fixtures/task_invalid_missing_field.yaml tests/fixtures/task_invalid_bad_pass_condition.yaml
git commit -m "feat: add TaskSpec pydantic model with yaml loading and uniqueness check"
```
