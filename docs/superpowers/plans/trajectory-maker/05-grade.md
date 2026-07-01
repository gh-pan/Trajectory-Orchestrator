# 05 · Grade — 公共评分原语

**Goal:** grade.py 实现 rubric 评分：script 类直接在容器内执行（exit_zero/output_contains/output_matches）；checklist 类用独立 driver（只读工具 + 结构化输出）判定。聚合为 score/verdict。verify 与 run 共用。

**Files:**
- Create: `src/trajectory_maker/grade.py`
- Create: `tests/test_grade.py`
- Create: `tests/fixtures/fake_claude_judge.py`

**Depends on:** 01-models、02-docker、03-driver

> 注：script 类评分的 docker exec 用真实 docker 集成测试；checklist 类用 fake judge 单测（不依赖真实 API）。

---

- [ ] **Step 1: 写 fake judge 脚本（模拟 checklist 判定实例）**

Create `tests/fixtures/fake_claude_judge.py`：

```python
"""Fake judge claude: reads one user message, emits a fixed StructuredOutput-style result event.

Usage: python fake_claude_judge.py <pass> <reason>
Emits a result event whose result text contains JSON {"pass": bool, "reason": str}.
"""
import json
import sys


def main():
    do_pass = sys.argv[1] == "true"
    reason = sys.argv[2]
    sys.stdin.readline()
    payload = json.dumps({"pass": do_pass, "reason": reason})
    event = {"type": "result", "subtype": "success", "result": payload}
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 写失败测试 test_grade.py**

Create `tests/test_grade.py`：

```python
from pathlib import Path

import pytest

from trajectory_maker.grade import (
    RubricResult,
    judge_pass_condition,
    aggregate,
    grade_script,
)
from trajectory_maker.models import load_task_spec

FIXTURES = Path(__file__).parent / "fixtures"


def test_judge_exit_zero_pass():
    assert judge_pass_condition("exit_zero", "", 0) is True


def test_judge_exit_zero_fail():
    assert judge_pass_condition("exit_zero", "", 1) is False


def test_judge_output_contains_pass():
    assert judge_pass_condition("output_contains:OK", "all OK here", 0) is True


def test_judge_output_contains_fail():
    assert judge_pass_condition("output_contains:OK", "no match", 0) is False


def test_judge_output_matches_pass():
    assert judge_pass_condition("output_matches:\\d+ files", "3 files checked", 0) is True


def test_judge_output_matches_fail():
    assert judge_pass_condition("output_matches:\\d+ files", "no digits here", 0) is False


def test_aggregate_partial_when_preferred_fails():
    results = [
        RubricResult(id="r1", type="script", severity="required", passed=True),
        RubricResult(id="r2", type="checklist", severity="required", passed=True),
        RubricResult(id="r3", type="checklist", severity="preferred", passed=False),
    ]
    summary = aggregate(results)
    assert summary.verdict == "partial"
    assert summary.required_pass == 2
    assert summary.required_total == 2
    assert summary.score == pytest.approx(0.8)  # (2*1.0 + 0*0.5)/(2*1.0+1*0.5)=2.0/2.5


def test_aggregate_fail_when_required_fails():
    results = [
        RubricResult(id="r1", type="script", severity="required", passed=True),
        RubricResult(id="r2", type="checklist", severity="required", passed=False),
    ]
    summary = aggregate(results)
    assert summary.verdict == "fail"
    assert summary.required_pass == 1


def test_aggregate_pass_when_all_pass():
    results = [
        RubricResult(id="r1", type="script", severity="required", passed=True),
        RubricResult(id="r2", type="checklist", severity="preferred", passed=True),
    ]
    summary = aggregate(results)
    assert summary.verdict == "pass"
    assert summary.required_total == 1
    assert summary.required_pass == 1


def test_aggregate_partial_when_only_preferred_fails():
    results = [
        RubricResult(id="r1", type="script", severity="required", passed=True),
        RubricResult(id="r2", type="checklist", severity="preferred", passed=False),
    ]
    summary = aggregate(results)
    assert summary.verdict == "partial"
    assert summary.required_total == 1
    assert summary.required_pass == 1


@pytest.mark.integration
def test_grade_script_runs_in_container(tmp_path):
    from trajectory_maker.docker import DockerClient

    client = DockerClient()
    image_tag = "tm-grade-test"
    dfdir = tmp_path / "ctx"
    dfdir.mkdir()
    (dfdir / "Dockerfile").write_text(
        "FROM alpine:3.20\nRUN apk add --no-cache bash\nWORKDIR /workspace\n"
        "ENTRYPOINT [\"tail\",\"-f\",\"/dev/null\"]\n"
    )
    client.build(dfdir, image_tag)
    container = "tm-grade-test-run"
    try:
        client.run(image_tag, container)
        # script: exit_zero on a true echo
        result = grade_script(
            container=container,
            docker=client,
            rubric_run_cmd=["bash", "-lc", "echo OK"],
            pass_condition="output_contains:OK",
            timeout_seconds=10,
        )
        assert result.passed is True
        assert result.exit_code == 0
    finally:
        client.stop(container)
        client.rm(container)
        client.rmi(image_tag)


def test_grade_checklist_uses_driver(monkeypatch):
    from trajectory_maker import grade

    captured = {}

    class FakeDriver:
        def __init__(self, **kw):
            pass

        def send_user_message(self, text):
            captured["prompt"] = text

        def events(self):
            yield {"type": "result", "result": '{"pass": true, "reason": "all good"}'}

        def close(self):
            pass

    monkeypatch.setattr(grade, "Driver", type("D", (), {"docker": staticmethod(lambda *a, **k: FakeDriver())}))
    result = grade.grade_checklist(
        container="c",
        docker=object(),
        objective="obj",
        criterion="crit",
        rubric_id="r1",
        description="desc",
        target_files=["src/**"],
    )
    assert result.passed is True
    assert result.reason == "all good"
    assert "obj" in captured["prompt"]
    assert "crit" in captured["prompt"]
```

- [ ] **Step 3: 运行测试验证失败**

Run:
```bash
uv run pytest tests/test_grade.py -v
```
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 4: 实现 grade.py**

Create `src/trajectory_maker/grade.py`：

```python
"""Grading primitive: script rubrics (exec in container) + checklist rubrics (claude judge)."""

import json
from dataclasses import dataclass, field
from typing import Literal

from .driver import Driver


Verdict = Literal["pass", "partial", "fail"]


@dataclass
class RubricResult:
    id: str
    type: str
    severity: str
    passed: bool
    reason: str = ""
    exit_code: int | None = None
    stdout_tail: str = ""


@dataclass
class ScoreSummary:
    verdict: Verdict
    score: float
    required_pass: int
    required_total: int
    preferred_pass: int
    preferred_total: int


def judge_pass_condition(condition: str, stdout: str, exit_code: int) -> bool:
    """Evaluate a script rubric's pass_condition against exec output."""
    if condition == "exit_zero":
        return exit_code == 0
    if condition.startswith("output_contains:"):
        substr = condition.split(":", 1)[1]
        return substr in stdout
    if condition.startswith("output_matches:"):
        import re

        pattern = condition.split(":", 1)[1]
        return re.search(pattern, stdout) is not None
    raise ValueError(f"unknown pass_condition: {condition}")


def aggregate(results: list[RubricResult]) -> ScoreSummary:
    req = [r for r in results if r.severity == "required"]
    pref = [r for r in results if r.severity == "preferred"]
    req_pass = sum(1 for r in req if r.passed)
    pref_pass = sum(1 for r in pref if r.passed)
    req_total = len(req)
    pref_total = len(pref)
    weight_req = 1.0
    weight_pref = 0.5
    total_weight = req_total * weight_req + pref_total * weight_pref
    gained = req_pass * weight_req + pref_pass * weight_pref
    score = gained / total_weight if total_weight else 0.0
    if req_pass < req_total:
        verdict: Verdict = "fail"
    elif pref_total > 0 and pref_pass < pref_total:
        verdict = "partial"
    else:
        verdict = "pass"
    return ScoreSummary(
        verdict=verdict,
        score=round(score, 4),
        required_pass=req_pass,
        required_total=req_total,
        preferred_pass=pref_pass,
        preferred_total=pref_total,
    )


def grade_script(
    container: str,
    docker,
    rubric_run_cmd: list[str],
    pass_condition: str,
    timeout_seconds: int = 120,
) -> RubricResult:
    """Execute a script rubric inside the container and judge by pass_condition."""
    try:
        code, stdout, stderr = docker.exec(container, rubric_run_cmd, timeout=timeout_seconds)
    except Exception as e:
        return RubricResult(id="", type="script", severity="required", passed=False, reason=f"exec error: {e}")
    passed = judge_pass_condition(pass_condition, stdout, code)
    return RubricResult(
        id="",
        type="script",
        severity="required",
        passed=passed,
        exit_code=code,
        stdout_tail=stdout[-200:],
        reason="" if passed else f"exit={code} stderr={stderr[-200:]}",
    )


def grade_checklist(
    container: str,
    docker,
    objective: str,
    criterion: str,
    rubric_id: str,
    description: str,
    target_files: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> RubricResult:
    """Run an independent read-only claude judge in the container; parse StructuredOutput from result."""
    system = (
        "你是严格的任务验收裁判。只能读文件、跑只读诊断命令。禁止修改任何文件。"
        "最终在 result 中返回 JSON：{\"pass\": bool, \"reason\": string}。"
    )
    user = (
        f"任务 objective：{objective}\n"
        f"待判 rubric id={rubric_id}：{description}\n"
        f"判定标准：{criterion}\n"
        f"目标文件：{target_files or '全部 /workspace'}\n"
        "请在容器 /workspace 内核查，给出 pass 与 reason。"
    )
    drv = Driver.docker(
        docker,
        container=container,
        env=env,
        add_dirs=["/workspace"],
    )
    drv.send_user_message(system + "\n\n" + user)
    result_text = ""
    for ev in drv.events():
        if ev.get("type") == "result":
            result_text = ev.get("result", "")
    drv.close()
    try:
        payload = json.loads(result_text)
        passed = bool(payload.get("pass"))
        reason = str(payload.get("reason", ""))
    except (json.JSONDecodeError, ValueError):
        passed = False
        reason = f"judge returned non-JSON: {result_text[:200]}"
    return RubricResult(
        id=rubric_id,
        type="checklist",
        severity="required",
        passed=passed,
        reason=reason,
    )


def grade(
    container: str,
    docker,
    task_spec,
    env: dict[str, str] | None = None,
) -> "GradeOutcome":
    """Grade all rubrics of a task_spec against the live container's /workspace."""
    results: list[RubricResult] = []
    for rb in task_spec.rubrics:
        if rb.type == "script":
            interp = rb.interpreter or "bash"
            cmd = [interp, "-lc", f"/workspace/{rb.run}"] if interp == "bash" else [interp, f"/workspace/{rb.run}"]
            r = grade_script(
                container=container,
                docker=docker,
                rubric_run_cmd=cmd,
                pass_condition=rb.pass_condition,
                timeout_seconds=rb.timeout_seconds,
            )
            r.id = rb.id
            r.severity = rb.severity
            results.append(r)
        else:  # checklist
            r = grade_checklist(
                container=container,
                docker=docker,
                objective=task_spec.objective,
                criterion=rb.criterion or "",
                rubric_id=rb.id,
                description=rb.description,
                target_files=rb.target_files,
                env=env,
            )
            r.severity = rb.severity
            results.append(r)
    summary = aggregate(results)
    return GradeOutcome(results=results, summary=summary)


@dataclass
class GradeOutcome:
    results: list[RubricResult]
    summary: ScoreSummary
```

- [ ] **Step 5: 运行单元测试验证通过**

Run:
```bash
uv run pytest tests/test_grade.py -v
```
Expected: 单测 PASS（9 passed），1 integration skip。

- [ ] **Step 6: 运行集成测试（需 docker）**

Run:
```bash
uv run pytest tests/test_grade.py::test_grade_script_runs_in_container -v --run-integration
```
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/trajectory_maker/grade.py tests/test_grade.py tests/fixtures/fake_claude_judge.py
git commit -m "feat: add grade primitive (script exec + checklist claude judge) with score aggregation"
```
