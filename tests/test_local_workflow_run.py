from pathlib import Path

import pytest

from trajectory_maker import run as run_module
from trajectory_maker.models import TaskSpec


def _spec(rubrics=None, init_script: str | None = None) -> TaskSpec:
    payload = {
        "task_id": "case-1",
        "category": "scripted-workflow",
        "source": {"type": "local-folder", "ref": "/case"},
        "initial_instruction": "在 /workspace 开始",
        "objective": "three stages",
        "input_env": {
            "dockerfile": "Dockerfile",
            "workspace": {"path": "workspace"},
            "base_image": "node:22-bookworm",
        },
        "expected_final_env": {"description": "all three stages done"},
    }
    if rubrics is not None:
        payload["rubrics"] = rubrics
    if init_script is not None:
        payload["input_env"]["workspace"]["init_script"] = init_script
    return TaskSpec.model_validate(payload)


class FakeLocalDriver:
    def __init__(self, cwd: str, secret: str):
        self.cwd = Path(cwd)
        self.secret = secret
        self.sent: list[str] = []
        self.closed = False

    def send_user_message(self, text: str) -> None:
        self.sent.append(text)
        if len(self.sent) == 3:
            output = self.cwd / "output"
            output.mkdir()
            (output / "generated.txt").write_text("done", encoding="utf-8")

    def events(self):
        yield {
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "text",
                    "text": f"working in {self.cwd}; accidental {self.secret}",
                }]
            },
        }
        yield {"type": "result", "subtype": "success"}

    def close(self) -> None:
        self.closed = True

    def kill(self) -> None:
        pass


class FakeProxy:
    def __init__(self, endpoint, raw_calls_dir, host):
        self.endpoint = endpoint
        self.raw_calls_dir = Path(raw_calls_dir)
        self.host = host
        self.stopped = False

    def start(self):
        self.raw_calls_dir.mkdir(parents=True)
        return "http://127.0.0.1:4321"

    def stop(self):
        self.stopped = True


def test_local_prepared_run_uses_temp_copy_one_driver_and_no_docker(tmp_path, monkeypatch):
    source = tmp_path / "case" / "workspace"
    source.mkdir(parents=True)
    (source / "original.txt").write_text("unchanged", encoding="utf-8")
    output = tmp_path / "dataset"
    secret = "local-sentinel-key-that-must-not-leak"
    captured = {}

    monkeypatch.setattr(run_module.shutil, "which", lambda name: "/usr/local/bin/claude")
    monkeypatch.setattr(run_module, "_claude_version_local", lambda path: "2.1.test")
    monkeypatch.setattr(
        run_module,
        "DockerClient",
        lambda: (_ for _ in ()).throw(AssertionError("local runtime must not use Docker")),
    )

    def make_proxy(*args, **kwargs):
        proxy = FakeProxy(*args, **kwargs)
        captured["proxy"] = proxy
        return proxy

    monkeypatch.setattr(run_module, "RecordingProxy", make_proxy)

    def make_driver(cls, *args, **kwargs):
        captured["driver_kwargs"] = kwargs
        driver = FakeLocalDriver(kwargs["cwd"], secret)
        captured["driver"] = driver
        return driver

    monkeypatch.setattr(run_module.Driver, "local", classmethod(make_driver))
    expected = output / "case-1" / "fake-run"

    def fake_package(**kwargs):
        captured["package"] = kwargs
        work_dir = kwargs["work_dir"]
        captured["work_dir"] = work_dir
        assert (work_dir / "initial_env" / "workspace" / "original.txt").read_text() == "unchanged"
        assert not (work_dir / "initial_env" / "workspace" / "output").exists()
        assert (work_dir / "actual_final_env" / "workspace" / "output" / "generated.txt").read_text() == "done"
        event_text = (work_dir / "events.jsonl").read_text()
        assert secret not in event_text
        assert str(captured["driver"].cwd) not in event_text
        return expected

    monkeypatch.setattr(run_module, "package_run_multiturn", fake_package)

    out = run_module.run_prepared_task_local(
        task_spec=_spec(),
        task_dir=source.parent,
        workspace_dir=source,
        endpoint="https://aihubmix.example",
        apikey=secret,
        model="claude-opus-4-8",
        output=output,
        scripted_instructions=[
            "第一轮写入 /workspace/output",
            "第二轮读取 /workspace/output",
            "第三轮完成 /workspace/output",
        ],
        keep=False,
    )

    assert out == expected
    assert (source / "original.txt").read_text() == "unchanged"
    assert not (source / "output").exists()
    assert captured["proxy"].host == "127.0.0.1"
    assert captured["proxy"].endpoint == "https://aihubmix.example"
    assert captured["proxy"].stopped is True
    assert captured["driver"].closed is True
    assert len(captured["driver"].sent) == 3
    assert all(str(source) not in turn for turn in captured["driver"].sent)
    assert all(str(captured["driver"].cwd) in turn for turn in captured["driver"].sent)
    assert captured["driver_kwargs"]["bare"] is False
    assert captured["driver_kwargs"]["no_session_persistence"] is True
    assert captured["driver_kwargs"]["tools"] == ["Bash", "Edit", "Read"]
    assert captured["driver_kwargs"]["allowed_tools"] == ["Bash", "Edit", "Read"]
    assert "Never read or truncate text by byte count" in captured["driver_kwargs"]["system_prompt"]
    assert "under 8 KiB and 100 lines" in captured["driver_kwargs"]["system_prompt"]
    assert "automatic output truncation" in captured["driver_kwargs"]["system_prompt"]
    assert "permission_mode" not in captured["driver_kwargs"]
    assert captured["driver_kwargs"]["settings"]["sandbox"]["enabled"] is True
    assert captured["driver_kwargs"]["settings"]["sandbox"]["filesystem"]["allowRead"] == [
        str(captured["driver"].cwd),
    ]
    assert "WebFetch" in captured["driver_kwargs"]["settings"]["permissions"]["deny"]
    assert captured["driver_kwargs"]["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4321"
    assert captured["package"]["model"] == "claude-opus-4-8"
    assert captured["package"]["injected_turns"] == 2
    assert captured["package"]["summary"].verdict == "pass"
    assert captured["package"]["summary"].score == 0.0
    assert not captured["work_dir"].exists()
    assert not captured["driver"].cwd.parent.exists()


def test_local_prepared_run_rejects_rubrics_before_side_effects(tmp_path, monkeypatch):
    spec = _spec(rubrics=[{
        "id": "r1",
        "type": "script",
        "description": "check",
        "severity": "required",
        "run": "rubrics/check.sh",
        "pass_condition": "exit_zero",
    }])
    monkeypatch.setattr(
        run_module.shutil,
        "which",
        lambda name: (_ for _ in ()).throw(AssertionError("must reject before local startup")),
    )

    with pytest.raises(ValueError, match="does not yet support rubric"):
        run_module.run_prepared_task_local(
            task_spec=spec,
            task_dir=tmp_path,
            workspace_dir=tmp_path,
            endpoint="https://aihubmix.example",
            apikey="secret",
            model="claude-opus-4-8",
            output=tmp_path / "dataset",
            scripted_instructions=["one"],
        )
    assert not (tmp_path / "dataset").exists()


def test_local_prepared_run_rejects_init_script_before_side_effects(tmp_path, monkeypatch):
    monkeypatch.setattr(
        run_module.shutil,
        "which",
        lambda name: (_ for _ in ()).throw(AssertionError("must reject before local startup")),
    )
    with pytest.raises(ValueError, match="does not execute workspace init scripts"):
        run_module.run_prepared_task_local(
            task_spec=_spec(init_script="init.sh"),
            task_dir=tmp_path,
            workspace_dir=tmp_path,
            endpoint="https://aihubmix.example",
            apikey="secret",
            model="claude-opus-4-8",
            output=tmp_path / "dataset",
            scripted_instructions=["one"],
        )
    assert not (tmp_path / "dataset").exists()


def test_local_prepared_run_rejects_symlink_escape(tmp_path, monkeypatch):
    source = tmp_path / "case" / "workspace"
    source.mkdir(parents=True)
    outside = tmp_path / "host-secret.txt"
    outside.write_text("do not expose", encoding="utf-8")
    (source / "escape.txt").symlink_to(outside)
    monkeypatch.setattr(run_module.shutil, "which", lambda name: "/usr/local/bin/claude")

    with pytest.raises(ValueError, match="absolute symlink"):
        run_module.run_prepared_task_local(
            task_spec=_spec(),
            task_dir=source.parent,
            workspace_dir=source,
            endpoint="https://aihubmix.example",
            apikey="secret",
            model="claude-opus-4-8",
            output=tmp_path / "dataset",
            scripted_instructions=["one"],
        )
    assert outside.read_text(encoding="utf-8") == "do not expose"
    assert not (tmp_path / "dataset").exists()


def test_local_auth_failure_sanitizes_kept_raw_calls(tmp_path, monkeypatch):
    source = tmp_path / "case" / "workspace"
    source.mkdir(parents=True)
    secret = "aihubmix-auth-failure-sentinel"
    captured = {}

    monkeypatch.setattr(run_module.shutil, "which", lambda name: "/usr/local/bin/claude")

    class AuthProxy(FakeProxy):
        def start(self):
            self.raw_calls_dir.mkdir(parents=True)
            raw = {
                "request": {
                    "headers": {"x-api-key": "<redacted>"},
                    "body": {"messages": [{"role": "user", "content": secret}]},
                },
                "response": {"body_raw": secret},
                "request_id": "req_auth",
            }
            import json
            (self.raw_calls_dir / "req_auth.jsonl").write_text(json.dumps(raw) + "\n")
            captured["raw_calls_dir"] = self.raw_calls_dir
            return "http://127.0.0.1:4321"

    class AuthDriver:
        def send_user_message(self, text):
            pass

        def events(self):
            yield {
                "type": "error",
                "error": {"type": "authentication_error"},
                "result": secret,
            }

        def close(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(run_module, "RecordingProxy", AuthProxy)
    monkeypatch.setattr(
        run_module.Driver,
        "local",
        classmethod(lambda cls, *args, **kwargs: AuthDriver()),
    )

    with pytest.raises(RuntimeError, match="auth error"):
        run_module.run_prepared_task_local(
            task_spec=_spec(),
            task_dir=source.parent,
            workspace_dir=source,
            endpoint="https://aihubmix.example",
            apikey=secret,
            model="claude-opus-4-8",
            output=tmp_path / "dataset",
            scripted_instructions=["one"],
            keep=True,
        )

    raw_text = (captured["raw_calls_dir"] / "req_auth.jsonl").read_text()
    assert secret not in raw_text
    assert "<redacted>" in raw_text


def test_local_run_refuses_to_package_key_written_into_workspace(tmp_path, monkeypatch):
    source = tmp_path / "case" / "workspace"
    source.mkdir(parents=True)
    secret = "aihubmix-workspace-sentinel"
    monkeypatch.setattr(run_module.shutil, "which", lambda name: "/usr/local/bin/claude")
    monkeypatch.setattr(run_module, "RecordingProxy", FakeProxy)

    class SecretWritingDriver:
        def __init__(self, cwd):
            self.cwd = Path(cwd)

        def send_user_message(self, text):
            (self.cwd / "leak.txt").write_text(secret)

        def events(self):
            yield {"type": "result", "subtype": "success"}

        def close(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(
        run_module.Driver,
        "local",
        classmethod(lambda cls, *args, **kwargs: SecretWritingDriver(kwargs["cwd"])),
    )
    monkeypatch.setattr(
        run_module,
        "package_run_multiturn",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not package a key")),
    )

    with pytest.raises(RuntimeError, match="credential appeared") as excinfo:
        run_module.run_prepared_task_local(
            task_spec=_spec(),
            task_dir=source.parent,
            workspace_dir=source,
            endpoint="https://aihubmix.example",
            apikey=secret,
            model="claude-opus-4-8",
            output=tmp_path / "dataset",
            scripted_instructions=["one"],
        )
    assert secret not in str(excinfo.value)
