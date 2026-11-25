"""Showcase HTTP server — multi-project, Range-streamed MP4, 1×1 pixel + events.

Endpoints
---------
  GET  /v/<project>/<recipient>            HTML landing (logs page_view)
  GET  /v/<project>/<recipient>/m.mp4      Range-supported MP4 (logs video_first_byte)
  GET  /p/<project>/<recipient>.png        1×1 GIF pixel (logs pixel)
  GET  /e?p=&id=&e=&t=                     play/pause/ended/progress beacon
  GET  /healthz                            "ok"
"""
from __future__ import annotations

import http.server
import os
import re
import socketserver
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .brand import render_page
from .config import load_config
from .filters import is_valid_slug
from .log import log_event

PIXEL_GIF = bytes.fromhex(
    "47494638396101000100800000000000FFFFFF21F90401000000002C0EXAMPLE01"
)
EVENT_TYPES = ("play", "pause", "ended", "progress")
PIXEL_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "showcase/0.1"

    def log_message(self, *a, **kw):
        return

    def _meta(self) -> dict:
        return {
            "ip": self.client_address[0],
            "ua": self.headers.get("User-Agent", ""),
            "ref": self.headers.get("Referer", ""),
        }

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Errors must NEVER be cached — a transient 404 must not persist at
        # the CF edge or in browser disk cache after the underlying bug ships.
        if code >= 400:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def _serve_page(self, project: str, recipient: str):
        cfg = load_config(project)
        if not cfg:
            return self._send(404, b"")
        ts = int(datetime.now().timestamp())
        html = render_page(cfg, project, recipient, ts).encode()
        self._send(200, html, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
        log_event(project, {"id": recipient, "event": "page_view", **self._meta()})

    def _serve_pixel(self, project: str, recipient: str):
        self._send(200, PIXEL_GIF, "image/gif", PIXEL_HEADERS)
        log_event(project, {"id": recipient, "event": "pixel", **self._meta()})

    def _video_path(self, project: str) -> Path | None:
        cfg = load_config(project) or {}
        p = (cfg.get("video", {}) or {}).get("path", "")
        path = Path(p) if p else None
        return path if (path and path.exists()) else None

    def _write_video_range(self, video_path: Path, start: int, end: int):
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Range", f"bytes {start}-{end}/{video_path.stat().st_size}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with video_path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(chunk)

    def _write_video_full(self, video_path: Path):
        size = video_path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with video_path.open("rb") as f:
            self.wfile.write(f.read())

    def _serve_video(self, project: str, recipient: str):
        video_path = self._video_path(project)
        if video_path is None:
            return self._send(500, b"video missing\n")
        size = video_path.stat().st_size
        rng = self.headers.get("Range", "")
        m = re.match(r"bytes=(\d+)-(\d*)", rng)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self._write_video_range(video_path, start, end)
            if start == 0:
                log_event(project, {"id": recipient, "event": "video_first_byte", **self._meta()})
        else:
            self._write_video_full(video_path)
            log_event(project, {"id": recipient, "event": "video_full_fetch", **self._meta()})

    def _serve_event(self, query: str):
        params = parse_qs(query, keep_blank_values=True)
        project = (params.get("p", [""])[0]).lower()
        rid = (params.get("id", [""])[0]).lower()
        ev = (params.get("e", [""])[0]).lower()
        try:
            t = int(params.get("t", ["0"])[0])
        except ValueError:
            t = 0
        if is_valid_slug(project) and is_valid_slug(rid) and ev in EVENT_TYPES:
            log_event(project, {"id": rid, "event": ev, "t": t, **self._meta()})
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch(self, path: str):
        m = re.match(r"^/v/([a-z0-9_-]+)/([a-z0-9_-]+)/?$", path)
        if m and is_valid_slug(m.group(1)) and is_valid_slug(m.group(2)):
            return (self._serve_page, (m.group(1), m.group(2)))
        m = re.match(r"^/v/([a-z0-9_-]+)/([a-z0-9_-]+)/m\.mp4$", path)
        if m and is_valid_slug(m.group(1)) and is_valid_slug(m.group(2)):
            return (self._serve_video, (m.group(1), m.group(2)))
        m = re.match(r"^/p/([a-z0-9_-]+)/([a-z0-9_-]+)\.png$", path)
        if m and is_valid_slug(m.group(1)) and is_valid_slug(m.group(2)):
            return (self._serve_pixel, (m.group(1), m.group(2)))
        if path == "/healthz":
            return (lambda: self._send(200, b"ok\n"), ())
        if path == "/":
            return (lambda: self._send(200, b"showcase\n"), ())
        return None

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/e":
            return self._serve_event(url.query)
        d = self._dispatch(url.path)
        if d is None:
            return self._send(404, b"")
        handler, args = d
        return handler(*args)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    host = os.environ.get("SHOWCASE_HOST", "127.0.0.1")
    port = int(os.environ.get("SHOWCASE_PORT", "9000"))
    print(f"showcase-server · http://{host}:{port}", flush=True)
    with ThreadedHTTPServer((host, port), Handler) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nshutdown", flush=True)
