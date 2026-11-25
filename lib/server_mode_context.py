"""server-mode context composer — warm per-channel context for relay spawns.

Problem (V>> 2026-06-26): a relay-spawned ``claude -p`` starts COLD per inbound
message — it has no awareness of what has happened in *this* channel, the
relevant memory, or what GRIP is doing across machines. This module composes a
BOUNDED, per-channel context preamble from GRIP's EXISTING substrates so each
spawn opens warm:

  1. warm-start kernel   — ``rules/session-context.md`` (HOLISTIC kernel + recall)
  2. channel history     — the per-channel relay session id + recent
                           ``relay-dispatcher.jsonl`` entries for THIS channel
                           ("what was said here, by whom, recently")
  3. relevant memory     — a KONO recall keyed on the channel's recent topic
  4. cross-machine mesh  — mesh peers + concurrent sessions (what else GRIP is
                           doing right now, across the second Mac / blackbook / VPS)

Design invariants (PARAMOUNT — no-stubs.md, feature-complete-input-domain.md):
- **Pure + read-only + side-effect-free.** It NEVER mutates state and NEVER
  touches the live relay. Wiring ``compose_channel_context`` into
  ``relay_dispatcher`` is a SEPARATE, operator-gated flip.
- **Honest graceful degradation, never a stub.** Every substrate call is
  wrapped; on absence/error the section renders a labelled
  ``(unavailable: <reason>)`` line — a true statement, not fabricated content.
- **Budget-bounded.** Total output is capped (``budget_chars``); sections are
  filled in priority order (kernel + channel-history first) so the most
  load-bearing context always survives truncation.
- **Per-channel isolation.** Only the requested ``channel_id``'s history is
  read; one channel never sees another's traffic.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

_DEFAULT_BUDGET = 4000
_KONO_TIMEOUT_S = 8


def _safe(reason_label: str, fn):
    """Run ``fn``; on any failure return an honest ``(unavailable: …)`` note.

    The note is a TRUE statement about absence — never fabricated content
    (no-stubs). Returns the function's string output on success.
    """
    try:
        out = fn()
        return out if (out and out.strip()) else f"  (none: {reason_label})"
    except Exception as e:  # noqa: BLE001 — read-only enrichment, never fatal
        return f"  (unavailable: {reason_label} — {type(e).__name__})"


def _warm_start_section(claude_dir: Path, max_chars: int) -> str:
    """The GRIP warm-start kernel head (HOLISTIC + Next Step + recall)."""
    path = claude_dir / "rules" / "session-context.md"
    text = path.read_text(encoding="utf-8", errors="ignore")
    # The kernel head carries the load-bearing block; cap to the budget slice.
    return text[:max_chars].rstrip()


def _channel_history_section(
    channel_id: str, claude_dir: Path, max_entries: int = 6
) -> str:
    """What has happened in THIS channel recently — the novel per-channel value.

    Combines (a) the per-channel relay resume-session id (proof continuity
    exists) and (b) the most recent ``relay-dispatcher.jsonl`` rows whose
    ``channel_id`` matches — summarised to sender + classification + gist.
    """
    lines: list[str] = []
    sess = claude_dir / "state" / "relay" / "sessions" / f"{channel_id}.txt"
    if sess.is_file():
        sid = sess.read_text(encoding="utf-8", errors="ignore").strip()
        lines.append(f"  resume-session: {sid[:24]}… (continuity active)")
    else:
        lines.append("  resume-session: none yet (first contact in this channel)")

    log = claude_dir / "logs" / "relay-dispatcher.jsonl"
    if log.is_file():
        rows: list[dict] = []
        # Tail-read: only the last slice matters, and the file can be large.
        for ln in log.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]:
            ln = ln.strip()
            if not ln or channel_id not in ln:
                continue
            try:
                row = json.loads(ln)
            except Exception:  # noqa: BLE001 — skip a malformed row, keep going
                continue
            if row.get("channel_id") == channel_id:
                rows.append(row)
        for row in rows[-max_entries:]:
            ts = (row.get("ts") or "")[:16]
            who = row.get("sender_id") or "?"
            cls = row.get("classification") or "?"
            n = row.get("prompt_chars") or 0
            lines.append(f"  {ts} · {who} · {cls} · {n} chars")
        if len(rows) == 0:
            lines.append("  (no prior relay turns logged for this channel)")
    return "\n".join(lines)


def _kono_section(query: str, claude_dir: Path) -> str:
    """A KONO semantic recall keyed on the channel's recent topic."""
    py = claude_dir / "venv" / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    proc = subprocess.run(
        [py, str(claude_dir / "lib" / "kono_cli.py"), "query", query],
        capture_output=True, text=True, timeout=_KONO_TIMEOUT_S,
        env={"PYTHONPATH": str(claude_dir)},
    )
    out = (proc.stdout or "").strip()
    # Cap recall to a few lines so it never dominates the budget.
    return "\n".join(f"  {l}" for l in out.splitlines()[:6]) if out else ""


def _mesh_section(claude_dir: Path) -> str:
    """What GRIP is doing across machines + concurrent sessions right now."""
    bits: list[str] = []
    try:
        from mesh_discovery import load_peers  # noqa: PLC0415
        peers = load_peers() or []
        if peers:
            names = ", ".join(p.get("name", "?") for p in peers[:6])
            bits.append(f"  mesh peers: {names}")
    except Exception:  # noqa: BLE001
        pass
    try:
        # Dependency-free concurrent-session count (exact-match excludes Claude
        # Desktop / MCP servers / hooks — mirrors rules/efficiency Rule 13).
        proc = subprocess.run(["pgrep", "-x", "claude"], capture_output=True, text=True)
        n = len([l for l in proc.stdout.splitlines() if l.strip()])
        bits.append(f"  concurrent claude sessions on this host: {n}")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(bits)


def compose_channel_context(
    channel_id: str,
    *,
    topic: str = "",
    budget_chars: int = _DEFAULT_BUDGET,
    claude_dir: Optional[Path] = None,
) -> str:
    """Compose a bounded, per-channel warm-context preamble for a relay spawn.

    Args:
        channel_id: the relay channel (e.g. a WhatsApp group jid). Only this
            channel's history is read (per-channel isolation).
        topic: optional recent-message text to key the KONO recall on. Empty →
            the recall is skipped (honest: no topic, no recall).
        budget_chars: hard cap on total output. Sections fill in priority order
            so the kernel + channel history always survive truncation.
        claude_dir: GRIP install dir (DIP — tests inject a fixture root).

    Returns a markdown preamble string. NEVER raises — every substrate degrades
    to an honest note (no-stubs).
    """
    cd = Path(claude_dir) if claude_dir else get_claude_dir()
    # Channel history + kernel are load-bearing → given the first, largest slices.
    kernel_budget = max(600, budget_chars // 2)

    sections: list[tuple[str, str]] = [
        ("Channel history (this channel only)",
         _safe("relay history", lambda: _channel_history_section(channel_id, cd))),
        ("GRIP kernel (warm-start)",
         _safe("warm-start", lambda: _warm_start_section(cd, kernel_budget))),
    ]
    if topic.strip():
        sections.append(
            ("Relevant memory (KONO recall)",
             _safe("kono recall", lambda: _kono_section(topic.strip(), cd))))
    sections.append(
        ("Cross-machine mesh", _safe("mesh", lambda: _mesh_section(cd))))

    header = (
        f"## GRIP server-mode context — channel {channel_id}\n"
        "_Warm per-channel context so this relay session does not start cold. "
        "Read-only; composed from GRIP substrates._\n"
    )
    body_parts = [f"### {title}\n{content}" for title, content in sections]
    out = header + "\n" + "\n\n".join(body_parts) + "\n"
    if len(out) > budget_chars:
        out = out[:budget_chars].rstrip() + "\n… (context truncated to budget)\n"
    return out


if __name__ == "__main__":  # pragma: no cover — manual smoke
    from wa_identities import MISSING_HINT, group_jid
    cid = sys.argv[1] if len(sys.argv) > 1 else group_jid("team-group")
    if not cid:
        sys.exit(f"usage: server_mode_context.py <chat-id> [topic] -- no default group: {MISSING_HINT}")
    print(compose_channel_context(cid, topic=" ".join(sys.argv[2:])))
