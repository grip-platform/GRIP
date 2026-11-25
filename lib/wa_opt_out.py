"""POPIA/GDPR opt-out purge for per-contact WhatsApp memory.

Public API::

    from lib.wa_opt_out import purge_contact

    result = purge_contact("27000000000@s.whatsapp.net", reason="opt-out")
    # → {"purged_paths": [...], "tombstone_path": "...", "audit_path": "..."}

Scope-limited (PARAMOUNT)
-------------------------
This purge ONLY removes content under
``$CLAUDE_DIR/state/wa-scopes/<slug>/`` — the per-contact scope dir.
It NEVER deletes broader GRIP memory (MEMORY.md, learning/, projects/),
session logs, or any global artefact. The slug derivation is the only
trust boundary: a malformed JID raises ``ValueError`` BEFORE any I/O.

Tombstone
---------
A purge leaves ONE file: ``state/wa-scopes/<slug>/tombstone.json``::

    {
      "opted_out_at_iso": "2026-05-28T15:42:00Z",
      "jid_hash_sha256": "<64-hex>",
      "reason": "opt-out",
      "version": 1
    }

The raw JID is NEVER persisted (POPIA/GDPR — sha256-hashed instead).
The tombstone tells future relay events "this contact is opted-out;
drop silently" without leaking PII.

Falsifier
---------
Wrong if a purge ever (a) deletes a path outside the scope dir,
(b) persists the raw JID, (c) silently swallows an I/O error without
audit-logging it. Verifier: ``tests/test_wa_control_commands.py``
opt-out cases.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

# Path portability — never hardcode user paths (rules/path-portability.md).
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
from wa_control_state import slug_for_jid  # noqa: E402

__all__ = ["purge_contact", "is_opted_out", "TOMBSTONE_VERSION"]

TOMBSTONE_VERSION = 1


def _scope_dir(slug: str) -> Path:
    return get_claude_dir() / "state" / "wa-scopes" / slug


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_jid(jid: str) -> str:
    """Return sha256 hex of the JID — used for tombstone, never the raw JID."""
    return hashlib.sha256(jid.encode("utf-8")).hexdigest()


def _audit_append(scope_dir: Path, record: dict) -> Path:
    """Append one JSONL line to the scope's audit log. Best-effort."""
    audit_path = scope_dir / "audit.jsonl"
    scope_dir.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    return audit_path


_PRESERVED_NAMES = frozenset({"tombstone.json", "audit.jsonl"})


def _purge_scope_files(scope_dir: Path) -> list[str]:
    """Delete every regular file in scope_dir except tombstone/audit.

    Symlink-escape paths (a child whose resolved target lies outside the
    scope) are refused AND audit-logged so an attacker-controlled symlink
    cannot produce an incomplete purge whose tombstone still claims
    success. (AGORA PR #3262 round-2 concern 3.)
    """
    purged: list[str] = []
    if not scope_dir.exists():
        return purged
    resolved_scope = scope_dir.resolve()
    for child in sorted(scope_dir.iterdir()):
        # Scope-isolation invariant — symlink escapes refused AND audited.
        try:
            child.resolve().relative_to(resolved_scope)
        except ValueError:
            # Record the skip so the audit trail reflects an incomplete purge.
            try:
                _audit_append(scope_dir, {
                    "event": "opt_out_symlink_escape_refused",
                    "path": str(child),
                    "when_iso": _now_iso(),
                })
            except OSError:
                pass  # audit best-effort; never block the purge
            continue
        if child.name in _PRESERVED_NAMES or not child.is_file():
            continue
        child.unlink()
        purged.append(str(child))
    return purged


def _write_tombstone(scope_dir: Path, jid_hash: str, reason: str,
                     when_iso: str) -> Path:
    """Atomically write tombstone.json (temp + os.replace)."""
    scope_dir.mkdir(parents=True, exist_ok=True)
    tombstone_path = scope_dir / "tombstone.json"
    payload = {
        "opted_out_at_iso": when_iso,
        "jid_hash_sha256": jid_hash,
        "reason": reason,
        "version": TOMBSTONE_VERSION,
    }
    tmp = tombstone_path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, tombstone_path)
    return tombstone_path


def purge_contact(jid: str, reason: str = "opt-out",
                  now: Optional[_dt.datetime] = None) -> dict:
    """POPIA/GDPR purge per-contact content. Leaves tombstone + audit line.

    Scope invariant: ONLY paths inside ``state/wa-scopes/<slug>/`` touched.
    Raises ``ValueError`` for malformed JIDs (no I/O performed).
    """
    slug = slug_for_jid(jid)
    scope_dir = _scope_dir(slug)
    jid_hash = _hash_jid(jid)
    when_iso = (now or _dt.datetime.now(_dt.timezone.utc)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    purged = _purge_scope_files(scope_dir)
    tombstone_path = _write_tombstone(scope_dir, jid_hash, reason, when_iso)
    audit_path = _audit_append(scope_dir, {
        "event": "opt_out_purge",
        "at_iso": when_iso,
        "jid_hash_sha256": jid_hash,
        "reason": reason,
        "purged_count": len(purged),
        "scope_dir": str(scope_dir),
    })
    return {
        "purged_paths": purged,
        "tombstone_path": str(tombstone_path),
        "audit_path": str(audit_path),
        "jid_hash_sha256": jid_hash,
    }


def is_opted_out(jid: str) -> bool:
    """Return True iff a tombstone exists for this JID. No JID is persisted."""
    try:
        slug = slug_for_jid(jid)
    except ValueError:
        return False
    return (_scope_dir(slug) / "tombstone.json").exists()
