# 07 · Verify — 阶段2 子命令

**Goal:** verify.py 在干净任务容器里验证任务可解：build→run→冒烟→跑验证 agent→grade()→写 verify_result.json。可单测的部分（冒烟命令构造、result 序列化、流程编排）TDD；真实容器流程为集成/e2e。

**Files:**
- Create: `src/trajectory_maker/verify.py`
- Create: `tests/test_verify.py`

**Depends on:** 01-models、02-docker、03-driver、05-grade、06-synthesize（复用 TaskSpec 加载）

---

- [ ] **Step 1: 写失败测试 test_verify.py**

Create `tests/test_verify.py`：

```python
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
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
uv run pytest tests/test_verify.py -v
```
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: 实现 verify.py**

Create `src/trajectory_maker/verify.py`：

```python
"""Stage 2: verify a synthesized task is solvable and its rubrics can judge."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .docker import DockerClient
from .driver import Driver
from .grade import grade, RubricResult, ScoreSummary
from .models import TaskSpec, load_task_spec


@dataclass
class VerifyResult:
    task_id: str
    verdict: str  # pass | fail
    smoke: dict
    rubric_results: list[RubricResult]
    summary: ScoreSummary
    agent_event_log: str | None = None
    judge_event_log: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def build_smoke_commands(init_script: str | None) -> list[list[str]]:
    cmds = [["claude", "--version"]]
    if init_script:
        cmds.append(["bash", "-lc", f"cd /workspace && test -f {init_script} && bash {init_script}"])
    return cmds


def run_smoke(docker: DockerClient, container: str, init_script: str | None) -> dict:
    smoke = {"build": True, "init": True, "claude_ok": True}
    for cmd in build_smoke_commands(init_script):
        code, _out, _err = docker.exec(container, cmd, timeout=60)
        if "claude" in " ".join(cmd) and code != 0:
            smoke["claude_ok"] = False
        if init_script and " ".join(cmd).endswith(init_script) and code != 0:
            smoke["init"] = False
    return smoke


def serialize_verify_result(vr: VerifyResult) -> dict:
    return {
        "task_id": vr.task_id,
        "verdict": vr.verdict,
        "smoke": vr.smoke,
        "rubric_results": [
            {
                "id": r.id, "type": r.type, "severity": r.severity,
                "pass": r.passed, "reason": r.reason,
                "exit_code": r.exit_code, "stdout_tail": r.stdout_tail,
            }
            for r in vr.rubric_results
        ],
        "summary": {
            "verdict": vr.summary.verdict, "score": vr.summary.score,
            "required_pass": vr.summary.required_pass, "required_total": vr.summary.required_total,
        },
        "agent_event_log": vr.agent_event_log,
        "judge_event_log": vr.judge_event_log,
        "timestamp": vr.timestamp,
    }


def verify(
    task_dir: Path,
    endpoint: str | None = None,
    apikey: str | None = None,
    model: str | None = None,
    keep_on_fail: bool = False,
) -> VerifyResult:
    """Full verify stage: build, run, smoke, run agent, grade, write result, cleanup."""
    spec = load_task_spec(task_dir / "task.yaml")
    docker = DockerClient()
    image_tag = f"tm-verify-{spec.task_id}"
    container = f"tm-verify-{spec.task_id}-{uuid.uuid4().hex[:6]}"
    agent_log_path = task_dir / "_verify_log" / "agent.jsonl"
    agent_log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        docker.build(task_dir, image_tag)
        docker.run(image_tag, container)
        smoke = run_smoke(docker, container, spec.input_env.workspace.init_script)
        if not all(smoke.values()):
            return _finish(spec, "fail", smoke, [], _empty_summary(), agent_log_path, docker, container, image_tag, keep_on_fail)

        # run verify agent
        env = _env(endpoint, apikey, model)
        drv = Driver.docker(docker, container, env=env, add_dirs=["/workspace"], model=model)
        drv.send_user_message(spec.initial_instruction)
        with agent_log_path.open("w") as f:
            for ev in drv.events():
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        drv.close()

        grade_outcome = grade(container, docker, spec, env=env)
        results = grade_outcome.results
        summary = grade_outcome.summary
        verdict = "pass" if summary.verdict == "pass" else "fail"
        return _finish(spec, verdict, smoke, results, summary, agent_log_path, docker, container, image_tag, keep_on_fail)
    finally:
        pass  # cleanup handled in _finish


def _empty_summary() -> ScoreSummary:
    return ScoreSummary(verdict="fail", score=0.0, required_pass=0, required_total=0, preferred_pass=0, preferred_total=0)


def _env(endpoint, apikey, model) -> dict[str, str]:
    env = {}
    if endpoint: env["ANTHROPIC_BASE_URL"] = endpoint
    if apikey: env["ANTHROPIC_API_KEY"] = apikey
    if model: env["ANTHROPIC_MODEL"] = model
    return env


def _finish(spec, verdict, smoke, results, summary, agent_log_path, docker, container, image_tag, keep_on_fail) -> VerifyResult:
    vr = VerifyResult(
        task_id=spec.task_id, verdict=verdict, smoke=smoke,
        rubric_results=results, summary=summary,
        agent_event_log=str(agent_log_path) if agent_log_path.exists() else None,
    )
    # cleanup
    if verdict == "pass" or not keep_on_fail:
        docker.stop(container)
        docker.rm(container)
        docker.rmi(image_tag)
    return vr
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
uv run pytest tests/test_verify.py -v
```
Expected: PASS（5 passed）。

- [ ] **Step 5: 提交**

```bash
git add src/trajectory_maker/verify.py tests/test_verify.py
git commit -m "feat: add verify stage (smoke, agent run, grade, result serialization)"
```
