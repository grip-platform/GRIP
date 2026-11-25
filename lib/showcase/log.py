"""Showcase event log — JSONL, V>>--<<V watermark, SHA-256 IP hash.

State layout
------------
  state/showcases/_salt                       per-installation 32-byte salt (0600, gitignored)
  state/showcases/<project>/views.jsonl       primary per-project event log
  state/stealth/<project>/<recipient>.jsonl   per-recipient mission log (preserves /140-85 trail)

Schema (one JSON line per event)
--------------------------------
  {ts, project, id, event, t?, ip_hash, ua, ref, watermark}

Privacy invariant
-----------------
  No raw IP is ever written to disk. Caller passes `ip` in rec; we hash + drop.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
SHOWCASES_DIR = CLAUDE_DIR / "state" / "showcases"
STEALTH_DIR = CLAUDE_DIR / "state" / "stealth"
SALT_PATH = SHOWCASES_DIR / "_salt"
WATERMARK = "V>>--<<V"
IP_HASH_LEN = 12


def _ensure_salt() -> bytes:
    SHOWCASES_DIR.mkdir(parents=True, exist_ok=True)
    if not SALT_PATH.exists():
        SALT_PATH.write_bytes(secrets.token_hex(16).encode())
        try:
            os.chmod(SALT_PATH, 0o600)
        except OSError:
            pass
    return SALT_PATH.read_bytes()


def hash_ip(ip: str) -> str:
    """SHA-256(salt + ip), hex-truncated. Empty string if no ip."""
    if not ip:
        return ""
    return hashlib.sha256(_ensure_salt() + ip.encode()).hexdigest()[:IP_HASH_LEN]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_jsonl(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(line)


def log_event(project: str, rec: dict) -> None:
    """Append event to per-project log + per-recipient stealth log.

    Mutates `rec`: hashes any `ip` key into `ip_hash` and drops the raw value.
    Adds `ts`, `project`, `watermark` if missing.
    """
    rec.setdefault("ts", now_iso())
    rec["project"] = project
    raw_ip = rec.pop("ip", None)
    if raw_ip:
        rec["ip_hash"] = hash_ip(raw_ip)
    rec["watermark"] = WATERMARK

    line = json.dumps(rec, separators=(",", ":")) + "\n"
    _write_jsonl(SHOWCASES_DIR / project / "views.jsonl", line)

    rid = rec.get("id")
    if rid:
        _write_jsonl(STEALTH_DIR / project / f"{rid}.jsonl", line)
