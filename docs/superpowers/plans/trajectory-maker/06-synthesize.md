# 06 · Synthesize — 阶段1 子命令

**Goal:** synthesize.py 用 local driver 驱动 claude code 读输入文件夹、生成 TaskSpec 产物；编排器校验产物结构、提取 task_id、唯一性检查、rename。可单测的部分（prompt 构建、产物校验、rename 逻辑）TDD；真实 claude 调用为 e2e。

**Files:**
- Create: `src/trajectory_maker/synthesize.py`
- Create: `src/trajectory_maker/resources/prompts/synthesize_system.md`
- Create: `tests/test_synthesize.py`
- Create: `tests/fixtures/synth_valid/` （完整产物 fixture）
- Create: `tests/fixtures/synth_missing_rubric_script/` （缺 script 文件）

**Depends on:** 01-models、03-driver

---

- [ ] **Step 1: 写 synthesize system prompt 模板**

Create `src/trajectory_maker/resources/prompts/synthesize_system.md`：

```markdown
你是 agentic 任务设计专家。严格按任务 schema 产出一份完整、自洽、可被 rubric 验证的 agentic 任务。

输出要求（全部写到指定的输出目录）：
1. `task.yaml`：字段包括 task_id(kebab-case)、category、source、initial_instruction、objective、input_env(dockerfile+workspace)、expected_final_env、rubrics(checklist 或 script)。
2. `Dockerfile`：基础镜像含 node，`npm install -g @anthropic-ai/claude-code`，`COPY workspace /workspace`，`COPY rubrics /workspace/rubrics` 并 `RUN chmod +x /workspace/rubrics/*.sh`，entrypoint 为 `tail -f /dev/null`，不内置任何 ANTHROPIC_* 凭证。rubric 脚本路径形如 `rubrics/check.sh`（grade 在容器内以 `/workspace/rubrics/check.sh` 执行）。
3. `workspace/`：任务初始文件（含可选 setup.sh）。
4. `rubrics/`：每个 script 类 rubric 引用的脚本文件，可执行。

约束：
- 任务必须源自输入文件夹的真实内容，不要凭空捏造。
- rubrics 必须能判定 objective 是否达成。
- task_id 基于内容语义生成 kebab-case。
```

- [ ] **Step 2: 写合法产物 fixture**

Create `tests/fixtures/synth_valid/task.yaml`：

```yaml
task_id: synth-demo-task
category: bugfix
source:
  type: local-folder
  ref: /tmp/demo
initial_instruction: |
  修复 bug。
objective: |
  测试通过。
input_env:
  dockerfile: Dockerfile
  workspace:
    path: workspace
    init_script: setup.sh
expected_final_env:
  description: 测试通过。
rubrics:
  - id: r1
    type: script
    run: rubrics/check.sh
    interpreter: bash
    pass_condition: exit_zero
    pass_value: ""
    timeout_seconds: 60
```

Create `tests/fixtures/synth_valid/Dockerfile`：

```dockerfile
FROM node:22
RUN npm install -g @anthropic-ai/claude-code
WORKDIR /workspace
ENTRYPOINT ["tail", "-f", "/dev/null"]
```

Create `tests/fixtures/synth_valid/workspace/setup.sh`：

```bash
#!/usr/bin/env bash
echo "setup"
```

Create `tests/fixtures/synth_valid/rubrics/check.sh`：

```bash
#!/usr/bin/env bash
echo "checked"
```

- [ ] **Step 3: 写缺 script 文件的 fixture**

复制 synth_valid 到 `tests/fixtures/synth_missing_rubric_script/`，但删除 `rubrics/check.sh`：

Run:
```bash
cp -r tests/fixtures/synth_valid tests/fixtures/synth_missing_rubric_script
rm tests/fixtures/synth_missing_rubric_script/rubrics/check.sh
```

- [ ] **Step 4: 写失败测试 test_synthesize.py**

Create `tests/test_synthesize.py`：

```python
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
```

- [ ] **Step 5: 运行测试验证失败**

Run:
```bash
uv run pytest tests/test_synthesize.py -v
```
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 6: 实现 synthesize.py**

Create `src/trajectory_maker/synthesize.py`：

```python
"""Stage 1: synthesize a TaskSpec from an input folder using a headless claude code."""

import shutil
import subprocess
from pathlib import Path

from .driver import Driver
from .models import TaskSpec, load_task_spec, TaskIdConflictError

PROMPT_FILE = Path(__file__).parent / "resources" / "prompts" / "synthesize_system.md"


class TaskDirValidationError(Exception):
    pass


def build_synthesize_prompt(input_path: str, output_dir: str) -> str:
    system = PROMPT_FILE.read_text(encoding="utf-8")
    user = (
        f"输入文件夹：{input_path}\n"
        f"输出目录：{output_dir}\n"
        "先通读输入文件夹结构，构思一个真实、自洽、可被 rubric 验证的任务，"
        "然后按 system 指示把 task.yaml / Dockerfile / workspace/ / rubrics/ 写到输出目录。"
        "完成后在最终回复中说明 task_id。"
    )
    return system + "\n\n" + user


def validate_task_dir(task_dir: Path) -> TaskSpec:
    """Structural (non-semantic) validation of synthesize output."""
    if not (task_dir / "task.yaml").exists():
        raise TaskDirValidationError(f"missing task.yaml in {task_dir}")
    if not (task_dir / "Dockerfile").exists():
        raise TaskDirValidationError(f"missing Dockerfile in {task_dir}")
    if not (task_dir / "workspace").is_dir():
        raise TaskDirValidationError(f"missing workspace/ in {task_dir}")
    spec = load_task_spec(task_dir / "task.yaml")
    for rb in spec.rubrics:
        if rb.type == "script":
            script_path = task_dir / rb.run
            if not script_path.exists():
                raise TaskDirValidationError(
                    f"script rubric {rb.id} references missing file: {rb.run}"
                )
        else:  # checklist
            if not rb.criterion:
                raise TaskDirValidationError(
                    f"checklist rubric {rb.id} missing criterion"
                )
    return spec


def finalize_task_dir(temp_dir: Path, tasks_root: Path) -> Path:
    """Validate, extract task_id, check uniqueness, rename temp_dir -> tasks_root/<task_id>."""
    spec = validate_task_dir(temp_dir)
    spec.check_id_unique(tasks_root)
    final = tasks_root / spec.task_id
    shutil.move(str(temp_dir), str(final))
    return final


def _prepare_input(input_ref: str, work_root: Path) -> tuple[Path, dict]:
    """Resolve input folder: clone github URL or use local path. Returns (path, source_meta)."""
    if input_ref.startswith("http") or input_ref.startswith("git@"):
        clone_dir = work_root / f"tm-clone-{abs(hash(input_ref)) % 10**8}"
        subprocess.run(["git", "clone", input_ref, str(clone_dir)], check=True)
        commit = subprocess.run(
            ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return clone_dir, {"type": "github", "ref": input_ref, "commit": commit}
    p = Path(input_ref).resolve()
    if not p.exists():
        raise FileNotFoundError(f"input not found: {input_ref}")
    return p, {"type": "local-folder", "ref": str(p)}


def synthesize(
    input_ref: str,
    tasks_root: Path,
    model: str | None = None,
) -> Path:
    """Run the full synthesize stage. Returns the final task dir path."""
    import uuid

    tasks_root.mkdir(parents=True, exist_ok=True)
    temp_dir = tasks_root / f"_synth_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True)
    input_path, _source_meta = _prepare_input(input_ref, temp_dir.parent)
    prompt = build_synthesize_prompt(str(input_path), str(temp_dir))
    drv = Driver.local(
        add_dirs=[str(input_path), str(temp_dir)],
        allowed_tools=["Read", "Glob", "Grep", "Write", "Bash(git clone)", "Bash(git log)"],
        model=model,
    )
    drv.send_user_message(prompt)
    # consume all events (claude writes files as tool_use side effects)
    for _ev in drv.events():
        pass
    drv.close()
    return finalize_task_dir(temp_dir, tasks_root)
```

- [ ] **Step 7: 运行测试验证通过**

Run:
```bash
uv run pytest tests/test_synthesize.py -v
```
Expected: PASS（6 passed）。

- [ ] **Step 8: 提交**

```bash
git add src/trajectory_maker/synthesize.py src/trajectory_maker/resources/prompts/synthesize_system.md tests/test_synthesize.py tests/fixtures/synth_valid tests/fixtures/synth_missing_rubric_script
git commit -m "feat: add synthesize stage (prompt, validation, finalize) with local driver"
```
