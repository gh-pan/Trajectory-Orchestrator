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
