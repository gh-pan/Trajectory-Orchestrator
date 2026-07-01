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
