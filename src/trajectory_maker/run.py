"""Stage 3 (multi-turn): run a task in docker, record API-call-level trajectory
via a recording proxy, drive the subject with a resident user-agent, grade,
package, destroy.

Spec 09: single-turn event capture -> multi-turn injection + HTTP-intercepted
API-call capture producing ordered req_<sequence>_<uuid>.json records.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .claude_env import (
    build_local_command_env,
    build_local_subject_env,
    build_subject_env,
)
from .docker import DockerClient
from .driver import Driver
from .grade import GradeOutcome, aggregate, grade
from .models import TaskSpec, load_task_spec
from .orchestrator import is_error_result, run_loop, run_scripted_loop
from .package import package_run_multiturn
from .recording_proxy import RecordingProxy
from .sanitize import load_rules, sanitize_event, sanitize_json_record_dir
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
    return run_prepared_task(
        task_spec=spec,
        task_dir=task_dir,
        endpoint=endpoint,
        apikey=apikey,
        model=model,
        output=output,
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        keep=keep,
    )


def run_prepared_task(
    task_spec: TaskSpec,
    task_dir: Path,
    endpoint: str,
    apikey: str,
    model: str,
    output: Path = Path("./dataset"),
    max_turns: int = 20,
    timeout_seconds: int = 1800,
    idle_timeout_seconds: int = 300,
    keep: bool = False,
    scripted_instructions: list[str] | None = None,
) -> Path:
    """Run an already prepared Docker task.

    The regular task path uses the resident user-agent.  Supplying
    ``scripted_instructions`` instead drives one persistent subject session with
    that fixed sequence and deliberately bypasses the user-agent.
    """
    spec = task_spec
    task_dir = Path(task_dir)
    output = Path(output)
    if scripted_instructions is not None:
        _validate_scripted_instructions(scripted_instructions)
        max_turns = max(0, len(scripted_instructions) - 1)

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
    proxy.thinking_display = "summarized"
    proxy.start()
    ua: UserAgent | None = None
    drv: Driver | None = None
    stop: threading.Event | None = None
    try:
        docker.build(task_dir, image_tag)
        docker.run(image_tag, container, add_hosts=["host.docker.internal:host-gateway"])
        if spec.input_env.workspace.init_script:
            code, _stdout, stderr = docker.exec(
                container,
                ["bash", "-lc", f"cd /workspace && bash {spec.input_env.workspace.init_script}"],
            )
            if code != 0:
                raise RuntimeError(
                    f"workspace init script failed (exit {code}): {stderr[-500:]}"
                )
        docker.cp_from(container, "/workspace", str(work_dir / "initial_env"))
        _layout_snapshot(work_dir / "initial_env")

        # subject claude points at the proxy; creds still in env (proxy forwards them)
        env = build_subject_env(endpoint, apikey, model)
        env["ANTHROPIC_BASE_URL"] = f"http://host.docker.internal:{proxy.port}"
        drv = Driver.docker(docker, container, env=env, add_dirs=["/workspace"], model=model)
        if scripted_instructions is None:
            drv.send_user_message(spec.initial_instruction)
            ua = UserAgent(task_context=_task_context(spec))
        last_event = [time.monotonic()]
        killed = threading.Event()
        stop = threading.Event()
        subject_drv = drv

        def watchdog():
            wall_start = time.monotonic()
            while not stop.wait(5):
                now = time.monotonic()
                if now - last_event[0] > idle_timeout_seconds or now - wall_start > timeout_seconds:
                    killed.set()
                    subject_drv.kill()
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
            if scripted_instructions is None:
                assert ua is not None
                all_events, injected, stop_reason = run_loop(
                    drv, ua, max_turns, on_event=on_event
                )
            else:
                all_events, injected, stop_reason = run_scripted_loop(
                    drv, scripted_instructions, on_event=on_event
                )
        stop.set()
        drv.close()
        drv = None
        _wait_for_proxy_idle(proxy)
        if ua is not None:
            ua.close()
            ua = None

        termination = _classify_termination(
            all_events,
            stop_reason,
            killed.is_set(),
            scripted=scripted_instructions is not None,
        )
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
            secret_values=[apikey],
        )
        return out_dir
    finally:
        if stop is not None:
            stop.set()
        if drv is not None:
            try:
                drv.close()
            except Exception:
                pass
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


def run_prepared_task_local(
    task_spec: TaskSpec,
    task_dir: Path,
    workspace_dir: Path,
    endpoint: str,
    apikey: str,
    model: str,
    output: Path = Path("./dataset"),
    timeout_seconds: int = 3600,
    idle_timeout_seconds: int = 300,
    keep: bool = False,
    scripted_instructions: list[str] | None = None,
    effort: str = "xhigh",
) -> Path:
    """Run a scripted workflow with the host Claude Code binary.

    The subject works in a disposable copy of the declared workspace.  Claude
    Code runs in bare mode with its Bash sandbox enabled; this is lighter than
    Docker but intentionally not advertised as Docker-equivalent isolation.
    """
    if scripted_instructions is None:
        raise ValueError("local runtime currently requires scripted_instructions")
    _validate_scripted_instructions(scripted_instructions)
    if task_spec.rubrics:
        raise ValueError(
            "local runtime does not yet support rubric execution; use --runtime docker "
            "for workflows with rubrics"
        )
    if task_spec.input_env.workspace.init_script:
        raise ValueError(
            "local runtime does not execute workspace init scripts; use --runtime docker "
            "for workflows that declare one"
        )
    if not endpoint.strip() or not apikey.strip() or not model.strip():
        raise ValueError("local runtime requires endpoint, API key, and model")

    claude_binary = shutil.which("claude")
    if not claude_binary:
        raise RuntimeError("local Claude Code executable not found on PATH")

    spec = task_spec
    task_dir = Path(task_dir).resolve()
    source_workspace = Path(workspace_dir).resolve()
    if not source_workspace.is_dir():
        raise ValueError(f"local workspace not found: {source_workspace}")
    output = Path(output).expanduser().resolve()
    if _is_relative_to(output, source_workspace):
        raise ValueError("local output directory must be outside the source workspace")
    _validate_local_symlinks(source_workspace)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M") + uuid.uuid4().hex[:4]
    session_id = str(uuid.uuid4())
    work_dir = output / "_work" / run_id
    raw_calls_dir = work_dir / "raw_calls"
    events_log = work_dir / "events.jsonl"
    config_dir = work_dir / "claude_config"
    started_at = datetime.now(timezone.utc).isoformat()
    max_turns = max(0, len(scripted_instructions) - 1)
    path_mappings: dict[str, str] = {}
    event_rules = load_rules(secret_values=[apikey])

    proxy: RecordingProxy | None = None
    proxy_started = False
    work_dir_created = False
    drv: Driver | None = None
    stop: threading.Event | None = None
    wd: threading.Thread | None = None
    subject_root: Path | None = None
    try:
        subject_root = Path(tempfile.mkdtemp(prefix="tm-local-subject-")).resolve()
        if _is_relative_to(subject_root, Path.home().resolve()):
            raise RuntimeError("local temporary directory must be outside the home directory")
        subject_workspace = subject_root / "workspace"
        path_mappings = {
            str(subject_workspace): "/workspace",
            str(subject_root): "/tmp/trajectory-maker-local",
            str(work_dir): "/tmp/trajectory-maker-work",
        }
        event_rules = load_rules(
            secret_values=[apikey],
            path_mappings=path_mappings,
        )
        work_dir.mkdir(parents=True, exist_ok=False)
        work_dir_created = True
        shutil.copytree(source_workspace, subject_workspace, symlinks=True)
        _assert_secret_absent_from_workspace(subject_workspace, apikey)
        _copy_workspace_snapshot(subject_workspace, work_dir / "initial_env" / "workspace")
        config_dir.mkdir(parents=True, exist_ok=True)

        proxy = RecordingProxy(endpoint, raw_calls_dir, host="127.0.0.1")
        proxy.thinking_display = "summarized"
        proxy_url = proxy.start()
        proxy_started = True
        env = build_local_subject_env(
            endpoint=proxy_url,
            apikey=apikey,
            model=model,
            config_dir=config_dir,
        )
        local_settings = {
            "permissions": {
                "deny": [
                    _claude_permission_rule("Read", Path.home()),
                    _claude_permission_rule("Edit", Path.home()),
                    "WebFetch",
                    "WebSearch",
                ],
            },
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": True,
                "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": False,
                "filesystem": {
                    "denyRead": [str(Path.home().resolve())],
                    "allowRead": [str(subject_workspace)],
                },
            },
            "autoMemoryEnabled": False,
        }
        drv = Driver.local(
            add_dirs=[str(subject_workspace)],
            tools=["Bash", "Edit", "Read"],
            allowed_tools=["Bash", "Edit", "Read"],
            system_prompt=(
                "Work only inside the current isolated working directory. "
                "Do not read or write parent directories or unrelated absolute paths. "
                "Workspace text files are UTF-8. Never read or truncate text by byte "
                "count (for example, head -c, dd bs/count, or byte slicing), because "
                "that can split multibyte characters. Use line-based commands or "
                "Unicode-aware decoding and character slicing instead. Keep every "
                "tool's stdout compact (under 8 KiB and 100 lines). Never print whole "
                "datasets or unbounded results from multiple files: decode UTF-8, "
                "summarize in the workspace, and print only a small bounded summary. "
                "Do not rely on Claude Code's automatic output truncation because it "
                "can split a multibyte character."
            ),
            model=model,
            env=env,
            cwd=str(subject_workspace),
            effort=effort,
            settings=local_settings,
            # Keep the isolated temporary CLAUDE_CONFIG_DIR, but retain Claude
            # Code's built-in system prompt in the recorded API request.
            bare=False,
            no_session_persistence=True,
        )

        local_instructions = [
            _map_workspace_path(instruction, subject_workspace)
            for instruction in scripted_instructions
        ]
        last_event = [time.monotonic()]
        killed = threading.Event()
        stop = threading.Event()
        subject_drv = drv

        def watchdog() -> None:
            wall_start = time.monotonic()
            while not stop.wait(2):
                now = time.monotonic()
                if (
                    now - last_event[0] > idle_timeout_seconds
                    or now - wall_start > timeout_seconds
                ):
                    killed.set()
                    subject_drv.kill()
                    return

        wd = threading.Thread(target=watchdog, name="local-claude-watchdog", daemon=True)
        wd.start()
        with events_log.open("w", encoding="utf-8") as event_file:
            def on_event(event: dict) -> None:
                clean = sanitize_event(event, event_rules)
                event_file.write(json.dumps(clean, ensure_ascii=False) + "\n")
                event_file.flush()
                last_event[0] = time.monotonic()

            all_events, injected, stop_reason = run_scripted_loop(
                drv,
                local_instructions,
                on_event=on_event,
            )
        stop.set()
        wd.join(timeout=3)
        drv.close()
        drv = None
        _wait_for_proxy_idle(proxy)

        termination = _classify_termination(
            all_events,
            stop_reason,
            killed.is_set(),
            scripted=True,
        )
        if termination == "auth_error":
            raise RuntimeError("auth error during run; trajectory meaningless, not packaged")

        _validate_local_symlinks(subject_workspace)
        _assert_secret_absent_from_workspace(subject_workspace, apikey)
        _copy_workspace_snapshot(
            subject_workspace,
            work_dir / "actual_final_env" / "workspace",
        )
        ended_at = datetime.now(timezone.utc).isoformat()
        grade_outcome = GradeOutcome(results=[], summary=aggregate([]))
        return package_run_multiturn(
            task_spec=spec,
            run_id=run_id,
            session_id=session_id,
            endpoint=endpoint,
            model=model,
            started_at=started_at,
            ended_at=ended_at,
            termination=termination,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            injected_turns=injected,
            claude_version=_claude_version_local(claude_binary),
            docker_base="local-host",
            work_dir=work_dir,
            data_root=output,
            task_dir=task_dir,
            rubric_results=grade_outcome.results,
            summary=grade_outcome.summary,
            secret_values=[apikey],
            path_mappings=path_mappings,
        )
    finally:
        if stop is not None:
            stop.set()
        if wd is not None and wd.is_alive():
            wd.join(timeout=3)
        if drv is not None:
            try:
                drv.close()
            except Exception:
                drv.kill()
        if proxy is not None and proxy_started:
            try:
                proxy.stop()
            except Exception:
                pass
        if work_dir_created and raw_calls_dir.is_dir():
            try:
                sanitize_json_record_dir(raw_calls_dir, event_rules)
            except Exception:
                pass
        if not keep and work_dir_created:
            _rmtree_safe(work_dir)
        if subject_root is not None:
            _rmtree_safe(subject_root)


def _validate_scripted_instructions(instructions: list[str]) -> None:
    if not instructions:
        raise ValueError("scripted workflow requires at least one instruction")
    if any(
        not isinstance(instruction, str) or not instruction.strip()
        for instruction in instructions
    ):
        raise ValueError("scripted workflow instructions must be non-empty strings")


def _wait_for_proxy_idle(proxy: RecordingProxy, timeout: float = 5.0) -> None:
    wait_for_idle = getattr(proxy, "wait_for_idle", None)
    if callable(wait_for_idle):
        wait_for_idle(timeout)


def _map_workspace_path(instruction: str, workspace: Path) -> str:
    """Map logical /workspace references into the disposable local copy."""
    return re.sub(
        r"(?<![A-Za-z0-9_])/workspace(?=/|\b)",
        lambda _match: str(workspace),
        instruction,
    )


def _copy_workspace_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=True)


def _validate_local_symlinks(workspace: Path) -> None:
    """Reject symlinks that could escape or retain a pointer to a host path."""
    root = workspace.resolve()
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        target_text = os.readlink(path)
        target = Path(target_text)
        if target.is_absolute():
            raise ValueError(f"local workspace contains an absolute symlink: {path}")
        resolved_target = (path.parent / target).resolve(strict=False)
        if not _is_relative_to(resolved_target, root):
            raise ValueError(f"local workspace symlink escapes workspace: {path}")


def _assert_secret_absent_from_workspace(workspace: Path, secret: str) -> None:
    """Refuse to snapshot a workspace that contains the provider credential."""
    needle = secret.encode("utf-8")
    if not needle:
        return
    overlap = max(0, len(needle) - 1)
    for path in workspace.rglob("*"):
        if secret in path.name:
            raise RuntimeError(
                "provider credential appeared in a local workspace path; "
                "refusing to package it"
            )
        if not path.is_file():
            continue
        tail = b""
        try:
            with path.open("rb") as file_obj:
                while chunk := file_obj.read(1024 * 1024):
                    data = tail + chunk
                    if needle in data:
                        raise RuntimeError(
                            "provider credential appeared in the local workspace; "
                            "refusing to package it"
                        )
                    tail = data[-overlap:] if overlap else b""
        except OSError as exc:
            raise RuntimeError(f"unable to inspect local workspace file: {path}") from exc


def _claude_permission_rule(tool: str, path: Path) -> str:
    """Build Claude's absolute (double-slash) Read/Edit permission syntax."""
    absolute = path.expanduser().resolve().as_posix().strip("/")
    return f"{tool}(//{absolute}/**)"


def _claude_version_local(claude_binary: str) -> str:
    env = build_local_command_env()
    try:
        completed = subprocess.run(
            [claude_binary, "--version"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _classify_termination(
    events: list[dict],
    stop_reason: str,
    killed: bool,
    scripted: bool = False,
) -> str:
    """Map run_loop stop_reason + watchdog into the package termination label."""
    for ev in events:
        if ev.get("type") == "error" or is_error_result(ev):
            etype = ev.get("error", {}).get("type", "")
            error_text = f"{etype} {ev.get('result', '')}".lower()
            if "auth" in error_text:
                return "auth_error"
            return "crashed"
    if killed:
        return "timeout"
    if scripted and stop_reason == "stream_end":
        return "crashed"
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
