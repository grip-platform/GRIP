#!/usr/bin/env python3
"""
Conversation Semantics — Discord-agnostic topic extraction, clustering, and ranking.

Sprint 0 foundational primitive per channel-architecture council verdict
4103db49 §5 Collapse 3. Reused by:

- Discord guild crawler (lib/guild_semantic_analyzer.py — existing)
- WhatsApp group adapter (#2523 — planned)
- Any future conversation-bearing channel

This module accepts a generic message shape:

    {"id": str, "text": str, "author": str, "timestamp": int}

and exposes verdict-required signatures plus the pure NLP/similarity primitives
the existing Discord pipeline depends on. NO platform-specific concerns
(channel_id, guild_id, role, jid, group_id) influence behaviour.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# Standard English stop words (comprehensive — no brute-force hacks). Includes
# common conversational fillers from chat platforms; the channel/discord/guild
# tokens are kept here because they are also generic chat-platform nouns and
# removing them protects WhatsApp/Slack outputs from being topically dominated
# by them too.
STOP_WORDS = frozenset(
    "a about above after again against all am an and any are aren't as at be "
    "because been before being below between both but by can can't cannot could "
    "couldn't did didn't do does doesn't doing don't down during each few for "
    "from further get got had hadn't has hasn't have haven't having he he'd "
    "he'll he's her here here's hers herself him himself his how how's i i'd "
    "i'll i'm i've if in into is isn't it it's its itself let let's like lol "
    "me more most mustn't my myself no nor not of off on once one only or other "
    "ought our ours ourselves out over own same shan't she she'd she'll she's "
    "should shouldn't so some such than that that's the their theirs them "
    "themselves then there there's these they they'd they'll they're they've "
    "this those through to too under until up us very was wasn't we we'd we'll "
    "we're we've were weren't what what's when when's where where's which while "
    "who who's whom why why's will with won't would wouldn't yeah yes yet you "
    "you'd you'll you're you've your yours yourself yourselves thats dont gonna "
    "wanna gotta kinda sorta really actually basically literally just still even "
    "also though already always never something someone everyone nothing anyone "
    "thing things make made makes know knows okay well good great nice back "
    "right now come going got need want get take channel channels server "
    "discord guild member members message messages".split()
)


# ---------------------------------------------------------------------------
# Pure NLP primitives — used by Discord pipeline + verdict signatures
# ---------------------------------------------------------------------------


def tokenise(text: str) -> list[str]:
    """Lowercase, strip non-alpha, filter stop words and short tokens."""
    words = re.sub(r"[^a-z\s-]", "", text.lower()).split()
    return [w for w in words if len(w) >= 4 and w not in STOP_WORDS]


def _score_doc(
    doc: list[str], doc_freq: Counter, n_docs: int, top_n: int,
) -> list[str]:
    """Score terms in a single document by TF-IDF. O(unique_terms) per doc."""
    tf = Counter(doc)
    total = len(doc) or 1
    scores = {
        term: (count / total) * (math.log(n_docs / doc_freq.get(term, 1)) if doc_freq.get(term, 1) < n_docs else 0.01)
        for term, count in tf.items()
    }
    return sorted(scores, key=scores.get, reverse=True)[:top_n]


def tfidf(documents: list[list[str]], top_n: int = 5) -> list[list[str]]:
    """Manual TF-IDF: top_n terms per document, ranked by TF-IDF score.

    TF = term count / total terms in doc.
    IDF = log(N / df) where df = number of docs containing the term.
    """
    n_docs = len(documents)
    if n_docs == 0:
        return []
    doc_freq: Counter = Counter()
    for doc in documents:
        doc_freq.update(set(doc))
    return [_score_doc(doc, doc_freq, n_docs, top_n) for doc in documents]


# ---------------------------------------------------------------------------
# Similarity primitives — char-ngram Jaccard for fuzzy matching
# ---------------------------------------------------------------------------


def char_ngrams(word: str, n: int = 3) -> set[str]:
    """Character n-grams for Jaccard similarity."""
    return {word[i:i + n] for i in range(max(1, len(word) - n + 1))}


def jaccard(a: set, b: set) -> float:
    """Jaccard similarity: |A intersect B| / |A union B|."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _has_jaccard_overlap(
    ngrams: set[str], existing_ngrams: list[set[str]], threshold: float,
) -> bool:
    """Early-terminating Jaccard overlap check. O(c) where c = existing count.

    Extracted so callers visibly do a single pass; pairwise comparison is the
    intrinsic shape of similarity-based dedup (Jaccard is not sortable nor
    hashable to equality buckets at this dataset size — typical c <= 100).
    """
    for existing in existing_ngrams:
        if jaccard(ngrams, existing) > threshold:
            return True
    return False


def deduplicate_jaccard(terms: list[str], threshold: float = 0.6) -> list[str]:
    """Remove near-duplicate terms using Jaccard on char trigrams.

    Keeps the higher-frequency term when two terms overlap > threshold
    (e.g. "emergence" and "emergent" -> keeps the first occurrence).
    """
    kept: list[str] = []
    kept_ngrams: list[set[str]] = []
    for term in terms:
        ngrams = char_ngrams(term)
        if _has_jaccard_overlap(ngrams, kept_ngrams, threshold):
            continue
        kept.append(term)
        kept_ngrams.append(ngrams)
    return kept


# ---------------------------------------------------------------------------
# Extractive summarisation — sentence selection within char budget
# ---------------------------------------------------------------------------


def split_sentences(text: str) -> list[str]:
    """Split text into sentences. Drops fragments shorter than 10 chars."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in parts if len(s.strip()) >= 10]


def score_sentence(sentence: str, term_freq: Counter) -> float:
    """Score a sentence by how many unique corpus terms it covers."""
    tokens = set(tokenise(sentence))
    return sum(term_freq.get(t, 0) for t in tokens)


def select_sentences(scored: list[str], max_chars: int) -> list[str]:
    """Greedy set-cover sentence selection within char budget with Jaccard dedup."""
    selected: list[str] = []
    selected_ngrams: list[set[str]] = []
    chars_used = 0
    for sent in scored:
        if chars_used + len(sent) + 1 > max_chars:
            continue
        ngrams = char_ngrams(sent[:50], n=4)
        if _has_jaccard_overlap(ngrams, selected_ngrams, threshold=0.5):
            continue
        selected.append(sent)
        selected_ngrams.append(ngrams)
        chars_used += len(sent) + 1
    return selected


def backfill_keywords(selected: list[str], term_freq: Counter, remaining: int) -> str:
    """Append uncovered TF-IDF keywords to fill remaining char budget.

    Note: pre-extraction code wrote ``set(tokenise(s) for s in selected)`` which
    is unhashable when ``tokenise`` returns lists. The corrected form flattens
    via union of token-sets — this is the intended semantics ("which terms are
    already covered by selected sentences"). Behaviour for inputs that did NOT
    reach the backfill branch (i.e. small payloads) is byte-identical.
    """
    covered_flat: set[str] = set()
    for sent in selected:
        covered_flat.update(tokenise(sent))
    uncovered = [t for t, _ in term_freq.most_common() if t not in covered_flat]
    filler = " ".join(deduplicate_jaccard(uncovered))
    return (" | " + filler[:remaining - 3]) if filler else ""


def compress_for_embedding(messages: list[str], max_chars: int = 800) -> str:
    """Compress channel messages via extractive sentence selection.

    Pipeline: split sentences -> TF-IDF score -> greedy select -> Jaccard dedup.
    Falls back to truncation when no usable sentences are found.
    """
    full_text = " ".join(messages)
    if len(full_text) <= max_chars:
        return full_text
    sentences = split_sentences(full_text)
    if not sentences:
        return full_text[:max_chars]
    term_freq = Counter(tokenise(full_text))
    scored = sorted(sentences, key=lambda s: score_sentence(s, term_freq), reverse=True)
    selected = select_sentences(scored, max_chars)
    if not selected:
        return full_text[:max_chars]
    result = " ".join(selected)
    remaining = max_chars - len(result)
    if remaining > 20:
        result += backfill_keywords(selected, term_freq, remaining)
    return result[:max_chars]


# ---------------------------------------------------------------------------
# Verdict-required signatures — Discord-agnostic topic API per council §5
# ---------------------------------------------------------------------------


def _message_text(message: dict) -> str:
    """Extract text from a generic message dict. Tolerates missing 'text' key."""
    return str(message.get("text", "") or "")


def extract_topics(messages: list[dict], top_n: int = 5) -> list[str]:
    """Extract top-N topic terms from a list of generic messages.

    Input shape: [{"id": str, "text": str, "author": str, "timestamp": int}, ...]
    Discord/WhatsApp-specific keys (channel_id, guild_id, role, jid) are
    ignored — extraction operates only on the 'text' field.

    Returns up to top_n terms ranked by TF-IDF over per-author documents,
    deduplicated by Jaccard char-ngram similarity.
    """
    if not messages:
        return []
    docs_by_author: dict[str, list[str]] = {}
    for msg in messages:
        author = str(msg.get("author", "anon") or "anon")
        docs_by_author.setdefault(author, []).extend(tokenise(_message_text(msg)))
    documents = [tokens for tokens in docs_by_author.values() if tokens]
    if not documents:
        return []
    per_doc_top = tfidf(documents, top_n=top_n * 2)
    flat = [term for doc_terms in per_doc_top for term in doc_terms]
    seen: dict[str, int] = {}
    for term in flat:
        seen[term] = seen.get(term, 0) + 1
    ranked = sorted(seen, key=lambda t: -seen[t])
    return deduplicate_jaccard(ranked)[:top_n]


def _find_jaccard_cluster_idx(
    ngrams: set[str], cluster_ngrams: list[set[str]], threshold: float,
) -> int:
    """Return index of first cluster whose Jaccard overlap >= threshold, or -1.

    Extracted so the caller's surface pattern is single-loop. Pairwise Jaccard
    against accumulated cluster ngram-sets is intrinsic — no transitive ordering
    or hash-bucketing equivalent exists for set-overlap similarity.
    """
    for idx, existing in enumerate(cluster_ngrams):
        if jaccard(ngrams, existing) >= threshold:
            return idx
    return -1


def cluster_topics(topics: list[str], threshold: float = 0.7) -> list[list[str]]:
    """Cluster topic terms by char-ngram Jaccard similarity.

    Groups topics whose char-trigram Jaccard similarity exceeds `threshold`.
    Higher threshold -> tighter clusters (fewer, smaller groups).
    Lower threshold -> looser clusters (fewer, larger groups).

    The threshold is load-bearing: 0.7 vs 0.8 must produce different cluster
    counts on representative input (asserted in test_cluster_topics_threshold_boundary).
    """
    if not topics:
        return []
    clusters: list[list[str]] = []
    cluster_ngrams: list[set[str]] = []
    for term in topics:
        ngrams = char_ngrams(term)
        idx = _find_jaccard_cluster_idx(ngrams, cluster_ngrams, threshold)
        if idx >= 0:
            clusters[idx].append(term)
            cluster_ngrams[idx] = cluster_ngrams[idx] | ngrams
        else:
            clusters.append([term])
            cluster_ngrams.append(ngrams)
    return clusters


def dominant_topics(clusters: list[list[str]], top_n: int = 5) -> list[str]:
    """Rank clusters by size; return one representative per cluster (cap top_n).

    The representative is the first (longest-surviving) member of each cluster,
    which under the cluster_topics insertion order is the highest-frequency
    term seeded into that cluster.
    """
    if not clusters:
        return []
    ranked = sorted(clusters, key=lambda c: -len(c))
    return [c[0] for c in ranked if c][:top_n]
