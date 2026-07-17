import json
from pathlib import Path

import pytest

from trajectory_maker.sanitize import (
    SanitizeRules,
    sanitize_event,
    sanitize_jsonl,
    scan_secrets,
    load_rules,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def rules():
    return load_rules()


def test_load_rules_has_entries(rules):
    assert "ANTHROPIC_API_KEY" in rules.secret_env_keys
    assert rules.path_replacements


def test_secret_env_key_value_redacted(rules):
    ev = {"type": "assistant", "message": {"content": [{"type": "text", "text": "key=ANTHROPIC_API_KEY=sk-ant-abc123"}]}}
    out = sanitize_event(ev, rules)
    text = out["message"]["content"][0]["text"]
    assert "sk-ant-abc123" not in text
    assert "<redacted>" in text


def test_secret_pattern_redacted(rules):
    ev = {"type": "user", "message": {"content": [{"type": "text", "text": "Bearer xyz123token"}]}}
    out = sanitize_event(ev, rules)
    assert "xyz123token" not in json.dumps(out)


def test_path_normalized(rules):
    ev = {"type": "assistant", "message": {"content": [{"type": "text", "text": "edit /Users/larr/src/app.py"}]}}
    out = sanitize_event(ev, rules)
    assert "/home/user/src/app.py" in out["message"]["content"][0]["text"]
    assert "/Users/larr" not in json.dumps(out)


def test_tmp_clone_path_normalized(rules):
    ev = {"type": "user", "message": {"content": [{"type": "text", "text": "cloned /tmp/tm-clone-x7f/repo"}]}}
    out = sanitize_event(ev, rules)
    assert "/tmp/tm-clone-" not in json.dumps(out)
    assert "/workspace/repo" in out["message"]["content"][0]["text"]


def test_per_run_secret_and_local_workspace_path_are_normalized():
    secret = "custom-aihubmix-token-format"
    local_workspace = "/private/tmp/tm-local-abc/subject_workspace"
    dynamic = load_rules(
        secret_values=[secret],
        path_mappings={local_workspace: "/workspace"},
    )
    event = {
        "type": "assistant",
        "message": {"content": [{
            "type": "text",
            "text": f"read {local_workspace}/input.csv with {secret}",
        }]},
    }
    clean = sanitize_event(event, dynamic)
    text = json.dumps(clean)
    assert secret not in text
    assert local_workspace not in text
    assert "/workspace/input.csv" in text


def test_metadata_session_id_removed(rules):
    ev = {"type": "system", "subtype": "init", "session_id": "s1", "cwd": "/Users/larr/x", "version": "2.1.175", "hostname": "h"}
    out = sanitize_event(ev, rules)
    assert "session_id" not in out
    assert "hostname" not in out
    assert out["cwd"] == "/workspace"
    assert out["version"] == "2.1.175"


def test_event_structure_preserved(rules):
    ev = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}}
    out = sanitize_event(ev, rules)
    assert out["type"] == "assistant"
    assert out["message"]["content"][0]["text"] == "hello"


def test_metadata_session_id_removed_from_assistant_and_user(rules):
    """claude code attaches session_id to assistant/user events too — must be scrubbed."""
    for ev_type in ("assistant", "user"):
        ev = {"type": ev_type, "session_id": "sess-x", "message": {"role": ev_type, "content": []}}
        out = sanitize_event(ev, rules)
        assert "session_id" not in out, f"session_id leaked on {ev_type} event"


def test_cwd_only_normalized_on_system_and_result(rules):
    """cwd normalization applies to system/result; other events keep their (absent) cwd."""
    ev = {"type": "assistant", "cwd": "/Users/x", "message": {"content": []}}
    out = sanitize_event(ev, rules)
    # cwd on non-system/result event is not force-normalized, but path replacement
    # in _scrub already rewrote /Users/x -> /home/user via the string replacer.
    assert out.get("cwd") == "/home/user"


def test_sanitize_jsonl_keeps_event_count(tmp_path, rules):
    inp = FIXTURES / "trajectory_dirty.jsonl"
    outp = tmp_path / "clean.jsonl"
    report = sanitize_jsonl(inp, outp, rules)
    raw_count = sum(1 for _ in inp.open())
    clean_count = sum(1 for _ in outp.open())
    assert clean_count == raw_count
    assert report.events_in == raw_count
    assert report.events_out == raw_count


def test_sanitize_jsonl_zero_secrets_after(tmp_path, rules):
    inp = FIXTURES / "trajectory_dirty.jsonl"
    outp = tmp_path / "clean.jsonl"
    sanitize_jsonl(inp, outp, rules)
    text = outp.read_text()
    matches = scan_secrets(text, rules)
    assert matches == [], f"remaining secrets: {matches}"


def test_sanitize_jsonl_valid_jsonl(tmp_path, rules):
    inp = FIXTURES / "trajectory_dirty.jsonl"
    outp = tmp_path / "clean.jsonl"
    sanitize_jsonl(inp, outp, rules)
    for line in outp.read_text().splitlines():
        if line.strip():
            json.loads(line)
