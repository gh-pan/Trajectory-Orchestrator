# 08 · Run + Package — 阶段3 轨迹采集与打包

**Goal:** orchestrator.py 实现完成判定（扫描末轮 assistant 文本完成语义 + result 事件）；run.py 跑完整轨迹采集流程（build→run→快照→driver 采集→快照→grade→清洗→打包→销毁）；package.py 组装"一数据一目录"。可单测部分 TDD，完整 run 为 e2e。

**Files:**
- Create: `src/trajectory_maker/orchestrator.py`
- Create: `src/trajectory_maker/run.py`
- Create: `src/trajectory_maker/package.py`
- Create: `tests/test_orchestrator.py`
- Create: `tests/test_package.py`

**Depends on:** 01-models、02-docker、03-driver、04-sanitize、05-grade

---

## Part A: Orchestrator（完成判定）

- [ ] **Step 1: 写失败测试 test_orchestrator.py**

Create `tests/test_orchestrator.py`：

```python
from trajectory_maker.orchestrator import detect_termination, COMPLETION_PHRASES


def _assistant(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _result():
    return {"type": "result", "subtype": "success", "result": ""}


def test_completed_when_result_and_completion_phrase():
    events = [_assistant("我来做。"), _assistant("任务完成。"), _result()]
    assert detect_termination(events) == "completed"


def test_stopped_without_claim_when_result_no_phrase():
    events = [_assistant("我先看看。"), _result()]
    assert detect_termination(events) == "stopped_without_claim"


def test_timeout_when_no_result_and_timeout_flag():
    events = [_assistant("工作中...")]
    assert detect_termination(events, timeout=True) == "timeout"


def test_max_turns_when_no_result_and_max_turns_flag():
    events = [_assistant("工作中...")]
    assert detect_termination(events, max_turns=True) == "max_turns"


def test_crashed_when_error_event():
    events = [{"type": "error", "error": {"type": "api_error"}}]
    assert detect_termination(events) == "crashed"


def test_auth_error_takes_precedence():
    events = [{"type": "error", "error": {"type": "authentication_error"}}]
    assert detect_termination(events) == "auth_error"


def test_completion_phrases_cover_multilingual():
    for phrase in ["完成", "已完成", "任务完成", "finished", "done", "all done", "完成了"]:
        events = [_assistant(phrase), _result()]
        assert detect_termination(events) == "completed", f"phrase {phrase!r} not detected"
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
uv run pytest tests/test_orchestrator.py -v
```
Expected: FAIL（`ModuleNotFoundError: No module named 'trajectory_maker.orchestrator'`）。

- [ ] **Step 3: 实现 orchestrator.py**

Create `src/trajectory_maker/orchestrator.py`：

```python
"""Orchestrator: completion detection and (future) multi-turn injection state machine."""

from .driver import last_assistant_text

COMPLETION_PHRASES = [
    "完成", "已完成", "任务完成", "完成了",
    "finished", "done", "all done", "complete", "completed",
]

Termination = str  # completed | stopped_without_claim | timeout | max_turns | crashed | auth_error


def _has_completion_phrase(text: str) -> bool:
    low = text.lower()
    return any(p in text or p in low for p in COMPLETION_PHRASES)


def detect_termination(
    events: list[dict],
    timeout: bool = False,
    max_turns: bool = False,
) -> Termination:
    """Classify how a run ended from its collected events."""
    # auth error takes precedence (trajectory meaningless)
    for ev in events:
        if ev.get("type") == "error":
            etype = ev.get("error", {}).get("type", "")
            if "auth" in etype.lower():
                return "auth_error"
            return "crashed"
    has_result = any(ev.get("type") == "result" for ev in events)
    if not has_result:
        if timeout:
            return "timeout"
        if max_turns:
            return "max_turns"
        # process ended without result event and no explicit signal
        return "crashed"
    last_text = last_assistant_text(events) or ""
    if _has_completion_phrase(last_text):
        return "completed"
    return "stopped_without_claim"
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
uv run pytest tests/test_orchestrator.py -v
```
Expected: PASS（7 passed）。

---

## Part B: Package（数据打包）

- [ ] **Step 5: 写失败测试 test_package.py**

Create `tests/test_package.py`：

```python
import json
from pathlib import Path

import pytest

from trajectory_maker.models import load_task_spec
from trajectory_maker.grade import RubricResult, ScoreSummary
from trajectory_maker.package import package_run, build_metadata

FIXTURES = Path(__file__).parent / "fixtures"


def _make_artifacts(tmp_path):
    work = tmp_path / "work"
    (work / "initial_env" / "workspace").mkdir(parents=True)
    (work / "initial_env" / "Dockerfile").write_text("FROM node:22")
    (work / "initial_env" / "workspace" / "a.txt").write_text("init")
    (work / "actual_final_env" / "workspace").mkdir(parents=True)
    (work / "actual_final_env" / "workspace" / "done.txt").write_text("hello")
    raw = work / "trajectory_raw.jsonl"
    raw.write_text('{"type":"system","subtype":"init","session_id":"s1","cwd":"/Users/x"}\n'
                   '{"type":"result","result":"任务完成"}\n')
    return work, raw


def test_build_metadata_has_no_apikey(tmp_path):
    spec = load_task_spec(FIXTURES / "task_valid.yaml")
    md = build_metadata(
        spec, run_id="r1", endpoint="https://api.example.com",
        model="m", started_at="t0", ended_at="t1",
        termination="completed", max_turns=1, timeout_seconds=1800,
        claude_version="2.1.175", docker_base="node:22",
    )
    assert "apikey" not in json.dumps(md).lower()
    assert md["run"]["endpoint"] == "https://api.example.com"
    assert md["run"]["termination"] == "completed"
    assert md["task_id"] == spec.task_id


def test_package_run_creates_six_entries(tmp_path):
    spec = load_task_spec(FIXTURES / "task_valid.yaml")
    work, raw = _make_artifacts(tmp_path)
    results = [RubricResult(id="r1", type="script", severity="required", passed=True, exit_code=0)]
    summary = ScoreSummary(verdict="pass", score=1.0, required_pass=1, required_total=1, preferred_pass=0, preferred_total=0)
    data_root = tmp_path / "dataset"
    out_dir = package_run(
        task_spec=spec, run_id="20260701-1530a7",
        endpoint="https://api.example.com", model="m",
        started_at="t0", ended_at="t1", termination="completed",
        max_turns=1, timeout_seconds=1800, claude_version="2.1.175", docker_base="node:22",
        work_dir=work, data_root=data_root, task_dir=FIXTURES / "synth_valid",
        rubric_results=results, summary=summary,
    )
    assert (out_dir / "metadata.yaml").exists()
    assert (out_dir / "final_score.json").exists()
    assert (out_dir / "initial_env").is_dir()
    assert (out_dir / "expected_final_env").is_dir()
    assert (out_dir / "actual_final_env").is_dir()
    assert (out_dir / "trajectory.jsonl").exists()
    entries = [p.name for p in out_dir.iterdir()]
    assert len(entries) == 6


def test_package_run_writes_index(tmp_path):
    spec = load_task_spec(FIXTURES / "task_valid.yaml")
    work, raw = _make_artifacts(tmp_path)
    results = []
    summary = ScoreSummary(verdict="pass", score=1.0, required_pass=1, required_total=1, preferred_pass=0, preferred_total=0)
    data_root = tmp_path / "dataset"
    package_run(
        task_spec=spec, run_id="r1", endpoint="https://api.x", model="m",
        started_at="t0", ended_at="t1", termination="completed",
        max_turns=1, timeout_seconds=1800, claude_version="2.1.175", docker_base="node:22",
        work_dir=work, data_root=data_root, task_dir=FIXTURES / "synth_valid",
        rubric_results=results, summary=summary,
    )
    index = (data_root / "index.jsonl").read_text()
    line = json.loads(index.strip().splitlines()[-1])
    assert line["task_id"] == spec.task_id
    assert line["run_id"] == "r1"
    assert "score" in line


def test_package_trajectory_sanitized(tmp_path):
    spec = load_task_spec(FIXTURES / "task_valid.yaml")
    work, raw = _make_artifacts(tmp_path)
    results = []
    summary = ScoreSummary(verdict="pass", score=1.0, required_pass=1, required_total=1, preferred_pass=0, preferred_total=0)
    data_root = tmp_path / "dataset"
    out_dir = package_run(
        task_spec=spec, run_id="r1", endpoint="https://api.x", model="m",
        started_at="t0", ended_at="t1", termination="completed",
        max_turns=1, timeout_seconds=1800, claude_version="2.1.175", docker_base="node:22",
        work_dir=work, data_root=data_root, task_dir=FIXTURES / "synth_valid",
        rubric_results=results, summary=summary,
    )
    text = (out_dir / "trajectory.jsonl").read_text()
    assert "s1" not in text  # session_id scrubbed
    assert "/Users/x" not in text  # path normalized
```

- [ ] **Step 6: 运行测试验证失败**

Run:
```bash
uv run pytest tests/test_package.py -v
```
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 7: 实现 package.py**

Create `src/trajectory_maker/package.py`：

```python
"""Package a run's artifacts into one-data-one-dir layout."""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .grade import RubricResult, ScoreSummary
from .models import TaskSpec
from .sanitize import load_rules, sanitize_jsonl


def build_metadata(
    spec: TaskSpec, run_id: str, endpoint: str, model: str,
    started_at: str, ended_at: str, termination: str,
    max_turns: int, timeout_seconds: int,
    claude_version: str, docker_base: str,
) -> dict:
    return {
        "task_id": spec.task_id,
        "run_id": run_id,
        "category": spec.category,
        "source": spec.source.model_dump(),
        "initial_instruction": spec.initial_instruction,
        "objective": spec.objective,
        "run": {
            "endpoint": endpoint,
            "model": model,
            "started_at": started_at,
            "ended_at": ended_at,
            "termination": termination,
            "max_turns": max_turns,
            "timeout_seconds": timeout_seconds,
        },
        "toolchain": {
            "claude_code_version": claude_version,
            "docker_image_base": docker_base,
        },
        "schema_version": 1,
    }


def _write_final_score(out_dir: Path, run_id: str, termination: str, results: list[RubricResult], summary: ScoreSummary) -> None:
    data = {
        "task_id": None,
        "run_id": run_id,
        "score": summary.score,
        "verdict": summary.verdict,
        "termination": termination,
        "rubric_results": [
            {"id": r.id, "type": r.type, "severity": r.severity, "pass": r.passed,
             "reason": r.reason, "exit_code": r.exit_code, "stdout_tail": r.stdout_tail}
            for r in results
        ],
        "required_pass": summary.required_pass,
        "required_total": summary.required_total,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "final_score.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _write_expected_env(out_dir: Path, task_dir: Path, spec: TaskSpec) -> None:
    exp = out_dir / "expected_final_env"
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "description.txt").write_text(spec.expected_final_env.description)
    if spec.expected_final_env.reference_patch:
        (exp / "reference_patch.diff").write_text(spec.expected_final_env.reference_patch)
    rub_dir = exp / "rubrics"
    rub_dir.mkdir(exist_ok=True)
    src_rub = task_dir / "rubrics"
    if src_rub.is_dir():
        for f in src_rub.iterdir():
            shutil.copy2(f, rub_dir / f.name)
    (rub_dir / "checklist.yaml").write_text(yaml.safe_dump(
        [{"id": r.id, "type": r.type, "description": r.description,
          "criterion": r.criterion, "severity": r.severity} for r in spec.rubrics if r.type == "checklist"],
        allow_unicode=True))


def package_run(
    task_spec: TaskSpec, run_id: str, endpoint: str, model: str,
    started_at: str, ended_at: str, termination: str,
    max_turns: int, timeout_seconds: int,
    claude_version: str, docker_base: str,
    work_dir: Path, data_root: Path, task_dir: Path,
    rubric_results: list[RubricResult], summary: ScoreSummary,
) -> Path:
    out_dir = data_root / task_spec.task_id / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # metadata
    md = build_metadata(task_spec, run_id, endpoint, model, started_at, ended_at,
                        termination, max_turns, timeout_seconds, claude_version, docker_base)
    (out_dir / "metadata.yaml").write_text(yaml.safe_dump(md, allow_unicode=True, sort_keys=False))

    # final score
    _write_final_score(out_dir, run_id, termination, rubric_results, summary)
    fs = json.loads((out_dir / "final_score.json").read_text())
    fs["task_id"] = task_spec.task_id
    (out_dir / "final_score.json").write_text(json.dumps(fs, ensure_ascii=False, indent=2))

    # initial / actual env (copied from work_dir snapshots)
    if (work_dir / "initial_env").exists():
        shutil.copytree(work_dir / "initial_env", out_dir / "initial_env", dirs_exist_ok=True)
    else:
        (out_dir / "initial_env").mkdir()
    if (work_dir / "actual_final_env").exists():
        shutil.copytree(work_dir / "actual_final_env", out_dir / "actual_final_env", dirs_exist_ok=True)
    else:
        (out_dir / "actual_final_env").mkdir()

    # expected env
    _write_expected_env(out_dir, task_dir, task_spec)

    # sanitized trajectory
    raw = work_dir / "trajectory_raw.jsonl"
    rules = load_rules()
    sanitize_jsonl(raw, out_dir / "trajectory.jsonl", rules)

    # integrity check: 6 entries
    entries = [p.name for p in out_dir.iterdir()]
    assert len(entries) == 6, f"expected 6 entries, got {entries}"

    # index
    index_line = {
        "task_id": task_spec.task_id, "run_id": run_id,
        "category": task_spec.category, "score": summary.score,
        "verdict": summary.verdict, "termination": termination,
        "path": str(out_dir),
    }
    with (data_root / "index.jsonl").open("a") as f:
        f.write(json.dumps(index_line, ensure_ascii=False) + "\n")

    return out_dir
```

- [ ] **Step 8: 运行测试验证通过**

Run:
```bash
uv run pytest tests/test_package.py -v
```
Expected: PASS（4 passed）。

---

## Part C: run.py（完整流程编排）

- [ ] **Step 9: 实现 run.py**

Create `src/trajectory_maker/run.py`：

```python
"""Stage 3: run a task in docker, record trajectory, grade, package, destroy."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .docker import DockerClient
from .driver import Driver
from .grade import grade
from .models import load_task_spec
from .orchestrator import detect_termination
from .package import package_run


def _env(endpoint, apikey, model) -> dict[str, str]:
    env = {}
    if endpoint: env["ANTHROPIC_BASE_URL"] = endpoint
    if apikey: env["ANTHROPIC_API_KEY"] = apikey
    if model: env["ANTHROPIC_MODEL"] = model
    return env


def run(
    task_dir: Path,
    endpoint: str,
    apikey: str,
    model: str,
    output: Path = Path("./dataset"),
    max_turns: int = 1,
    timeout_seconds: int = 1800,
    keep: bool = False,
) -> Path:
    spec = load_task_spec(task_dir / "task.yaml")
    docker = DockerClient()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M") + uuid.uuid4().hex[:4]
    image_tag = f"tm-run-{spec.task_id}-{run_id}"
    container = image_tag
    work_dir = output / "_work" / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_path = work_dir / "trajectory_raw.jsonl"
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        docker.build(task_dir, image_tag)
        docker.run(image_tag, container)
        # init script
        if spec.input_env.workspace.init_script:
            docker.exec(container, ["bash", "-lc", f"cd /workspace && bash {spec.input_env.workspace.init_script}"])
        # initial env snapshot
        docker.cp_from(container, "/workspace", str(work_dir / "initial_env"))
        # rename cp result to expected layout: initial_env/workspace
        _layout_snapshot(work_dir / "initial_env")

        env = _env(endpoint, apikey, model)
        drv = Driver.docker(docker, container, env=env, add_dirs=["/workspace"], model=model)
        drv.send_user_message(spec.initial_instruction)
        events = []
        with raw_path.open("w") as f:
            for ev in drv.events():
                events.append(ev)
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        drv.close()

        # auth error short-circuit: do not package
        termination = detect_termination(events)
        if termination == "auth_error":
            raise RuntimeError("auth error during run; trajectory meaningless, not packaged")

        # actual final env snapshot BEFORE grading
        docker.cp_from(container, "/workspace", str(work_dir / "actual_final_env"))
        _layout_snapshot(work_dir / "actual_final_env")

        ended_at = datetime.now(timezone.utc).isoformat()
        # grade against live container
        grade_outcome = grade(container, docker, spec, env=env)

        # package
        from .grade import RubricResult, ScoreSummary
        results = grade_outcome.results
        summary = grade_outcome.summary
        out_dir = package_run(
            task_spec=spec, run_id=run_id, endpoint=endpoint, model=model,
            started_at=started_at, ended_at=ended_at, termination=termination,
            max_turns=max_turns, timeout_seconds=timeout_seconds,
            claude_version=_claude_version(docker, container), docker_base=spec.input_env.base_image or "",
            work_dir=work_dir, data_root=output, task_dir=task_dir,
            rubric_results=results, summary=summary,
        )
        return out_dir
    finally:
        if not keep:
            docker.stop(container)
            docker.rm(container)
            docker.rmi(image_tag)
            shutil_rmtree_safe(work_dir)


def _layout_snapshot(snap_dir: Path) -> None:
    """docker cp of /workspace puts contents directly; ensure snap_dir/workspace exists."""
    # docker cp container:/workspace dest  -> dest gets `workspace` dir
    # so dest/workspace already exists; nothing to do if so
    if not (snap_dir / "workspace").exists() and snap_dir.exists():
        # cp dumped contents into snap_dir itself; wrap them
        tmp = snap_dir.parent / (snap_dir.name + "_tmp")
        snap_dir.rename(tmp)
        snap_dir.mkdir()
        tmp.rename(snap_dir / "workspace")


def _claude_version(docker, container) -> str:
    try:
        code, out, _ = docker.exec(container, ["claude", "--version"], timeout=30)
        return out.strip() if code == 0 else "unknown"
    except Exception:
        return "unknown"


def shutil_rmtree_safe(path: Path) -> None:
    import shutil
    try:
        shutil.rmtree(path)
    except Exception:
        pass
```

- [ ] **Step 10: 运行全部单元测试确认无回归**

Run:
```bash
uv run pytest -v
```
Expected: 所有单元测试 PASS，集成/e2e skip。

- [ ] **Step 11: 提交**

```bash
git add src/trajectory_maker/orchestrator.py src/trajectory_maker/run.py src/trajectory_maker/package.py tests/test_orchestrator.py tests/test_package.py
git commit -m "feat: add run stage (completion detection, trajectory capture, grade, package, destroy)"
```
