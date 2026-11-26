#!/usr/bin/env python3
"""
Hybrid History Search for /remind-yourself command.

Combines keyword search (ripgrep on JSONL) with semantic search (embeddings.py)
using Reciprocal Rank Fusion (RRF) for better recall.

Adapted from Cortex by Tomas Hjertefolger (MIT License).
https://github.com/hjertefolger/cortex
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path.home() / ".claude"


def get_project_history_dir(project_path: str = None) -> Optional[Path]:
    """Get the history directory for current or specified project."""
    if not project_path:
        project_path = os.getcwd()

    # Encode path: ~/project -> -Users-you-project
    encoded = project_path.replace("/", "-").replace(".", "-")
    if encoded.startswith("-"):
        encoded = encoded  # Keep leading dash

    history_dir = CLAUDE_DIR / "projects" / encoded
    if history_dir.exists():
        return history_dir

    # Try to find with fd
    try:
        result = subprocess.run(
            ["fd", "-t", "d", "--", encoded, str(CLAUDE_DIR / "projects")],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            return Path(result.stdout.strip().split("\n")[0])
    except Exception:
        pass

    return None


def _parse_rg_match(line: str) -> dict | None:
    """Parse one ripgrep --json match line into a result dict, or None."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if data.get("type") != "match":
        return None

    match_data = data.get("data", {})
    file_path = match_data.get("path", {}).get("text", "")
    text = match_data.get("lines", {}).get("text", "")

    try:
        entry = json.loads(text)
    except json.JSONDecodeError:
        return None

    message = entry.get("message", {})
    content = message.get("content", "") if isinstance(message, dict) else str(message)
    return {
        "session_id": entry.get("sessionId", Path(file_path).stem),
        "file_path": file_path,
        "line_number": match_data.get("line_number", 0),
        "content": content[:500],
        "timestamp": entry.get("timestamp", ""),
        "score": 1.0,
        "source": "keyword",
    }


def keyword_search(query: str, history_dir: Path, limit: int = 20) -> list[dict]:
    """Search history using ripgrep for keywords."""
    cmd = [
        "rg", "-i", "-n", "--json", query, str(history_dir),
        "--glob", "*.jsonl", "--glob", "!agent-*",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return []
    except Exception as e:
        print(f"Keyword search error: {e}", file=sys.stderr)
        return []

    results = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parsed = _parse_rg_match(line)
        if parsed is not None:
            results.append(parsed)
    return results[:limit]


def _ensure_own_lib() -> None:
    """Put this module's own lib folder on sys.path once, never the shared tree's (GRIP#9857)."""
    own_lib = str(Path(__file__).resolve().parent)
    if own_lib not in sys.path:
        sys.path.insert(0, own_lib)


def semantic_search(query: str, limit: int = 20) -> list[dict]:
    """Search using embeddings.py semantic search."""
    results = []

    try:
        # Import embeddings module
        _ensure_own_lib()
        from embeddings import get_engine

        engine = get_engine()

        # Use hybrid search from embeddings.py
        search_results = engine.search_hybrid(
            query_text=query,
            limit=limit,
            vector_weight=0.6,
            keyword_weight=0.4
        )

        for result in search_results:
            if not isinstance(result, dict):
                continue
            # The live engine returns rows whose ``metadata`` key is present with
            # the value None (measured 2 of 10 rows on 2026-09-22), so a default
            # on ``.get("metadata", {})`` never applies. Normalise per row and fall
            # back to the top-level ``session_id`` / ``created_at`` the rows carry.
            meta = result.get("metadata")
            if not isinstance(meta, dict):
                meta = {}
            content = result.get("content") or ""
            results.append({
                "session_id": meta.get("session_id") or result.get("session_id") or "",
                "content": content[:500],
                "timestamp": meta.get("timestamp") or result.get("created_at") or "",
                "score": result.get("score", result.get("final_score", 0.0)),
                "source": "semantic"
            })

    except ImportError:
        # Embeddings not available, fall back to keyword only
        pass
    except Exception as e:
        # Say plainly that the semantic half is not a complete result: a reader
        # of the merged output must not mistake "keyword-only" for "hybrid".
        print(
            f"Semantic search UNAVAILABLE or PARTIAL ({type(e).__name__}: {e}); "
            f"returning {len(results)} semantic result(s), the keyword results stand alone",
            file=sys.stderr,
        )

    return results[:limit]


def hybrid_search(
    query: str,
    project_path: str = None,
    limit: int = 20,
    keyword_weight: float = 0.4,
    semantic_weight: float = 0.6,
    recency_weight: float = 0.3
) -> list[dict]:
    """
    Perform hybrid search combining keyword and semantic results.

    Uses Reciprocal Rank Fusion (RRF) to combine rankings.
    """
    from collections import defaultdict
    import math

    RRF_K = 60  # Standard RRF constant

    # Get results from both sources
    history_dir = get_project_history_dir(project_path)
    keyword_results = keyword_search(query, history_dir, limit * 2) if history_dir else []
    semantic_results = semantic_search(query, limit * 2)

    # RRF fusion
    scores = defaultdict(float)
    all_results = {}

    # Score keyword results
    for rank, result in enumerate(keyword_results):
        key = f"{result['session_id']}:{result.get('line_number', 0)}"
        scores[key] += keyword_weight / (RRF_K + rank + 1)
        all_results[key] = result

    # Score semantic results
    for rank, result in enumerate(semantic_results):
        key = f"{result['session_id']}:{hash(result['content'][:100])}"
        scores[key] += semantic_weight / (RRF_K + rank + 1)
        if key not in all_results:
            all_results[key] = result

    # Apply recency decay
    def parse_timestamp(ts: str) -> datetime:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now()

    HALF_LIFE_DAYS = 7
    now = datetime.now()

    final_scores = []
    for key, content_score in scores.items():
        result = all_results[key]
        ts = parse_timestamp(result.get("timestamp", ""))
        age_days = (now - ts.replace(tzinfo=None)).total_seconds() / 86400
        decay = math.pow(0.5, age_days / HALF_LIFE_DAYS)

        final_score = content_score * (1 - recency_weight) + content_score * recency_weight * decay
        result["final_score"] = final_score
        final_scores.append(result)

    # Sort by final score and return top results
    final_scores.sort(key=lambda x: -x.get("final_score", 0))
    return final_scores[:limit]


def main():
    """CLI interface for hybrid history search."""
    import argparse

    parser = argparse.ArgumentParser(description="Hybrid history search")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--limit", "-n", type=int, default=10, help="Max results")
    parser.add_argument("--project", "-p", help="Project path (default: cwd)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    results = hybrid_search(
        query=args.query,
        project_path=args.project,
        limit=args.limit
    )

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(f"\n=== Hybrid Search Results for: {args.query} ===\n")
        for i, result in enumerate(results, 1):
            print(f"{i}. [{result.get('source', 'unknown')}] Score: {result.get('final_score', 0):.4f}")
            print(f"   Session: {result.get('session_id', 'unknown')}")
            print(f"   Time: {result.get('timestamp', 'unknown')}")
            print(f"   Content: {result.get('content', '')[:200]}...")
            print()


if __name__ == "__main__":
    main()
