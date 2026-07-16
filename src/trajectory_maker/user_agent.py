"""Resident user-agent: a skill-activated claude that reacts to the subject
agent's turns with natural user follow-ups, driving long-chain multi-turn tasks.

It is a separate Driver.local process on the HOST:
  - meta endpoint credentials (via env), CLAUDE_CONFIG_DIR pointing at an
    isolated config dir that holds only the user-reactor skill (no host hooks,
    no project .claude/settings.local.json — cwd is a clean temp dir too);
  - its own conversation memory across turns (one stream-json session);
  - NEVER goes through the recording proxy (uses the meta endpoint directly),
    so its API calls never leak into the subject's req_*.json.

Per spec 09 (B): resident, stateful, skill-activated. The user-agent only
*observes* the subject; it is not part of the subject's conversation.
"""

import shutil
import tempfile
import threading
import time
from pathlib import Path

from .driver import Driver, last_assistant_text

SKILL_SRC = Path(__file__).parent / "resources" / "skills" / "user-reactor"
# sentinel the user-agent may return to signal "task done, stop injecting"
STOP_SENTINELS = {"[STOP]", "[DONE]", "[NO_FOLLOWUP]"}


def _prime_prompt(task_context: str) -> str:
    return (
        "接下来你要扮演一个真实的人类开发者，和一个 AI 编码助手协作完成一个长链路任务。"
        "我会把那个助手每轮的输出转述给你，你要像真人那样回一句简短的跟进消息——接话、纠偏、追问、"
        "确认、催进度、收尾都行——我会把你这句转给它。\n\n"
        f"任务背景：\n{task_context}\n\n"
        "风格：像真人在聊天工具里打字。casual、简洁、口语，中英文随任务自然混用，带任务推进感。"
        "不要列要点、不要写长段、不要带任何机器腔或元指令（比如不要说'作为用户我建议'）。"
        "一次只回一句，直接说要它干嘛或问什么。"
        "如果任务确实已经做完、没什么可再跟进的，就只回 [STOP] 三个字符。\n\n"
        "现在先确认你理解了这个角色，用一句话随意回应即可。"
    )


def _react_prompt(subject_output: str) -> str:
    return (
        f"助手刚才这一轮的输出（摘录）：\n{subject_output}\n\n"
        "你作为用户下一句怎么回？只回这一句，或 [STOP]。"
    )


class UserAgent:
    """A resident skill-activated claude that produces natural user follow-ups."""

    def __init__(
        self,
        task_context: str,
        drv: Driver | None = None,
        model: str | None = None,
        idle_timeout: float = 120.0,
        meta_env: dict[str, str] | None = None,
    ):
        self.idle_timeout = idle_timeout
        self._owns_drv = drv is None
        self._tmpdir: Path | None = None
        if drv is not None:
            self.drv = drv
        else:
            self.drv, self._tmpdir = self._spawn(model or _default_meta_model(), meta_env or _default_meta_env())
        # prime: establish the user-reactor role
        self._turn(_prime_prompt(task_context))

    def react(self, subject_output: str) -> str | None:
        """Feed the subject's last turn; return the next user message to inject,
        or None when the user-agent signals stop / returns nothing usable."""
        events = self._turn(_react_prompt(subject_output))
        text = (last_assistant_text(events) or "").strip()
        if not text or text in STOP_SENTINELS:
            return None
        # strip a wrapping [STOP] if it appears alongside text
        for s in STOP_SENTINELS:
            text = text.replace(s, "").strip()
        return text or None

    def close(self) -> None:
        try:
            self.drv.close()
        finally:
            if self._tmpdir and self._tmpdir.exists():
                shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ---- internals ----

    def _spawn(self, model: str, meta_env: dict[str, str]) -> tuple[Driver, Path]:
        from .claude_env import _meta_creds, _anthropic_vars  # local import to avoid cycle
        # isolated config dir: holds only the skill, no settings.json (creds via env)
        tmp = Path(tempfile.mkdtemp(prefix="tm-useragent-"))
        config_dir = tmp / "config"
        skills_dst = config_dir / "skills" / "user-reactor"
        skills_dst.mkdir(parents=True, exist_ok=True)
        if SKILL_SRC.is_dir():
            for f in SKILL_SRC.iterdir():
                shutil.copy2(f, skills_dst / f.name)
        # clean cwd so claude does not pick up the project's .claude/settings.local.json
        cwd = tmp / "cwd"
        cwd.mkdir(parents=True, exist_ok=True)
        # strip host CLAUDE_CODE_*/CLAUDE_* session state so the user-agent does
        # not inherit the parent's session id / entrypoint / agent-teams flags.
        env = {k: v for k, v in meta_env.items() if not k.startswith("CLAUDE_CODE_")}
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("CLAUDE_EFFORT", None)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        drv = Driver.local(
            add_dirs=[str(cwd)],
            model=model,
            env=env,
            cwd=str(cwd),
        )
        return drv, tmp

    def _turn(self, user_text: str) -> list[dict]:
        """Send one user message and read events until the result event (one turn)."""
        self.drv.send_user_message(user_text)
        events: list[dict] = []
        deadline = time.monotonic() + self.idle_timeout
        stop = threading.Event()

        def watchdog():
            while not stop.wait(2):
                if time.monotonic() > deadline:
                    self.drv.kill()
                    return

        wd = threading.Thread(target=watchdog, daemon=True)
        wd.start()
        try:
            for ev in self.drv.events():
                events.append(ev)
                if ev.get("type") == "result":
                    break
                if time.monotonic() > deadline:
                    break
        finally:
            stop.set()
        return events


def _default_meta_env() -> dict[str, str]:
    from .claude_env import build_meta_env
    return build_meta_env()


def _default_meta_model() -> str | None:
    from .claude_env import meta_model
    return meta_model()
