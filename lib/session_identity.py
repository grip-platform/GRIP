"""Who is speaking — resolved once, in one place.

GRASP, and the assignment is the whole point of this module existing.

``mesh_room.post()`` is a PERSISTENCE function. It writes a row. It has no way
to learn which model is driving the process that called it, and the measurement
that forced this module is that the environment does not carry the answer
either: a live Claude Code session exports CLAUDE_CODE_SESSION_ID, _EFFORT_LEVEL,
_ENTRYPOINT and _EXECPATH, and no model name anywhere. So a resolution ladder
bolted inside ``post()`` would read variables nothing sets — a declared input
reaching no actor, which looks wired and is dead.

The INFORMATION EXPERT for "which model am I" is the running session itself.
That is knowledge it holds and nothing else can derive, so the session must
DECLARE it; this module is the PURE FABRICATION that gives the declaration one
home. Callers depend on ``resolve()`` rather than on the spelling of an
environment variable, so where identity is stored can change without touching
``post()`` or any seat (protected variations).

It never guesses. A model name that was not declared comes back as
``UNRECORDED`` with a source saying so, because inventing an attribution would
fabricate exactly the provenance these records exist to make checkable.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

ENV_MODEL = "GRIP_SEAT_MODEL"
UNRECORDED = "unrecorded"

# Session id: the harness exports CLAUDE_CODE_SESSION_ID to Bash. CLAUDE_SESSION_ID
# is unset there and is kept only as a compatibility fallback, matching how the
# afk marker resolves its own session scope.
_SID_VARS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")


def session_id() -> str | None:
    for var in _SID_VARS:
        val = (os.environ.get(var) or "").strip()
        if val:
            return val
    return None


def _declaration_path(sid: str) -> Path:
    return get_claude_dir() / "state" / "session" / f"model-{sid[:16]}.json"


def declare(model: str, sid: str | None = None) -> Path:
    """Record what only this session knows. Returns the file written.

    The declaration binds to the voice it speaks for, via ``default_author_id``
    derived at read time. Without that binding it is about a PROCESS, and a
    process can post on another author's behalf -- which is how three seeded
    peers ended up labelled with this session's model. Verified at runtime, not
    in a test: entries authored by session-alpha, seat-adversary and
    seat-restore-in-hal were served over HTTP and two carried an attribution
    that was simply false.
    """
    model = (model or "").strip()
    if not model:
        raise ValueError("refusing to declare an empty model")
    sid = sid or session_id()
    if not sid:
        raise RuntimeError(
            "no session id in the environment; cannot scope a model declaration"
        )
    path = _declaration_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # The author id is DERIVED, never stored. An earlier version wrote it into
    # this file, and when the derivation was corrected every existing
    # declaration silently stopped matching -- a declared session was served
    # "unrecorded" with nothing anywhere saying why. A stored copy of a
    # derivation is a staleness surface for no gain, so both sides compute it.
    tmp.write_text(json.dumps({"model": model, "session": sid}, indent=2) + "\n")
    tmp.replace(path)          # atomic: a reader never sees a half-written file
    return path


def default_author_id(sid: str) -> str:
    """The room author id this session writes under.

    DELEGATED, never reimplemented. An earlier version of this function
    returned ``f"cc-{sid[:8]}"`` -- a convention invented here that nothing
    else used. The real one is ``mesh_identity.resolve_session_id()``, which
    the CLI and bin/mesh already share, and it keeps the FULL id. The
    mismatch made every post from the peer CLI look like another author, so a
    session that HAD declared itself was served "unrecorded": the check
    intended to prevent a false attribution was instead suppressing a true
    one. Found by driving ``mesh room say``, not by any test.

    ``sid`` is HONOURED, not decorative. It used to be dropped whenever the
    resolver succeeded -- ``default_author_id("BBBB...")`` returned
    ``cc-AAAA...`` for whatever session the environment named. Nothing was
    misattributed, because every caller passes the current session's id and the
    two paths read the same variables in the same order. But a parameter that
    is accepted and ignored is a trap for the next caller, and it made the
    tests here pass by coincidence: they set the environment to match, so they
    never exercised the argument they appear to be testing.

    The resolver takes an env mapping, so the id can be threaded through it --
    honouring the argument without reimplementing the ``cc-`` convention, which
    is the whole reason this delegates.
    """
    try:
        import mesh_identity
        env = {"CLAUDE_CODE_SESSION_ID": sid} if sid else None
        resolved = mesh_identity.resolve_session_id(env=env)
        if resolved:
            return resolved
    except Exception:
        pass          # fall back rather than fail a lookup that must not raise
    return f"cc-{sid}"


def resolve(explicit: str | None = None,
            author_id: str | None = None) -> tuple[str, str]:
    """Return ``(model, source)``. Never raises, never invents.

    Precedence:

    1. an explicit argument -- the caller is asserting something about THIS
       row, which is knowledge ``resolve`` does not have, so it wins outright;
    2. this session's model, from ``GRIP_SEAT_MODEL`` or its declaration file,
       but ONLY for rows this session actually authored;
    3. UNRECORDED.

    The owner check is the whole point, and it governs rung 2 ENTIRELY -- both
    the environment and the declaration. Both answer "which model am I", which
    is a statement about ONE speaker. A process may legitimately post on
    another author's behalf, and handing either source to such a row produces a
    confident false attribution: strictly worse than an absent one, because a
    blank invites a question and a wrong name ends it.

    WHAT THESE GATES CANNOT ESTABLISH, and it is worth knowing before trusting
    the column. Every check here keys on WHO IS SPEAKING -- which session, which
    author. The thing recorded is WHICH MODEL, and nothing here verifies that.
    The gates stop one session's declaration reaching another session's words;
    they cannot make the recorded name true.

    The visible consequence: a session declares one model, the operator switches
    mid-session, the declaration is never rewritten, and every later row names
    the model that is no longer running. Both gates pass, because both are
    satisfied -- the session is itself and the author is itself.

    So ``model`` records WHAT THE SESSION DECLARED IT WAS RUNNING, not what
    generated any particular row. That is still worth recording, and it is
    weaker than it looks. Nothing in a Claude Code session's environment changes
    when the model changes (measured, GRIP#6285), so no cheap check closes it.
    Named by Claude Fable 5, reading this file, after gpt-6-astra had confirmed
    convergence from a description of it.

    The environment used to sit ABOVE the owner check, so a launcher that
    exported the variable would have attributed its own model to every row the
    process wrote for anyone else. Nothing sets it today, so this closes a trap
    before it is armed rather than fixing a live leak -- and it is armed
    deliberately by GRIP#6285, which proposes exporting exactly this variable
    from the launcher. Found by gpt-6-astra in a sovereign council, seal
    011e60efc736322d.
    """
    explicit = (explicit or "").strip()
    if explicit:
        return explicit, "explicit"
    sid = session_id()
    # Rung 2 begins here, and the owner gate covers BOTH of its sources.
    mine = author_id is None or author_id == default_author_id(sid or "")
    env = (os.environ.get(ENV_MODEL) or "").strip()
    if env:
        return (env, "env") if mine else (UNRECORDED, "other-author")
    if sid:
        path = _declaration_path(sid)
        try:
            data = json.loads(path.read_text())
            # THE DECLARATION MUST BE THIS SESSION'S. The path is keyed on only
            # the first 16 characters of the session id, so two sessions sharing
            # that prefix share the file -- and the later writer wins. Without
            # this check the owner test below still PASSES for the reader, since
            # it compares the ROW's author against the reader's own id and never
            # asks whether the declaration belongs to the reader. Reproduced:
            # two ids differing only after character 16, the second declares,
            # and the first is then served the second's model against its own
            # words. Found by a non-Anthropic council (seal 011e60efc736322d),
            # not by any test here.
            if (data.get("session") or "").strip() != sid:
                return UNRECORDED, "foreign-declaration"
            declared = (data.get("model") or "").strip()
            owner = default_author_id(sid)   # derived NOW, never read from file
            if declared and (author_id is None or author_id == owner):
                return declared, "declared"
            if declared:
                return UNRECORDED, "other-author"
        except (OSError, ValueError):
            pass       # a missing or corrupt declaration is simply undeclared
    return UNRECORDED, "none"
