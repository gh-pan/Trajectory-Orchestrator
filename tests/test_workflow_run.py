from pathlib import Path

import pytest

from trajectory_maker.grade import GradeOutcome, ScoreSummary
from trajectory_maker.models import TaskSpec
from trajectory_maker import run as run_module


class FakeDocker:
    def __init__(self):
        self.built = []
        self.ran = []
        self.snapshots = []

    def build(self, task_dir, image_tag):
        self.built.append((Path(task_dir), image_tag))

    def run(self, image_tag, container, add_hosts=None):
        self.ran.append((image_tag, container, add_hosts))

    def exec(self, container, command, timeout=None):
        return 0, "claude test", ""

    def cp_from(self, container, source, destination):
        destination = Path(destination)
        destination.mkdir(parents=True)
        (destination / "state.txt").write_text(str(len(self.snapshots)))
        self.snapshots.append((container, source, destination))

    def stop(self, container):
        pass

    def rm(self, container):
        pass

    def rmi(self, image_tag):
        pass


class FakeSubjectDriver:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.streams = [
            [
                {"type": "assistant", "message": {"content": [
                    {"type": "text", "text": "第一阶段完成"}
                ]}},
                {"type": "result"},
            ],
            [
                {"type": "assistant", "message": {"content": [
                    {"type": "text", "text": "第二阶段完成"}
                ]}},
                {"type": "result"},
            ],
            [
                {"type": "assistant", "message": {"content": [
                    {"type": "text", "text": "第三阶段完成"}
                ]}},
                {"type": "result"},
            ],
        ]

    def send_user_message(self, text):
        self.sent.append(text)

    def events(self):
        yield from self.streams.pop(0)

    def close(self):
        self.closed = True

    def kill(self):
        pass


class FakeProxy:
    def __init__(self, endpoint, raw_calls_dir, host):
        self.endpoint = endpoint
        self.raw_calls_dir = raw_calls_dir
        self.host = host
        self.port = 4321
        self.stopped = False

    def start(self):
        return "http://127.0.0.1:4321"

    def stop(self):
        self.stopped = True


def _spec() -> TaskSpec:
    return TaskSpec.model_validate({
        "task_id": "case-1",
        "category": "scripted-workflow",
        "source": {"type": "local-folder", "ref": "/case"},
        "initial_instruction": "第一条",
        "objective": "three stages",
        "input_env": {
            "dockerfile": "Dockerfile",
            "workspace": {"path": "workspace"},
            "base_image": "node:22-bookworm",
        },
        "expected_final_env": {"description": "all three stages done"},
    })


def test_prepared_scripted_run_uses_one_driver_and_existing_packager(tmp_path, monkeypatch):
    docker = FakeDocker()
    subject = FakeSubjectDriver()
    proxy_holder = {}
    packaged = {}

    monkeypatch.setattr(run_module, "DockerClient", lambda: docker)

    def make_proxy(*args, **kwargs):
        proxy = FakeProxy(*args, **kwargs)
        proxy_holder["proxy"] = proxy
        return proxy

    monkeypatch.setattr(run_module, "RecordingProxy", make_proxy)
    monkeypatch.setattr(
        run_module.Driver, "docker", classmethod(lambda cls, *args, **kwargs: subject)
    )
    monkeypatch.setattr(
        run_module,
        "UserAgent",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("scripted workflow must not create UserAgent")
        ),
    )
    monkeypatch.setattr(
        run_module,
        "grade",
        lambda *args, **kwargs: GradeOutcome(
            results=[],
            summary=ScoreSummary(
                verdict="pass", score=0.0, required_pass=0, required_total=0,
                preferred_pass=0, preferred_total=0,
            ),
        ),
    )

    expected = tmp_path / "dataset" / "case-1" / "run"

    def fake_package(**kwargs):
        packaged.update(kwargs)
        return expected

    monkeypatch.setattr(run_module, "package_run_multiturn", fake_package)
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "Dockerfile").write_text("FROM scratch")

    out = run_module.run_prepared_task(
        task_spec=_spec(),
        task_dir=task_dir,
        endpoint="https://api.example.com",
        apikey="secret",
        model="model-x",
        output=tmp_path / "dataset",
        timeout_seconds=60,
        idle_timeout_seconds=60,
        keep=True,
        scripted_instructions=["第一条", "第二条", "第三条"],
    )

    assert out == expected
    assert subject.sent == ["第一条", "第二条", "第三条"]
    assert subject.closed is True
    assert len(docker.built) == 1
    assert len(docker.ran) == 1
    assert len(docker.snapshots) == 2
    assert packaged["injected_turns"] == 2
    assert packaged["max_turns"] == 2
    assert packaged["termination"] == "completed"
    assert proxy_holder["proxy"].stopped is True


def test_prepared_scripted_run_validates_all_instructions_before_side_effects(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        run_module,
        "DockerClient",
        lambda: (_ for _ in ()).throw(AssertionError("Docker must not be constructed")),
    )

    with pytest.raises(ValueError, match="non-empty strings"):
        run_module.run_prepared_task(
            task_spec=_spec(),
            task_dir=tmp_path,
            endpoint="https://api.example.com",
            apikey="secret",
            model="model-x",
            scripted_instructions=["第一条", "   "],
        )


def test_scripted_stream_end_is_classified_as_crashed():
    assert run_module._classify_termination(
        [], "stream_end", killed=False, scripted=True
    ) == "crashed"
    assert run_module._classify_termination(
        [], "stream_end", killed=False, scripted=False
    ) == "stopped_without_claim"
