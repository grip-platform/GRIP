#!/usr/bin/env python3
"""
GRIP Metrics Server - serves metrics.jsonl as JSON for Grafana Infinity.

Security (v1.1.0):
  - Bearer auth required on all endpoints except /health
  - Binds to 127.0.0.1 only (use Tailscale for remote access)
  - CORS restricted to localhost Grafana origins
  - Auth credential loaded from file or env

Version: 1.1.0
"""

import json
import os
import secrets
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

METRICS_FILE = Path.home() / ".claude" / "metrics.jsonl"
CREDENTIAL_FILE = Path.home() / ".claude" / ".metrics-auth"
PORT = int(os.environ.get("GRIP_METRICS_PORT", "9100"))
BIND_ADDR = "127.0.0.1"

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def _sanitise_header_value(value: str) -> str:
    """Strip CR/LF characters to prevent HTTP response splitting."""
    return value.replace("\r", "").replace("\n", "")


def load_auth_credential() -> str:
    """Load bearer credential from file. Generate if missing."""
    if CREDENTIAL_FILE.exists():
        value = CREDENTIAL_FILE.read_text().strip()
        if value:
            return value

    value = secrets.token_urlsafe(32)
    CREDENTIAL_FILE.write_text(value + "\n")
    CREDENTIAL_FILE.chmod(0o600)
    print(f"Generated metrics auth credential: {value}")
    print(f"Saved to: {CREDENTIAL_FILE}")
    return value


AUTH_CREDENTIAL = load_auth_credential()


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_json({"status": "ok"})
            return

        if not self._check_auth():
            return

        if self.path == "/metrics":
            self.serve_metrics()
        elif self.path == "/metrics/summary":
            self.serve_summary()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_response(204)
            self.send_header(
                "Access-Control-Allow-Origin", _sanitise_header_value(origin)
            )
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization")
            self.send_header("Access-Control-Max-Age", "3600")
            self.end_headers()
        else:
            self.send_error(403)

    def _check_auth(self) -> bool:
        """Verify Bearer credential. Returns False and sends 401 if invalid."""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="GRIP Metrics"')
            self.send_header("Content-Type", "application/json")
            content = json.dumps({"error": "Bearer credential required"}).encode()
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
            return False

        provided = auth[7:].strip()
        if not secrets.compare_digest(provided, AUTH_CREDENTIAL):
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            content = json.dumps({"error": "Invalid credential"}).encode()
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
            return False

        return True

    def serve_metrics(self):
        """Serve all metrics as JSON array."""
        self.send_json(self._load_metrics())

    def serve_summary(self):
        """Serve aggregated summary."""
        metrics = self._load_metrics()
        counts = {}
        for m in metrics:
            event = m.get("event", "unknown")
            counts[event] = counts.get(event, 0) + 1
        self.send_json([{"event": k, "count": v} for k, v in counts.items()])

    @staticmethod
    def _load_metrics() -> list:
        metrics = []
        if METRICS_FILE.exists():
            with open(METRICS_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            metrics.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        return metrics

    def send_json(self, data):
        content = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(content))
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header(
                "Access-Control-Allow-Origin", _sanitise_header_value(origin)
            )
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print(f"GRIP Metrics Server on http://{BIND_ADDR}:{PORT}")
    print(f"  /metrics         - All events (auth required)")
    print(f"  /metrics/summary - Aggregated counts (auth required)")
    print(f"  /health          - Health check (no auth)")
    print(f"  Credential file: {CREDENTIAL_FILE}")
    HTTPServer((BIND_ADDR, PORT), MetricsHandler).serve_forever()
