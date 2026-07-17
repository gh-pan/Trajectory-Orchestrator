import json
from pathlib import Path

import pytest
import yaml

from trajectory_maker.models import load_task_spec
from trajectory_maker.grade import RubricResult, ScoreSummary
from trajectory_maker.package import package_run, package_run_multiturn, build_metadata

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
    assert len(entries) == 7


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


def test_package_multiturn_preserves_req_trajectory_format(tmp_path):
    spec = load_task_spec(FIXTURES / "task_valid.yaml")
    work = tmp_path / "work"
    (work / "initial_env" / "workspace").mkdir(parents=True)
    (work / "actual_final_env" / "workspace").mkdir(parents=True)
    (work / "events.jsonl").write_text('{"type":"result"}\n')
    raw_calls = work / "raw_calls"
    raw_calls.mkdir()
    sse_events = [
        {"type": "message_start", "message": {
            "id": "msg_1", "model": "m", "type": "message", "role": "assistant",
            "content": [], "usage": {"input_tokens": 1},
        }},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]
    body_raw = "".join(f"data: {json.dumps(event)}\n\n" for event in sse_events)
    raw = {
        "request": {
            "timestamp": 0,
            "method": "POST",
            "url": "/v1/messages",
            "headers": {"x-api-key": "<redacted>"},
            "body": {"model": "m", "messages": [], "output_config": {"effort": "high"}},
        },
        "response": {"status_code": 200, "body_raw": body_raw},
        "request_id": "req_test",
    }
    (raw_calls / "req_test.jsonl").write_text(json.dumps(raw) + "\n")
    results = [RubricResult(
        id="r1", type="script", severity="required", passed=True, exit_code=0
    )]
    summary = ScoreSummary(
        verdict="pass", score=1.0, required_pass=1, required_total=1,
        preferred_pass=0, preferred_total=0,
    )

    out_dir = package_run_multiturn(
        task_spec=spec,
        run_id="workflow-run",
        session_id="session-1",
        endpoint="https://api.example.com",
        model="m",
        started_at="t0",
        ended_at="t1",
        termination="completed",
        max_turns=2,
        timeout_seconds=3600,
        injected_turns=2,
        claude_version="test",
        docker_base="node:22",
        work_dir=work,
        data_root=tmp_path / "dataset",
        task_dir=FIXTURES / "synth_valid",
        rubric_results=results,
        summary=summary,
    )

    assert {path.name for path in out_dir.iterdir()} == {
        "metadata.yaml", "final_score.json", "initial_env", "expected_final_env",
        "actual_final_env", "rubrics", "session-1", "events.jsonl",
    }
    record = json.loads((out_dir / "session-1" / "req_test.json").read_text())
    assert set(record) == {
        "session_id", "request_id", "timestamp", "thinking_effort", "is_garbled",
        "request", "response",
    }
    assert set(record["response"]) == {"response_data"}
    assert record["request"] == {
        "model": "m", "messages": [], "output_config": {"effort": "high"}
    }
    metadata = yaml.safe_load((out_dir / "metadata.yaml").read_text())
    assert metadata["schema_version"] == 2
    assert metadata["run"]["capture_mode"] == "api_call_level"
    assert metadata["run"]["injected_turns"] == 2
    index = json.loads((tmp_path / "dataset" / "index.jsonl").read_text().strip())
    assert index["session_id"] == "session-1"
    assert index["injected_turns"] == 2


def test_package_multiturn_sanitizes_events_with_per_run_values(tmp_path):
    spec = load_task_spec(FIXTURES / "task_valid.yaml")
    work = tmp_path / "work"
    (work / "initial_env" / "workspace").mkdir(parents=True)
    (work / "actual_final_env" / "workspace").mkdir(parents=True)
    (work / "raw_calls").mkdir()
    secret = "aihubmix-custom-sentinel-token"
    local_workspace = "/private/tmp/tm-local-x/subject_workspace"
    (work / "events.jsonl").write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [{
            "type": "text",
            "text": f"{local_workspace}/result.txt {secret}",
        }]},
    }) + "\n")
    summary = ScoreSummary(
        verdict="pass", score=0.0, required_pass=0, required_total=0,
        preferred_pass=0, preferred_total=0,
    )

    out_dir = package_run_multiturn(
        task_spec=spec,
        run_id="local-run",
        session_id="session-local",
        endpoint="https://aihubmix.example",
        model="claude-opus-4-8",
        started_at="t0",
        ended_at="t1",
        termination="completed",
        max_turns=2,
        timeout_seconds=3600,
        injected_turns=2,
        claude_version="test",
        docker_base="local-host",
        work_dir=work,
        data_root=tmp_path / "dataset",
        task_dir=FIXTURES / "synth_valid",
        rubric_results=[],
        summary=summary,
        secret_values=[secret],
        path_mappings={local_workspace: "/workspace"},
    )

    event_text = (out_dir / "events.jsonl").read_text()
    assert secret not in event_text
    assert local_workspace not in event_text
    assert "/workspace/result.txt" in event_text
