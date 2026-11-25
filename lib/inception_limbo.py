#!/usr/bin/env python3
"""inception_limbo.py — the limbo ledger: recurrence, review, and refusal.

`inception.py` partitions dreamt ideas into ADMITTED and LIMBO, and the first
version simply threw limbo away. That was too blunt, and wrong in two directions.

It discarded the only signal that distinguishes a bad idea from an EARLY one. An
idea can be unanchored because it is worthless, or because the evidence that
would ground it does not exist yet. Nothing in a single night's output tells
those apart — but recurrence across nights does.

And it was unselected in the other direction. The pathology this whole subsystem
exists to resist is accumulating artefacts nobody evaluates (777 genes with zero
recorded successes; an achievement log 96.7% telemetry). A limbo pile that is
silently dropped is never evaluated either — it just fails invisibly instead of
visibly. Making the operator the judge turns the discard path into a real
selection mechanism, with a human as the exogenous anchor.

RECURRENCE IS ONLY EVIDENCE WHEN THE SEEDS WERE INDEPENDENT.

This is the load-bearing constraint and the one that keeps the idea rigorous
rather than mystical. The same seed and the same model will produce the same
thought on ten consecutive nights: that is determinism, and counting it would
manufacture significance out of a fixed point. Recurrence is recorded ONLY when a
materially similar idea arrives from a seed that has not contributed to that
entry before. Ten repeats of one seed count once. Three arrivals from three
unrelated seeds count three times, and that is the number worth a human's
attention — different starting points converging on the same unanchored thought.

Similarity is deterministic by default (token-set Jaccard over normalised text),
for the same reason the limbo sweep takes no model judgement: a check on
confabulation that is itself a model call can be confabulated. A semantic
comparator can be injected where better recall is worth the dependency.

THE OPERATOR'S THREE VERDICTS

    APPROVE      - promote it; it leaves limbo and carries its recurrence with it
    DENY_ONCE    - not this time; the entry stays and may recur again
    DENY_ALWAYS  - refuse this whole CLASS of idea, permanently

DENY_ALWAYS matches by similarity, not by string equality. A denial that only
catches the exact wording is defeated by a synonym on the next night, which would
make refusal useless precisely where it matters most — against an idea the
generator is drawn to repeatedly.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations
from pathlib import Path
from typing import Callable, Iterable, Optional

__all__ = [
    "Convergence",
    "find_convergences",
    "relational_signature",
    "Verdict",
    "LimboEntry",
    "LimboLedger",
    "jaccard_similarity",
    "normalise",
]

# Words carrying no discriminating signal for similarity. Deliberately short:
# an aggressive stoplist makes unrelated ideas look alike, and a false MERGE is
# worse than a false split — merging hides an idea behind another entry, where
# splitting only costs a duplicate row the operator can see and judge.
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this",
    "to", "of", "in", "on", "for", "with", "as", "is", "are", "be", "it", "its",
    "by", "at", "from", "we", "should", "could", "would", "can", "may", "might",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Default bar for surfacing an unanchored idea to a human. Three independent
# seeds, not two: two can coincide, and the cost of a false surface is the
# operator's attention — the scarcest input in the system.
DEFAULT_RECURRENCE_THRESHOLD = 3

# Similarity at or above this counts as "the same idea". Tuned to merge
# rewordings while keeping genuinely different proposals apart; adjust with the
# corpus, never to hit a target count.
DEFAULT_SIMILARITY = 0.6


class Verdict(str, Enum):
    APPROVE = "approve"
    DENY_ONCE = "deny_once"
    DENY_ALWAYS = "deny_always"


def normalise(text: str) -> frozenset[str]:
    """Lowercase, tokenise, drop stopwords. Deterministic and dependency-free."""
    return frozenset(
        t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS
    )


def jaccard_similarity(a: str, b: str) -> float:
    """Token-set overlap in [0, 1]. Two empty texts are dissimilar, not identical.

    Returning 0.0 for empty input matters: an idea with no meaningful tokens must
    never silently merge into an existing entry, which would hide it.
    """
    ta, tb = normalise(a), normalise(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


SimilarityFn = Callable[[str, str], float]


@dataclass
class LimboEntry:
    """One unanchored idea, and every independent seed that has produced it."""

    id: str
    text: str
    seeds: list[str] = field(default_factory=list)
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    denied_always: bool = False

    @property
    def recurrence(self) -> int:
        """Distinct seeds that produced this idea — never raw sighting count."""
        return len(self.seeds)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "text": self.text, "seeds": list(self.seeds),
            "recurrence": self.recurrence, "first_seen": self.first_seen,
            "last_seen": self.last_seen, "denied_always": self.denied_always,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LimboEntry":
        return cls(
            id=d["id"], text=d["text"], seeds=list(d.get("seeds", [])),
            first_seen=d.get("first_seen"), last_seen=d.get("last_seen"),
            denied_always=bool(d.get("denied_always", False)),
        )


class LimboLedger:
    """Persistent record of unanchored ideas, their recurrence, and refusals.

    Not a queue. Entries persist across nights precisely so recurrence can
    accumulate; that is the whole mechanism. Growth is bounded by DENY_ALWAYS
    (the operator pruning a class) and by the caller's own retention policy —
    the ledger deliberately does not expire entries on its own, because silent
    expiry would reintroduce exactly the invisible discard this replaces.
    """

    def __init__(
        self,
        entries: Optional[Iterable[LimboEntry]] = None,
        *,
        similarity: SimilarityFn = jaccard_similarity,
        threshold: float = DEFAULT_SIMILARITY,
    ) -> None:
        self._entries: list[LimboEntry] = list(entries or [])
        self._similarity = similarity
        self._threshold = threshold

    @property
    def entries(self) -> list[LimboEntry]:
        return list(self._entries)

    def _match(self, text: str, *, include_denied: bool) -> Optional[LimboEntry]:
        """Best entry at or above the similarity threshold, or None."""
        best, best_score = None, 0.0
        for e in self._entries:
            if e.denied_always and not include_denied:
                continue
            score = self._similarity(text, e.text)
            if score >= self._threshold and score > best_score:
                best, best_score = e, score
        return best

    def is_denied(self, text: str) -> bool:
        """True when this idea belongs to a class the operator refused outright."""
        for e in self._entries:
            if e.denied_always and self._similarity(text, e.text) >= self._threshold:
                return True
        return False

    def record(self, idea_id: str, text: str, seed: str, *, when: str) -> Optional[LimboEntry]:
        """Log one limbo sighting. Returns the entry, or None if class-denied.

        A seed already present does NOT increment recurrence — the same origin
        producing the same thought again is determinism, not corroboration. The
        sighting still refreshes ``last_seen`` so staleness stays visible.
        """
        if self.is_denied(text):
            return None
        entry = self._match(text, include_denied=False)
        if entry is None:
            entry = LimboEntry(id=idea_id, text=text, seeds=[seed],
                               first_seen=when, last_seen=when)
            self._entries.append(entry)
            return entry
        if seed not in entry.seeds:
            entry.seeds.append(seed)
        entry.last_seen = when
        return entry

    def due_for_review(
        self, *, threshold: int = DEFAULT_RECURRENCE_THRESHOLD,
    ) -> list[LimboEntry]:
        """Entries recurring across enough INDEPENDENT seeds to warrant a human.

        Sorted by recurrence descending: the most independently-rediscovered idea
        is the one most worth a look, whatever its evidence still lacks.
        """
        due = [e for e in self._entries
               if not e.denied_always and e.recurrence >= threshold]
        return sorted(due, key=lambda e: (-e.recurrence, e.id))

    def apply(self, entry_id: str, verdict: Verdict) -> Optional[LimboEntry]:
        """Record the operator's decision. Returns the affected entry.

        APPROVE removes the entry and hands it back so the caller can promote it
        with its recurrence intact — how many independent seeds found it is part
        of the case for it. DENY_ONCE leaves it to recur. DENY_ALWAYS keeps the
        entry as a tombstone: the text must survive for similarity matching to
        suppress the class in future.
        """
        for i, e in enumerate(self._entries):
            if e.id != entry_id:
                continue
            if verdict is Verdict.APPROVE:
                return self._entries.pop(i)
            if verdict is Verdict.DENY_ALWAYS:
                e.denied_always = True
            return e
        return None

    # --- persistence ------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Write atomically so a crash cannot truncate history, and two writers
        cannot corrupt each other.

        The first version wrote to a FIXED ``.tmp`` name and replaced it. That
        looks atomic and is not: two saves overlapping (a scheduled night and a
        manual run) write into the same scratch path, their bytes interleave, and
        whichever replace lands second publishes a mixture that is neither
        ledger. ``mkstemp`` creates a UNIQUE scratch file per call, which reduces
        the worst case to last-writer-wins on a whole, valid document — the same
        fix ``lib/inception_wake.py::write_wake`` already shipped (AGORA deferral
        on GRIP#6874, tracked as #6877).
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(p.parent), prefix=f"{p.name}.", suffix=".tmp",
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(
                    "\n".join(json.dumps(e.to_dict(), sort_keys=True)
                              for e in self._entries),
                )
            # mkstemp opens 0600; ordinary tooling reads this file, so restore
            # the permissions a plain write would have produced.
            os.chmod(tmp, 0o644)
            os.replace(tmp, p)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @classmethod
    def load(
        cls, path: str | Path, *, similarity: SimilarityFn = jaccard_similarity,
        threshold: float = DEFAULT_SIMILARITY,
    ) -> "LimboLedger":
        """Read a ledger, skipping unparseable lines rather than failing.

        A corrupt row must not cost the whole history — losing every recorded
        recurrence would destroy the signal this file exists to accumulate.
        """
        p = Path(path)
        entries: list[LimboEntry] = []
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(LimboEntry.from_dict(json.loads(line)))
                except (ValueError, KeyError):
                    continue
        return cls(entries, similarity=similarity, threshold=threshold)


# ---------------------------------------------------------------------------
# Convergence — unrelated ideas that share a structure
#
# Recurrence is VERTICAL: one idea arriving from independent seeds. Convergence
# is HORIZONTAL: separate ideas, each unanchored alone, that turn out to describe
# the same shape. Building blocks that only mean something together.
#
# Gentner's Structure-Mapping Theory supplies the test, and it is the OPPOSITE of
# the similarity used for recurrence. Two ideas sharing their NOUNS are the same
# idea reworded — high surface overlap, no new information. Two ideas sharing
# their RELATIONS while talking about unrelated objects are an analogy, and that
# is what deserves a human's attention: seemingly unrelated, sensible together.
#
#     recurrence  -> high surface overlap        (same thing, seen again)
#     convergence -> high RELATIONAL overlap AND low surface overlap
#
# Deterministic, like the limbo sweep. The relation/entity split is a heuristic,
# not a parser. It must never become a model call: a model asked "are these
# analogous?" will manufacture an analogy, the precise failure this resists.
# ---------------------------------------------------------------------------

# Relational vocabulary: words naming how things stand to one another rather than
# what they are. Explicit rather than inferred, so a bad match traces to a word.
_RELATION_WORDS = frozenset({
    "blocks", "block", "blocked", "causes", "cause", "caused", "prevents",
    "prevent", "prevented", "requires", "require", "required", "produces",
    "produce", "produced", "replaces", "replace", "replaced", "gates", "gate",
    "gated", "verifies", "verify", "verified", "refutes", "refute", "refuted",
    "grounds", "ground", "grounded", "accumulates", "accumulate", "hides",
    "hide", "hidden", "delays", "delay", "delayed", "duplicates", "duplicate",
    "drifts", "drift", "stale", "silent", "silently", "inert", "unbounded",
    "truncates", "truncate", "truncated", "discards", "discard", "discarded",
    "overwrites", "overwrite", "crowds", "crowd", "masks", "mask", "masked",
    "inherits", "inherit", "inherited", "propagates", "propagate", "surfaces",
    "surface", "surfaced", "confirms", "confirm", "confirmed",
})

_RELATION_SUFFIXES = ("ing", "tion", "sion", "ment", "ance", "ence")

# A relation shared by more than this fraction of entries discriminates nothing —
# it is a stopword for the corpus at hand, and pairing on it only records that
# two ideas are written alike. Skipping such buckets is what keeps the candidate
# scan from degenerating into an all-pairs comparison.
_COMMON_RELATION_FRACTION = 0.5
# Below this many entries, "common" is meaningless and every bucket is kept.
_MIN_ENTRIES_FOR_CEILING = 8


# Irregular forms that suffix rules cannot reach. "hidden" and "hide" are one
# relation, but no rule turns one into the other — stripping "en" gives "hidd",
# which matches nothing. English irregulars need a lexicon, and since the
# relational vocabulary is already an explicit curated list, the exceptions can
# be too. Kept small on purpose: every entry is a claim that two words name the
# same relation, and a wrong entry silently merges distinct structures.
# Raised by AGORA review of PR #4236.
_IRREGULAR_STEMS = {
    "hidden": "hide", "hid": "hide",
    "blocked": "block", "built": "build",
    "lost": "lose", "kept": "keep", "held": "hold",
    "shown": "show", "shown_to": "show", "written": "write",
    "broken": "break", "driven": "drive", "grown": "grow",
}


def _stem(word: str) -> str:
    """Crude deterministic stem, applied to RELATIONS only.

    Without this the mechanism cannot fire at all: "hides" and "hide" are the
    same relation, but as raw tokens they never intersect, so two structurally
    identical ideas score zero shared relations. Checked longest-suffix-first so
    "truncates" and "truncated" reduce alike, and floored at four characters so
    short words are not mangled into collisions — that floor is why "hides"
    yields "hide" rather than "hid".

    Irregulars are looked up first, because no suffix rule reaches them.
    """
    if word in _IRREGULAR_STEMS:
        return _IRREGULAR_STEMS[word]
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def relational_signature(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """Split an idea into ``(relations, entities)``.

    Relations are the explicit relational vocabulary plus process-shaped words,
    reduced to stems so morphological variants of one relation match. Entities
    are everything else, left unstemmed — they are only ever compared for
    OVERLAP, and stemming them would blur distinct subjects together and weaken
    the surface ceiling that keeps rewordings out.

    Approximate by design, meant to be inspected rather than trusted blindly.
    """
    tokens = normalise(text)
    relations = frozenset(
        _stem(t) for t in tokens
        if t in _RELATION_WORDS or (len(t) > 5 and t.endswith(_RELATION_SUFFIXES))
    )
    entities = frozenset(
        t for t in tokens
        if not (t in _RELATION_WORDS or (len(t) > 5 and t.endswith(_RELATION_SUFFIXES)))
    )
    return relations, entities


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class Convergence:
    """Two separately-unanchored ideas that share a relational shape."""

    entry_ids: list[str]
    texts: list[str]
    shared_relations: list[str]
    relational_overlap: float
    surface_overlap: float

    def to_dict(self) -> dict:
        return {
            "entry_ids": list(self.entry_ids), "texts": list(self.texts),
            "shared_relations": sorted(self.shared_relations),
            "relational_overlap": round(self.relational_overlap, 4),
            "surface_overlap": round(self.surface_overlap, 4),
        }


def _index_relations(live: list[LimboEntry]) -> dict[str, list[int]]:
    """Inverted index: relation word -> positions of entries containing it.

    Avoids comparing every entry with every other. Only entries sharing a
    relation word can possibly converge, so the index turns an all-pairs scan
    into a scan over the (much smaller) set of genuinely shared-relation pairs.
    """
    index: dict[str, list[int]] = defaultdict(list)
    for pos, entry in enumerate(live):
        relations, _ = relational_signature(entry.text)
        _add_positions(index, relations, pos)
    return index


def _add_positions(index: dict[str, list[int]], words: Iterable[str], pos: int) -> None:
    for word in words:
        index[word].append(pos)


def _candidate_pairs(
    index: dict[str, list[int]], min_shared: int, *, total: int,
) -> list[tuple[int, int]]:
    """Pairs co-occurring in at least ``min_shared`` relation buckets.

    Buckets holding more than ``_COMMON_RELATION_FRACTION`` of all entries are
    skipped, and this is a correctness argument before it is a performance one.
    A relation appearing in most entries carries no discriminating signal — it is
    a stopword for this corpus, and pairing on it says only that both ideas are
    written in the same register. The same skip removes the blow-up the AGORA
    review of PR #4236 flagged: without it, one word shared by 5,000 entries
    generates ~12.5 million pairs in memory before anything is filtered.

    A bucket is only skipped when there are enough entries for "common" to mean
    something; on small corpora every bucket is kept, since a word appearing in
    three of four entries there is not evidence of anything.
    """
    counts: Counter = Counter()
    ceiling = max(_MIN_ENTRIES_FOR_CEILING, int(total * _COMMON_RELATION_FRACTION))
    for positions in index.values():
        unique = sorted(set(positions))
        if len(unique) > ceiling:
            continue
        counts.update(combinations(unique, 2))
    return [pair for pair, n in counts.items() if n >= min_shared]


def _pair_convergence(
    a: LimboEntry, b: LimboEntry, *, min_rel: float, max_surf: float, min_shared: int,
) -> Optional[Convergence]:
    """Test one candidate pair against BOTH thresholds."""
    rel_a, ent_a = relational_signature(a.text)
    rel_b, ent_b = relational_signature(b.text)
    shared = rel_a & rel_b
    if len(shared) < min_shared:
        return None
    r, s = _overlap(rel_a, rel_b), _overlap(ent_a, ent_b)
    if r < min_rel or s > max_surf:
        return None
    return Convergence(
        entry_ids=[a.id, b.id], texts=[a.text, b.text],
        shared_relations=sorted(shared), relational_overlap=r, surface_overlap=s,
    )


def find_convergences(
    entries: Iterable[LimboEntry],
    *,
    min_relational_overlap: float = 0.34,
    max_surface_overlap: float = 0.25,
    min_shared_relations: int = 2,
) -> list[Convergence]:
    """Find limbo entries that are structurally alike but topically apart.

    The thresholds do opposite jobs and both are required. The relational FLOOR
    asks "do these describe the same shape?"; the surface CEILING asks "are they
    nonetheless about different things?". Drop the ceiling and this degenerates
    into the recurrence detector, returning rewordings that are already handled
    and are not what a human needs to see.

    ``min_shared_relations`` guards a small-set artefact: one relation in common
    can clear a ratio test on two short ideas without meaning anything.

    Denied-always entries are excluded — a refused class must not return through
    the side door of a cluster.
    """
    live = [e for e in entries if not e.denied_always]
    pairs = _candidate_pairs(
        _index_relations(live), min_shared_relations, total=len(live),
    )
    found = (
        _pair_convergence(
            live[i], live[j], min_rel=min_relational_overlap,
            max_surf=max_surface_overlap, min_shared=min_shared_relations,
        )
        for i, j in pairs
    )
    # Strongest structural match with least topical overlap first — that ordering
    # puts the most genuinely analogical pair at the top of the review.
    return sorted(
        (c for c in found if c is not None),
        key=lambda c: (-c.relational_overlap, c.surface_overlap),
    )
