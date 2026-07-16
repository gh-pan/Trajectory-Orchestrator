"""Tests for orchestrator.run_loop — the multi-turn injection state machine.

Uses fake driver + fake user-agent to verify inject-vs-stop decisions without
a real claude process.
"""
from trajectory_maker.orchestrator import run_loop


class FakeDriver:
    """Yields a scripted event stream across turns; records injected messages.

    Like a real stream-json stdout: one persistent generator yields every turn's
    events in order. send_user_message just records (the next turn's events are
    already queued in _streams)."""
    def __init__(self, event_streams: list[list[dict]]):
        self._streams = [list(s) for s in event_streams]
        self.injected: list[str] = []

    def events(self):
        while self._streams:
            for ev in self._streams.pop(0):
                yield ev

    def send_user_message(self, text):
        self.injected.append(text)

    def kill(self):
        pass


class FakeUserAgent:
    def __init__(self, replies: list[str | None]):
        self._replies = list(replies)
    def react(self, subject_output):
        return self._replies.pop(0) if self._replies else None


def _assistant(text, tool=None):
    content = [{"type": "text", "text": text}]
    if tool:
        content.append({"type": "tool_use", "name": tool})
    return {"type": "assistant", "message": {"content": content}}

def _result():
    return {"type": "result", "subtype": "success", "result": ""}


def test_injects_after_end_turn_then_completes():
    drv = FakeDriver([
        [_assistant("我跑下测试", "Bash"), _result()],          # turn 0: tool_use turn -> wait, end_turn? text+tool
        [_assistant("测试通过了，任务完成"), _result()],          # turn 1: claims completion
    ])
    ua = FakeUserAgent(["把结果贴出来"])  # one reply then would be None
    events, injected, reason = run_loop(drv, ua, max_turns=5)
    assert reason == "completed"
    assert injected == 1
    assert drv.injected == ["把结果贴出来"]


def test_stops_when_user_agent_returns_none():
    drv = FakeDriver([
        [_assistant("改完了，等你指示"), _result()],
        [_assistant("再做一次"), _result()],
    ])
    ua = FakeUserAgent([None])  # user-agent decides no follow-up
    events, injected, reason = run_loop(drv, ua, max_turns=5)
    assert reason == "user_agent_stop"
    assert injected == 0


def test_max_turns_budget_exhausted():
    streams = [[_assistant("继续"), _result()]] * 10
    drv = FakeDriver(streams)
    ua = FakeUserAgent(["继续"] * 10)
    events, injected, reason = run_loop(drv, ua, max_turns=3)
    assert reason == "max_turns"
    assert injected == 3


def test_error_event_stops_immediately():
    drv = FakeDriver([[{"type": "error", "error": {"type": "api_error"}}]])
    ua = FakeUserAgent([])
    events, injected, reason = run_loop(drv, ua, max_turns=5)
    assert reason == "error"
    assert injected == 0
    assert any(e["type"] == "error" for e in events)


def test_completion_phrase_on_first_turn_no_inject():
    drv = FakeDriver([[_assistant("任务完成"), _result()]])
    ua = FakeUserAgent([])  # should never be called
    events, injected, reason = run_loop(drv, ua, max_turns=5)
    assert reason == "completed"
    assert injected == 0
    assert drv.injected == []


def test_on_event_callback_receives_every_event():
    seen = []
    drv = FakeDriver([[_assistant("hi"), _result()]])
    ua = FakeUserAgent([])
    run_loop(drv, ua, max_turns=5, on_event=seen.append)
    assert [e["type"] for e in seen] == ["assistant", "result"]


def test_multi_turn_chain():
    drv = FakeDriver([
        [_assistant("装依赖", "Bash"), _result()],
        [_assistant("跑测试", "Bash"), _result()],
        [_assistant("49 passing，任务完成"), _result()],
    ])
    ua = FakeUserAgent(["跑测试", "贴一下 mocha 行"])
    events, injected, reason = run_loop(drv, ua, max_turns=5)
    assert reason == "completed"
    assert injected == 2
    assert drv.injected == ["跑测试", "贴一下 mocha 行"]
