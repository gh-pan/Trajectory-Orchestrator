"""Stage 1: synthesize a TaskSpec from an input folder using a headless claude code."""

import shutil
import subprocess
from pathlib import Path

from .claude_env import build_meta_env, meta_model
from .driver import Driver
from .models import TaskSpec, load_task_spec, TaskIdConflictError

PROMPT_FILE = Path(__file__).parent / "resources" / "prompts" / "synthesize_system.md"


class TaskDirValidationError(Exception):
    pass


def build_synthesize_prompt(input_path: str, output_dir: str) -> str:
    system = PROMPT_FILE.read_text(encoding="utf-8")
    user = (
        f"输入文件夹：{input_path}\n"
        f"输出目录：{output_dir}\n"
        "先通读输入文件夹结构，构思一个真实、自洽、可被 rubric 验证的任务，"
        "然后按 system 指示把 task.yaml / Dockerfile / workspace/ / rubrics/ 写到输出目录。"
        "完成后在最终回复中说明 task_id。"
    )
    return system + "\n\n" + user


def validate_task_dir(task_dir: Path) -> TaskSpec:
    """Structural (non-semantic) validation of synthesize output."""
    if not (task_dir / "task.yaml").exists():
        raise TaskDirValidationError(f"missing task.yaml in {task_dir}")
    if not (task_dir / "Dockerfile").exists():
        raise TaskDirValidationError(f"missing Dockerfile in {task_dir}")
    if not (task_dir / "workspace").is_dir():
        raise TaskDirValidationError(f"missing workspace/ in {task_dir}")
    spec = load_task_spec(task_dir / "task.yaml")
    for rb in spec.rubrics:
        if rb.type == "script":
            script_path = task_dir / rb.run
            if not script_path.exists():
                raise TaskDirValidationError(
                    f"script rubric {rb.id} references missing file: {rb.run}"
                )
        else:  # checklist
            if not rb.criterion:
                raise TaskDirValidationError(
                    f"checklist rubric {rb.id} missing criterion"
                )
    return spec


def finalize_task_dir(temp_dir: Path, tasks_root: Path) -> Path:
    """Validate, extract task_id, check uniqueness, rename temp_dir -> tasks_root/<task_id>."""
    spec = validate_task_dir(temp_dir)
    spec.check_id_unique(tasks_root)
    final = tasks_root / spec.task_id
    shutil.move(str(temp_dir), str(final))
    return final


def _prepare_input(input_ref: str, work_root: Path) -> tuple[Path, dict]:
    """Resolve input folder: clone github URL or use local path. Returns (path, source_meta)."""
    if input_ref.startswith("http") or input_ref.startswith("git@"):
        clone_dir = work_root / f"tm-clone-{abs(hash(input_ref)) % 10**8}"
        subprocess.run(["git", "clone", input_ref, str(clone_dir)], check=True)
        commit = subprocess.run(
            ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return clone_dir, {"type": "github", "ref": input_ref, "commit": commit}
    p = Path(input_ref).resolve()
    if not p.exists():
        raise FileNotFoundError(f"input not found: {input_ref}")
    return p, {"type": "local-folder", "ref": str(p)}


def synthesize(
    input_ref: str,
    tasks_root: Path,
    model: str | None = None,
    idle_timeout_seconds: int = 300,
) -> Path:
    """Run the full synthesize stage. Returns the final task dir path."""
    import threading
    import time
    import uuid

    tasks_root.mkdir(parents=True, exist_ok=True)
    temp_dir = tasks_root / f"_synth_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True)
    input_path, _source_meta = _prepare_input(input_ref, temp_dir.parent)
    prompt = build_synthesize_prompt(str(input_path), str(temp_dir))
    meta_env = build_meta_env()
    drv = Driver.local(
        add_dirs=[str(input_path), str(temp_dir)],
        allowed_tools=["Read", "Glob", "Grep", "Write", "Bash(git clone)", "Bash(git log)"],
        model=model or meta_model(),
        env=meta_env,
    )
    drv.send_user_message(prompt)
    # Watchdog: kill synthesize claude if no event for idle_timeout_seconds.
    # Without this a stalled API call hangs synthesize forever (files may
    # already be written, so killing is safe — finalize will validate them).
    last_event = [time.monotonic()]
    stop = threading.Event()

    def watchdog():
        while not stop.wait(5):
            if time.monotonic() - last_event[0] > idle_timeout_seconds:
                try:
                    drv._proc.terminate()
                except Exception:
                    pass
                return

    wd = threading.Thread(target=watchdog, daemon=True)
    wd.start()
    # consume all events (claude writes files as tool_use side effects)
    for _ev in drv.events():
        last_event[0] = time.monotonic()
    stop.set()
    drv.close()
    return finalize_task_dir(temp_dir, tasks_root)
