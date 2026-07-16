"""Stage 3 (multi-turn): run a task in docker, record API-call-level trajectory
via a recording proxy, drive the subject with a resident user-agent, grade,
package, destroy.

Spec 09: single-turn event capture -> multi-turn injection + HTTP-intercepted
API-call capture producing req_<uuid>.json (aligned with the golden sample).
"""

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .claude_env import build_subject_env
from .convert import convert_dir
from .docker import DockerClient
from .driver import Driver, last_assistant_text
from .grade import grade
from .models import load_task_spec
from .orchestrator import detect_termination, run_loop
from .package import package_run_multiturn
from .recording_proxy import RecordingProxy
from .user_agent import UserAgent


def run(
    task_dir: Path,
    endpoint: str,
    apikey: str,
    model: str,
    output: Path = Path("./dataset"),
    max_turns: int = 20,
    timeout_seconds: int = 1800,
    idle_timeout_seconds: int = 300,
    keep: bool = False,
) -> Path:
    spec = load_task_spec(task_dir / "task.yaml")
    docker = DockerClient()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M") + uuid.uuid4().hex[:4]
    session_id = str(uuid.uuid4())
    image_tag = f"tm-run-{spec.task_id}-{run_id}"
    container = image_tag
    work_dir = output / "_work" / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_calls_dir = work_dir / "raw_calls"
    events_log = work_dir / "events.jsonl"
    started_at = datetime.now(timezone.utc).isoformat()

    # proxy listens on 0.0.0.0 so the container (reaching the host via the
    # docker gateway IP, not 127.0.0.1) can connect.
    proxy = RecordingProxy(endpoint, raw_calls_dir, host="0.0.0.0")
    proxy.start()
    ua: UserAgent | None = None
    try:
        docker.build(task_dir, image_tag)
        docker.run(image_tag, container, add_hosts=["host.docker.internal:host-gateway"])
        if spec.input_env.workspace.init_script:
            docker.exec(container, ["bash", "-lc", f"cd /workspace && bash {spec.input_env.workspace.init_script}"])
        docker.cp_from(container, "/workspace", str(work_dir / "initial_env"))
        _layout_snapshot(work_dir / "initial_env")

        # subject claude points at the proxy; creds still in env (proxy forwards them)
        env = build_subject_env(endpoint, apikey, model)
        env["ANTHROPIC_BASE_URL"] = f"http://host.docker.internal:{proxy.port}"
        drv = Driver.docker(docker, container, env=env, add_dirs=["/workspace"], model=model)
        drv.send_user_message(spec.initial_instruction)

        ua = UserAgent(task_context=_task_context(spec))
        last_event = [time.monotonic()]
        killed = threading.Event()
        stop = threading.Event()

        def watchdog():
            wall_start = time.monotonic()
            while not stop.wait(5):
                now = time.monotonic()
                if now - last_event[0] > idle_timeout_seconds or now - wall_start > timeout_seconds:
                    killed.set()
                    drv.kill()
                    try:
                        docker.exec(container, ["pkill", "-9", "-f", "claude"], timeout=10)
                    except Exception:
                        pass
                    return

        wd = threading.Thread(target=watchdog, daemon=True)
        wd.start()
        with events_log.open("w") as f:
            def on_event(ev):
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                last_event[0] = time.monotonic()
            all_events, injected, stop_reason = run_loop(drv, ua, max_turns, on_event=on_event)
        stop.set()
        drv.close()
        ua.close()

        termination = _classify_termination(all_events, stop_reason, killed.is_set())
        if termination == "auth_error":
            raise RuntimeError("auth error during run; trajectory meaningless, not packaged")

        docker.cp_from(container, "/workspace", str(work_dir / "actual_final_env"))
        _layout_snapshot(work_dir / "actual_final_env")
        ended_at = datetime.now(timezone.utc).isoformat()
        grade_outcome = grade(container, docker, spec)

        out_dir = package_run_multiturn(
            task_spec=spec, run_id=run_id, session_id=session_id,
            endpoint=endpoint, model=model, started_at=started_at, ended_at=ended_at,
            termination=termination, max_turns=max_turns, timeout_seconds=timeout_seconds,
            injected_turns=injected, claude_version=_claude_version(docker, container),
            docker_base=spec.input_env.base_image or "", work_dir=work_dir, data_root=output,
            task_dir=task_dir, rubric_results=grade_outcome.results, summary=grade_outcome.summary,
        )
        return out_dir
    finally:
        proxy.stop()
        if ua is not None:
            try:
                ua.close()
            except Exception:
                pass
        if not keep:
            docker.stop(container)
            docker.rm(container)
            docker.rmi(image_tag)
            _rmtree_safe(work_dir)


def _classify_termination(events: list[dict], stop_reason: str, killed: bool) -> str:
    """Map run_loop stop_reason + watchdog into the package termination label."""
    for ev in events:
        if ev.get("type") == "error":
            etype = ev.get("error", {}).get("type", "")
            if "auth" in etype.lower():
                return "auth_error"
            return "crashed"
    if killed:
        return "timeout"
    return {"completed": "completed", "user_agent_stop": "stopped_without_claim",
            "max_turns": "max_turns", "error": "crashed",
            "stream_end": "stopped_without_claim"}.get(stop_reason, "stopped_without_claim")


def _task_context(spec) -> str:
    return (
        f"任务：{spec.objective}\n"
        f"初始指令：{spec.initial_instruction}\n"
        f"分类：{spec.category}\n"
        "你在和一个 AI 编码助手协作，它在容器 /workspace 里改代码/跑测试。"
    )


def _layout_snapshot(snap_dir: Path) -> None:
    if not (snap_dir / "workspace").exists() and snap_dir.exists():
        tmp = snap_dir.parent / (snap_dir.name + "_tmp")
        snap_dir.rename(tmp)
        snap_dir.mkdir()
        tmp.rename(snap_dir / "workspace")


def _claude_version(docker, container) -> str:
    try:
        code, out, _ = docker.exec(container, ["claude", "--version"], timeout=30)
        return out.strip() if code == 0 else "unknown"
    except Exception:
        return "unknown"


def _rmtree_safe(path: Path) -> None:
    import shutil
    try:
        shutil.rmtree(path)
    except Exception:
        pass
