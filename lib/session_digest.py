#!/usr/bin/env python3
"""Session digest -- the running state a final session report is rendered FROM.

The problem is a placement one. An operator who steps away from a long session
and comes back reads the last response, which describes the last trivial thing
that happened; to learn where the work actually stands he has to scroll back
through dozens of turns. Composing a per-turn summary instead is the wrong
trade -- it spends hundreds of tokens every turn to buy a report at the end.

So the turn stays terse and its salient points are CAPTURED here as they
happen, one line at a time, and the report is RENDERED from the capture at a
judged stopping point. Nothing is composed twice.

Four properties are load-bearing.

**Belief is recorded as a prediction, never as a memory.** A belief written
after an outcome is a story; the same belief written before it, timestamped
into an append-only file, is a falsifiable prior. That is the whole reason
``expect`` and ``outcome`` are separate kinds, and why :func:`render` refuses
to pair them unless the ``expect`` is STRICTLY earlier. It is also why there is
no free-text ``beliefs`` field: a section writable at any time degenerates into
unverifiable self-report, which is exactly the defect
``rules/canonical-output-style.md`` recorded when it made its memory-chain
section conditional rather than mandatory.

**No field can hold an essay.** Every field is machine-filled, a verbatim copy
of something external, or one capped sentence. There is no ``note`` kind and no
``misc`` field, deliberately: an escape hatch becomes the default and the enum
stops discriminating.

**The file is append-only.** Rewriting is the mechanism by which a
retrospective belief gets inserted beside an earlier one, so the capability is
simply absent -- every write is a single ``O_APPEND`` line and no function here
edits one. It is also what makes concurrent appends safe: POSIX advances the
offset and writes in one step for ``O_APPEND``, so short single-line writes do
not interleave on a local filesystem. (Unverified on network filesystems.)

**Nothing here raises on I/O.** A digest that breaks a turn is worse than a
digest that loses a beat, so every I/O path returns a value and reports on
stderr. Losing silently would be worse still -- that is how a blind measurement
returns a confident zero -- so each failure prints exactly one line.

Usage from a turn is one line, no read, no JSON authored by hand::

    python3 lib/session_digest.py --add '{"kind":"did","text":"...","cite":"..."}'
    python3 lib/session_digest.py --render
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from dataclasses import replace, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

#: On-disk format version. Bumped only for a change a reader cannot infer.
SCHEMA = 1

#: The six kinds, each derived from a part of the report contract rather than
#: invented: ``did`` is what/when; ``found`` and ``chose`` are how/why (a
#: ``chose`` carries the rejected alternative, so it renders as a decision
#: record); ``expect`` is the prior, written BEFORE its outcome; ``outcome``
#: refs the ``expect`` it resolves, so "result relative to that prior" is
#: structural rather than narrated; ``blocked`` is the limits clause.
#:
#: There is no ``note`` or ``misc``. If a point fits none of six it is not
#: salient, and an escape hatch would become the default.
KINDS = ("did", "found", "chose", "expect", "outcome", "blocked")

#: Kinds whose claim must point at something openable. ``expect`` is exempt
#: because a prediction has no artefact yet, and ``blocked`` because the absence
#: of a result is the point. A beat of any other kind with no ``cite`` is not
#: dropped -- it renders under UNVERIFIED, because silently discarding an
#: unbacked claim hides it and promoting it launders it.
CITE_REQUIRED = ("did", "found", "chose", "outcome")

#: Hard cap on one beat's text. A rejection, never a truncation: the point of
#: the cap is that the caller rewrites shorter, and a silently shortened claim
#: is a claim nobody wrote.
MAX_TEXT = 200

#: Heuristic substance floor. "done", "ok", "fixed" carry nothing a reader can
#: use three days later. Crude by construction -- the exact-match list below is
#: the precise half; this only catches the shapes nobody bothered to enumerate.
MIN_TEXT = 12

#: Openings that mark a restatement of the plan rather than a result. The
#: restatement test: could the operator have written this line from the
#: original request alone? If yes it adds nothing to a report he already knows
#: the premise of.
NOISE_PREFIXES = (
    "i will ", "i'll ", "i am going to ", "i'm going to ", "let me ",
    "next i ", "next, i ", "now i ", "about to ", "going to ",
    "planning to ", "starting ", "beginning ", "continuing ", "proceeding ",
)

#: Bare verdicts with no referent. Each is true, none is reportable: the
#: operator learns nothing from "tests pass" that "51 tests pass, and only
#: because the suite imports from the shared tree" would not have told him.
BARE_VERDICTS = frozenset({
    "done", "ok", "okay", "fixed", "works", "it works", "complete",
    "completed", "success", "succeeded", "tests pass", "all tests pass",
    "no changes", "nothing to do", "looks good", "lgtm", "passing",
})

#: Directory override, for a caller that wants the state somewhere explicit
#: (CI, a test, an operator with a different layout). First rung of
#: :func:`state_dir`.
ENV_DIR = "GRIP_SESSION_DIGEST_DIR"

#: The harness's own session id. Note the spelling: ``CLAUDE_SESSION_ID`` is a
#: DIFFERENT, absent variable that at least one hook in this tree reads and so
#: records an empty id. Copying the wrong name is the likeliest way this file
#: silently shares one digest between every concurrent session.
ENV_SESSION = "CLAUDE_CODE_SESSION_ID"

#: How many did/found/chose lines the narrative renders before it stops listing
#: and starts counting. The overflow is NAMED, never silently dropped.
MAX_NARRATIVE = 6

_WS = re.compile(r"\s+")


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Beat:
    """One recorded salient point. Immutable, and one line of JSONL.

    :param id: six hex chars, generated at append. Random rather than a
        counter because a counter needs a read to find the max, which both
        costs a file read per append and reintroduces a race between
        concurrent appenders.
    :param at: ISO-8601 UTC with MICROSECOND precision. Not fussiness: the
        ``expect`` before ``outcome`` ordering is the anti-confabulation check,
        and second granularity breaks it for anything that happens fast.
    :param cite: verbatim. A ``file:line``, a SHA, a PR URL, a command -- a
        pointer, never a value, so a credential cannot ride into a report.
    :param refs: ids of earlier beats this one resolves or contradicts. An
        unresolvable ref is kept as written and surfaces at render time; a ref
        dropped at append time would be a fact silently deleted.
    """

    id: str
    at: str
    kind: str
    text: str
    cite: Optional[str] = None
    refs: tuple = ()

    def as_json(self) -> dict:
        """The wire form. ``cite``/``refs`` are omitted when empty."""
        out = {"t": "beat", "id": self.id, "at": self.at,
               "kind": self.kind, "text": self.text}
        if self.cite:
            out["cite"] = self.cite
        if self.refs:
            out["refs"] = list(self.refs)
        return out


@dataclass
class SessionState:
    """The whole digest: an immutable header plus an append-only beat log.

    ``request`` is the operator's words VERBATIM and ``intent`` is one sentence
    of how they were read. Both exist so a wrong session is caught at the top
    of the report rather than at the bottom: if the reading is wrong,
    everything below it is wrong too.
    """

    session_id: str = ""
    started_at: str = ""
    cwd: str = ""
    branch: str = ""
    request: str = ""
    intent: str = ""
    beats: list = field(default_factory=list)
    schema: int = SCHEMA
    #: Set when the digest existed but could not be read. An empty session and
    #: an unreadable one must never render the same words: the first is true,
    #: the second is the operator being told nothing happened when it did.
    unreadable: str = ""

    def head_json(self) -> dict:
        """The header line's wire form."""
        return {"t": "head", "schema": self.schema, "session_id": self.session_id,
                "started_at": self.started_at, "cwd": self.cwd,
                "branch": self.branch, "request": self.request,
                "intent": self.intent}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _now() -> str:
    """ISO-8601 UTC, microseconds kept. See :class:`Beat`.``at``."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return secrets.token_hex(3)


def _norm(text: str) -> str:
    """Comparison form: lowercased, whitespace collapsed, trailing stops cut."""
    return _WS.sub(" ", (text or "").strip().lower()).rstrip(".!,;:")


def _warn(message: str) -> None:
    """One stderr line. Never raises, never silent."""
    try:
        print("[session-digest] %s" % message, file=sys.stderr)
    except Exception:  # pragma: no cover - stderr itself is gone
        pass


# --------------------------------------------------------------------------
# location
# --------------------------------------------------------------------------

def state_dir() -> Path:
    """Where digests live: ``$GRIP_SESSION_DIGEST_DIR`` else ``<repo>/state/session``.

    Derived from THIS FILE's own resolved location, deliberately, and NOT from
    ``CLAUDE_DIR``. That variable is routinely unset, so it falls back to
    ``~/.claude`` -- which on this machine is a symlink to the shared tree every
    concurrent session and every launchd agent also reads. State written there
    is state written into a directory whose contents belong to somebody else.
    Resolving from ``__file__`` instead means a worktree's digest stays in that
    worktree, and a shared-tree invocation writes to the shared tree because
    that genuinely is where its code lives.

    ``resolve()`` is used so two names for one checkout (``~/.claude/lib/...``
    and ``~/.grip/lib/...``) agree on one location rather than forking the
    digest in half.

    The trade, stated rather than hidden: a worktree is deleted at teardown and
    takes its digests with it. That is accepted because the RENDERED report is
    the durable artefact and the digest is scratch -- and :data:`ENV_DIR` is the
    escape hatch for a caller who needs otherwise.
    """
    env = os.environ.get(ENV_DIR, "").strip()
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent / "state" / "session"


def session_id() -> str:
    """The harness session id, or ``"unknown"`` -- loudly.

    A missing id is reported rather than shrugged off, because the failure it
    causes is invisible: every session would quietly share one file and the
    report would describe work from three sessions at once.
    """
    sid = os.environ.get(ENV_SESSION, "").strip()
    if sid:
        return sid
    _warn("%s unset -- writing to beats-unknown.jsonl (digests may be shared)"
          % ENV_SESSION)
    return "unknown"


def state_path(sid: Optional[str] = None) -> Path:
    """Full path for one session's digest. Full UUID, unmangled, in the name."""
    return state_dir() / ("beats-%s.jsonl" % (sid or session_id()))


# --------------------------------------------------------------------------
# the salience discriminator
# --------------------------------------------------------------------------

def salient(kind: str, text: str, cite: Optional[str] = None,
            existing: Sequence[Beat] = ()) -> Optional[str]:
    """Decide whether a point is worth recording. ``None`` keeps it.

    Returns a human-readable REASON when the point should be dropped, so a
    caller can say why rather than discarding in silence.

    Four mechanical tests, in order of how cheap they are to be sure about: the
    kind must be one of six (an unknown kind is an escape hatch attempt); the
    text must fit one capped sentence; it must not be a restatement of the plan
    or a bare verdict with no referent; and it must not repeat a point already
    recorded.

    The honest limit: tests three and four are heuristics over wording, so a
    genuinely novel sentence phrased like a plan will be refused and a
    reworded duplicate will pass. They are a floor under a judgement, not a
    replacement for one. The judgement itself is a budget -- roughly one beat
    per turn, ~12-20 a session; above that the enum is being used as a log.
    """
    if kind not in KINDS:
        return "unknown kind %r (expected one of %s)" % (kind, ", ".join(KINDS))

    body = (text or "").strip()
    if not body:
        return "empty text"
    if len(body) > MAX_TEXT:
        return "text is %d chars, cap is %d -- rewrite it shorter, do not truncate" % (
            len(body), MAX_TEXT)

    norm = _norm(body)
    if norm in BARE_VERDICTS:
        return "bare verdict %r -- name the referent that makes it checkable" % body
    if len(norm) < MIN_TEXT:
        return "too thin to be worth reading three days later: %r" % body
    for prefix in NOISE_PREFIXES:
        if norm.startswith(prefix):
            return "restates the plan rather than a result (%r)" % prefix.strip()

    for prior in existing:
        if prior.kind != kind:
            continue
        if _norm(prior.text) == norm:
            return "duplicate of beat %s" % prior.id
        if cite and prior.cite == cite:
            return "same kind and cite already recorded as beat %s" % prior.id
    return None


# --------------------------------------------------------------------------
# I/O -- append-only, never raises
# --------------------------------------------------------------------------

def _append_line(path: Path, payload: dict) -> bool:
    """Append one JSON line under ``O_APPEND``. Returns success, never raises."""
    try:
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return True
    except (OSError, TypeError, ValueError) as exc:
        _warn("lost a record (%s): %s" % (type(exc).__name__, exc))
        return False


def load(path: Optional[Path] = None) -> SessionState:
    """Read a digest. A missing, unreadable, or partly corrupt file is empty-or-partial.

    A corrupt line is skipped with one warning rather than aborting the read:
    the whole point of an append-only log is that a torn final write costs one
    beat, not the session.
    """
    target = path or state_path()
    state = SessionState()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        # A session that has recorded nothing yet. Silence is correct here.
        return state
    except OSError as exc:
        # An unreadable digest is NOT an empty session, and reporting it as one
        # reproduces the exact failure this module exists to fix: the operator
        # returns, is told "No salient points recorded", and believes nothing
        # happened. Every other except in this module warns; this was the only
        # silent one, and it sat on the path whose output the operator reads.
        _warn("cannot read %s (%s): %s" % (target, type(exc).__name__, exc))
        return replace(state, unreadable="%s: %s" % (type(exc).__name__, exc))

    for lineno, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            _warn("skipping unreadable line %d of %s" % (lineno, target))
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("t") == "head":
            state = _head_into(state, rec)
        elif rec.get("t") == "beat":
            state.beats.append(_beat_from(rec))
    return state


def _head_into(state: SessionState, rec: dict) -> SessionState:
    """Fold a header record into ``state``, keeping the beats already read."""
    state.schema = rec.get("schema", SCHEMA)
    state.session_id = rec.get("session_id", "") or ""
    state.started_at = rec.get("started_at", "") or ""
    state.cwd = rec.get("cwd", "") or ""
    state.branch = rec.get("branch", "") or ""
    state.request = rec.get("request", "") or ""
    state.intent = rec.get("intent", "") or ""
    return state


def _beat_from(rec: dict) -> Beat:
    refs = rec.get("refs") or ()
    return Beat(
        id=str(rec.get("id", "")),
        at=str(rec.get("at", "")),
        kind=str(rec.get("kind", "")),
        text=str(rec.get("text", "")),
        cite=rec.get("cite") or None,
        refs=tuple(str(r) for r in refs),
    )


def open_session(request: str = "", intent: str = "",
                 path: Optional[Path] = None,
                 sid: Optional[str] = None,
                 branch: str = "", cwd: str = "") -> bool:
    """Write the header. Appended like everything else, so it never rewrites.

    A second call appends a second header, which :func:`load` folds over the
    first -- that is how an intent is revised without the file ever being
    edited, and the earlier reading stays on disk where a reader can see it
    changed.
    """
    target = path or state_path(sid)
    state = SessionState(
        session_id=sid or session_id(),
        started_at=_now(),
        cwd=cwd or os.getcwd(),
        branch=branch,
        request=(request or "").strip(),
        intent=(intent or "").strip(),
    )
    return _append_line(target, state.head_json())


def append(kind: str, text: str, cite: Optional[str] = None,
           refs: Iterable[str] = (), path: Optional[Path] = None) -> Optional[Beat]:
    """Record one salient point. The one-line call a turn makes.

    Returns the :class:`Beat` on success, or ``None`` when the point was
    dropped as noise or the write failed -- both reported on stderr. Call
    :func:`salient` directly when the reason itself is wanted.

    The header is created on demand, so a turn that records a point before
    anyone set a request still produces a readable digest.
    """
    target = path or state_path()
    state = load(target)
    if not state.started_at:
        open_session(path=target)

    reason = salient(kind, text, cite, state.beats)
    if reason is not None:
        _warn("dropped: %s" % reason)
        return None

    beat = Beat(id=_new_id(), at=_now(), kind=kind, text=text.strip(),
                cite=(cite or None), refs=tuple(refs))
    if not _append_line(target, beat.as_json()):
        return None
    return beat


# --------------------------------------------------------------------------
# render -- pure, writes nothing, safe to call at any stopping point
# --------------------------------------------------------------------------

def _of(beats: Sequence[Beat], *kinds: str) -> list:
    return [b for b in beats if b.kind in kinds]


def _clock(iso: str) -> str:
    """``HH:MM:SS`` UTC for display. The full microsecond value stays on disk --
    the precision exists for the strictly-earlier check, not for the reader."""
    return iso[11:19] if len(iso) >= 19 else iso


def _cited(text: str, cite: Optional[str]) -> str:
    """Evidence in the sentence it supports, verbatim -- never a bottom ledger."""
    return "%s (%s)" % (text.rstrip("."), cite) if cite else text


def _resolve_prior(beat: Beat, index: dict) -> tuple:
    """Pair an ``outcome`` with the prior it resolves. Returns (prior, problem).

    The temporal check is the anti-confabulation control: a prior may only be
    paired when it was recorded STRICTLY earlier, so a belief written at report
    time cannot be dressed up as one held beforehand.
    """
    for ref in beat.refs:
        prior = index.get(ref)
        if prior is None:
            return None, "refs %s, which is not in this digest" % ref
        if prior.kind not in ("expect", "chose"):
            return None, "refs %s, which is a %s, not a prior" % (ref, prior.kind)
        if not (prior.at < beat.at):
            return None, "refs %s, which is not strictly earlier" % ref
        return prior, ""
    return None, ""


def _block_ask(state: SessionState) -> list:
    lines = []
    if state.request:
        lines.append("**You asked:** %s" % state.request)
    if state.intent:
        lines.append("**Taken as:** %s" % state.intent)
    return lines


def _block_decide(beats: Sequence[Beat], open_blocks: Sequence[Beat]) -> list:
    latest = (_of(beats, "outcome") or _of(beats, "chose") or _of(beats, "did"))
    lines = []
    if latest:
        lines.append("**Where it stands.** %s" % _cited(latest[-1].text, latest[-1].cite))
    if open_blocks:
        lines.append("**Still open.** " + " · ".join(
            _cited(b.text, b.cite) for b in open_blocks))
    elif latest:
        lines.append("Nothing else is waiting on you.")
    return lines


def _block_happened(beats: Sequence[Beat]) -> list:
    narrative = _of(beats, "did", "found", "chose")
    if not narrative:
        return []
    shown = narrative[:MAX_NARRATIVE]
    body = " ".join(_cited(b.text, b.cite).rstrip(".") + "." for b in shown)
    lines = ["**What happened.** %s" % body]
    if len(narrative) > MAX_NARRATIVE:
        lines.append("%d further points recorded -- see the digest."
                     % (len(narrative) - MAX_NARRATIVE))
    return lines


def _block_prior(beats: Sequence[Beat], index: dict) -> tuple:
    """The (c) block: prior -> acted on -> result, and the refs that did not hold."""
    lines, broken = [], []
    for beat in _of(beats, "outcome"):
        prior, problem = _resolve_prior(beat, index)
        if problem:
            broken.append("%s: %s" % (_cited(beat.text, beat.cite), problem))
        elif prior is not None:
            lines.append("Expected %s, recorded %s; got %s."
                         % (prior.text.rstrip("."), _clock(prior.at),
                            _cited(beat.text, beat.cite)))
    if lines:
        lines.insert(0, "**The prior, and what happened to it.**")
    return lines, broken


def _block_cites(beats: Sequence[Beat]) -> list:
    seen, cites = set(), []
    for beat in beats:
        if beat.cite and beat.cite not in seen:
            seen.add(beat.cite)
            cites.append(beat.cite)
    return ["**cite.verify** · " + " · ".join(cites)] if cites else []


def _block_unverified(beats: Sequence[Beat], broken: Sequence[str]) -> list:
    missing = [b.text for b in beats if b.kind in CITE_REQUIRED and not b.cite]
    if not missing and not broken:
        return []
    lines = ["**UNVERIFIED** -- asserted here, not backed by an openable referent:"]
    lines.extend("- %s" % m for m in list(missing) + list(broken))
    return lines


def _prose(state: SessionState, open_blocks: Sequence[Beat]) -> list:
    """The report body as sentences, capped at six.

    Order is fixed because it matches how an operator reads on return: what they
    asked, what was done, what is waiting, what is next. Each element is dropped
    entirely when it has nothing to say -- never rendered as an empty label.
    """
    out: list = []
    if state.request:
        asked = "You asked to %s." % state.request.rstrip(".")
        if state.intent:
            asked += " Taken as: %s." % state.intent.rstrip(".")
        out.append(asked)

    done = [b for b in state.beats if b.kind in ("did", "chose", "outcome")]
    if done:
        parts = ["%s (%s)" % (b.text.rstrip("."), b.cite) if b.cite else b.text.rstrip(".")
                 for b in done[-3:]]
        lead = "Done: " if len(done) <= 3 else "Done (latest of %d): " % len(done)
        out.append(lead + "; ".join(parts) + ".")

    if open_blocks:
        out.append("Waiting: " + "; ".join(b.text.rstrip(".") for b in open_blocks[:2]) + ".")

    # No "Next:" line here. ``next`` is not in KINDS (:74) and ``add_beat``
    # rejects any kind outside it (:301), so the branch that used to sit here
    # could never render. The next step is the canonical block's own terminal
    # line (rules/canonical-output-style.md), not a digest beat.
    return [" ".join(out)] if out else []


def render(state: SessionState) -> str:
    """The report. Pure -- reads the state, writes nothing, safe to re-run.

    An empty state renders as empty. It never synthesises a narrative from a
    session it has no record of, because a fabricated report is worse than a
    missing one: the operator cannot tell it apart from a real one.
    """
    if not state.request and not state.intent and not state.beats:
        if state.unreadable:
            return ("DIGEST UNREADABLE -- this session's record could not be read "
                    "(%s). This is NOT an empty session; do not conclude nothing "
                    "happened." % state.unreadable)
        return "No salient points recorded for this session."

    index = {b.id: b for b in state.beats}
    referenced = {r for b in state.beats for r in b.refs}
    open_blocks = [b for b in _of(state.beats, "blocked") if b.id not in referenced]
    prior_lines, broken = _block_prior(state.beats, index)

    # PROSE, not fields. The labelled-block form printed "Where it stands" and
    # "What happened" as separate sections, which repeated the latest beat in
    # both, and printed a header for every section including ones with nothing
    # to say. A cross-provider council with a non-Anthropic judge (seal
    # 7de6438e) compared a three-sentence prose recap against the field form and
    # killed the fields: a slot with nothing to say still prints the slot.
    blocks = [
        _prose(state, open_blocks),
        prior_lines,
        _block_cites(state.beats),
        _block_unverified(state.beats, broken),
    ]
    if not state.beats:
        blocks.append(["DIGEST UNREADABLE -- %s" % state.unreadable]
                      if state.unreadable else ["No salient points recorded yet."])

    out = []
    for block in blocks:
        if block:
            out.extend(block)
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _do_add(raw: str, path: Optional[Path]) -> int:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        _warn("--add needs a JSON object: %s" % exc)
        return 2
    if not isinstance(payload, dict):
        _warn("--add needs a JSON object, got %s" % type(payload).__name__)
        return 2

    beat = append(payload.get("kind", ""), payload.get("text", ""),
                  payload.get("cite"), payload.get("refs") or (), path)
    if beat is None:
        return 1
    print("%s %s" % (beat.id, beat.kind))
    return 0


def main(argv: Sequence[str]) -> int:
    """CLI: ``python3 lib/session_digest.py --render | --add '<json>'``."""
    parser = argparse.ArgumentParser(prog="session_digest", description=__doc__.split("\n")[0])
    parser.add_argument("--render", action="store_true", help="print the session report")
    parser.add_argument("--add", metavar="JSON", help='e.g. {"kind":"did","text":"...","cite":"..."}')
    parser.add_argument("--request", metavar="TEXT", help="record the operator's request, verbatim")
    parser.add_argument("--intent", metavar="TEXT", help="record how it was read, one sentence")
    parser.add_argument("--path", metavar="FILE", help="digest file (default: this session's)")
    parser.add_argument("--where", action="store_true", help="print the resolved digest path")
    args = parser.parse_args(list(argv[1:]))

    path = Path(args.path).expanduser() if args.path else None
    if args.where:
        print(path or state_path())
        return 0
    if args.request is not None or args.intent is not None:
        prior = load(path or state_path())
        ok = open_session(request=args.request if args.request is not None else prior.request,
                          intent=args.intent if args.intent is not None else prior.intent,
                          path=path)
        if not ok:
            return 1
    if args.add:
        return _do_add(args.add, path)
    if args.render:
        print(render(load(path or state_path())), end="")
        return 0
    if args.request is None and args.intent is None:
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
