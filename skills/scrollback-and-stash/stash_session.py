#!/usr/bin/env python3
"""Runnable driver for /scrollback-and-stash (steps 4-7), safe by construction.

Stashes a session transcript into the local LRU substrate
(``~/.claude/stash/index.db``) for fast warm-start retrieval, using the real
primitives ``fold_compress.fold`` (honest size metric) + ``ScrollbackLRU.stash``.

SAFETY: redaction (``lib/secrets_redact.py``) is NOT wired and GRIP session
JSONLs carry secret-shaped lines, so this stores a NON-VERBATIM *structural*
digest (uuid / record + role counts / token-equiv / fold metrics / sha), never
the raw transcript. Lossy => non-recoverable => no secret-shaped line reaches
the stash. The index.db is git-ignored (``.gitignore`` ``*.db``) and local-only.

Usage:
    stash_session.py                 # stash the most-recent session for this cwd's project
    stash_session.py <uuid|path> ... # stash the named session(s)

The cosmetic ``clear`` (skill step 1) is a no-op on context and is intentionally
NOT performed here (it only declutters the human's terminal; use a shell alias).
"""
from __future__ import annotations
import sys
import json
import time
from pathlib import Path


def _claude_dir() -> Path:
    """Canonical install dir, env-overridable (rules/path-portability.md)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
        from claude_dir import get_claude_dir  # type: ignore
        return get_claude_dir()
    except Exception:
        import os
        env = (os.environ.get("CLAUDE_DIR") or "").strip()
        return Path(env) if env else (Path.home() / ".claude")


CLAUDE = _claude_dir()
sys.path.insert(0, str(CLAUDE / "lib"))
from fold_compress import fold                                    # noqa: E402
from scrollback_lru import ScrollbackLRU, _STASH_DIR, _INDEX_DB  # noqa: E402

PROJECTS = CLAUDE / "projects"


def _slug_for_cwd() -> str:
    """CC project-dir slug for the current working dir (path-encoding convention)."""
    return str(Path.cwd()).replace("/", "-").replace(".", "-")


def _resolve_targets(argv: list[str]) -> list[Path]:
    """argv entries -> JSONL paths; empty argv -> most-recent JSONL for this cwd's slug."""
    if argv:
        out = []
        for a in argv:
            p = Path(a)
            if p.suffix == ".jsonl" and p.exists():
                out.append(p)
            else:  # treat as a UUID under this cwd's project dir
                out.append(PROJECTS / _slug_for_cwd() / f"{a}.jsonl")
        return out
    proj = PROJECTS / _slug_for_cwd()
    jsonls = sorted(proj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonls[:1]


def _digest(path: Path) -> dict:
    """Single linear pass -> NON-VERBATIM structural digest of one session."""
    raw = path.read_bytes()
    fr = fold(raw, depth=11)  # honest size/convergence metric (text-space dedupe)
    roles, records, total = {}, 0, 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        records += 1
        total += len(line)
        try:
            obj = json.loads(line)
        except Exception:
            roles["_unparsed"] = roles.get("_unparsed", 0) + 1
            continue
        r = obj.get("type") or (obj.get("message") or {}).get("role") or "?"
        roles[r] = roles.get(r, 0) + 1
    return {
        "session_uuid": path.stem, "slug": path.parent.name,
        "jsonl_bytes": len(raw), "records": records, "role_counts": roles,
        "token_equiv": total // 4,
        "fold_iterations": fr.iterations, "fold_converged": fr.converged,
        "fold_compression_ratio": round(fr.compression_ratio, 4),
        "final_sha256": fr.final_sha256, "stashed_at": time.time(),
        "note": "NON-VERBATIM structural digest; redaction unwired => raw transcript intentionally NOT stored",
    }


def _stash_one(lru: ScrollbackLRU, path: Path) -> tuple:
    if not path.exists():
        return (path.stem[:8], "MISS", 0, 0, "-", "")
    d = _digest(path)
    key_query = f"session:{d['session_uuid']}:{d['slug']}"
    lru.stash(key_query, json.dumps(d))                 # -> git-ignored local index.db
    return (d["session_uuid"][:8], "OK", d["records"], d["token_equiv"],
            f"{d['fold_compression_ratio']:.4f}", key_query)


def main(argv: list[str]) -> int:
    targets = _resolve_targets(argv)
    if not targets:
        print(f"no session JSONL found under {PROJECTS / _slug_for_cwd()}", file=sys.stderr)
        return 1
    lru = ScrollbackLRU()  # creates ~/.claude/stash/ + index.db on first init
    rows = [_stash_one(lru, p) for p in targets]

    print(f"STASH_DIR   {_STASH_DIR}  (index.db git-ignored via *.db)")
    print(f"INDEX_DB    {_INDEX_DB}  exists={_INDEX_DB.exists()}")
    print("uuid8     stat  records  tok~equiv  fold_ratio  key")
    last_key = ""
    for u8, st, rec, tok, ratio, kq in rows:
        print(f"{u8}  {st:<4}  {rec:>6}  {tok:>9,}  {ratio:>9}  {kq}")
        if st == "OK":
            last_key = kq
    if last_key:  # round-trip proof
        hit = lru.fetch(last_key)
        print(f"\nROUND-TRIP  fetch(last-key) -> tier={getattr(hit, 'tier_name', hit)} "
              f"conf={getattr(hit, 'confidence', None)} bytes={len(getattr(hit, 'content', '') or '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
