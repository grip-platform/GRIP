#!/usr/bin/env python3
"""Session Pattern Matcher -- find historically similar sessions.

Scans converge-metrics.jsonl to build session profiles, then computes
multi-dimensional similarity to recommend approaches for new tasks.

CLI:
    python3 -m lib.session_matcher match "fix auth middleware"
    python3 -m lib.session_matcher similar abc123
    python3 -m lib.session_matcher index
    python3 -m lib.session_matcher recommend "fix auth middleware"
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SessionProfile:
    session_id: str
    description: str
    task_types: List[str] = field(default_factory=list)
    tool_distribution: Dict[str, float] = field(default_factory=dict)
    outcome: str = "unknown"  # "satisfied" | "not" | "unknown"
    score: float = 0.0
    duration_minutes: float = 0.0
    genes_used: List[str] = field(default_factory=list)


@dataclass
class SimilarityResult:
    session_a: str
    session_b: str
    similarity_score: float
    match_reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Similarity primitives
# ---------------------------------------------------------------------------

def jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    """Cosine similarity between two sparse vectors (dicts)."""
    all_keys = set(a) | set(b)
    if not all_keys:
        return 1.0
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in all_keys)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def duration_similarity(a: float, b: float) -> float:
    """1 - abs(diff) / max, clamped to [0, 1]."""
    if a == 0.0 and b == 0.0:
        return 1.0
    max_val = max(abs(a), abs(b))
    if max_val == 0.0:
        return 1.0
    return max(0.0, 1.0 - abs(a - b) / max_val)


def tokenize(text: str) -> set:
    """Lowercase word tokens from a string, stripping punctuation."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

WEIGHTS = {
    "description": 0.30,
    "task_type": 0.20,
    "tool_distribution": 0.20,
    "gene_overlap": 0.15,
    "duration": 0.15,
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def build_profile(session_data: dict) -> SessionProfile:
    """Build a SessionProfile from a merged session record.

    Expects a dict with keys from converge-metrics start/end events:
      run_id, task, task_type, satisfied, final_score,
      elapsed_minutes, expressed_genes, invoked_genes,
      tool_distribution (optional).
    """
    genes = list(set(
        session_data.get("expressed_genes", [])
        + session_data.get("invoked_genes", [])
    ))

    task_type = session_data.get("task_type", "")
    task_types = [task_type] if task_type else []

    satisfied = session_data.get("satisfied")
    if satisfied is True:
        outcome = "satisfied"
    elif satisfied is False:
        outcome = "not"
    else:
        outcome = "unknown"

    return SessionProfile(
        session_id=session_data.get("run_id", ""),
        description=session_data.get("task", ""),
        task_types=task_types,
        tool_distribution=session_data.get("tool_distribution", {}),
        outcome=outcome,
        score=float(session_data.get("final_score", 0.0)),
        duration_minutes=float(session_data.get("elapsed_minutes", 0.0)),
        genes_used=genes,
    )


def compute_similarity(profile_a: SessionProfile, profile_b: SessionProfile) -> SimilarityResult:
    """Multi-dimensional similarity between two session profiles."""
    reasons: List[str] = []

    # 1. Description similarity (Jaccard on tokens)
    tokens_a = tokenize(profile_a.description)
    tokens_b = tokenize(profile_b.description)
    desc_sim = jaccard(tokens_a, tokens_b)
    if desc_sim > 0.3:
        overlap = tokens_a & tokens_b
        reasons.append(f"description overlap ({', '.join(sorted(overlap)[:5])})")

    # 2. Task type overlap
    types_a = set(profile_a.task_types)
    types_b = set(profile_b.task_types)
    type_sim = jaccard(types_a, types_b)
    if type_sim > 0.0 and types_a and types_b:
        reasons.append(f"task type match ({', '.join(types_a & types_b)})")

    # 3. Tool distribution (cosine)
    tool_sim = cosine_similarity(profile_a.tool_distribution, profile_b.tool_distribution)
    if tool_sim > 0.5 and profile_a.tool_distribution and profile_b.tool_distribution:
        reasons.append("similar tool usage")

    # 4. Gene overlap (Jaccard)
    genes_a = set(profile_a.genes_used)
    genes_b = set(profile_b.genes_used)
    gene_sim = jaccard(genes_a, genes_b)
    if gene_sim > 0.3 and genes_a and genes_b:
        overlap = genes_a & genes_b
        reasons.append(f"gene overlap ({', '.join(sorted(overlap)[:5])})")

    # 5. Duration similarity
    dur_sim = duration_similarity(profile_a.duration_minutes, profile_b.duration_minutes)
    if dur_sim > 0.7:
        reasons.append("similar duration")

    total = (
        WEIGHTS["description"] * desc_sim
        + WEIGHTS["task_type"] * type_sim
        + WEIGHTS["tool_distribution"] * tool_sim
        + WEIGHTS["gene_overlap"] * gene_sim
        + WEIGHTS["duration"] * dur_sim
    )

    return SimilarityResult(
        session_a=profile_a.session_id,
        session_b=profile_b.session_id,
        similarity_score=round(total, 4),
        match_reasons=reasons,
    )


def find_similar(
    target_description: str,
    profiles: List[SessionProfile],
    top_k: int = 5,
) -> List[SimilarityResult]:
    """Find the most similar sessions to a description string.

    Creates a minimal profile from the description and ranks all
    profiles by similarity.
    """
    target = SessionProfile(session_id="__query__", description=target_description)
    results = [compute_similarity(target, p) for p in profiles]
    results.sort(key=lambda r: r.similarity_score, reverse=True)
    return results[:top_k]


def find_similar_to_session(
    session_id: str,
    profiles: List[SessionProfile],
    top_k: int = 5,
) -> List[SimilarityResult]:
    """Find sessions most similar to a given session ID."""
    target = None
    for p in profiles:
        if p.session_id == session_id:
            target = p
            break
    if target is None:
        return []

    results = []
    for p in profiles:
        if p.session_id == session_id:
            continue
        results.append(compute_similarity(target, p))
    results.sort(key=lambda r: r.similarity_score, reverse=True)
    return results[:top_k]


def build_index(data_dir: str) -> List[SessionProfile]:
    """Scan converge-metrics.jsonl and build session profiles.

    Merges converge_start and converge_end events by run_id.
    """
    metrics_path = os.path.join(data_dir, "converge-metrics.jsonl")
    if not os.path.isfile(metrics_path):
        return []

    # Accumulate events per run_id
    runs: Dict[str, dict] = {}
    with open(metrics_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            run_id = event.get("run_id")
            if not run_id:
                continue
            if run_id not in runs:
                runs[run_id] = {}
            # Merge fields (later events overwrite earlier for same key)
            runs[run_id].update(event)

    profiles = []
    for run_id, data in runs.items():
        # Only include sessions that have both a task description and end data
        if "task" not in data:
            continue
        data["run_id"] = run_id
        profiles.append(build_profile(data))
    return profiles


def recommend_approach(
    target_description: str,
    profiles: List[SessionProfile],
    top_k: int = 5,
) -> dict:
    """Suggest an approach based on similar session outcomes.

    Returns a dict with:
      - similar_sessions: top-K SimilarityResult list
      - recommendation: text summary
      - avg_score: mean score of successful similar sessions
      - avg_duration: mean duration of similar sessions
      - common_genes: genes that appear in >50% of similar sessions
      - success_rate: fraction of similar sessions that were satisfied
    """
    similar = find_similar(target_description, profiles, top_k=top_k)
    if not similar:
        return {
            "similar_sessions": [],
            "recommendation": "No similar sessions found. Start fresh.",
            "avg_score": 0.0,
            "avg_duration": 0.0,
            "common_genes": [],
            "success_rate": 0.0,
        }

    # Gather matched profiles
    id_to_profile = {p.session_id: p for p in profiles}
    matched = [id_to_profile[s.session_b] for s in similar if s.session_b in id_to_profile]

    if not matched:
        return {
            "similar_sessions": similar,
            "recommendation": "No profile data for matched sessions.",
            "avg_score": 0.0,
            "avg_duration": 0.0,
            "common_genes": [],
            "success_rate": 0.0,
        }

    # Stats
    scores = [p.score for p in matched if p.score > 0]
    durations = [p.duration_minutes for p in matched if p.duration_minutes > 0]
    satisfied_count = sum(1 for p in matched if p.outcome == "satisfied")
    success_rate = satisfied_count / len(matched) if matched else 0.0

    # Common genes (appear in >50% of matched sessions)
    gene_counts: Dict[str, int] = {}
    for p in matched:
        for g in p.genes_used:
            gene_counts[g] = gene_counts.get(g, 0) + 1
    threshold = len(matched) / 2.0
    common_genes = sorted(g for g, c in gene_counts.items() if c > threshold)

    avg_score = sum(scores) / len(scores) if scores else 0.0
    avg_duration = sum(durations) / len(durations) if durations else 0.0

    # Build recommendation text
    parts = []
    if success_rate >= 0.8:
        parts.append(f"High success rate ({success_rate:.0%}) in similar sessions.")
    elif success_rate >= 0.5:
        parts.append(f"Moderate success rate ({success_rate:.0%}) in similar sessions.")
    else:
        parts.append(f"Low success rate ({success_rate:.0%}) in similar sessions -- proceed carefully.")

    if avg_duration > 0:
        parts.append(f"Expect ~{avg_duration:.0f} minutes.")
    if common_genes:
        parts.append(f"Common genes: {', '.join(common_genes)}.")

    # Top match context
    best = similar[0]
    best_profile = id_to_profile.get(best.session_b)
    if best_profile:
        parts.append(
            f"Most similar: \"{best_profile.description}\" "
            f"(score {best.similarity_score:.2f}, outcome: {best_profile.outcome})."
        )

    return {
        "similar_sessions": similar,
        "recommendation": " ".join(parts),
        "avg_score": round(avg_score, 1),
        "avg_duration": round(avg_duration, 1),
        "common_genes": common_genes,
        "success_rate": round(success_rate, 2),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_data_dir() -> str:
    return os.environ.get("GRIP_LEARNING_DIR", os.path.join(
        os.environ.get("HOME", ""), ".claude", "learning"
    ))


def _format_results(results: List[SimilarityResult]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        reasons = ", ".join(r.match_reasons) if r.match_reasons else "weak match"
        lines.append(f"  {i}. {r.session_b}  score={r.similarity_score:.4f}  [{reasons}]")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="session_matcher",
        description="Find historically similar sessions",
    )
    parser.add_argument(
        "--data-dir", default=_default_data_dir(),
        help="Directory containing converge-metrics.jsonl",
    )
    sub = parser.add_subparsers(dest="command")

    # match
    p_match = sub.add_parser("match", help="Find sessions matching a description")
    p_match.add_argument("description", help="Task description to match")
    p_match.add_argument("--top-k", type=int, default=5, help="Number of results")

    # similar
    p_similar = sub.add_parser("similar", help="Find sessions similar to a given run_id")
    p_similar.add_argument("run_id", help="Run ID to match against")
    p_similar.add_argument("--top-k", type=int, default=5, help="Number of results")

    # index
    sub.add_parser("index", help="Rebuild index and show stats")

    # recommend
    p_rec = sub.add_parser("recommend", help="Recommend approach for a task")
    p_rec.add_argument("description", help="Task description")
    p_rec.add_argument("--top-k", type=int, default=5, help="Number of similar sessions to consider")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    profiles = build_index(args.data_dir)

    if args.command == "index":
        satisfied = sum(1 for p in profiles if p.outcome == "satisfied")
        print(f"Indexed {len(profiles)} sessions ({satisfied} satisfied)")
        for p in profiles[:10]:
            print(f"  {p.session_id}: {p.description[:60]}  [{p.outcome}]")
        if len(profiles) > 10:
            print(f"  ... and {len(profiles) - 10} more")
        return 0

    if args.command == "match":
        results = find_similar(args.description, profiles, top_k=args.top_k)
        if not results:
            print("No sessions found.")
            return 0
        print(f"Top {len(results)} matches for \"{args.description}\":")
        print(_format_results(results))
        return 0

    if args.command == "similar":
        results = find_similar_to_session(args.run_id, profiles, top_k=args.top_k)
        if not results:
            print(f"No matches (run_id '{args.run_id}' not found or no other sessions).")
            return 0
        print(f"Top {len(results)} similar to {args.run_id}:")
        print(_format_results(results))
        return 0

    if args.command == "recommend":
        rec = recommend_approach(args.description, profiles, top_k=args.top_k)
        print(f"Recommendation for \"{args.description}\":")
        print(f"  {rec['recommendation']}")
        print(f"  Success rate: {rec['success_rate']:.0%}")
        print(f"  Avg score: {rec['avg_score']}")
        print(f"  Avg duration: {rec['avg_duration']} min")
        if rec["common_genes"]:
            print(f"  Common genes: {', '.join(rec['common_genes'])}")
        if rec["similar_sessions"]:
            print(f"\nSimilar sessions:")
            print(_format_results(rec["similar_sessions"]))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
