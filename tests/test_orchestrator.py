from trajectory_maker.orchestrator import detect_termination, COMPLETION_PHRASES


def _assistant(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _result():
    return {"type": "result", "subtype": "success", "result": ""}


def test_completed_when_result_and_completion_phrase():
    events = [_assistant("我来做。"), _assistant("任务完成。"), _result()]
    assert detect_termination(events) == "completed"


def test_stopped_without_claim_when_result_no_phrase():
    events = [_assistant("我先看看。"), _result()]
    assert detect_termination(events) == "stopped_without_claim"


def test_timeout_when_no_result_and_timeout_flag():
    events = [_assistant("工作中...")]
    assert detect_termination(events, timeout=True) == "timeout"


def test_max_turns_when_no_result_and_max_turns_flag():
    events = [_assistant("工作中...")]
    assert detect_termination(events, max_turns=True) == "max_turns"


def test_crashed_when_error_event():
    events = [{"type": "error", "error": {"type": "api_error"}}]
    assert detect_termination(events) == "crashed"


def test_auth_error_takes_precedence():
    events = [{"type": "error", "error": {"type": "authentication_error"}}]
    assert detect_termination(events) == "auth_error"


def test_completion_phrases_cover_multilingual():
    for phrase in ["完成", "已完成", "任务完成", "完成了", "finished", "done", "all done", "complete", "completed"]:
        events = [_assistant(phrase), _result()]
        assert detect_termination(events) == "completed", f"phrase {phrase!r} not detected"
