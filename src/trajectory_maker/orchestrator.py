"""Orchestrator: completion detection and (future) multi-turn injection state machine."""

from .driver import last_assistant_text

COMPLETION_PHRASES = [
    "完成", "已完成", "任务完成", "完成了",
    "finished", "done", "all done", "complete", "completed",
]

Termination = str  # completed | stopped_without_claim | timeout | max_turns | crashed | auth_error


def _has_completion_phrase(text: str) -> bool:
    low = text.lower()
    return any(p in text or p in low for p in COMPLETION_PHRASES)


def detect_termination(
    events: list[dict],
    timeout: bool = False,
    max_turns: bool = False,
) -> Termination:
    """Classify how a run ended from its collected events."""
    # auth error takes precedence (trajectory meaningless)
    for ev in events:
        if ev.get("type") == "error":
            etype = ev.get("error", {}).get("type", "")
            if "auth" in etype.lower():
                return "auth_error"
            return "crashed"
    has_result = any(ev.get("type") == "result" for ev in events)
    if not has_result:
        if timeout:
            return "timeout"
        if max_turns:
            return "max_turns"
        # process ended without result event and no explicit signal
        return "crashed"
    last_text = last_assistant_text(events) or ""
    if _has_completion_phrase(last_text):
        return "completed"
    return "stopped_without_claim"
