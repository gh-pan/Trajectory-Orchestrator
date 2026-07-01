"""Grading primitive: script rubrics (exec in container) + checklist rubrics (claude judge)."""

import json
import re
from dataclasses import dataclass
from typing import Literal

from .claude_env import build_meta_env, meta_model
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


@dataclass
class GradeOutcome:
    results: list[RubricResult]
    summary: ScoreSummary


def judge_pass_condition(condition: str, stdout: str, exit_code: int) -> bool:
    """Evaluate a script rubric's pass_condition against exec output.

    Supports inline form: 'exit_zero', 'output_contains:<substr>', 'output_matches:<regex>'.
    """
    if condition == "exit_zero":
        return exit_code == 0
    if condition.startswith("output_contains:"):
        substr = condition.split(":", 1)[1]
        return substr in stdout
    if condition.startswith("output_matches:"):
        pattern = condition.split(":", 1)[1]
        return re.search(pattern, stdout) is not None
    raise ValueError(f"unknown pass_condition: {condition}")


def _normalize_pass_condition(condition: str, pass_value: str) -> str:
    """Reconcile spec's pass_value field with inline condition form.

    If condition is a bare 'output_contains'/'output_matches' (no inline value) and pass_value
    is non-empty, build 'condition:pass_value'. Otherwise return condition unchanged.
    """
    if condition in ("output_contains", "output_matches") and pass_value:
        return f"{condition}:{pass_value}"
    return condition


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
    id: str = "",
    severity: str = "required",
) -> RubricResult:
    """Execute a script rubric inside the container and judge by pass_condition."""
    try:
        code, stdout, stderr = docker.exec(container, rubric_run_cmd, timeout=timeout_seconds)
    except Exception as e:
        return RubricResult(id=id, type="script", severity=severity, passed=False, reason=f"exec error: {e}")
    passed = judge_pass_condition(pass_condition, stdout, code)
    return RubricResult(
        id=id,
        type="script",
        severity=severity,
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
    severity: str = "required",
) -> RubricResult:
    """Run an independent read-only claude judge in the container; parse StructuredOutput from result.

    The judge is meta work — it uses the project's pinned meta endpoint (build_meta_env) rather
    than the subject agent's caller-supplied credentials, so judging stays independent of the
    agent under test and free of cc-switch leakage.
    """
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
        env=build_meta_env(in_container=True),
        add_dirs=["/workspace"],
        allowed_tools=["Read", "Glob", "Grep", "Bash(cat *)", "Bash(grep *)", "Bash(ls *)", "Bash(pyflakes *)"],
        model=meta_model(),
    )
    drv.send_user_message(system + "\n\n" + user)
    # Watchdog: kill the judge if no event for 240s (deepseek can stall on a
    # single API call). No judge should take minutes for a read-only check.
    import threading
    import time
    last_event = [time.monotonic()]
    stop = threading.Event()

    def watchdog():
        while not stop.wait(5):
            if time.monotonic() - last_event[0] > 240:
                try:
                    drv._proc.terminate()
                except Exception:
                    pass
                return

    wd = threading.Thread(target=watchdog, daemon=True)
    wd.start()
    result_text = ""
    try:
        for ev in drv.events():
            last_event[0] = time.monotonic()
            if ev.get("type") == "result":
                result_text = ev.get("result", "")
    finally:
        stop.set()
        drv.close()
    if not result_text:
        return RubricResult(id=rubric_id, type="checklist", severity=severity, passed=False, reason="judge produced no result event")
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
        severity=severity,
        passed=passed,
        reason=reason,
    )


def grade(
    container: str,
    docker,
    task_spec,
) -> GradeOutcome:
    """Grade all rubrics of a task_spec against the live container's /workspace."""
    results: list[RubricResult] = []
    for rb in task_spec.rubrics:
        if rb.type == "script":
            interp = rb.interpreter or "bash"
            cmd = [interp, "-lc", f"/workspace/{rb.run}"] if interp == "bash" else [interp, f"/workspace/{rb.run}"]
            condition = _normalize_pass_condition(rb.pass_condition, rb.pass_value or "")
            r = grade_script(
                container=container,
                docker=docker,
                rubric_run_cmd=cmd,
                pass_condition=condition,
                timeout_seconds=rb.timeout_seconds,
                id=rb.id,
                severity=rb.severity,
            )
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
                severity=rb.severity,
            )
            results.append(r)
    summary = aggregate(results)
    return GradeOutcome(results=results, summary=summary)
