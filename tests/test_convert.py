"""Tests for convert.py — raw RawPair -> spec req_*.json.

The golden round-trip test reverse-generates an SSE stream from a real sample's
response_data, decodes it, and asserts exact equality (fields, values, order) —
proving the decoder matches the sample's collection-system output.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trajectory_maker.convert import (
    convert_pair, convert_dir, reconstruct_message, iso_z,
)

GOLDEN_DIR = Path(
    "/Users/larr/Desktop/EntropyOrder/traj/YX_20260716_02_sample20_v2/"
    "YX_20260716_02_sample20/f54d9034-71e2-4595-b34a-411a0fbcd99c"
)


def _sse(events: list[dict]) -> str:
    return "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)


def _generate_sse(rd: dict) -> str:
    """Reverse-generate an SSE stream from a response_data message dict.

    Produces the same event shape Anthropic streams: message_start, per-block
    start/delta(s)/stop, message_delta, message_stop. tool_use input is emitted
    as a single input_json_delta fragment = JSON.stringify(input).
    """
    events: list[dict] = []
    base_usage = rd.get("usage") or {}
    msg_start = {
        "type": "message", "id": rd.get("id"), "model": rd.get("model"),
        "role": rd.get("role", "assistant"), "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": base_usage,
    }
    events.append({"type": "message_start", "message": msg_start})
    for i, b in enumerate(rd.get("content", [])):
        bt = b.get("type")
        if bt == "thinking":
            events.append({"type": "content_block_start", "index": i,
                           "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
            events.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "thinking_delta", "thinking": b.get("thinking", "")}})
            events.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "signature_delta", "signature": b.get("signature", "")}})
        elif bt == "text":
            events.append({"type": "content_block_start", "index": i,
                           "content_block": {"type": "text", "text": ""}})
            events.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "text_delta", "text": b.get("text", "")}})
        elif bt == "tool_use":
            cb = {"type": "tool_use", "id": b.get("id"), "name": b.get("name"), "input": ""}
            if "caller" in b:
                cb["caller"] = b["caller"]
            events.append({"type": "content_block_start", "index": i, "content_block": cb})
            events.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "input_json_delta",
                                     "partial_json": json.dumps(b.get("input", {}), ensure_ascii=False)}})
        events.append({"type": "content_block_stop", "index": i})
    events.append({"type": "message_delta",
                   "delta": {"stop_reason": rd.get("stop_reason"),
                             "stop_sequence": rd.get("stop_sequence"),
                             "stop_details": rd.get("stop_details")},
                   "usage": {"output_tokens": base_usage.get("output_tokens", 0)}})
    events.append({"type": "message_stop"})
    return _sse(events)


def test_iso_z_from_epoch():
    ts = datetime(2026, 7, 16, 3, 33, 17, tzinfo=timezone.utc).timestamp()
    assert iso_z(ts) == "2026-07-16T03:33:17Z"


def test_iso_z_from_iso_string_with_fractional():
    assert iso_z("2026-07-16T03:33:17.123Z") == "2026-07-16T03:33:17Z"


def test_lift_request_body_and_drop_transport_fields():
    ts = datetime(2026, 7, 16, 3, 33, 17, tzinfo=timezone.utc).timestamp()
    raw = {
        "request": {
            "timestamp": ts, "method": "POST",
            "url": "https://api.anthropic.com/v1/messages",
            "headers": {"x-api-key": "sk-x"},
            "body": {
                "model": "claude-opus-4-8",
                "messages": [{"role": "user", "content": "hi"}],
                "system": [{"type": "text", "text": "sys"}],
                "tools": [], "metadata": {"user_id": "{}"}, "max_tokens": 64000,
                "thinking": {"type": "adaptive", "display": "summarized"},
                "output_config": {"effort": "xhigh"},
                "stream": True, "temperature": 1.0, "top_p": 0.95,
            },
        },
        "response": {"status_code": 200, "body_raw": ""},
        "request_id": "req_abc123",
    }
    rec = convert_pair(raw, "sess-1")
    assert rec["session_id"] == "sess-1"
    assert rec["request_id"] == "req_abc123"
    assert rec["timestamp"] == "2026-07-16T03:33:17Z"
    assert rec["thinking_effort"] == "xhigh"
    assert rec["is_garbled"] is True  # empty body_raw
    # body lifted; transport fields dropped
    assert rec["request"]["model"] == "claude-opus-4-8"
    assert rec["request"]["output_config"] == {"effort": "xhigh"}
    assert "url" not in rec["request"]
    assert "headers" not in rec["request"]
    assert "method" not in rec["request"]


def test_sse_decode_thinking_text_tooluse_with_caller():
    events = [
        {"type": "message_start", "message": {"id": "msg_1", "model": "claude-opus-4-8",
            "type": "message", "role": "assistant", "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0,
            "service_tier": "standard", "inference_geo": "not_available",
            "cache_creation_input_tokens": 211, "cache_read_input_tokens": 37027,
            "cache_creation": {"ephemeral_5m_input_tokens": 211}}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "let me think"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig123"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "hello"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": "", "caller": {"type": "direct"}}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"command":"ls"}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None, "stop_details": None},
         "usage": {"output_tokens": 138}},
        {"type": "message_stop"},
    ]
    msg, garbled = reconstruct_message(_sse(events))
    assert garbled is False
    assert msg["id"] == "msg_1"
    assert msg["model"] == "claude-opus-4-8"
    assert msg["stop_reason"] == "tool_use"
    assert msg["usage"]["output_tokens"] == 138
    assert msg["usage"]["service_tier"] == "standard"
    assert msg["usage"]["inference_geo"] == "not_available"
    assert msg["usage"]["input_tokens"] == 10
    assert msg["usage"]["cache_creation"] == {"ephemeral_5m_input_tokens": 211}
    assert msg["content"][0] == {"type": "thinking", "thinking": "let me think", "signature": "sig123"}
    assert msg["content"][1] == {"type": "text", "text": "hello"}
    tu = msg["content"][2]
    assert tu["type"] == "tool_use" and tu["id"] == "toolu_1" and tu["name"] == "Bash"
    assert tu["input"] == {"command": "ls"}
    assert tu["caller"] == {"type": "direct"}


def test_message_delta_updates_stop_reason_only_when_present():
    events = [
        {"type": "message_start", "message": {"id": "m", "model": "x", "type": "message",
            "role": "assistant", "content": [], "stop_reason": None, "usage": {"input_tokens": 5}}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ]
    msg, garbled = reconstruct_message(_sse(events))
    assert msg["stop_reason"] == "end_turn"
    assert msg["stop_sequence"] is None
    assert msg["stop_details"] is None
    assert garbled is False


def test_garbled_when_tool_input_json_invalid():
    events = [
        {"type": "message_start", "message": {"id": "m", "model": "x", "type": "message", "role": "assistant", "content": []}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t", "name": "Bash", "input": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{not json"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]
    msg, garbled = reconstruct_message(_sse(events))
    assert garbled is True
    # input stays as the raw string when parse fails
    assert msg["content"][0]["input"] == "{not json"


@pytest.mark.skipif(not GOLDEN_DIR.is_dir(), reason="golden sample not available")
def test_roundtrip_golden_sample_exact():
    """Decode our reverse-generated SSE of a real sample's response_data;
    assert the result matches the sample byte-for-byte (fields, values, order)."""
    f = GOLDEN_DIR / "req_9e4e3b0f2cf742deb8da3142550e9626.json"
    rd = json.loads(f.read_text())["response"]["response_data"]
    msg, garbled = reconstruct_message(_generate_sse(rd))
    assert garbled is False
    assert json.dumps(msg, ensure_ascii=False) == json.dumps(rd, ensure_ascii=False)


@pytest.mark.skipif(not GOLDEN_DIR.is_dir(), reason="golden sample not available")
def test_roundtrip_golden_sample_all_reqs():
    """Every req in the golden session round-trips through decode cleanly."""
    files = sorted(GOLDEN_DIR.glob("req_*.json"))
    assert len(files) > 50
    for f in files:
        rd = json.loads(f.read_text())["response"]["response_data"]
        msg, garbled = reconstruct_message(_generate_sse(rd))
        assert not garbled, f"{f.name} decoded garbled"
        assert json.dumps(msg, ensure_ascii=False) == json.dumps(rd, ensure_ascii=False), f"{f.name} mismatch"




def test_tool_use_input_dict_at_start_handled():
    """deepseek/anthropic send content_block_start with input:{} (dict);
    reconstruct must seed as string and accumulate, not dict+str."""
    events = [
        {"type": "message_start", "message": {"id": "m", "model": "x", "type": "message", "role": "assistant", "content": [], "usage": {"input_tokens": 1}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "t", "name": "Bash", "input": {}, "caller": {"type": "direct"}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"command":"ls"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]
    msg, garbled = reconstruct_message(_sse(events))
    assert garbled is False
    tu = msg["content"][0]
    assert tu["input"] == {"command": "ls"}
    assert tu["caller"] == {"type": "direct"}


def test_convert_dir_writes_one_json_per_raw(tmp_path):
    raw_dir = tmp_path / "raw_calls"
    raw_dir.mkdir()
    ts = datetime(2026, 7, 16, 3, 33, 17, tzinfo=timezone.utc).timestamp()
    raw = {
        "request": {"timestamp": ts, "body": {"model": "m", "messages": [], "output_config": {"effort": "high"}}},
        "response": {"body_raw": _sse([
            {"type": "message_start", "message": {"id": "msg_1", "model": "m", "type": "message", "role": "assistant", "content": [], "usage": {"input_tokens": 1}}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ])},
        "request_id": "req_xyz",
    }
    (raw_dir / "req_xyz.jsonl").write_text(json.dumps(raw) + "\n")
    out_dir = tmp_path / "out"
    n = convert_dir(raw_dir, out_dir, "sess-9")
    assert n == 1
    out = json.loads((out_dir / "req_xyz.json").read_text())
    assert out["session_id"] == "sess-9"
    assert out["request_id"] == "req_xyz"
    assert out["thinking_effort"] == "high"
    assert out["is_garbled"] is False
    assert out["response"]["response_data"]["stop_reason"] == "end_turn"
