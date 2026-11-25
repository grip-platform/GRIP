"""The Room, live — a read-only view of the shared peer transcript.

`lib/mesh_room.py` gives GRIP peers one ordered transcript in SQLite. It is
excellent and it is CLI-only: you see the room when you run `mesh room tail`.
Watching several models correct each other AS IT HAPPENS needs a surface that
pushes, and nothing in the tree pushes anything — there is no socket, SSE or
websocket layer anywhere in apps/ or lib/ (checked before writing this).

WHY SERVER-SENT EVENTS AND NOT A WEBSOCKET. The traffic is strictly one way:
the room changes, the browser renders. SSE is a plain HTTP response that never
ends, it reconnects on its own, and it needs no dependency and no handshake.
A websocket would add a bidirectional channel nobody needs, and a writable
channel into a transcript is exactly what this must NOT have.

THREE PROPERTIES THIS REFUSES TO GIVE UP, each pinned by a test:

1. LOOPBACK ONLY. The room carries internal working content — session names,
   file paths, verbatim reasoning, corrections naming real people. Binding it
   to 0.0.0.0 publishes all of that to the local network. `resolve_bind`
   RAISES on any non-loopback host rather than warning, because a warning on a
   server you launch and walk away from is not a control.

2. READ-ONLY AT THE DRIVER. The connection is opened with `mode=ro` through a
   file: URI, so the process cannot write the transcript even if a later edit
   asks it to. The room is an append-only record several peers depend on; a
   VIEWER that can mutate it is a liability, not a feature.

3. INERT AT THE RENDER BOUNDARY. Peers post arbitrary prose, and a session
   name or message body containing markup is untrusted content by
   construction — it arrives from another process, not from this one. So it
   is never parsed as markup: the payload is JSON and the client writes every
   field with `textContent`, which emits characters and never elements.

   It is deliberately NOT `html.escape`d on the way out. Escaping has to match
   the context a value lands in, and pre-escaping for HTML that is then
   written as TEXT produces entities the reader sees — `&#x27;` where an
   apostrophe belongs. That was the behaviour until 2026-09-20 and it was
   visible on the live page. `tests/test_room_live_escaping.py` pins both
   halves: the payload carries the author's characters unchanged, and the
   rendered page contains no `innerHTML`.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

DEFAULT_PORT = 7420          # unclaimed in rules/port-registry.md, verified free
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class UnsafeBind(ValueError):
    """Refused: this would expose the transcript beyond this machine."""


def resolve_bind(host: str) -> str:
    """Return host, or RAISE. Loopback only, no exceptions and no override.

    Deliberately has no `--allow-remote` escape hatch. Every plausible reason to
    want one (show a colleague, watch from a phone) is better served by an SSH
    tunnel, which authenticates; a flag here would be an unauthenticated
    broadcast of internal reasoning to whatever network the laptop is on.
    """
    if host not in LOOPBACK:
        raise UnsafeBind(
            f"refusing to bind {host!r}: the room transcript is internal "
            "working content. Loopback only — use an SSH tunnel for remote "
            "viewing, which authenticates where this would not."
        )
    return host


def open_readonly(db_path: str) -> sqlite3.Connection:
    """A connection that CANNOT write, enforced by the driver rather than by us."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def rows_since(conn: sqlite3.Connection, seq: int, room: str = "main",
               limit: int = 200) -> list:
    """Messages newer than `seq`, oldest first, bounded."""
    # Column names read from the live schema, not assumed: the table is `room`
    # and the author columns are author_*, not speaker_*. Guessing cost one
    # round trip and is exactly the kind of thing PRAGMA table_info settles.
    cur = conn.execute(
        "SELECT seq, room, author_id, author_name, author_kind, kind, body, "
        "created_at, reply_to, tldr, model FROM room WHERE room = ? AND seq > ? "
        "ORDER BY seq ASC LIMIT ?",
        (room, seq, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    _name_the_authors(conn, rows)
    # Both resolvers run while author_id is still here: the name comes from the
    # mesh, the model from that session's transcript. Neither writes the row.
    _derive_models(rows)
    for r in rows:
        # author_id is fetched only to resolve the name above. It is the
        # session's identity and the page has no use for it, so it does not
        # travel to the client.
        r.pop("author_id", None)
    return rows


_UUID_NAME = re.compile(r"^cc-[0-9a-f]{8}-[0-9a-f]{4}-")
# What a session id may contain before it becomes a glob pattern: letters,
# digits, dot, underscore, hyphen. No path separator and no glob
# metacharacter, which is the whole point -- see ``_session_of``.
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]+$")

# Lines scanned per transcript before giving up. The model is named on the
# first assistant event, so this is generous; it exists so a huge transcript
# cannot stall a read.
_TRANSCRIPT_SCAN_CAP = 2000
_model_cache: dict = {}


def _transcript_root() -> Path:
    """Where the harness writes session transcripts.

    Resolved through the same shim the rest of GRIP uses, so a redirected
    CLAUDE_DIR -- which is what the test suite does -- points this at a
    temporary tree rather than at the operator's real sessions.
    """
    try:                                   # pragma: no cover - import-shape branch
        from claude_dir import get_claude_dir
        return Path(get_claude_dir()) / "projects"
    except ImportError:                    # pragma: no cover - the packaged path
        return Path.home() / ".claude" / "projects"


def _model_in_line(line: str) -> str:
    """The model named on one transcript event, or '' for every other line."""
    if '"model"' not in line:
        return ""                          # cheap reject before paying for JSON
    try:
        event = json.loads(line)
    except ValueError:
        return ""
    message = event.get("message")
    model = message.get("model") if isinstance(message, dict) else None
    return model.strip() if isinstance(model, str) else ""


def _scan_transcript(path: Path) -> str:
    """First model named in one transcript file, or '' if it names none."""
    try:
        with path.open(errors="ignore") as handle:
            for index, line in enumerate(handle):
                if index > _TRANSCRIPT_SCAN_CAP:
                    return ""
                model = _model_in_line(line)
                if model:
                    return model
    except OSError:
        return ""
    return ""


def _session_of(author_id: str) -> str:
    """The harness session id inside a room author id, or '' if there is none.

    Two shapes reach this. A plain author is ``cc-<uuid>``. A COUNCIL SEAT is
    ``cc-<uuid>:seat-adversary`` -- one session speaking under several named
    identities, which is the operator watching an argument rather than a
    monologue, and exactly what the room exists to show.

    The seat suffix is display, not identity: the transcript belongs to the
    parent session. Measured 2026-09-23 -- the last 3 unattributed rows were
    all seats of ONE session whose transcript names its model plainly, and they
    were unresolvable only because ``cc-<uuid>:seat-x`` is not a filename.

    The result becomes a GLOB PATTERN in ``_model_from_transcript``, and the
    author id it comes from is read out of the shared room table, which any
    peer writes. So the extracted id is held to a character allowlist before
    it is returned. Measured 2026-09-24 on the venv's Python 3.14.7 without
    this check: ``cc-../../secret`` made the glob return a file two levels
    ABOVE the projects root, and ``cc-*`` made it return every transcript in
    every project, so whichever sorted first spoke for a session it never
    ran. A council speaker raised the shape of this on GRIP#6527 in all three
    sittings ("the file search should only ever be handed simple characters,
    because the value it searches with comes out of the shared record
    itself"). It was non-blocking, and it was right.
    """
    if not author_id.startswith("cc-"):
        return ""
    session = author_id[3:].split(":", 1)[0]
    return session if _SAFE_SESSION_ID.match(session) else ""


def _model_from_transcript(session_id: str, misses: set | None = None) -> str:
    """The model this session ran on, read from the session's own transcript.

    The mesh stores no model: ``sessions`` carries id, name, machine, role,
    pair_partner, registered_at and last_heartbeat, and nothing else. So a row
    whose writer never declared one looked unrecoverable -- until you notice
    that every such author_id IS a harness session id, and the harness writes
    the model into that session's transcript on every assistant event.

    Measured 2026-09-23 on the live room: 14 rows carried no model, all 14
    carried a cc-<uuid> author, and 11 of them resolve here.

    Takes the FIRST model named. On a session that ran one model throughout --
    the ordinary case -- first and last agree; on one that switched, neither is
    right for every row, which is why the caller marks this derived rather than
    presenting it as the writer's own declaration.

    CACHES ONLY SUCCESS -- the choice ``get_shield`` makes in this module,
    for the same reason: a miss is recoverable, since a transcript that had
    not been written when the page first looked can exist on the next read,
    and memoising the miss would hide that recovery until the process
    restarted. The first version cached the miss too, and the plain-language
    speaker on GRIP#6527 said so in all three sittings. Within ONE read a
    miss is still remembered, in ``misses``, because one room read touches
    the same absent author repeatedly and that repetition is what the cache
    was for in the first place.
    """
    if not session_id:
        return ""
    if session_id in _model_cache:
        return _model_cache[session_id]
    if misses is not None and session_id in misses:
        return ""
    try:
        candidates = sorted(_transcript_root().glob(f"*/{session_id}.jsonl"))
    except OSError:
        candidates = []
    found = ""
    for path in candidates:
        found = _scan_transcript(path)
        if found:
            break
    if found:
        _model_cache[session_id] = found
    elif misses is not None:
        misses.add(session_id)
    return found


def _derive_models(rows: list) -> None:
    """Fill a missing model at READ time, and say which rows were derived.

    Same shape as ``_heading``: the stored row is never touched, so this fixes
    every writer at once without a migration -- and the shared ``room`` table
    is exactly where a migration was refuted, because ``ensure_schema`` runs
    from read paths too and would land on in-flight peers.

    ``model_source`` travels with the row so the page can show a derived model
    differently from a declared one. Presenting an inference as a declaration
    would assert something this cannot establish: the transcript names the
    session's model, not the model that generated one particular post.
    """
    misses: set = set()      # absent sessions, remembered for this read only
    for row in rows:
        stored = (row.get("model") or "").strip()
        if stored and stored != "unrecorded":
            row["model_source"] = "declared"
            continue
        author = row.get("author_id") or ""
        derived = _model_from_transcript(_session_of(author), misses)
        row["model"] = derived or stored
        row["model_source"] = "derived" if derived else ""


def _name_the_authors(conn: sqlite3.Connection, rows: list) -> None:
    """Replace a raw-id author name with the session's own readable one.

    Rows written before the display-name fix are authored by a 39-character
    UUID, which is what the operator meant by not being able to spot the
    sessions talking to each other. The mesh already records a readable name
    per session, so this resolves rather than rewrites -- the stored row is
    never touched, exactly as a missing heading is derived rather than
    backfilled.

    Falls back to whatever was stored, so a session the mesh never registered
    keeps its id. An unreadable name beats a wrong one.
    """
    unresolved = {r.get("author_id") for r in rows
                  if r.get("author_id") and _UUID_NAME.match(str(r.get("author_name") or ""))}
    if not unresolved:
        return
    names = {}
    try:
        marks = ",".join("?" for _ in unresolved)
        for sid, name in conn.execute(
                f"SELECT id, name FROM sessions WHERE id IN ({marks})", tuple(unresolved)):
            readable = str(name or "").strip()
            if readable.startswith("grip-session-"):
                parts = readable.split("-")
                readable = "-".join(parts[:3]) if len(parts) >= 3 else readable
            if readable:
                names[sid] = readable
    except sqlite3.Error:
        return          # the sessions table is a convenience, never a dependency
    for r in rows:
        better = names.get(r.get("author_id"))
        if better:
            r["author_name"] = better


_SHIELD_SENTINEL = object()
_shield_cache = _SHIELD_SENTINEL


def get_shield():
    """The public-profile redaction shield, or None when unreadable.

    Caches only SUCCESS. A failure is re-probed every call because it is
    recoverable — an operator runs `git-crypt unlock` and the denylist becomes
    readable. Memoising the None would make that recovery invisible until the
    process restarted (the defect fixed in GRIP#6050 for the public emitter).
    """
    global _shield_cache
    if _shield_cache is _SHIELD_SENTINEL or _shield_cache is None:
        _shield_cache = None
        try:
            from redaction_shield import Shield, denylist_state, load_denylist
            if denylist_state() == "ok":
                _shield_cache = Shield(load_denylist())
        except Exception:          # noqa: BLE001 - absent module is a valid state
            _shield_cache = None
    return _shield_cache


HELD = ("[HELD — an unallowlisted name appears in this entry. It is visible in "
        "`mesh room tail`; the shield refuses to serve it over HTTP.]")
UNSHIELDED = ("[HELD — the redaction denylist is unreadable, so this entry "
              "cannot be shielded. Run `git-crypt unlock`.]")


def shield_body(text: str) -> str:
    """Redact an entry for HTTP, or HOLD it. Never returns raw text unshielded.

    THIS IS THE POINT, and it replaces a cruder earlier design. The first
    version of this viewer answered "the transcript is sensitive" with "never
    leave loopback" — suppression. That is not what GRIP does anywhere else.
    The IDR chain faces the identical problem: a decision record is sensitive,
    and the answer is not secrecy, it is sign + redact + publish PROVABLY. The
    relation carried across is the load-bearing part, not the vocabulary —
    sensitivity is handled by TRANSFORMATION, and provable cleanliness is what
    licenses exposure. Same structure, different medium.

    So: every body is redacted through the public profile, and an entry
    carrying a name on neither list is HELD rather than served — `blockers()`
    from GRIP#6061, which reports what the public profile deliberately will not
    rewrite because the person-pattern would mangle ordinary prose.

    FAILS CLOSED. No shield means no serving, matching GRIP#6050: a viewer that
    emits raw text whenever the denylist happens to be locked is the fail-open
    this codebase keeps rediscovering.
    """
    sh = get_shield()
    if sh is None:
        return UNSHIELDED
    # REDACT FIRST, then ask what is still exposed. blockers() documents itself
    # as being "called on ALREADY-SHIELDED text ... which is what keeps it quiet
    # about denylisted identities -- those are [ORG-1] tokens by then." This ran
    # it on the RAW text, so a name ON the denylist still tripped the blocker and
    # the entry was HELD instead of being served with that name replaced. Adding
    # someone to the denylist therefore did not unblock their entries, which is
    # the entire remedy the denylist exists to provide. Entry #10 in the live
    # room was held for a person who had been denylisted hours earlier.
    #
    # Strictly no weaker: blockers() on the redacted text still catches every
    # name the denylist did NOT cover, which is exactly what it is for.
    shielded = sh.redact(text).text
    if sh.blockers(shielded):
        return HELD
    return shielded


def _heading(row) -> str:
    """The row's heading, derived at READ time when the writer stored none.

    A heading is derived in ``mesh_room.post()``, so every row written through
    it carries one. Rows keep arriving without one anyway, because the room
    table is SHARED: a peer running a checkout from before those columns
    existed executes the old INSERT and stores NULL. Reproduced against a
    migrated table, and observed live -- entry #18 arrived headless AFTER every
    headless row had been backfilled.

    Deriving here fixes that for every writer at once, without a write. The
    alternative the converge fleet proposed -- a CHECK constraint or a
    generated column -- was adversarially REFUTED and should not be retried:
    ``ensure_schema()`` runs from six entry points including READ paths, so the
    migration lands on the first operation any new-code session performs, and
    an in-flight peer writing the old shape gets IntegrityError on every write.
    ``PRAGMA table_info`` also does not list VIRTUAL generated columns, so the
    guard meant to make that safe cannot hold.

    Total by construction: ``body`` is NOT NULL, so there is always something
    to derive from. The ``~`` marker is the same one ``post()`` uses, so a
    machine-cut summary never reads as one a person wrote, whichever side
    produced it.
    """
    stored = str(row.get("tldr") or "").strip()
    if stored:
        # Rows written before the glossary pass existed carry jargon headings;
        # translating at render keeps the page plain without touching the
        # read-only transcript. Same helper as post(), so a heading reads the
        # same whichever side produced it (V>> 2026-10-01).
        try:
            from mesh_room import plain
        except ImportError:
            return stored
        return plain(stored)
    body = str(row.get("body") or "").strip()
    if not body:
        return ""
    try:
        # Imported HERE, matching how this module reaches redaction_shield: the
        # viewer stays importable when a sibling is not on the path, and the
        # page degrades to a missing heading rather than failing to serve.
        # Not reimplemented locally -- one derivation, so a row's heading reads
        # the same whether post() wrote it or this produced it.
        from mesh_room import derive_tldr
    except ImportError:
        return ""
    return "~ " + derive_tldr(body)


def as_event(row: dict) -> str:
    """One SSE frame.

    NOT HTML-escaped, and that is the fix rather than a regression. This used
    to run every field through `html.escape` with the stated intent that "no
    renderer can forget to". The intent was right and the LAYER was wrong:
    escaping has to match the context the value is written into, and this
    value is written into a JSON payload that the client renders with
    `textContent`.

    `textContent` is already complete protection -- it writes a string as
    characters and never parses markup -- and it does NOT decode entities.
    So escaping here did not add a defence; it added visible damage. The
    operator saw it immediately: an apostrophe he typed came back as
    `&#x27;`, and `<seq>` as `&lt;seq&gt;`, in the middle of the instructions
    telling a reader what to type. Escaped text that is never unescaped is
    not safer, it is just wrong.

    The "no renderer can forget" guarantee is kept and made STRONGER, by
    pinning the actual contract instead of pre-damaging the data: the
    renderer uses `textContent` and never `innerHTML`, and
    `tests/test_room_live_escaping.py` asserts that against the served page
    source. A future edit that reaches for `innerHTML` turns that red.

    Shielding is untouched. It is redaction, not escaping -- a different job
    with a different reason, and it still runs on every rendered field.
    """
    safe = {
        "seq": row["seq"],
        "name": str(row.get("author_name") or "?"),
        "who": str(row.get("author_kind") or "?"),
        "kind": str(row.get("kind") or "say"),
        "at": str(row.get("created_at") or ""),
        "body": shield_body(str(row.get("body") or "")),
        "reply_to": row.get("reply_to"),
        # The heading is shielded too. A summary of a held entry would leak the
        # very sentence the body was held for.
        "tldr": shield_body(_heading(row)),
        "model": str(row.get("model") or ""),
        # "declared" (the writer said so), "derived" (read from the session's
        # transcript), or "" (neither). The page needs the difference: a
        # derived model is an inference about the session, not the row.
        "model_source": str(row.get("model_source") or ""),
    }
    # SSE frames are newline-delimited, so the payload must carry no raw newline.
    return f"id: {row['seq']}\ndata: {json.dumps(safe)}\n\n"
