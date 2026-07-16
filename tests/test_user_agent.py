"""Tests for user_agent — uses a FakeDriver to verify turn/react/stop logic
without a real claude process."""
from trajectory_maker.user_agent import UserAgent, STOP_SENTINELS


class FakeDriver:
    """Records sent messages; returns canned event streams per turn."""
    def __init__(self, turn_responses: list[list[dict]]):
        self._responses = list(turn_responses)
        self.sent: list[str] = []
        self.closed = False

    def send_user_message(self, text):
        self.sent.append(text)

    def events(self):
        if not self._responses:
            return
        for ev in self._responses.pop(0):
            yield ev

    def kill(self):
        pass

    def close(self):
        self.closed = True


def _assistant(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}

def _result():
    return {"type": "result", "subtype": "success", "result": ""}


def test_prime_consumes_first_turn():
    drv = FakeDriver([[_assistant("ok 我懂了"), _result()]])
    ua = UserAgent("做一个 calc 包", drv=drv)
    assert len(drv.sent) == 1
    assert "calc 包" in drv.sent[0]
    assert "角色" in drv.sent[0] or "扮演" in drv.sent[0]
    ua.close()
    assert drv.closed


def test_react_returns_assistant_text():
    drv = FakeDriver([
        [_assistant("懂了"), _result()],                     # prime
        [_assistant("你先 npm test 把计数贴出来"), _result()],  # react
    ])
    ua = UserAgent("task", drv=drv)
    out = ua.react("助手: 我已经装好依赖了")
    assert out == "你先 npm test 把计数贴出来"
    # react prompt includes the subject's output
    assert "我已经装好依赖了" in drv.sent[-1]


def test_react_returns_none_on_stop_sentinel():
    drv = FakeDriver([
        [_assistant("懂了"), _result()],
        [_assistant("[STOP]"), _result()],
    ])
    ua = UserAgent("task", drv=drv)
    assert ua.react("助手: 全部测试通过了") is None


def test_react_returns_none_on_empty():
    drv = FakeDriver([
        [_assistant("懂了"), _result()],
        [_assistant(""), _result()],
    ])
    ua = UserAgent("task", drv=drv)
    assert ua.react("助手: done") is None


def test_react_strips_embedded_sentinel():
    drv = FakeDriver([
        [_assistant("懂了"), _result()],
        [_assistant("再跑一次 npm test [STOP]"), _result()],
    ])
    ua = UserAgent("task", drv=drv)
    out = ua.react("助手: 改完了")
    assert out == "再跑一次 npm test"


def test_multiple_reacts_share_one_driver_session():
    """user-agent is resident: one driver, multiple turns, memory implied."""
    drv = FakeDriver([
        [_assistant("懂了"), _result()],
        [_assistant("先 cd repo"), _result()],
        [_assistant("把 mocha 那行贴出来"), _result()],
        [_assistant("[STOP]"), _result()],
    ])
    ua = UserAgent("task", drv=drv)
    assert ua.react("r1") == "先 cd repo"
    assert ua.react("r2") == "把 mocha 那行贴出来"
    assert ua.react("r3") is None
    # 1 prime + 3 reacts = 4 sent
    assert len(drv.sent) == 4
