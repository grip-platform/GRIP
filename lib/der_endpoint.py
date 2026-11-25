"""grip-der-serve — the Decision Evidence Receipt wedge endpoint (#527, #546).

A minimal HTTP wrapper over lib/der_receipt: POST a decision, get back a receipt
a third party verifies against Bitcoin with zero vendor trust.

  POST /receipt   body = a sealed decision record (or {"record": {...}})
                  -> 200 the Decision Evidence Receipt (Ed25519 sig + OTS anchor)
                  query ?stamp=false skips the (network) OpenTimestamps stamp
  POST /verify    body = a receipt (or {"receipt": {...}})
                  -> 200 {ok, checks, detail}   query ?anchor=false skips the OTS check
  GET  /health    -> 200 service status

127.0.0.1 by default; optional bearer token. stdlib http.server only — the receipt
crypto lives in lib/der_receipt (stdlib + cryptography + ots). Matches the
lib/guard_endpoint.py pattern. Task #546, V>> 2026-07-18.
"""
from __future__ import annotations

import argparse
import hmac
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from lib.der_receipt import DER_VERSION, build_receipt, verify_receipt  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8646
SERVICE = "grip-der-endpoint"


class _DERHandler(BaseHTTPRequestHandler):
    """Bind an auth token via make_handler(); routes wrap lib.der_receipt."""

    auth_token: Optional[str] = None

    def log_message(self, *_args):  # silence default stderr spam
        return

    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        if not self.auth_token:
            return True
        header = self.headers.get("Authorization") or ""
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix):], self.auth_token)

    def _read_json(self) -> Optional[dict]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
            return payload
        except (ValueError, TypeError):
            self._send(400, {"ok": False, "error": "body must be a JSON object"})
            return None

    def _flag(self, name: str, default: bool) -> bool:
        vals = parse_qs(urlparse(self.path).query).get(name)
        return default if not vals else vals[0].lower() not in ("false", "0", "no")

    def do_GET(self):  # noqa: N802 (stdlib naming)
        if urlparse(self.path).path.rstrip("/") in ("/health", ""):
            self._send(200, {"ok": True, "service": SERVICE,
                             "der_version": DER_VERSION,
                             "capabilities": ["receipt", "verify"],
                             "auth": bool(self.auth_token)})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802 (stdlib naming)
        dispatch = {"/receipt": self._do_receipt, "/verify": self._do_verify}
        handler = dispatch.get(urlparse(self.path).path.rstrip("/"))
        if handler is None:
            self._send(404, {"ok": False, "error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        body = self._read_json()
        if body is not None:
            handler(body)

    def _do_receipt(self, body: dict) -> None:
        record = body["record"] if "record" in body else body
        try:
            receipt = build_receipt(record, do_stamp=self._flag("stamp", True))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"ok": False, "error": f"build failed: {type(exc).__name__}: {exc}"})
            return
        self._send(200, receipt)

    def _do_verify(self, body: dict) -> None:
        receipt = body["receipt"] if "receipt" in body else body
        try:
            result = verify_receipt(receipt, check_anchor=self._flag("anchor", True))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"ok": False, "error": f"verify failed: {type(exc).__name__}: {exc}"})
            return
        self._send(200, result)


def make_handler(auth_token: Optional[str] = None):
    return type("_BoundDERHandler", (_DERHandler,), {"auth_token": auth_token})


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          auth_token: Optional[str] = None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(auth_token))
    print(f"[{SERVICE}] http://{host}:{server.server_address[1]}  "
          f"(POST /receipt · POST /verify · GET /health)")
    return server


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Serve the DER wedge endpoint")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--token", default=None, help="optional bearer token")
    a = ap.parse_args(argv)
    server = serve(a.host, a.port, a.token)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
