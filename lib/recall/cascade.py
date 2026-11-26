"""Local recall cascade: explicit evidence, coverage and replayable answers."""
from __future__ import annotations

import json
import sqlite3
import time
from contextvars import ContextVar
from datetime import datetime
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lib import context_fold_cache
from lib.claude_dir import get_claude_dir
from lib.recall import corpus, index, links, rulings, snapshot
from lib.recall.normalise import normalise

CODE_VERSION = "recall-cascade/1"
_TIMINGS = ContextVar("recall_timings", default={})


@dataclass
class _State:
    question: str
    mode: str
    key: str | None
    why: bool
    deep: bool
    k: int
    roots: corpus.CorpusRoots
    db_path: Path
    moat_store: Path
    snap: snapshot.Snapshot | None = None
    fresh: bool = False
    key_absent: bool = False
    degraded: bool = False
    reason: str | None = None
    complete: bool = True
    candidates: list = field(default_factory=list)
    searched: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    coverage: dict = field(default_factory=dict)


def last_timings() -> dict:
    """Milliseconds from the last call in this execution context, outside the answer."""
    return dict(_TIMINGS.get())


def _skip(state, scope, reason):
    item = {"scope": scope, "reason": reason}
    if item not in state.skipped:
        state.skipped.append(item)
    state.complete = False


def _failed(state, scope, reason):
    state.degraded = True
    if state.reason is None:
        state.reason = reason
    _skip(state, scope, reason)


def _run(state, step, scope, function, *args):
    started = time.perf_counter()
    try:
        return function(state, *args)
    except Exception:
        # A failed optional backend must never become evidence of absence.
        reason = "corpus_refresh_failed" if step == "S0" else step.lower() + "_failed"
        _failed(state, scope, reason)
        return None
    finally:
        _TIMINGS.get()[step] = (time.perf_counter() - started) * 1000


def _indexed_state(db_path):
    try:
        return corpus.indexed_state(db_path)
    except (OSError, sqlite3.Error):
        return None, {}


def _prepare(state):
    state.snap = snapshot.take(state.roots, moat_meta=state.moat_store / "meta.json",
                               idr_path=get_claude_dir() / "state" / "precog" / "idr.jsonl")
    signature, coverage = _indexed_state(state.db_path)
    # A manifest also catches deletions and edits below another store's max epoch.
    if signature != corpus.digest(state.snap.stores):
        corpus.refresh(state.db_path, state.roots)
        signature, coverage = corpus.indexed_state(state.db_path)
    state.fresh = signature == corpus.digest(state.snap.stores)
    state.coverage.update(coverage)
    if not state.fresh:
        _failed(state, "corpus", "corpus_refresh_failed")


def _ruling_id(ruling, roots):
    path = Path(ruling.source.rsplit(":", 1)[0])
    if path.name == "MEMORY.md":
        return _pinned_id(ruling, path)
    for kind, field_name in corpus.STORES.items():
        root = getattr(roots, field_name)
        if root is not None and path.is_relative_to(root):
            return corpus.PREFIXES[kind] + "/" + path.relative_to(root).as_posix()
    return "ruling:" + ruling.source


def _pinned_id(ruling, path):
    number = int(ruling.source.rsplit(":", 1)[1])
    # The same decoding as rulings._read, which found this line number.
    line = path.read_text(encoding="utf-8", errors="replace").splitlines()[number - 1]
    target = line.rsplit(" :: ", 1)[1].strip().removesuffix(".md")
    return "memory/" + target + ".md"


def _l0(state):
    found = rulings.match(state.question, rulings.load(get_claude_dir(), roots=state.roots))
    state.candidates.extend({"id": _ruling_id(r, state.roots), "title": r.title,
                             "source": r.source, "superseded_by": r.superseded_by,
                             "certainty": "RULING", "layer": "L0"} for r in found)
    state.searched.append("rulings")


def _moat_available(store):
    return all((store / name).is_file() for name in ("index.db", "events.jsonl", "mmr.json"))


def _artefact_hit(art):
    superseded = art.get("supersedes") if art.get("status") == "superseded" else None
    return {"id": art["path"], "title": art["subject"], "kind": art["type"],
            "certainty": "VERIFIED", "currently_binds": None, "layer": "L1",
            "superseded_by": superseded, "status": art.get("status")}


def _built_at_ns(built_at):
    try:
        return int(datetime.fromisoformat(str(built_at).replace("Z", "+00:00")).timestamp() * 1e9)
    except ValueError:
        return None


def _moat_stale(state):
    """True when a moat source changed after the last build, or the build time is unknown.

    A verified moat is genuine, not current: verification checks its own audit
    commitment, never whether newer decision or belief records exist."""
    built = _built_at_ns(state.snap.moat.get("built_at")) if state.snap else None
    log = state.snap.decision_log.get("path") if state.snap else None
    if built is None or log is None:
        return True
    sources = (Path(log), Path(log).with_name("context.jsonl"))
    return any((stamp := snapshot.stamp(path)) is not None and stamp[0] > built for path in sources)


def _l1(state):
    state.coverage["moat"] = {"count": None, "epoch": None, "fresh": False}
    if not _moat_available(state.moat_store):
        _skip(state, "moat", "missing or unbuilt")
        return
    result = index.query_subject(state.question, store=state.moat_store)
    if not result.freshness.get("verified"):
        _failed(state, "moat", "moat_verification_failed")
        return
    fresh = not _moat_stale(state)
    state.coverage["moat"] = {"count": result.freshness["event_count"],
                              "epoch": state.snap.moat.get("built_at") if state.snap else None,
                              "fresh": fresh}
    if not fresh:
        _skip(state, "moat", "partial: records written after its last build are not indexed")
    state.candidates.extend(_artefact_hit(a) for a in result.artefacts
                            if a.get("status") != "invalidated")
    state.searched.append("moat")


def _store_coverage(state, kind):
    source = state.snap.stores[kind] if state.snap else {"available": False}
    coverage = state.coverage.setdefault(kind, {"count": 0, "epoch": 0})
    coverage["fresh"] = state.fresh and source["available"]
    if not source["available"]:
        _skip(state, kind, "missing or disabled")
    elif not state.fresh:
        _skip(state, kind, "corpus not fresh")


def _l2(state):
    for kind in corpus.STORES:
        _store_coverage(state, kind)
    if state.key is not None and state.fresh:
        state.key_absent = not corpus.has_key(state.db_path, state.key)


def _found(hit, layer):
    return {**asdict(hit), "certainty": "FOUND", "calibrated": False, "layer": layer}


def _l3(state):
    if not state.fresh:
        return
    # Collect disagreements before applying the final active-hit limit.
    limit = sum(state.coverage[kind]["count"] for kind in corpus.STORES)
    found = corpus.search(state.db_path, state.question, limit)
    state.candidates.extend(_found(hit, "L3") for hit in found)
    state.searched.extend(kind for kind in corpus.STORES if state.coverage[kind]["fresh"])


def _l4(state):
    if not state.fresh:
        _skip(state, "links", "corpus not fresh")
        return
    edges, docs = corpus.graph(state.db_path)
    seeds = [_seed_id(hit["id"], state.roots) for hit in state.candidates]
    ranked = links.personalised_pagerank(edges, seeds)
    found = []
    for doc_id, score in ranked:
        if doc_id in docs and score > 0:
            hit = _found(docs[doc_id], "L4")
            hit["score"] = score
            found.append(hit)
    state.candidates.extend(found[:state.k])
    state.searched.append("links")


def _seed_id(doc_id, roots):
    path = Path(doc_id)
    if not path.is_absolute():
        return doc_id
    for kind, field_name in corpus.STORES.items():
        root = getattr(roots, field_name)
        if root is not None and path.is_relative_to(root):
            return corpus.PREFIXES[kind] + "/" + path.relative_to(root).as_posix()
    return doc_id


def _merge(state):
    hits, disagreements, seen = [], [], set()
    superseded_seen = set()
    rank = {"L0": 0, "L1": 1, "L3": 2, "L4": 3}
    for hit in sorted(state.candidates, key=lambda h: rank[h["layer"]]):
        identity = _seed_id(hit["id"], state.roots)
        if hit.get("superseded_by"):
            if identity not in superseded_seen:
                disagreements.append(hit)
                superseded_seen.add(identity)
            continue
        if identity not in seen:
            seen.add(identity)
            hits.append(hit)
    return hits[:state.k], disagreements


def _escalations(state):
    for scope in ("vectors", "transcripts", "code walk"):
        _skip(state, scope, "escalation not run by the cascade")


def _absence(state, hits, disagreements):
    if state.mode != "absence":
        return None
    if not state.complete:
        return "not_searched_complete"
    if state.key_absent:
        return "key_absent"
    if hits or disagreements or state.key is not None:
        return None
    return "no_evidence"


def _answer(state, hits, disagreements):
    return {"query": state.question, "mode": state.mode,
            "certainty": hits[0]["certainty"] if hits else "NOT_FOUND",
            "absence": _absence(state, hits, disagreements),
            "hits": hits, "disagreements": disagreements,
            "scopes_searched": state.searched, "scopes_not_searched": state.skipped,
            "coverage": state.coverage, "degraded": state.degraded, "reason": state.reason,
            "model_used": None, "snapshot": state.snap.key() if state.snap else None}


def _compute(state):
    for step, scope, function in (("L0", "rulings", _l0), ("L1", "moat", _l1),
                                   ("L2", "coverage", _l2), ("L3", "corpus", _l3)):
        _run(state, step, scope, function)
    if state.why or state.deep:
        _run(state, "L4", "links", _l4)
    merged = _run(state, "L5", "merge", _merge)
    hits, disagreements = merged if merged is not None else ([], [])
    if state.deep:
        _run(state, "L6", "escalation", _escalations)
    return _answer(state, hits, disagreements)


class _Uncacheable(Exception):
    def __init__(self, answer):
        self.answer = answer


def _encode_fresh(state, computed):
    answer = _compute(state)
    computed.append(answer)
    if answer["degraded"]:
        raise _Uncacheable(answer)
    return json.dumps(answer, sort_keys=True).encode()


def _memo_source(state):
    # k and the verbatim echo distinguish answers even when normalised queries agree.
    return json.dumps([normalise(state.question), state.mode, state.key, state.why, state.deep,
                       state.snap.key(), CODE_VERSION, state.k, state.question],
                      sort_keys=True).encode()


def _memoised(state):
    started = time.perf_counter()
    computed = []
    try:
        request = context_fold_cache.DerivationRequest(_memo_source(state), "recall")
        result = context_fold_cache.derive(request, lambda _: _encode_fresh(state, computed),
                                           cache_dir=state.db_path.parent / "cascade-memo")
        return json.loads(result.output)
    except _Uncacheable as exc:
        return exc.answer
    except (OSError, ValueError):
        return computed[0] if computed else _compute(state)
    finally:
        _TIMINGS.get()["memo"] = (time.perf_counter() - started) * 1000


def recall(question, *, mode="lookup", key=None, why=False, deep=False, k=5,
           roots=None, db_path=None, moat_store=None, memo=True) -> dict:
    if mode not in ("lookup", "absence") or k < 1:
        raise ValueError("mode must be lookup or absence and k must be positive")
    _TIMINGS.set({})
    roots = roots if roots is not None else corpus.CorpusRoots.default()
    db_path = Path(db_path) if db_path is not None else get_claude_dir() / "state" / "recall" / "corpus.db"
    state = _State(question, mode, key, why, deep, k, roots, db_path, index.resolve_store(moat_store))
    _run(state, "S0", "corpus", _prepare)
    if memo and state.fresh:
        return _memoised(state)
    return _compute(state)
