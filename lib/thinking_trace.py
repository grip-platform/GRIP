#!/usr/bin/env python3
"""thinking_trace.py — preserve the model's reasoning trace as a DIAGNOSTIC
specimen, never as evidence of cause.

Origin: V>> directive 2026-09-09 — "make reasoning appear, as the reasoning
reveals much more than the final response." Companion to
``drafts/cc-paper/thinking-traces-essay.md`` (the design rationale) and the
cryptographic-causation moat (`IDR.md`, `MEMORY_CONTEXT.md`, `CONGRUENCE.md`).

WHAT THIS IS
------------
Thinking blocks are already captured verbatim in every session transcript
(``…/projects/<slug>/<session>.jsonl``, ``{"type":"thinking",...}`` blocks) and
are among the first things GRIP compaction / ultraclear DISCARD, because they
are large and are not the answer. This module rescues them: it content-addresses
each block, stamps it ``authoritative: false`` / ``role: diagnostic``, records
its temporal position (the assistant message it belongs to, the timestamp, the
model, the provider's own thinking ``signature``), and writes it to a durable
store under ``state/thinking-traces/`` that compaction does not touch.

THE FENCE (load-bearing — read before extending this module)
------------------------------------------------------------
A model's account of its own reasoning is the LEAST reliable artefact it
produces — the confabulation class (`rules/prove-it-claims-canonical.md`, council
seal ``b32af619``; `CONGRUENCE.md` locked decision D1: "the unit is a falsifiable
commitment, never narrated chain-of-thought"). A preserved trace is admissible as
a specimen to be EXAMINED, and inadmissible as evidence of CAUSE. This module
therefore, BY CONSTRUCTION, offers:

  * capture / content-address / store / list / read-for-examination

and DELIBERATELY offers NO function that returns a trace in a form meant to be
consumed as a premise, injected into a prompt, cited by a verdict, or read back
by the model as established fact. If you are tempted to add
``as_context()`` / ``inject()`` / ``as_premise()`` / ``settle_against()`` —
DON'T. That converts a diagnostic into the confabulation-laundering the essay's
Section 4 objection warns against, and it is the one falsifier that makes the
whole design wrong (essay Section 8). A specimen is looked at. It never testifies.

Zero new cryptography: the same content-addressing the moat uses. The store is
per-machine runtime state (gitignored, like the context and continuity chains).

CONFIGURATION (so a downstream user need not read the hook source)
------------------------------------------------------------------
* Store root: ``$CLAUDE_DIR/state/thinking-traces/<session_id>/<sha256>.json``.
  ``CLAUDE_DIR`` resolves via ``lib/claude_dir.py::get_claude_dir`` — the
  ``$CLAUDE_DIR`` environment variable when set, else ``$HOME/.claude``.
* Automatic preservation is OPT-IN: the Stop hook
  ``hooks/thinking-trace-preserve-hook.py`` is a no-op unless
  ``GRIP_THINKING_TRACE_PRESERVE`` is ``on`` / ``1`` / ``true`` / ``yes``.
* CLI: ``grip-thinking-trace {preserve [session] | list <session> |
  read <session> <addr> | stats}`` (``bin/grip-thinking-trace``, a thin shim
  over ``_main`` below; ``python3 -m lib.thinking_trace …`` works too).

CONSISTENCY (read before building tooling on the read surface)
--------------------------------------------------------------
``list_specimens()`` and ``stats()`` WALK THE STORE AT CALL TIME. They are not
snapshot-consistent with a concurrently running ``preserve_session``: a listing
taken mid-preserve may see a subset of the specimens that preserve eventually
writes. What they never see is a partial record — every specimen is written to
a per-writer temporary file and atomically renamed into place, so a reader sees
a complete record or no record. Two preservers racing on the same transcript
each write identical bytes; last rename wins and the store converges to one
complete file per content address (their per-call ``new`` counts may then
over-report by the collision, since each observed the file absent).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import secrets
import sys
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
STORE_ROOT = CLAUDE_DIR / "state" / "thinking-traces"
PROJECTS_ROOT = CLAUDE_DIR / "projects"

_WS = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """NFC + whitespace-collapse so the specimen's identity is its semantic
    content, not its formatting. Mirrors HAL ``polyglot_canonical._normalise_text``
    and GRIP ``lib/precog/idr.py`` canonicalisation intent: two byte-different
    renderings of the same reasoning share one address. LOSSLESS for content."""
    text = unicodedata.normalize("NFC", text)
    return _WS.sub(" ", text).strip()


def content_addr(thinking_text: str) -> str:
    """``sha256:<hex>`` over the normalised thinking text. The specimen's
    identity IS its content — so preservation is idempotent (re-running harvests
    nothing new; the Banach fixed-point property the moat specs require)."""
    norm = _normalise(thinking_text)
    return "sha256:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TraceSpecimen:
    """A reasoning-trace specimen. NON-AUTHORITATIVE by construction.

    ``authoritative`` and ``role`` are fixed literals, not parameters — a
    specimen cannot be constructed as authoritative. They are serialised into
    every stored record so a reader (human or verifier) can never mistake a
    trace for evidence of cause.
    """

    content_addr: str
    session_id: str
    message_id: str
    model: str
    ts: str
    signature: str          # the provider's own thinking signature (evidence the block is genuine, not a paraphrase)
    uuid: str               # the transcript line uuid — temporal-position anchor
    git_branch: str
    effort: str
    thinking: str           # the raw specimen text (verbatim)
    authoritative: bool = False   # FENCE: always False. Never a parameter that can be flipped.
    role: str = "diagnostic"      # FENCE: always "diagnostic". A specimen, never a witness.

    def to_record(self) -> dict:
        d = asdict(self)
        # Belt-and-braces: even if a future edit adds a constructor path, the
        # stored record asserts the fence.
        d["authoritative"] = False
        d["role"] = "diagnostic"
        d["_fence"] = (
            "diagnostic specimen — examine, never consume as a premise or cite "
            "as cause (thinking_trace.py fence; CONGRUENCE.md D1)"
        )
        return d


def _resolve_transcripts(session_id: str | None, transcript_path: str | Path | None) -> list[Path]:
    """Every transcript file that holds this session (possibly none).

    An explicit ``transcript_path`` is the only candidate. Otherwise the session
    id is globbed across EVERY project slug, because one session id can
    legitimately sit under more than one slug — the slug is the launch cwd, and
    a session resumed from a different directory writes a second transcript
    with the same id. ALL matches are returned (sorted, deterministic), so
    preservation is the UNION across them rather than an arbitrary first
    match (the AGORA-deferred concern on GRIP#5393 → GRIP#5396). Content
    addressing dedups any specimen present in more than one file."""
    if transcript_path:
        p = Path(transcript_path)
        return [p] if p.is_file() else []
    if not session_id:
        return []
    # DEDUPE BY FILE IDENTITY, not by path. `projects/` carries symlinked slug
    # directories — measured 2026-09-10: 281 of them, and a glob for ONE session
    # id returned 275 paths that all shared inode 51658937, i.e. one 9MB file.
    # Without this the union re-read the same transcript 275 times per preserve
    # (~2.5GB of I/O) on a Stop hook. Content addressing meant no duplicate
    # specimens were ever written, so the cost was wasted reads, not bad data.
    # (st_dev, st_ino) collapses symlinks AND hardlinks; a genuinely distinct
    # transcript under another slug still unions in, which is the point.
    seen: set[tuple[int, int]] = set()
    unique: list[Path] = []
    for candidate in sorted(PROJECTS_ROOT.glob(f"*/{session_id}.jsonl")):
        try:
            stat = candidate.stat()
        except OSError:
            continue  # vanished or unreadable mid-glob — skip, never raise
        key = (stat.st_dev, stat.st_ino)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _specimen_from_block(block: object, line: dict, msg: dict) -> TraceSpecimen | None:
    """Build one specimen from one content block, or None if it is not a
    non-empty thinking block. Single responsibility — no loop."""
    if not isinstance(block, dict) or block.get("type") != "thinking":
        return None
    text = block.get("thinking") or ""
    if not text.strip():
        return None
    return TraceSpecimen(
        content_addr=content_addr(text),
        session_id=str(line.get("sessionId") or line.get("session_id") or ""),
        message_id=str(msg.get("id") or ""),
        model=str(msg.get("model") or ""),
        ts=str(line.get("timestamp") or ""),
        signature=str(block.get("signature") or ""),
        uuid=str(line.get("uuid") or ""),
        git_branch=str(line.get("gitBranch") or ""),
        effort=str(line.get("effort") or ""),
        thinking=text,
    )


def _specimens_from_record(line: dict) -> list[TraceSpecimen]:
    """Every thinking specimen carried by one parsed transcript record.
    Thinking blocks live only on assistant messages whose content is a list."""
    msg = line.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    found = (_specimen_from_block(block, line, msg) for block in content)
    return [spec for spec in found if spec is not None]


def iter_specimens(transcript_path: str | Path) -> Iterator[TraceSpecimen]:
    """Yield a TraceSpecimen for every thinking block in a transcript.

    Fail-soft: an unparseable line is skipped, never fatal. The transcript is
    the canonical capture; this only re-shapes what is already there."""
    path = Path(transcript_path)
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError:
        return
    with fh:
        for raw in fh:
            if '"thinking"' not in raw:
                continue
            try:
                line = json.loads(raw)
            except (ValueError, TypeError):
                continue
            yield from _specimens_from_record(line)


def _write_specimen(spec: TraceSpecimen, out_dir: Path) -> bool:
    """Write one specimen if not already present (content-addressed dedup).
    Returns True iff a new file was written."""
    fname = spec.content_addr.split(":", 1)[-1] + ".json"
    target = out_dir / fname
    if target.exists():
        return False
    # PER-WRITER temp name. Two processes preserving the same transcript at once
    # must never share a temp path: with one shared name, writer A's rename can
    # move writer B's half-written file into place, and B's own rename then
    # fails on a path that no longer exists. Each writer renames its OWN
    # complete file; the bytes are identical, so last-rename-wins is harmless
    # and a reader only ever sees a complete record (GRIP#5396).
    tmp = target.with_name(f"{target.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(spec.to_record(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)   # atomic rename; overwrites an identical racer harmlessly
    return True


def preserve_session(
    session_id: str | None = None,
    transcript_path: str | Path | None = None,
    store_root: Path | None = None,
) -> dict:
    """Harvest and preserve every thinking specimen in a session's transcript.

    Idempotent by content address: re-running writes only genuinely-new
    specimens (Banach f∘f == f). Returns a plain-language count.

    Never raises on a missing/broken transcript — returns a status dict so a
    hook can stay advisory and fail-open.
    """
    root = store_root or STORE_ROOT
    paths = _resolve_transcripts(session_id, transcript_path)
    if not paths:
        # The requested session_id is echoed even on failure, so a caller can
        # always tell WHICH session the status is about (GRIP#5396).
        return {"ok": False, "reason": "transcript not found", "session_id": session_id,
                "transcripts": 0, "new": 0, "existing": 0, "total": 0}

    sid = session_id or paths[0].stem
    out_dir = root / sid
    out_dir.mkdir(parents=True, exist_ok=True)

    # Union across every transcript that holds the session (see
    # _resolve_transcripts); content addressing dedups cross-file duplicates.
    specimens = itertools.chain.from_iterable(iter_specimens(p) for p in paths)
    new = total = 0
    for spec in specimens:
        total += 1
        if _write_specimen(spec, out_dir):
            new += 1
    return {"ok": True, "session_id": sid, "store": str(out_dir),
            "transcripts": len(paths),
            "new": new, "existing": total - new, "total": total}


# ---------------------------------------------------------------------------
# EXAMINATION-ONLY read surface. These exist so a HUMAN or an external verifier
# can look at preserved specimens. They are NOT a premise-injection path — they
# return records marked non-authoritative, for display, never for the model to
# consume as established fact. (The fence: see module docstring.)
# ---------------------------------------------------------------------------

def list_specimens(session_id: str, store_root: Path | None = None) -> list[dict]:
    """List preserved specimens for a session (for human/verifier examination)."""
    out_dir = (store_root or STORE_ROOT) / session_id
    if not out_dir.is_dir():
        return []
    records = []
    for f in sorted(out_dir.glob("*.json")):
        try:
            records.append(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
    return records


def read_specimen(session_id: str, addr: str, store_root: Path | None = None) -> dict | None:
    """Read ONE preserved specimen by content address (for examination)."""
    fname = addr.split(":", 1)[-1] + ".json"
    f = (store_root or STORE_ROOT) / session_id / fname
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def stats(store_root: Path | None = None) -> dict:
    """Corpus-wide counts across all preserved sessions."""
    root = store_root or STORE_ROOT
    if not root.is_dir():
        return {"sessions": 0, "specimens": 0, "store": str(root)}
    sessions = [d for d in root.iterdir() if d.is_dir()]
    specimens = sum(len(list(d.glob("*.json"))) for d in sessions)
    return {"sessions": len(sessions), "specimens": specimens, "store": str(root)}


def _cmd_preserve(argv: list[str]) -> int:
    sid = argv[1] if len(argv) > 1 else None
    res = preserve_session(session_id=sid)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


def _cmd_list(argv: list[str]) -> int:
    if len(argv) < 2:
        print("list requires a session id", file=sys.stderr)
        return 2
    recs = list_specimens(argv[1])
    for r in recs:
        print(f"{r.get('content_addr')}  {r.get('ts')}  {r.get('model')}  "
              f"[{r.get('role')} authoritative={r.get('authoritative')}]  "
              f"{r.get('thinking', '')[:80]!r}")
    print(f"# {len(recs)} specimen(s)")
    return 0


def _cmd_read(argv: list[str]) -> int:
    if len(argv) < 3:
        print("read requires <session> <addr>", file=sys.stderr)
        return 2
    rec = read_specimen(argv[1], argv[2])
    if rec is None:
        print("not found", file=sys.stderr)
        return 1
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    return 0


def _main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: thinking_trace.py {preserve [session] | list <session> | read <session> <addr> | stats}")
        return 0
    dispatch = {"preserve": _cmd_preserve, "list": _cmd_list, "read": _cmd_read}
    handler = dispatch.get(argv[0])
    if handler is not None:
        return handler(argv)
    if argv[0] == "stats":
        print(json.dumps(stats(), ensure_ascii=False))
        return 0
    print(f"unknown command: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
