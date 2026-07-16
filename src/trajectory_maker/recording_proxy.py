"""Recording HTTP proxy (B1): captures every /v1/messages call's request body
and SSE response body_raw, transparently forwarding to the real endpoint.

The subject claude's ANTHROPIC_BASE_URL points at this proxy (plain HTTP on
127.0.0.1). The proxy forwards to the real HTTPS endpoint and records each
request/response pair as a RawPair (claude-trace shape) under raw_calls/.

The proxy only records — it does not lift/decode (that's convert.py's job).
Credentials in headers are forwarded to the real endpoint but redacted in the
recorded RawPair, so the on-disk record never holds the key.
"""

import http.client
import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

_SENSITIVE_HEADERS = {"authorization", "x-api-key", "x-auth-token",
                      "anthropic-auth-token", "cookie", "set-cookie"}
# request/response headers we strip when forwarding back to the client (we run
# in Connection: close mode, managing framing ourselves).
_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate",
               "proxy-authorization", "te", "trailers", "transfer-encoding",
               "upgrade", "content-length"}


def _redact_headers(headers: dict) -> dict:
    out = {}
    for k, v in headers.items():
        out[k] = "<redacted>" if k.lower() in _SENSITIVE_HEADERS else v
    return out


def _new_request_id() -> str:
    return "req_" + uuid.uuid4().hex


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # silence default stderr access logging (we run embedded)
    def log_message(self, *args, **kwargs):
        pass

    @property
    def _proxy(self) -> "RecordingProxy":
        return self.server.proxy  # type: ignore[attr-defined]

    def do_POST(self):
        # strip query string (claude sends /v1/messages?beta=true)
        path_no_query = self.path.split("?", 1)[0]
        self._handle(record=path_no_query.rstrip("/").endswith("/v1/messages"))

    def do_GET(self):
        self._handle(record=False)

    def _handle(self, record: bool) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body_bytes = self.rfile.read(length) if length else b""

        request_id = _new_request_id() if record else None
        req_timestamp = time.time()

        # parse real endpoint
        real = self._proxy.real_url  # urlparse result
        conn_cls = http.client.HTTPSConnection if real.scheme == "https" else http.client.HTTPConnection
        forward_path = (real.path.rstrip("/") + self.path) if real.path else self.path

        # forward headers: copy client headers, drop hop-by-hop + host (http.client sets Host)
        fwd_headers: dict[str, str] = {}
        for k, v in self.headers.items():
            if k.lower() in _HOP_BY_HOP or k.lower() == "host":
                continue
            fwd_headers[k] = v

        resp = None
        try:
            conn = conn_cls(real.hostname, real.port or (443 if real.scheme == "https" else 80),
                            timeout=self._proxy.timeout)
            conn.request(self.command, forward_path, body=body_bytes, headers=fwd_headers)
            resp = conn.getresponse()
        except Exception as exc:
            self._send_error(502, f"upstream connect failed: {exc}")
            return

        # stream the response back to the client while capturing body_raw
        self.send_response(resp.status, resp.reason)
        sent_headers: dict[str, str] = {}
        for k, v in resp.getheaders():
            if k.lower() in _HOP_BY_HOP:
                continue
            self.send_header(k, v)
            sent_headers[k] = v
        self.send_header("Connection", "close")
        self.end_headers()

        chunks: list[bytes] = []
        try:
            while True:
                chunk = resp.read1(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                chunks.append(chunk)
        except Exception:
            # client disconnected mid-stream — still record what we got
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if record and request_id is not None:
            self._record(request_id, req_timestamp, body_bytes, resp, chunks, fwd_headers, sent_headers)

    def _send_error(self, code: int, msg: str) -> None:
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _record(self, request_id, req_ts, body_bytes, resp, chunks, fwd_headers, sent_headers) -> None:
        # parse request body if JSON
        try:
            body_obj = json.loads(body_bytes) if body_bytes else None
        except (json.JSONDecodeError, ValueError):
            body_obj = body_bytes.decode("utf-8", errors="replace")

        body_raw = b"".join(chunks).decode("utf-8", errors="replace")
        pair = {
            "request": {
                "timestamp": req_ts,
                "method": self.command,
                "url": self.path,
                "headers": _redact_headers(fwd_headers),
                "body": body_obj,
            },
            "response": {
                "timestamp": time.time(),
                "status_code": resp.status,
                "headers": _redact_headers(sent_headers),
                "body_raw": body_raw,
            },
            "request_id": request_id,
            "logged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        # atomic-ish write: one file per pair
        out = self._proxy.raw_calls_dir / f"{request_id}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")
        self._proxy._recorded.append(request_id)


class RecordingProxy:
    """A local plain-HTTP proxy that records /v1/messages calls."""

    def __init__(self, real_base_url: str, raw_calls_dir: Path,
                 host: str = "127.0.0.1", port: int = 0, timeout: float = 600.0):
        self.real_url = urlparse(real_base_url)
        if not self.real_url.scheme or not self.real_url.hostname:
            raise ValueError(f"invalid real_base_url: {real_base_url}")
        self.raw_calls_dir = Path(raw_calls_dir)
        self.timeout = timeout
        self._server = ThreadingHTTPServer((host, port), _ProxyHandler)
        self._server.proxy = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None
        self._recorded: list[str] = []

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def recorded_request_ids(self) -> list[str]:
        return list(self._recorded)

    def start(self) -> str:
        self.raw_calls_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)
