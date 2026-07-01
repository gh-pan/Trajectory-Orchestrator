"""Stage 3: run a task in docker, record trajectory, grade, package, destroy."""

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .claude_env import build_subject_env
from .docker import DockerClient
from .driver import Driver
from .grade import grade
from .models import load_task_spec
from .orchestrator import detect_termination
from .package import package_run


def run(
    task_dir: Path,
    endpoint: str,
    apikey: str,
    model: str,
    output: Path = Path("./dataset"),
    max_turns: int = 1,
    timeout_seconds: int = 1800,
    idle_timeout_seconds: int = 300,
    keep: bool = False,
) -> Path:
    spec = load_task_spec(task_dir / "task.yaml")
    docker = DockerClient()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M") + uuid.uuid4().hex[:4]
    image_tag = f"tm-run-{spec.task_id}-{run_id}"
    container = image_tag
    work_dir = output / "_work" / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_path = work_dir / "trajectory_raw.jsonl"
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        docker.build(task_dir, image_tag)
        docker.run(image_tag, container)
        # init script
        if spec.input_env.workspace.init_script:
            docker.exec(container, ["bash", "-lc", f"cd /workspace && bash {spec.input_env.workspace.init_script}"])
        # initial env snapshot
        docker.cp_from(container, "/workspace", str(work_dir / "initial_env"))
        _layout_snapshot(work_dir / "initial_env")

        env = build_subject_env(endpoint, apikey, model)
        drv = Driver.docker(docker, container, env=env, add_dirs=["/workspace"], model=model)
        drv.send_user_message(spec.initial_instruction)
        events = []

        # Watchdog: kill the claude process if no event for idle_timeout_seconds
        # (stuck on a long API call) OR wall-clock exceeds timeout_seconds.
        # Without this the events() loop blocks forever on a hung API call.
        last_event = [time.monotonic()]
        killed = threading.Event()
        stop = threading.Event()

        def watchdog():
            wall_start = time.monotonic()
            while not stop.wait(5):
                now = time.monotonic()
                if now - last_event[0] > idle_timeout_seconds:
                    killed.set()
                    try:
                        drv._proc.terminate()
                    except Exception:
                        pass
                    return
                if now - wall_start > timeout_seconds:
                    killed.set()
                    try:
                        drv._proc.terminate()
                    except Exception:
                        pass
                    return

        wd = threading.Thread(target=watchdog, daemon=True)
        wd.start()
        with raw_path.open("w") as f:
            for ev in drv.events():
                events.append(ev)
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                last_event[0] = time.monotonic()
                if ev.get("type") == "result":
                    break
        stop.set()
        drv.close()

        # auth error short-circuit: do not package
        termination = detect_termination(events, timeout=killed.is_set())
        if termination == "auth_error":
            raise RuntimeError("auth error during run; trajectory meaningless, not packaged")

        # actual final env snapshot BEFORE grading
        docker.cp_from(container, "/workspace", str(work_dir / "actual_final_env"))
        _layout_snapshot(work_dir / "actual_final_env")

        ended_at = datetime.now(timezone.utc).isoformat()
        # grade against live container
        grade_outcome = grade(container, docker, spec)

        # package
        results = grade_outcome.results
        summary = grade_outcome.summary
        out_dir = package_run(
            task_spec=spec, run_id=run_id, endpoint=endpoint, model=model,
            started_at=started_at, ended_at=ended_at, termination=termination,
            max_turns=max_turns, timeout_seconds=timeout_seconds,
            claude_version=_claude_version(docker, container), docker_base=spec.input_env.base_image or "",
            work_dir=work_dir, data_root=output, task_dir=task_dir,
            rubric_results=results, summary=summary,
        )
        return out_dir
    finally:
        if not keep:
            docker.stop(container)
            docker.rm(container)
            docker.rmi(image_tag)
            shutil_rmtree_safe(work_dir)


def _layout_snapshot(snap_dir: Path) -> None:
    """docker cp of /workspace puts contents directly; ensure snap_dir/workspace exists."""
    # docker cp container:/workspace dest  -> dest gets `workspace` dir
    # so dest/workspace already exists; nothing to do if so
    if not (snap_dir / "workspace").exists() and snap_dir.exists():
        # cp dumped contents into snap_dir itself; wrap them
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


def shutil_rmtree_safe(path: Path) -> None:
    import shutil
    try:
        shutil.rmtree(path)
    except Exception:
        pass
