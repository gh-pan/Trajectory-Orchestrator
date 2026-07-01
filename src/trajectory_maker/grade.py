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
