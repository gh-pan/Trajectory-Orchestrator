# 03 · Driver — stream-json 双向流

**Goal:** driver.py 抽象"一个 stream-json 双向流的 claude 进程"，支持 local（宿主子进程）与 docker（docker exec）两后端；提供事件解析、send_user_message、events 迭代。测试用 fake claude 脚本，不依赖真实 API。

**Files:**
- Create: `src/trajectory_maker/driver.py`
- Create: `tests/test_driver.py`
- Create: `tests/fixtures/fake_claude.py`
- Create: `tests/fixtures/events_result_complete.jsonl`
- Create: `tests/fixtures/events_error_auth.jsonl`

**Depends on:** 00-bootstrap、02-docker（仅 docker 后端签名依赖 DockerClient 类型，单测不实例化）

---

- [ ] **Step 1: 写 fake claude 脚本**

Create `tests/fixtures/fake_claude.py`：

```python
"""Fake claude that reads one stdin user message, then emits canned stream-json events.

Usage: python fake_claude.py <events_file>
Behaves like `claude --input-format stream-json --output-format stream-json --print`:
reads a JSON user line from stdin, then writes each line of <events_file> to stdout.
"""
import sys
from pathlib import Path


def main():
    events_file = Path(sys.argv[1])
    # consume one user message from stdin (the activation)
    sys.stdin.readline()
    for line in events_file.read_text().splitlines():
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    # mimic claude exiting after the turn
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 写 fixture 事件流（正常完成）**

Create `tests/fixtures/events_result_complete.jsonl`：

```jsonl
{"type":"system","subtype":"init","session_id":"sess-abc","cwd":"/workspace","version":"2.1.175"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"我来创建文件。"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"tu1","name":"Bash","input":{"command":"echo hello > /workspace/done.txt"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu1","content":"ok"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"任务完成，文件已创建。"}]}}
{"type":"result","subtype":"success","result":"任务完成，文件已创建。","session_id":"sess-abc"}
```

- [ ] **Step 3: 写 fixture 事件流（鉴权错误）**

Create `tests/fixtures/events_error_auth.jsonl`：

```jsonl
{"type":"system","subtype":"init","session_id":"sess-xyz","cwd":"/workspace"}
{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}
```

- [ ] **Step 4: 写失败测试 test_driver.py**

Create `tests/test_driver.py`：

```python
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
```

- [ ] **Step 5: 运行测试验证失败**

Run:
```bash
uv run pytest tests/test_driver.py -v
```
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 6: 实现 driver.py**

Create `src/trajectory_maker/driver.py`：

```python
"""Driver: a stream-json bidirectional claude code process (local or docker backend)."""

import json
import subprocess
from typing import Iterator


def parse_event(line: str) -> dict:
    """Parse one stream-json output line into a dict."""
    line = line.strip()
    if not line:
        raise ValueError("empty line")
    return json.loads(line)


def last_assistant_text(events: list[dict]) -> str | None:
    """Return the text of the last assistant message containing a text block, or None."""
    for ev in reversed(events):
        if ev.get("type") == "assistant":
            content = ev.get("message", {}).get("content", [])
            texts = [b.get("text", "") for b in content if b.get("type") == "text"]
            if texts:
                return texts[-1]
    return None


class Driver:
    """Wraps a live claude process with stdin/stdout pipes for stream-json I/O."""

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc

    @classmethod
    def local(
        cls,
        add_dirs: list[str] | None = None,
        allowed_tools: list[str] | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> "Driver":
        args = [
            "claude",
            "--print",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
        ]
        for d in add_dirs or []:
            args += ["--add-dir", d]
        for t in allowed_tools or []:
            args += ["--allowedTools", t]
        if system_prompt:
            args += ["--append-system-prompt", system_prompt]
        if model:
            args += ["--model", model]
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        return cls(proc)

    @classmethod
    def docker(
        cls,
        docker_client,
        container: str,
        env: dict[str, str] | None = None,
        add_dirs: list[str] | None = None,
        model: str | None = None,
    ) -> "Driver":
        cmd = [
            "claude",
            "--print",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
        ]
        for d in add_dirs or []:
            cmd += ["--add-dir", d]
        if model:
            cmd += ["--model", model]
        proc = docker_client.exec_pipes(container, cmd, env=env)
        return cls(proc)

    def send_user_message(self, text: str) -> None:
        """Inject a standard user turn via stdin."""
        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        }
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def events(self) -> Iterator[dict]:
        """Yield parsed events from stdout until it closes."""
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            if not line.strip():
                continue
            try:
                yield parse_event(line)
            except (json.JSONDecodeError, ValueError):
                # skip non-json lines (e.g. stray stderr bleed); keep going
                continue

    def wait(self, timeout: float | None = None) -> int:
        return self._proc.wait(timeout=timeout)

    def close(self) -> None:
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=10)
        except Exception:
            try:
                self._proc.terminate()
            except Exception:
                pass
```

- [ ] **Step 7: 运行测试验证通过**

Run:
```bash
uv run pytest tests/test_driver.py -v
```
Expected: PASS（9 passed）。

- [ ] **Step 8: 提交**

```bash
git add src/trajectory_maker/driver.py tests/test_driver.py tests/fixtures/fake_claude.py tests/fixtures/events_result_complete.jsonl tests/fixtures/events_error_auth.jsonl
git commit -m "feat: add stream-json driver with local/docker backends and event parsing"
```
