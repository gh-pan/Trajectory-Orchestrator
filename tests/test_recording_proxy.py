"""Tests for recording_proxy — start proxy + fake SSE endpoint, assert recording."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from trajectory_maker.recording_proxy import RecordingProxy


# A fake Anthropic endpoint: echoes a canned SSE stream for any POST /v1/messages.
SSE_BODY = (
    "data: " + json.dumps({"type": "message_start", "message": {
        "id": "msg_test", "model": "m", "type": "message", "role": "assistant",
        "content": [], "stop_reason": None, "usage": {"input_tokens": 3}}}) + "\n\n"
    "data: " + json.dumps({"type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""}}) + "\n\n"
    "data: " + json.dumps({"type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "pong"}}) + "\n\n"
    "data: " + json.dumps({"type": "content_block_stop", "index": 0}) + "\n\n"
    "data: " + json.dumps({"type": "message_delta",
        "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}) + "\n\n"
    "data: " + json.dumps({"type": "message_stop"}) + "\n\n"
)


class _FakeAnthropicHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args, **kwargs):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        # write as chunked: <hexlen>\r\n<data>\r\n ... 0\r\n\r\n
        data = SSE_BODY.encode()
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n0\r\n\r\n")
        self.wfile.flush()


def _start_fake_endpoint() -> tuple[str, ThreadingHTTPServer]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAnthropicHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    host, port = srv.server_address[:2]
    return f"http://127.0.0.1:{port}", srv


def _send_messages(base_url: str, messages: list, apikey: str = "sk-secret") -> tuple[int, str]:
    import http.client
    u = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(base_url)
    conn = http.client.HTTPConnection(u.hostname, u.port)
    body = json.dumps({"model": "m", "messages": messages, "stream": True})
    conn.request("POST", "/v1/messages", body=body,
                 headers={"Content-Type": "application/json",
                          "x-api-key": apikey,
                          "anthropic-version": "2023-06-01"})
    r = conn.getresponse()
    data = r.read().decode()
    conn.close()
    return r.status, data


def test_proxy_records_pair_and_forwards_sse(tmp_path: Path):
    real_url, fake_srv = _start_fake_endpoint()
    try:
        proxy = RecordingProxy(real_url, tmp_path / "raw_calls")
        proxy.start()
        try:
            status, body = _send_messages(proxy.base_url,
                                          [{"role": "user", "content": "ping"}])
            assert status == 200
            # SSE forwarded intact
            assert "message_start" in body
            assert "pong" in body
            assert "message_stop" in body
            # one pair recorded
            rids = proxy.recorded_request_ids
            assert len(rids) == 1
            rid = rids[0]
            assert rid.startswith("req_")
            raw_file = tmp_path / "raw_calls" / f"{rid}.jsonl"
            assert raw_file.is_file()
            pair = json.loads(raw_file.read_text())
            assert pair["request_id"] == rid
            assert pair["request"]["method"] == "POST"
            assert pair["request"]["body"]["model"] == "m"
            assert pair["response"]["status_code"] == 200
            assert "message_start" in pair["response"]["body_raw"]
            assert "pong" in pair["response"]["body_raw"]
            # api key redacted in recorded headers (but was forwarded to upstream)
            assert pair["request"]["headers"]["x-api-key"] == "<redacted>"
            assert "sk-secret" not in json.dumps(pair)
        finally:
            proxy.stop()
    finally:
        fake_srv.shutdown()


def test_proxy_forwards_apikey_to_upstream(tmp_path: Path):
    """The real endpoint must receive the original api key (not redacted)."""
    received = {}

    class Handler(_FakeAnthropicHandler):
        def do_POST(self):
            received["x-api-key"] = self.headers.get("x-api-key")
            super().do_POST()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    real_url = f"http://127.0.0.1:{port}"
    try:
        proxy = RecordingProxy(real_url, tmp_path / "raw_calls")
        proxy.start()
        try:
            _send_messages(proxy.base_url, [{"role": "user", "content": "hi"}], apikey="sk-real-123")
            assert received.get("x-api-key") == "sk-real-123"
        finally:
            proxy.stop()
    finally:
        srv.shutdown()


def test_proxy_only_records_v1_messages(tmp_path: Path):
    proxy = RecordingProxy("http://127.0.0.1:1", tmp_path / "raw_calls")
    proxy.start()
    try:
        import http.client
        u = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(proxy.base_url)
        # a GET to / (not /v1/messages) — upstream will fail, but recording must stay empty
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
        try:
            conn.request("GET", "/something")
            conn.getresponse().read()
        except Exception:
            pass
        conn.close()
        # no pair recorded for non-/v1/messages
        assert proxy.recorded_request_ids == []
    finally:
        proxy.stop()
