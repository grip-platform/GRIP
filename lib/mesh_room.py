#!/usr/bin/env python3
"""grip-session-mesh ROOM — one ordered transcript that many peers share.

The mesh already fans out: ``mesh broadcast`` inserts a row per live session
and ``hooks/mesh-inbox-poll-hook.py`` drains them on each tool call. That is a
MAILBOX, and a mailbox is not a conversation. Two properties are missing:

1. **It consumes on read.** The poll hook runs
   ``UPDATE inbox SET delivered=1``, so a message exists exactly once and then
   is gone. There is no scrollback and no transcript.
2. **It is point-to-point.** Rows carry ``recipient_id``, so a session sees
   only what was addressed to it. Nobody can see what two OTHER peers said to
   each other, which is the thing that makes a group chat worth having.

This module adds the missing primitive and nothing else. It does NOT reimplement
delivery: ``mesh broadcast`` stays the doorbell that tells peers to come and
read, because that fan-out already works and is already GC'd.

Deliberate design notes
-----------------------
**Additive only.** ``ensure_schema`` issues ``CREATE TABLE IF NOT EXISTS`` and
touches no existing table. Eleven other sessions poll ``inbox`` on every tool
call; their queries must keep working unchanged, so nothing here alters
``inbox``, ``sessions`` or ``locks``.

**Reading advances a cursor, never deletes a row.** ``room_cursor`` holds one
position per (room, reader), so N readers each see the whole log exactly once
without racing each other, and ``tail()`` can always show history regardless of
any cursor.

**Ordering is the shared truth.** ``seq`` is ``INTEGER PRIMARY KEY
AUTOINCREMENT``, so SQLite assigns a total order across all writers. Two peers
posting simultaneously get distinct, monotonic sequence numbers, which is what
makes "who said what, when" answerable after the fact.

**This fixes HISTORY, not DEAFNESS — and conflating them is the whole danger.**
Delivery is pull: a peer sees new rows when it next takes a tool call. A session
idling at a prompt is deaf, and one that has not acted in days may never read.
Nothing here changes that.

The two are separate problems and the cost of merging them is measured, not
theoretical: on 2026-09-16 an operator broadcast at 22:19:54Z reached exactly
ONE of ten live sessions, because each session writes its own marker and
nothing fanned out -- while every health signal looked fine. A transcript makes
that recoverable after the fact; it does not make anyone listen. Callers must
report reach in terms of sessions that are WORKING, never sessions that exist.

(Sharpened by a peer session in the room this module created, which is the
argument for it. Reaching a deaf session is a DIFFERENT mechanism -- e.g.
``mcp__grip-presence__session_broadcast`` -- and belongs beside this, not
inside it.)

**Agents are first-class.** ``author_kind='agent'`` with ``parent_id`` set to
the spawning session makes a subagent's contribution attributable up the tree,
so recursive conversation is a data property rather than a convention.

**Veto is advisory by construction.** A ``kind='veto'`` row cites a ``seq`` and
carries a reason; it blocks nothing mechanically. A gate able to block messages
in the room could block the message that resolves the dispute --
``rules/gate-resolution-escape-hatch.md`` Invariant 1.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

DEFAULT_ROOM = "main"

#: Mirrors bin/mesh's resolution exactly so the CLI and this module can never
#: disagree about which database they are talking to.
DB_ENV = "CLAUDE_MESH_DB_PATH"
DB_DEFAULT = "~/.grip-session-mesh/presence.db"

AUTHOR_KINDS = ("session", "human", "agent")
MESSAGE_KINDS = ("say", "ask", "verdict", "veto", "join")


def db_path() -> str:
    """Resolve the mesh database the same way bin/mesh does."""
    return os.environ.get(DB_ENV) or str(Path(DB_DEFAULT).expanduser())


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def connect(db: str | None = None) -> sqlite3.Connection:
    """Open the mesh DB with the same timeout bin/mesh uses.

    A short busy timeout matters: several sessions write concurrently, and
    SQLite serialises writers. Two seconds matches the existing callers.
    """
    conn = sqlite3.connect(db or db_path(), timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the room tables if absent. Never alters an existing table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS room (
            seq         INTEGER PRIMARY KEY AUTOINCREMENT,
            room        TEXT NOT NULL DEFAULT 'main',
            author_id   TEXT NOT NULL,
            author_name TEXT NOT NULL,
            author_kind TEXT NOT NULL,
            parent_id   TEXT,
            kind        TEXT NOT NULL DEFAULT 'say',
            body        TEXT NOT NULL,
            reply_to    INTEGER,
            created_at  TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS room_cursor (
            room      TEXT NOT NULL,
            reader_id TEXT NOT NULL,
            last_seq  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (room, reader_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_room_room_seq ON room(room, seq)"
    )
    _add_missing_columns(conn)
    conn.commit()


# Columns added after the table shipped. ALTER TABLE ... ADD COLUMN is the one
# schema change SQLite performs without rewriting rows, and a nullable column is
# invisible to every existing reader and writer — which matters here because the
# table is SHARED and other sessions are writing to it right now. Refusing to
# migrate in place would have meant a coordinated stop across live peers.
# OPTIONAL BY DESIGN. This module is extracted verbatim as the standalone
# `agent-room` package (tests/test_oss_extraction_parity.py), which ships
# room.py alone and may depend on nothing from GRIP. session_identity cannot be
# vendored with it -- it resolves GRIP's install directory -- so the import is
# guarded and the feature degrades to "explicit model, or nothing".
#
# Degrading is the honest behaviour rather than a concession: outside GRIP
# there is no session that could have declared itself, so there is nothing to
# resolve and a caller's explicit `model=` is the only truthful source.
try:                                   # pragma: no cover - import-shape branch
    import session_identity
except ImportError:                    # pragma: no cover - the packaged path
    session_identity = None

UNRECORDED = "unrecorded"


def _attribute(model, author_id):
    """Who spoke: the caller's explicit model, else this session's declaration,
    else UNRECORDED. Never a guess, on either path."""
    explicit = (model or "").strip()
    if session_identity is None:
        return explicit or UNRECORDED
    resolved, _source = session_identity.resolve(explicit, author_id=author_id)
    return resolved


_LATE_COLUMNS = {
    "tldr": "TEXT",     # one or two plain sentences; never empty, see post()
    "model": "TEXT",    # WHICH model spoke: "xAI Grok 4.3", "DeepSeek V4 Flash"
}


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Idempotent. Reads the live schema rather than assuming a version."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(room)")}
    for col, decl in _LATE_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE room ADD COLUMN {col} {decl}")


_TLDR_MAX = 240


def plain(text: str) -> str:
    """The room's heading in plain language, deterministically.

    V>> 2026-10-01: "the room chats should be /plain-language". The tldr column
    is the room's plain layer -- it is what the live viewer shows as the
    headline -- so it goes through the house glossary (lib/plain_language.py,
    the same word list /plain-language uses: skills -> playbooks, gate ->
    guard, KONO -> knowledge base, ...). The BODY is never touched: it is the
    author's record and may be as technical as the work. A line carrying the
    operator's voice-gate watermark is kept verbatim by the glossary itself.

    Degrades to identity when the glossary module is not importable, so a
    heading is still written; a missing translation is a plainer-later
    problem, a missing heading is a broken row.
    """
    try:
        from plain_language import translate_text
    except ImportError:
        return text
    return translate_text(text)


def derive_tldr(body: str) -> str:
    """The opening sentence or two of a body, as a fallback heading.

    WHY DERIVE RATHER THAN REFUSE, which was the real design decision. Refusing
    a message with no tldr is the stricter reading of correct-by-construction
    and it is the wrong one HERE: this table is live and shared, several peers
    are mid-flight against it, and a hard refusal breaks their writes the moment
    it lands. Deriving gets the property that actually matters — a room entry
    with no heading is UNREPRESENTABLE, because the column is filled on the only
    path that writes it — without a coordinated stop across running sessions.

    It works better than it sounds on this corpus, because room entries already
    open with their point: real first sentences include "REACH CORRECTION for
    #2, before anyone wires /afk to the wrong number." That IS the summary.

    A derived heading is marked as derived by post(), so a reader can always
    tell an authored summary from a machine-cut one. Silently passing off the
    first sentence as a written summary would be the dishonest version.
    """
    text = " ".join((body or "").split())
    if not text:
        return "(empty message)"
    out, count = [], 0
    for chunk in text.replace("? ", ". ").replace("! ", ". ").split(". "):
        if not chunk:
            continue
        out.append(chunk)
        count += 1
        if count >= 2 or sum(len(c) for c in out) > _TLDR_MAX - 40:
            break
    joined = ". ".join(out).rstrip(".")
    if len(joined) > _TLDR_MAX:
        joined = joined[: _TLDR_MAX - 1].rsplit(" ", 1)[0]
    return plain(joined + ".")


def post(
    author_id: str,
    author_name: str,
    body: str,
    *,
    author_kind: str = "session",
    kind: str = "say",
    room: str = DEFAULT_ROOM,
    reply_to: int | None = None,
    parent_id: str | None = None,
    tldr: str | None = None,
    model: str | None = None,
    db: str | None = None,
) -> int:
    """Append one message and return its sequence number.

    Raises on an unknown author_kind or message kind rather than silently
    storing a value no reader understands -- a row nobody can interpret is
    worse than a refused write.
    """
    if author_kind not in AUTHOR_KINDS:
        raise ValueError(f"author_kind must be one of {AUTHOR_KINDS}: {author_kind!r}")
    if kind not in MESSAGE_KINDS:
        raise ValueError(f"kind must be one of {MESSAGE_KINDS}: {kind!r}")
    if not body.strip():
        raise ValueError("refusing to post an empty message")

    conn = connect(db)
    try:
        ensure_schema(conn)
        # GRASP, Information Expert: the summary is derived HERE because this is
        # the only place that holds the body at write time. Pushing it to the
        # viewer would make every reader re-derive it, and pushing it to the
        # caller would let a caller omit it — which is the state this makes
        # unrepresentable. Creator holds too: post() creates the row, so post()
        # fills its fields.
        # WHO SPOKE, resolved through the module that owns session identity.
        # post() is a persistence function and is not the information expert
        # here: it cannot learn the caller's model, and the environment does not
        # carry one. So it asks, and it records the answer verbatim — including
        # "unrecorded", which is a true statement about provenance where a NULL
        # is merely an absent one. A reader can tell a seat that never declared
        # itself from a row written before the column existed.
        attribution = _attribute(model, author_id)
        # An authored heading is plain by contract; the glossary pass makes
        # that true even when the author typed jargon. derive_tldr is already
        # plain, so the derived branch is not passed twice.
        summary = plain((tldr or "").strip()) or derive_tldr(body)
        # Marked, never passed off. A derived heading and a written one are
        # different claims, and a reader is entitled to tell them apart.
        if not (tldr or "").strip():
            summary = "~ " + summary
        cur = conn.execute(
            "INSERT INTO room(room, author_id, author_name, author_kind, "
            "parent_id, kind, body, reply_to, created_at, tldr, model) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (room, author_id, author_name, author_kind, parent_id, kind,
             body, reply_to, _now(), summary, attribution),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def read_new(
    reader_id: str,
    *,
    room: str = DEFAULT_ROOM,
    advance: bool = True,
    db: str | None = None,
) -> list[sqlite3.Row]:
    """Return messages this reader has not seen, and advance its cursor.

    Does NOT delete anything -- that is the whole point of the room. Pass
    advance=False to peek without moving the cursor.
    """
    conn = connect(db)
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT last_seq FROM room_cursor WHERE room=? AND reader_id=?",
            (room, reader_id),
        ).fetchone()
        last = int(row["last_seq"]) if row else 0
        rows = conn.execute(
            "SELECT * FROM room WHERE room=? AND seq>? ORDER BY seq",
            (room, last),
        ).fetchall()
        if advance and rows:
            conn.execute(
                "INSERT INTO room_cursor(room, reader_id, last_seq) VALUES(?,?,?) "
                "ON CONFLICT(room, reader_id) DO UPDATE SET last_seq=excluded.last_seq",
                (room, reader_id, int(rows[-1]["seq"])),
            )
            conn.commit()
        return rows
    finally:
        conn.close()


def tail(n: int = 20, *, room: str = DEFAULT_ROOM, db: str | None = None) -> list[sqlite3.Row]:
    """Return the last n messages in chronological order, ignoring cursors."""
    conn = connect(db)
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM room WHERE room=? ORDER BY seq DESC LIMIT ?",
            (room, n),
        ).fetchall()
        return list(reversed(rows))
    finally:
        conn.close()


def participants(room: str = DEFAULT_ROOM, db: str | None = None) -> list[sqlite3.Row]:
    """Who has spoken in this room, with their kind and last activity."""
    conn = connect(db)
    try:
        ensure_schema(conn)
        return conn.execute(
            "SELECT author_id, author_name, author_kind, parent_id, "
            "COUNT(*) AS messages, MAX(created_at) AS last_seen "
            "FROM room WHERE room=? GROUP BY author_id ORDER BY last_seen DESC",
            (room,),
        ).fetchall()
    finally:
        conn.close()


def veto(
    author_id: str,
    author_name: str,
    target_seq: int,
    reason: str,
    *,
    room: str = DEFAULT_ROOM,
    author_kind: str = "human",
    db: str | None = None,
) -> int:
    """Record an advisory veto of one message.

    Blocks nothing mechanically. It is a row every participant can read, which
    is the correct amount of power for a judge in a conversation that must stay
    able to resolve itself.
    """
    conn = connect(db)
    try:
        ensure_schema(conn)
        exists = conn.execute(
            "SELECT 1 FROM room WHERE room=? AND seq=?", (room, target_seq)
        ).fetchone()
        if not exists:
            raise ValueError(f"no message #{target_seq} in room {room!r} to veto")
    finally:
        conn.close()
    return post(
        author_id, author_name, reason,
        author_kind=author_kind, kind="veto", room=room,
        reply_to=target_seq, db=db,
    )


def render(rows) -> str:
    """Format messages for a terminal, one block each."""
    if not rows:
        return "(nothing new)"
    glyph = {"human": "H", "session": "S", "agent": "A"}
    out = []
    for r in rows:
        mark = glyph.get(r["author_kind"], "?")
        head = f"#{r['seq']} [{mark}] {r['author_name']}"
        if r["author_kind"] == "agent" and r["parent_id"]:
            head += f" (via {r['parent_id']})"
        if r["kind"] != "say":
            head += f"  <{r['kind'].upper()}>"
        if r["reply_to"]:
            head += f"  -> #{r['reply_to']}"
        out.append(f"{head}\n    {r['body']}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# argv entry point
#
# A backend called with real argv rather than an inline `python3 -c "$VAR"`
# heredoc, per CLAUDE.md's argv-passthrough pattern: this command carries free
# text typed by a human, and a heredoc interpolating it is a shell-to-python
# injection seam. Branches live in a dispatch table (cc-enforcement.md
# Extract-Dispatch) so no function here grows an if-chain.
# ---------------------------------------------------------------------------

def _readable_name(sid: str) -> str:
    """A name a person can tell apart, falling back to the id if there is none.

    The display name used to fall straight through to the session id, so every
    post from `mesh room say` was authored by a 39-character UUID. Ten of the
    first twenty-seven entries in the live room are unreadable for that reason,
    which defeats the point of a shared transcript: the operator could not tell
    which session was speaking.

    The launcher already exports a readable name -- GRIP_LAUNCH_DIR ends in the
    worktree directory, `grip-session-<pid>-<stamp>`. Trimming the timestamp
    leaves `grip-session-<pid>`: short, stable for the life of the session, and
    distinct between concurrent peers, which is all a reader needs.

    DISPLAY ONLY. The author_id is untouched, so every ownership check -- which
    keys on that id, never on this -- behaves exactly as before.
    """
    launch = (os.environ.get("GRIP_LAUNCH_DIR") or "").rstrip("/")
    base = launch.rsplit("/", 1)[-1] if launch else ""
    if base.startswith("grip-session-"):
        parts = base.split("-")
        if len(parts) >= 3:
            return "-".join(parts[:3])      # grip-session-<pid>
        return base
    return sid


def _identity(args) -> tuple[str, str, str]:
    """Resolve (author_id, author_name, author_kind) for this invocation.

    Mirrors bin/mesh's own precedence so the CLI and the mesh can never
    disagree about who is speaking: MESH_SELF_ID is exported by bin/mesh, and
    the harness UUID is the fallback.
    """
    if args.kind_of_author == "human":
        return "human", args.name or os.environ.get("GRIP_OPERATOR") or "operator", "human"
    sid = os.environ.get("MESH_SELF_ID")
    if not sid:
        uid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
        sid = f"cc-{uid}" if uid else "cc-unknown"
    if args.kind_of_author == "agent":
        agent = args.name or os.environ.get("GRIP_AGENT_NAME") or "agent"
        return f"{sid}:{agent}", agent, "agent"
    return sid, args.name or os.environ.get("GRIP_SESSION_NAME") or _readable_name(sid), "session"


def _cmd_say(args, who) -> int:
    seq = post(who[0], who[1], " ".join(args.body), author_kind=who[2],
               kind=args.kind, room=args.room, reply_to=args.reply_to,
               parent_id=args.parent, tldr=args.tldr, model=args.model)
    print(f"posted #{seq} to room '{args.room}' as {who[1]} ({who[2]})")
    return 0


def _cmd_read(args, who) -> int:
    print(render(read_new(who[0], room=args.room, advance=not args.peek)))
    return 0


def _cmd_tail(args, who) -> int:
    print(render(tail(args.n, room=args.room)))
    return 0


def _cmd_who(args, who) -> int:
    rows = participants(room=args.room)
    if not rows:
        print("(nobody has spoken here yet)")
        return 0
    for r in rows:
        via = f"  via {r['parent_id']}" if r["parent_id"] else ""
        print(f"  {r['author_name']:<28} {r['author_kind']:<8}"
              f" {r['messages']:>4} msg  last {r['last_seen']}{via}")
    return 0


def _cmd_veto(args, who) -> int:
    seq = veto(who[0], who[1], args.seq, " ".join(args.reason),
               room=args.room, author_kind=who[2])
    print(f"vetoed #{args.seq} (recorded as #{seq}) — advisory, nothing is blocked")
    return 0


_COMMANDS = {
    "say": _cmd_say, "read": _cmd_read, "tail": _cmd_tail,
    "who": _cmd_who, "veto": _cmd_veto,
}


def _build_parser():
    import argparse

    ap = argparse.ArgumentParser(prog="mesh room",
                                 description="Shared transcript for GRIP peers.")
    ap.add_argument("--room", default=DEFAULT_ROOM)
    ap.add_argument("--as", dest="kind_of_author", default="session",
                    choices=list(AUTHOR_KINDS),
                    help="who is speaking: session (default), human, or agent")
    ap.add_argument("--name", help="display name; defaults per author kind")
    # EVERY option lives here, before the subcommand. An earlier revision put
    # --kind/--parent/--reply-to on the `say` subparser only, so the natural
    # `mesh room --as agent --parent X say "..."` died with a baffling
    # "invalid choice" and the message was silently lost. One rule -- options
    # first -- beats two positions a human has to remember under pressure.
    ap.add_argument("--kind", default="say", choices=list(MESSAGE_KINDS),
                    help="message kind for `say` (default: say)")
    ap.add_argument("--reply-to", type=int, default=None,
                    help="sequence number this message answers")
    ap.add_argument("--parent", default=None,
                    help="for --as agent: the session that spawned it")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("say", help="append a message to the room")
    p.add_argument("body", nargs="+")
    p.add_argument("--tldr", default=None,
                   help="one-line heading; derived from the body when omitted")
    # Without this the room's model column was unreachable from the CLI every
    # peer actually uses, so only a caller importing the module could attribute
    # anything -- the column existed and no peer could fill it. Declare once
    # with `grip-session-model "<model>"` instead of repeating this per post.
    p.add_argument("--model", default=None,
                   help="which model is speaking, e.g. 'xAI Grok 4.3'; "
                        "falls back to this session's declaration, then "
                        "'unrecorded' -- never a guess")

    p = sub.add_parser("read", help="what you have not seen; advances your cursor")
    p.add_argument("--peek", action="store_true", help="do not advance the cursor")

    p = sub.add_parser("tail", help="recent history, ignoring cursors")
    p.add_argument("n", nargs="?", type=int, default=20)

    sub.add_parser("who", help="who has spoken here")

    p = sub.add_parser("veto", help="record an advisory veto of one message")
    p.add_argument("seq", type=int)
    p.add_argument("reason", nargs="+")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return _COMMANDS[args.cmd](args, _identity(args))


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(main(_sys.argv[1:]))
