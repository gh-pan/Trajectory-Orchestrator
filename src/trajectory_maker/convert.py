"""Convert raw API call pairs into ordered spec req_*.json files.

Two-step transform (spec 09):
  1. Lift request.body.* up one level -> request.* (drop url/headers/method).
  2. Decode SSE body_raw -> full Anthropic message -> response.response_data.*.

Top-level metadata (session_id/request_id/timestamp/thinking_effort/is_garbled)
is added here — claude itself does not produce it (verified by reverse-engineering
the native binary: thinking_effort/is_garbled/response_data are 0 hits).

The RawPair input shape mirrors claude-trace's interceptor output:
  {"request": {"timestamp": <float sec>, "method", "url", "headers", "body": {...}},
   "response": {"timestamp", "status_code", "headers", "body_raw": "<SSE stream>"},
   "request_id": "req_<uuid>",   # added by our recording proxy
   "logged_at": "<iso>"}
"""

import json
import re
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

# Field order matches the golden sample's response_data exactly.
_MESSAGE_KEYS = [
    "model", "id", "type", "role", "content",
    "stop_reason", "stop_sequence", "stop_details", "usage",
]


def parse_sse_events(body_raw: str) -> list[dict]:
    """Parse an SSE stream body into a list of event dicts (the `data:` payloads).

    Malformed `data:` lines are kept as {"__parse_error": <raw>} so the caller
    can flag the result garbled.
    """
    events: list[dict] = []
    for line in body_raw.splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        try:
            events.append(json.loads(data))
        except json.JSONDecodeError:
            events.append({"__parse_error": data})
    return events


def reconstruct_message(body_raw: str) -> tuple[dict, bool]:
    """Reconstruct a full Anthropic message from an SSE stream.

    Returns (message, is_garbled). Mirrors claude-trace
    reconstructMessageFromSSE, preserving caller/service_tier/cache_creation.

    is_garbled is True when: any `data:` line failed to parse, a tool_use input
    JSON failed to parse, or a content block slot was left unfilled.
    """
    message: dict = {k: ([] if k == "content" else ({} if k == "usage" else None)) for k in _MESSAGE_KEYS}
    message["type"] = "message"
    message["role"] = "assistant"
    blocks: list[dict | None] = []
    cur_idx = -1
    garbled = False

    for ev in parse_sse_events(body_raw):
        if not isinstance(ev, dict) or "__parse_error" in ev:
            garbled = True
            continue
        etype = ev.get("type")
        if etype == "message_start":
            msg = ev.get("message", {}) or {}
            for k in _MESSAGE_KEYS:
                if k in msg and msg[k] is not None:
                    message[k] = msg[k]
        elif etype == "content_block_start":
            cur_idx = ev.get("index", -1)
            block = dict(ev.get("content_block", {}) or {})
            # tool_use input arrives as input_json_delta fragments; seed as empty
            # string so += accumulates. Anthropic/deepseek send input:{} at start,
            # which we overwrite and rebuild from deltas (parsed at content_block_stop).
            if block.get("type") == "tool_use":
                block["input"] = ""
            while len(blocks) <= cur_idx:
                blocks.append(None)
            blocks[cur_idx] = block
        elif etype == "content_block_delta":
            if 0 <= cur_idx < len(blocks) and blocks[cur_idx] is not None:
                _apply_delta(blocks[cur_idx], ev.get("delta", {}) or {})
        elif etype == "content_block_stop":
            if 0 <= cur_idx < len(blocks) and blocks[cur_idx] is not None:
                block = blocks[cur_idx]
                if block.get("type") == "tool_use" and isinstance(block.get("input"), str):
                    raw_input = block["input"]
                    try:
                        block["input"] = json.loads(raw_input) if raw_input else {}
                    except json.JSONDecodeError:
                        garbled = True
        elif etype == "message_delta":
            delta = ev.get("delta", {}) or {}
            for k in ("stop_reason", "stop_sequence", "stop_details"):
                if delta.get(k) is not None:
                    message[k] = delta[k]
            usage = ev.get("usage")
            if isinstance(usage, dict):
                merged = dict(message.get("usage") or {})
                merged.update(usage)
                message["usage"] = merged
        elif etype == "message_stop":
            pass

    message["content"] = [b for b in blocks if b is not None]
    if any(b is None for b in blocks):
        garbled = True
    return message, garbled


def _apply_delta(block: dict, delta: dict) -> None:
    dtype = delta.get("type")
    if dtype == "text_delta" and block.get("type") == "text":
        block["text"] = block.get("text", "") + delta.get("text", "")
    elif dtype == "input_json_delta" and block.get("type") == "tool_use":
        cur = block.get("input", "")
        if not isinstance(cur, str):
            cur = ""
        block["input"] = cur + delta.get("partial_json", "")
    elif dtype == "thinking_delta" and block.get("type") == "thinking":
        block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
    elif dtype == "signature_delta" and block.get("type") == "thinking":
        block["signature"] = block.get("signature", "") + delta.get("signature", "")


def iso_z(ts) -> str:
    """Convert a timestamp (float seconds / ISO string / None) to YYYY-MM-DDTHH:MM:SSZ."""
    if ts is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(ts)
    if s.endswith("Z"):
        # strip fractional seconds if present (e.g. ...T03:33:17.123Z)
        m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", s)
        return (m.group(1) if m else s) + "Z"
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", s)
    return (m.group(1) + "Z") if m else s


def _derive_request_id(resp: dict) -> str:
    body = resp.get("body")
    if isinstance(body, dict) and body.get("id"):
        return "req_" + body["id"].replace("msg_", "")[:32]
    return "req_" + _uuid.uuid4().hex


def convert_pair(raw: dict, session_id: str) -> dict:
    """Convert one RawPair dict into a spec req_*.json record."""
    req = raw.get("request", {}) or {}
    resp = raw.get("response", {}) or {}
    body = req.get("body") if isinstance(req.get("body"), dict) else {}
    request_id = raw.get("request_id") or _derive_request_id(resp)

    # step 1: lift body.* -> request.*
    request_out = dict(body)

    # step 2: decode response (SSE -> response_data, or pass-through JSON body)
    body_raw = resp.get("body_raw")
    if isinstance(body_raw, str) and body_raw.strip():
        response_data, is_garbled = reconstruct_message(body_raw)
    elif isinstance(resp.get("body"), dict):
        response_data, is_garbled = resp["body"], False
    else:
        response_data, is_garbled = {}, True

    thinking_effort = None
    oc = body.get("output_config")
    if isinstance(oc, dict):
        thinking_effort = oc.get("effort")

    return {
        "session_id": session_id,
        "request_id": request_id,
        "timestamp": iso_z(req.get("timestamp")),
        "thinking_effort": thinking_effort,
        "is_garbled": is_garbled,
        "request": request_out,
        "response": {"response_data": response_data},
    }


def convert_dir(raw_calls_dir: Path, out_dir: Path, session_id: str) -> int:
    """Convert RawPairs into chronologically numbered JSON files.

    Output names use ``req_<sequence>_<request-id-suffix>.json``.  The sequence
    follows the original request timestamp, while the record's internal
    ``request_id`` remains unchanged.  Equal or unparseable timestamps retain
    their deterministic source-file/line order.

    Each raw file holds one RawPair per line. Returns the number converted.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pending: list[tuple[tuple, dict]] = []
    ordinal = 0
    for f in sorted(raw_calls_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            rec = convert_pair(raw, session_id)
            pending.append((_request_timestamp_sort_key(raw, ordinal), rec))
            ordinal += 1

    pending.sort(key=lambda item: item[0])
    for sequence, (_sort_key, rec) in enumerate(pending, start=1):
        request_id = str(rec["request_id"])
        suffix = request_id[4:] if request_id.startswith("req_") else request_id
        filename = f"req_{sequence:03d}_{suffix}.json"
        (out_dir / filename).write_text(
            json.dumps(rec, ensure_ascii=False), encoding="utf-8"
        )
    return len(pending)


def _request_timestamp_sort_key(raw: dict, ordinal: int) -> tuple:
    """Return a total ordering for a RawPair request timestamp."""
    request = raw.get("request") if isinstance(raw.get("request"), dict) else {}
    timestamp = request.get("timestamp")
    if isinstance(timestamp, (int, float)):
        return (0, float(timestamp), "", ordinal)
    if isinstance(timestamp, str):
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return (0, parsed.timestamp(), "", ordinal)
        except (ValueError, OverflowError):
            return (1, 0.0, "", ordinal)
    return (2, 0.0, "", ordinal)
