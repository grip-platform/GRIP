"""Verbind store -- append-only state under ``${CLAUDE_DIR:-~/.claude}/state/verbind/``.

Three JSON Lines files, never rewritten:

  bindings.jsonl    bind / use / ultralearned / promote / forget events
  unresolved.jsonl  unresolved / denied / round / clear events
  receipts.jsonl    one receipt per sealed decision; each carries the sha256 of
                    the previous receipt, so an edit anywhere breaks the chain

Current state is a replay of the events, so nothing is ever lost, and a line
that does not parse is skipped rather than trusted. What is kept about a prompt
is its sha256, never its text (rules/operator-anonymity.md). The operator's own
note on a binding stays in this local directory and leaves it only as a hash.

The gate seams that live here, with the contract's definitions:

  INDEPENDENT  0 for a retry (the same prompt hash within ``RETRY_WINDOW_S``),
               a pasted copy (the same prompt hash seen earlier), or a
               non-operator origin; otherwise 1.
  QUALITY      tested AND monitored AND ultralearned, where
                 tested       the binding's recorded fixture replays: its
                              meaning hash still matches, and its target is
                              still in the catalogue and still permitted;
                 monitored    no correction in the binding's lineage
                              (``version == 1``); a forget starts a new one;
                 ultralearned an /ultralearn seal id is recorded for it.
  PROMOTE      a deliberate v1 boundary, not a stub: it records a promotion
               event and opens one GitHub issue titled for the skill. It does
               NOT write a SKILL.md -- crystallising the skill stays a reviewed
               /create step, because promotion must grant no new capability.
               Idempotent per (verb, version).
"""
from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

try:
    from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
except ImportError:  # imported as lib.verbind, so lib/ is not itself on sys.path
    from lib._compat import fcntl

from .gate import Binding, Envelope, Meaning, digest

__all__ = [
    "BindingLog",
    "GENESIS",
    "Promotion",
    "RETRY_WINDOW_S",
    "ReceiptChain",
    "Store",
    "UnresolvedLog",
    "default_root",
    "independence",
    "monitored",
    "prompt_hash",
    "quality",
    "tested",
    "ultralearned",
]

BINDINGS, UNRESOLVED, RECEIPTS = "bindings.jsonl", "unresolved.jsonl", "receipts.jsonl"
RETRY_WINDOW_S = 600
GENESIS = "genesis"
_SEAL_RE = re.compile(r"[0-9a-f]{16,64}")
#: Fields a receipt carries but does not hash: the hash itself, the id derived
#: from it, and the IDR leaf id that is only known once the leaf is written.
_UNHASHED = ("hash", "receipt_id", "idr_id")


def default_root() -> Path:
    from claude_dir import get_claude_dir  # noqa: PLC0415 -- resolved at call time

    return get_claude_dir() / "state" / "verbind"


def prompt_hash(prompt: str) -> str:
    return digest("verbind/prompt", prompt or "")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _locked(path: Path, mode: str) -> Iterator[Any]:
    """Open ``path`` under an exclusive lock: POSIX fcntl, or the LockFileEx lock on Windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield fh
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _torn(path: Path) -> bool:
    """True when the file does not end in a newline: a write was cut short."""
    try:
        with open(path, "rb") as raw:
            if raw.seek(0, os.SEEK_END) == 0:
                return False
            raw.seek(-1, os.SEEK_END)
            return raw.read(1) != b"\n"
    except OSError:
        return False


def _write_line(fh: Any, record: dict) -> None:
    """Append one record on a line of its own. After a torn write the record
    starts a fresh line, so the damage stays one line and never swallows it."""
    if _torn(Path(fh.name)):
        fh.write("\n")
    fh.write(_canonical(record) + "\n")
    fh.flush()
    os.fsync(fh.fileno())


class _Log:
    """One append-only JSON Lines file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: dict) -> None:
        with _locked(self.path, "a") as fh:
            _write_line(fh, record)

    def lines(self) -> list:
        """Every line in order, parsed; one that is not a JSON object is None."""
        try:
            raw = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [_parse(line) for line in raw]

    def read(self) -> list:
        """The records. A torn or edited line is skipped, never trusted."""
        return [rec for rec in self.lines() if rec is not None]


def _parse(line: str) -> Optional[dict]:
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


class _Events:
    """What every event log shares: one file and one clock."""

    def __init__(self, log: _Log, clock: Callable[[], float]) -> None:
        self._log = log
        self._clock = clock

    def _now(self) -> str:
        return _iso(self._clock())


# ── bindings ─────────────────────────────────────────────────────────────────


def _replay_bind(state: dict, rec: dict) -> None:
    meaning = Meaning.from_dict(rec["meaning"])
    state[rec["verb"]] = Binding(rec["verb"], meaning, version=int(rec["version"]), uses=1,
                                 created=str(rec.get("ts", "")),
                                 fixture=dict(rec.get("fixture") or {}))


def _same_version(state: dict, rec: dict) -> Optional[Binding]:
    b = state.get(rec["verb"])
    return b if b is not None and b.version == int(rec["version"]) else None


def _replay_use(state: dict, rec: dict) -> None:
    b = _same_version(state, rec)
    if b is not None:
        state[b.verb] = replace(b, uses=b.uses + (1 if int(rec["n"]) == 1 else 0))


def _replay_ultralearned(state: dict, rec: dict) -> None:
    b = _same_version(state, rec)
    if b is not None:
        state[b.verb] = replace(b, ultralearn_seal=str(rec["seal"]))


def _replay_forget(state: dict, rec: dict) -> None:
    state.pop(rec["verb"], None)


_REPLAY = {"bind": _replay_bind, "use": _replay_use,
           "ultralearned": _replay_ultralearned, "forget": _replay_forget}


class BindingLog(_Events):
    """Bindings, replayed from their events. Writes only ever append."""

    def current(self) -> dict:
        state: dict = {}
        for rec in self._log.read():
            step = _REPLAY.get(rec.get("op"))
            if step is None:
                continue
            try:
                step(state, rec)
            except (KeyError, TypeError, ValueError):
                continue  # a malformed event changes nothing
        return state

    def lookup(self, verb: str) -> Optional[Binding]:
        return self.current().get(verb)

    def make(self, verb: str, meaning: Meaning, envelope: Envelope) -> Binding:
        """Build the next version of a binding. Writes nothing; ``commit`` does."""
        existing = self.lookup(verb)
        version = existing.version + 1 if existing else 1
        fixture = {"verb": verb, "kind": meaning.kind, "target": meaning.target,
                   "meaning_hash": meaning.digest()}
        return Binding(verb, meaning, version=version, uses=1, created=self._now(),
                       fixture=fixture)

    def commit(self, binding: Binding) -> Binding:
        self._log.append({"op": "bind", "verb": binding.verb, "version": binding.version,
                          "meaning": binding.meaning.to_dict(), "fixture": binding.fixture,
                          "ts": binding.created or self._now()})
        return binding

    def record_use(self, binding: Binding, n: int) -> Binding:
        n = 1 if n == 1 else 0
        if n:
            self._log.append({"op": "use", "verb": binding.verb, "version": binding.version,
                              "n": n, "ts": self._now()})
        return replace(binding, uses=binding.uses + n)

    def forget(self, verb: str) -> bool:
        if self.lookup(verb) is None:
            return False
        self._log.append({"op": "forget", "verb": verb, "ts": self._now()})
        return True


# ── unresolved verbs and Koppel rounds ───────────────────────────────────────

_UNRESOLVED_OPS = frozenset({"unresolved", "denied", "round", "clear"})


class UnresolvedLog(_Events):
    """Deferred verbs, denials and the persisted Koppel round count."""

    def _events(self, verb: str) -> list:
        return [r for r in self._log.read() if r.get("verb") == verb]

    def unresolved(self, verb: str) -> bool:
        """True when the verb's latest unresolved-or-clear event is unresolved."""
        state = [r["op"] for r in self._events(verb) if r.get("op") in ("unresolved", "clear")]
        return bool(state) and state[-1] == "unresolved"

    def rounds_used(self, verb: str) -> int:
        """Koppel rounds since the verb was last resolved or cleared."""
        count = 0
        for r in self._events(verb):
            op = r.get("op")
            count = 0 if op in ("unresolved", "clear") else count + (op == "round")
        return count

    def record(self, verb: str, op: str, **fields: Any) -> None:
        if op not in _UNRESOLVED_OPS:
            raise ValueError(f"unknown unresolved-log op {op!r}")
        self._log.append({"op": op, "verb": verb, "ts": self._now(), **fields})

    def deferred(self) -> list:
        """Every verb currently unresolved, in the order first seen."""
        seen = dict.fromkeys(r.get("verb") for r in self._log.read() if r.get("verb"))
        return [v for v in seen if self.unresolved(v)]


# ── receipts ─────────────────────────────────────────────────────────────────


def _receipt_hash(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k not in _UNHASHED}
    return digest("verbind/receipt", _canonical(body))


def _safe_anchor(anchor: Optional[Callable[[dict], Optional[str]]], rec: dict) -> Optional[str]:
    if anchor is None:
        return None
    try:
        return anchor(dict(rec))
    except Exception:  # noqa: BLE001 -- the receipt stands even if the leaf fails
        return None


class ReceiptChain:
    """Receipts chained by sha256 of the previous receipt."""

    def __init__(self, path: Path, clock: Callable[[], float]) -> None:
        self.path = path
        self._clock = clock

    def records(self) -> list:
        return _Log(self.path).read()

    def append(self, body: dict, anchor: Optional[Callable[[dict], Optional[str]]] = None) -> dict:
        """Chain one receipt. ``anchor`` (the IDR leaf writer) runs under the lock.

        The receipt's hash is fixed before the anchor sees it, so the leaf can
        carry it; the leaf id is then stored beside the receipt, unhashed.
        """
        with _locked(self.path, "a+") as fh:
            now = self._clock()
            rec = {**body, "prev": self._last_hash(), "t": int(now), "ts": _iso(now)}
            rec["hash"] = _receipt_hash(rec)
            rec["receipt_id"] = "vb-" + rec["hash"][:16]
            rec["idr_id"] = _safe_anchor(anchor, rec)
            _write_line(fh, rec)
        return rec

    def _last_hash(self) -> str:
        recs = self.records()
        return str(recs[-1].get("hash")) if recs else GENESIS

    def verify(self) -> tuple:
        """(True, None) when every line is a receipt that hashes and links; else
        (False, line index).

        A line that does not parse fails here, where ``records`` skips it.
        Skipping it would let the newest receipt be torn or edited into
        nonsense and the chain verify as if that receipt had never existed.
        """
        prev = GENESIS
        for i, rec in enumerate(_Log(self.path).lines()):
            if rec is None or rec.get("prev") != prev or rec.get("hash") != _receipt_hash(rec):
                return False, i
            prev = rec["hash"]
        return True, None

    def last_seen(self, p_hash: str) -> Optional[float]:
        times = [r.get("t") for r in self.records() if r.get("prompt_hash") == p_hash]
        times = [t for t in times if isinstance(t, (int, float))]
        return max(times) if times else None


def independence(chain: ReceiptChain, prompt: str, envelope: Envelope,
                 clock: Callable[[], float] = time.time) -> tuple:
    """INDEPENDENT with its reason: (0|1, non-operator|retry|pasted-copy|independent)."""
    if not envelope.trusted:
        return 0, "non-operator"
    last = chain.last_seen(prompt_hash(prompt))
    if last is None:
        return 1, "independent"
    return 0, "retry" if clock() - last <= RETRY_WINDOW_S else "pasted-copy"


# ── quality and promotion ────────────────────────────────────────────────────


def tested(b: Binding, exists: Callable[[str, str], bool],
           permitted: Callable[[str, str], bool]) -> bool:
    m, fx = b.meaning, b.fixture or {}
    return (fx.get("meaning_hash") == m.digest() and fx.get("target") == m.target
            and exists(m.kind, m.target) and permitted(m.kind, m.target))


def monitored(b: Binding) -> bool:
    return b.version == 1


def ultralearned(b: Binding) -> bool:
    return bool(_SEAL_RE.fullmatch(b.ultralearn_seal or ""))


def quality(b: Binding, *, exists: Callable[[str, str], bool],
            permitted: Callable[[str, str], bool]) -> bool:
    return tested(b, exists, permitted) and monitored(b) and ultralearned(b)


class Promotion(_Events):
    """Ultralearn seals and the one-time promotion of a binding."""

    def mark_ultralearned(self, binding: Binding, seal: str) -> None:
        if not _SEAL_RE.fullmatch(seal or ""):
            raise ValueError("an ultralearn seal id is 16-64 lowercase hex characters")
        self._log.append({"op": "ultralearned", "verb": binding.verb,
                          "version": binding.version, "seal": seal, "ts": self._now()})

    def promoted(self, binding: Binding) -> bool:
        return any(r.get("op") == "promote" and r.get("verb") == binding.verb
                   and r.get("version") == binding.version and r.get("status") == "issue-opened"
                   for r in self._log.read())

    def promote(self, binding: Binding, opener: Callable[[Binding], Optional[str]]) -> Optional[dict]:
        """Record the promotion and open its issue, once per (verb, version)."""
        if self.promoted(binding):
            return None
        try:
            url = opener(binding)
        except Exception:  # noqa: BLE001 -- recorded as failed; retried on the next use
            url = None
        rec = {"op": "promote", "verb": binding.verb, "version": binding.version,
               "kind": binding.meaning.kind, "target": binding.meaning.target,
               "status": "issue-opened" if url else "issue-failed", "issue": url,
               "ts": self._now()}
        self._log.append(rec)
        return rec


@dataclass(frozen=True)
class Store:
    """The three logs under one root. Construct with ``Store.at``."""

    root: Path
    bindings: BindingLog
    unresolved: UnresolvedLog
    receipts: ReceiptChain
    promotion: Promotion

    @classmethod
    def at(cls, root: Optional[Path] = None, clock: Callable[[], float] = time.time) -> "Store":
        root = Path(root) if root is not None else default_root()
        blog = _Log(root / BINDINGS)
        return cls(root, BindingLog(blog, clock), UnresolvedLog(_Log(root / UNRESOLVED), clock),
                   ReceiptChain(root / RECEIPTS, clock), Promotion(blog, clock))
