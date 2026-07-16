"""Tests for req_*.json sanitization (leak redaction + device_id)."""
import json
from pathlib import Path
from trajectory_maker.sanitize import sanitize_req_file, load_rules


def _write(tmp_path: Path, rec: dict) -> Path:
    p = tmp_path / "req_test.json"
    p.write_text(json.dumps(rec, ensure_ascii=False))
    return p


def test_device_id_in_metadata_user_id_redacted(tmp_path):
    rec = {
        "session_id": "s", "request_id": "req_x", "timestamp": "2026-07-16T03:33:17Z",
        "thinking_effort": "xhigh", "is_garbled": False,
        "request": {"metadata": {"user_id": json.dumps(
            {"device_id": "9623d286f74e613316cee7ae375009cb9f99098c8908d109838362f1d102447c",
             "account_uuid": "", "session_id": "s"})}},
        "response": {"response_data": {}},
    }
    p = _write(tmp_path, rec)
    sanitize_req_file(p, p, load_rules())
    out = json.loads(p.read_text())
    uid = json.loads(out["request"]["metadata"]["user_id"])
    assert uid["device_id"] == "<redacted>"


def test_leak_patterns_redacted_in_messages(tmp_path):
    rec = {
        "session_id": "s", "request_id": "req_x", "timestamp": "t",
        "thinking_effort": "xhigh", "is_garbled": False,
        "request": {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "the recording_proxy at host.docker.internal:8080"}]}]},
        "response": {"response_data": {}},
    }
    p = _write(tmp_path, rec)
    sanitize_req_file(p, p, load_rules())
    out = json.loads(p.read_text())
    text = out["request"]["messages"][0]["content"][0]["text"]
    assert "recording_proxy" not in text
    assert "host.docker.internal" not in text
    assert "<redacted>" in text


def test_apikey_redacted_in_request_body(tmp_path):
    rec = {
        "session_id": "s", "request_id": "req_x", "timestamp": "t",
        "thinking_effort": None, "is_garbled": False,
        "request": {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "ANTHROPIC_API_KEY=sk-ant-abc123"}]}]},
        "response": {"response_data": {}},
    }
    p = _write(tmp_path, rec)
    sanitize_req_file(p, p, load_rules())
    out = json.loads(p.read_text())
    assert "sk-ant-abc123" not in json.dumps(out)
