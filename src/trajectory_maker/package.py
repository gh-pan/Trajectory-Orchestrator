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


def _write_final_score(out_dir: Path, task_id: str, run_id: str, termination: str, results: list[RubricResult], summary: ScoreSummary) -> None:
    data = {
        "task_id": task_id,
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
    """The EXPECTED final environment: what the workspace should look like after
    the agent runs. This is a description (+ optional reference patch), NOT the
    grading scripts — rubrics are grading tools, not part of the expected env."""
    exp = out_dir / "expected_final_env"
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "description.txt").write_text(spec.expected_final_env.description)
    if spec.expected_final_env.reference_patch:
        (exp / "reference_patch.diff").write_text(spec.expected_final_env.reference_patch)


def _write_rubrics(out_dir: Path, task_dir: Path, spec: TaskSpec) -> None:
    """Top-level rubrics/ : the grading tools (script files + checklist
    definitions). Separate from expected_final_env — these judge the outcome,
    they are not the expected environment itself."""
    rub_dir = out_dir / "rubrics"
    rub_dir.mkdir(parents=True, exist_ok=True)
    src_rub = task_dir / "rubrics"
    if src_rub.is_dir():
        for f in src_rub.iterdir():
            shutil.copy2(f, rub_dir / f.name)
    checklist_rubrics = [r for r in spec.rubrics if r.type == "checklist"]
    if checklist_rubrics:
        (rub_dir / "checklist.yaml").write_text(yaml.safe_dump(
            [{"id": r.id, "type": r.type, "description": r.description,
              "criterion": r.criterion, "severity": r.severity} for r in checklist_rubrics],
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
    _write_final_score(out_dir, task_spec.task_id, run_id, termination, rubric_results, summary)

    # initial / actual env (copied from work_dir snapshots)
    if (work_dir / "initial_env").exists():
        shutil.copytree(work_dir / "initial_env", out_dir / "initial_env", dirs_exist_ok=True)
    else:
        (out_dir / "initial_env").mkdir()
    # initial_env also includes the Dockerfile used to build the image
    # (the /workspace snapshot doesn't contain it — it lives in task_dir).
    if (task_dir / "Dockerfile").exists():
        shutil.copy2(task_dir / "Dockerfile", out_dir / "initial_env" / "Dockerfile")
    if (work_dir / "actual_final_env").exists():
        shutil.copytree(work_dir / "actual_final_env", out_dir / "actual_final_env", dirs_exist_ok=True)
    else:
        (out_dir / "actual_final_env").mkdir()

    # expected env (description + reference patch only — NOT rubrics)
    _write_expected_env(out_dir, task_dir, task_spec)

    # rubrics (grading tools, top-level — separate from expected env)
    _write_rubrics(out_dir, task_dir, task_spec)

    # sanitized trajectory
    raw = work_dir / "trajectory_raw.jsonl"
    rules = load_rules()
    sanitize_jsonl(raw, out_dir / "trajectory.jsonl", rules)

    # integrity check: 7 entries
    entries = [p.name for p in out_dir.iterdir()]
    assert len(entries) == 7, f"expected 7 entries, got {entries}"

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
