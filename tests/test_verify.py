import json
from pathlib import Path

import pytest

from trajectory_maker.verify import (
    build_smoke_commands,
    serialize_verify_result,
    VerifyResult,
)
from trajectory_maker.grade import RubricResult, ScoreSummary

FIXTURES = Path(__file__).parent / "fixtures"


def test_smoke_commands_include_claude_version_and_init():
    cmds = build_smoke_commands(init_script="setup.sh")
    assert any("claude --version" in " ".join(c) for c in cmds)
    assert any("setup.sh" in " ".join(c) for c in cmds)


def test_smoke_commands_empty_when_no_init():
    cmds = build_smoke_commands(init_script=None)
    assert any("claude --version" in " ".join(c) for c in cmds)
    # no init script command
    assert not any("setup.sh" in " ".join(c) for c in cmds)


def test_serialize_verify_result_pass():
    results = [RubricResult(id="r1", type="script", severity="required", passed=True, exit_code=0)]
    summary = ScoreSummary(verdict="pass", score=1.0, required_pass=1, required_total=1, preferred_pass=0, preferred_total=0)
    vr = VerifyResult(task_id="t1", verdict="pass", smoke={"build": True, "init": True, "claude_ok": True}, rubric_results=results, summary=summary)
    data = serialize_verify_result(vr)
    assert data["verdict"] == "pass"
    assert data["rubric_results"][0]["pass"] is True
    assert "timestamp" in data


def test_serialize_verify_result_fail():
    results = [RubricResult(id="r1", type="script", severity="required", passed=False, reason="exit 1")]
    summary = ScoreSummary(verdict="fail", score=0.0, required_pass=0, required_total=1, preferred_pass=0, preferred_total=0)
    vr = VerifyResult(task_id="t1", verdict="fail", smoke={"build": True, "init": False, "claude_ok": True}, rubric_results=results, summary=summary)
    data = serialize_verify_result(vr)
    assert data["verdict"] == "fail"
    assert data["smoke"]["init"] is False


def test_run_smoke_returns_dict(monkeypatch):
    from trajectory_maker import verify

    class FakeDocker:
        def __init__(self): self.called = []
        def exec(self, c, cmd, timeout=None):
            self.called.append(cmd)
            return 0, "ok", ""

    fake = FakeDocker()
    out = verify.run_smoke(fake, "container-x", init_script="setup.sh")
    assert out["claude_ok"] is True
    assert out["init"] is True
    assert len(fake.called) >= 2
