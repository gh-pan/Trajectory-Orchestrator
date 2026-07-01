import json
import subprocess
import sys
from pathlib import Path

import pytest

from trajectory_maker.driver import Driver, parse_event, last_assistant_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_event_system():
    ev = parse_event('{"type":"system","subtype":"init","session_id":"s1","cwd":"/workspace"}')
    assert ev["type"] == "system"
    assert ev["subtype"] == "init"


def test_parse_event_assistant_with_text():
    ev = parse_event('{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"hi"}]}}')
    assert ev["type"] == "assistant"
    assert ev["message"]["content"][0]["text"] == "hi"


def test_parse_event_result():
    ev = parse_event('{"type":"result","subtype":"success","result":"done"}')
    assert ev["type"] == "result"


def test_parse_event_error():
    ev = parse_event('{"type":"error","error":{"type":"authentication_error"}}')
    assert ev["type"] == "error"
    assert ev["error"]["type"] == "authentication_error"


def test_last_assistant_text_extracts_final_text():
    events = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "任务完成"}]}},
        {"type": "result", "result": "任务完成"},
    ]
    assert last_assistant_text(events) == "任务完成"


def test_last_assistant_text_none_when_no_assistant():
    events = [{"type": "system", "subtype": "init"}]
    assert last_assistant_text(events) is None


def _fake_proc(events_file: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(FIXTURES / "fake_claude.py"), str(events_file)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def test_driver_collects_events_until_result():
    proc = _fake_proc(FIXTURES / "events_result_complete.jsonl")
    drv = Driver(proc)
    drv.send_user_message("去做任务")
    events = list(drv.events())
    drv.close()
    types = [e["type"] for e in events]
    assert "system" in types
    assert "result" in types
    assert events[-1]["type"] == "result"
    assert last_assistant_text(events) == "任务完成，文件已创建。"


def test_driver_collects_error_event():
    proc = _fake_proc(FIXTURES / "events_error_auth.jsonl")
    drv = Driver(proc)
    drv.send_user_message("去做任务")
    events = list(drv.events())
    drv.close()
    assert any(e["type"] == "error" for e in events)


def test_local_backend_builds_correct_command(monkeypatch):
    captured = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr("trajectory_maker.driver.subprocess.Popen", FakePopen)
    Driver.local(add_dirs=["/some/dir"], model="claude-sonnet-4-6")
    args = captured["args"]
    assert args[0] == "claude"
    assert "--input-format" in args and "stream-json" in args
    assert "--output-format" in args
    assert "--print" in args
    assert "--add-dir" in args and "/some/dir" in args
    assert "--model" in args and "claude-sonnet-4-6" in args


def test_docker_backend_uses_exec_pipes(monkeypatch):
    calls = {}

    class FakeDocker:
        def exec_pipes(self, container, cmd, env=None):
            calls["container"] = container
            calls["cmd"] = cmd
            calls["env"] = env
            class FakeProc:
                stdin = None
                stdout = None
                stderr = None
                def poll(self): return 0
                def wait(self, timeout=None): return 0
                def terminate(self): pass
            return FakeProc()

    drv = Driver.docker(
        FakeDocker(),
        container="tm-run-x",
        env={"ANTHROPIC_API_KEY": "k", "ANTHROPIC_MODEL": "m"},
        add_dirs=["/workspace"],
        model="m",
    )
    assert calls["container"] == "tm-run-x"
    assert "claude" in calls["cmd"]
    assert "--dangerously-skip-permissions" in calls["cmd"]
    assert calls["env"]["ANTHROPIC_API_KEY"] == "k"
