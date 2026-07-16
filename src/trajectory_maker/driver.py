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
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> "Driver":
        args = [
            "claude",
            "--print",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
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
            env=env,
            cwd=cwd,
        )
        return cls(proc)

    @classmethod
    def docker(
        cls,
        docker_client,
        container: str,
        env: dict[str, str] | None = None,
        add_dirs: list[str] | None = None,
        allowed_tools: list[str] | None = None,
        model: str | None = None,
    ) -> "Driver":
        cmd = [
            "claude",
            "--print",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        for d in add_dirs or []:
            cmd += ["--add-dir", d]
        for t in allowed_tools or []:
            cmd += ["--allowedTools", t]
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

    def kill(self) -> None:
        """Hard-kill the underlying process (SIGKILL). Use from a watchdog when
        terminate() isn't enough (e.g. docker exec client ignoring SIGTERM)."""
        try:
            self._proc.kill()
        except Exception:
            pass

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
                self._proc.kill()
            except Exception:
                pass
