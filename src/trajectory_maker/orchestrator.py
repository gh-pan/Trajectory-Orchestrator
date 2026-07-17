"""Orchestrator: completion detection, termination classification, and helpers
for the multi-turn run loop (spec 09)."""

from .driver import last_assistant_text

COMPLETION_PHRASES = [
    "完成", "已完成", "任务完成", "完成了",
    "finished", "done", "all done", "complete", "completed",
]

Termination = str  # completed | stopped_without_claim | timeout | max_turns | crashed | auth_error


def _has_completion_phrase(text: str) -> bool:
    low = text.lower()
    return any(p in text or p in low for p in COMPLETION_PHRASES)


def has_completion_phrase(text: str) -> bool:
    """Public alias for the completion-phrase check (used by the run turn loop)."""
    return _has_completion_phrase(text)


def is_error_result(event: dict) -> bool:
    """Return True for Claude ``result`` envelopes that represent a failed turn."""
    if event.get("type") != "result":
        return False
    if event.get("is_error") is True:
        return True
    subtype = str(event.get("subtype", "")).lower()
    return subtype.startswith("error") or subtype in {"failed", "failure"}


def detect_termination(
    events: list[dict],
    timeout: bool = False,
    max_turns: bool = False,
) -> Termination:
    """Classify how a run ended from its collected events."""
    for ev in events:
        if ev.get("type") == "error" or is_error_result(ev):
            etype = ev.get("error", {}).get("type", "")
            error_text = f"{etype} {ev.get('result', '')}".lower()
            if "auth" in error_text:
                return "auth_error"
            return "crashed"
    has_result = any(ev.get("type") == "result" for ev in events)
    if not has_result:
        if timeout:
            return "timeout"
        if max_turns:
            return "max_turns"
        return "crashed"
    last_text = last_assistant_text(events) or ""
    if _has_completion_phrase(last_text):
        return "completed"
    return "stopped_without_claim"



def run_loop(
    drv,
    ua,
    max_turns: int,
    on_event=None,
) -> tuple[list[dict], int, str]:
    """Drive the subject driver with user-agent injection (pure I/O via drv/ua).

    Each `result` event ends a turn: if the subject's last text claims
    completion, stop (completed). If the turn budget is exhausted, stop
    (max_turns). Otherwise ask the user-agent for a follow-up and inject it.
    The user-agent returning None (stop sentinel) ends the run.

    Returns (events, injected_turns, stop_reason) where stop_reason is one of
    completed | user_agent_stop | max_turns | error | stream_end.
    """
    events: list[dict] = []
    injected = 0
    stop_reason = "stream_end"
    for ev in drv.events():
        events.append(ev)
        if on_event is not None:
            on_event(ev)
        etype = ev.get("type")
        if etype == "error":
            stop_reason = "error"
            break
        if etype == "result":
            if is_error_result(ev):
                stop_reason = "error"
                break
            last_text = last_assistant_text(events) or ""
            if has_completion_phrase(last_text):
                stop_reason = "completed"
                break
            if injected >= max_turns:
                stop_reason = "max_turns"
                break
            try:
                follow = ua.react(extract_subject_output(events))
            except Exception:
                follow = None
            if follow is None:
                stop_reason = "user_agent_stop"
                break
            drv.send_user_message(follow)
            injected += 1
    return events, injected, stop_reason


def run_scripted_loop(
    drv,
    instructions: list[str],
    on_event=None,
) -> tuple[list[dict], int, str]:
    """Run a fixed sequence of user turns in one persistent subject session.

    Each instruction is sent only after the preceding turn emits a ``result``
    event.  Unlike :func:`run_loop`, completion phrases in intermediate turns
    are intentionally ignored: exhausting the supplied instruction sequence is
    the only successful completion condition.

    Returns ``(events, injected_turns, stop_reason)``.  The first instruction is
    the activating turn, so ``injected_turns`` counts only later instructions.
    ``stop_reason`` is ``completed``, ``error``, or ``stream_end``.
    """
    if not instructions:
        raise ValueError("scripted workflow requires at least one instruction")
    if any(not isinstance(text, str) or not text.strip() for text in instructions):
        raise ValueError("scripted workflow instructions must be non-empty strings")

    events: list[dict] = []
    sent_turns = 0
    for instruction in instructions:
        drv.send_user_message(instruction)
        sent_turns += 1
        saw_result = False
        for ev in drv.events():
            events.append(ev)
            if on_event is not None:
                on_event(ev)
            etype = ev.get("type")
            if etype == "error":
                return events, max(0, sent_turns - 1), "error"
            if etype == "result":
                if is_error_result(ev):
                    return events, max(0, sent_turns - 1), "error"
                saw_result = True
                break
        if not saw_result:
            return events, max(0, sent_turns - 1), "stream_end"

    return events, max(0, sent_turns - 1), "completed"


def _last_turn_window(events: list[dict]) -> list[dict]:
    """Events belonging to the latest turn: everything after the last *injected*
    user message (a user message carrying a text block, not a tool_result)."""
    start = 0
    for i, ev in enumerate(events):
        if ev.get("type") != "user":
            continue
        content = (ev.get("message") or {}).get("content")
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()
            for b in content
        ):
            start = i + 1
    return events[start:]


def extract_subject_output(events: list[dict]) -> str:
    """Summarize the subject agent's latest turn for the user-agent.

    Includes the last assistant text (the end_turn reply), the tool calls made
    this turn, and a short excerpt of the last tool result — enough context for
    a natural follow-up, without leaking the full message array.
    """
    window = _last_turn_window(events)
    tool_names: list[str] = []
    result_summary = ""
    for ev in window:
        etype = ev.get("type")
        if etype == "assistant":
            for b in (ev.get("message") or {}).get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name"):
                    tool_names.append(b["name"])
        elif etype == "user":
            content = (ev.get("message") or {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        result_summary = str(b.get("content", ""))[:600]
    last_text = last_assistant_text(window) or ""
    out: list[str] = []
    if last_text:
        out.append(last_text)
    if tool_names:
        out.append(f"(这一轮调用了工具: {', '.join(tool_names)})")
    if result_summary:
        out.append(f"最近的工具输出摘录:\n{result_summary}")
    return "\n\n".join(out) if out else "(助手这轮没有文本输出)"
