#!/usr/bin/env python3
"""
Embedding CLI - Command-line interface for embedding operations.
Part of GRIP v6.0 SRP refactoring.

Preserves full backward compatibility with original embeddings.py CLI.
"""

import argparse
import json
import os
from .engine import EmbeddingEngine, VECTOR_DIM


def _add_text_sub(sub, name, help_text, extra=None):
    """Add subparser with required 'text' arg."""
    p = sub.add_parser(name, help=help_text)
    p.add_argument("text", help="Text input")
    for a, kw in (extra or []):
        p.add_argument(*a, **kw)


def _build_parser():
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Semantic RL++ Embedding Engine")
    sub = parser.add_subparsers(dest="command", help="Commands")
    sub.add_parser("init", help="Initialise database schema")
    sub.add_parser("stats", help="Show database statistics")

    _add_text_sub(sub, "embed", "Generate embedding", [
        (["--type"], {"default": "prompt"}), (["--project"], {}),
        (["--session"], {}), (["--store"], {"action": "store_true"})])
    _add_text_sub(sub, "search", "Search embeddings", [
        (["--type"], {}), (["--limit"], {"type": int, "default": 5}),
        (["--mode"], {"choices": ["hybrid", "vector", "keyword"], "default": "hybrid"}),
        (["--exclude-project"], {}), (["--cross-project"], {"action": "store_true"})])
    _add_text_sub(sub, "novelty", "Calculate novelty score")
    _add_text_sub(sub, "queue", "Queue for embedding", [
        (["--type"], {"default": "prompt"}),
        (["--priority"], {"choices": ["high", "medium", "low"]}), (["--project"], {})])

    p = sub.add_parser("process", help="Process queue")
    p.add_argument("--priority", choices=["high", "medium", "low"])
    p = sub.add_parser("prune", help="Prune old embeddings")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--min-success", type=float, default=0.3)

    _add_text_sub(sub, "checkpoint", "Detect checkpoint", [
        (["--threshold"], {"type": float, "default": 0.7})])
    _add_text_sub(sub, "classify", "Full classification")
    _add_text_sub(sub, "intent", "Classify intent")
    _add_text_sub(sub, "feedback", "Classify feedback")
    _add_text_sub(sub, "match", "Match agent/skill/command", [
        (["--type"], {"choices": ["agent", "skill", "command"], "default": "agent"})])
    return parser


def _dispatch_search(engine, args):
    """Handle search command with mode routing."""
    exclude = args.exclude_project
    if args.cross_project and not exclude:
        exclude = os.getcwd()
    if args.mode == "hybrid":
        results = engine._search_hybrid(query_text=args.text, embed_type=args.type, limit=args.limit)
        return {"status": "ok", "mode": args.mode, "results": engine._apply_recency_decay(results, half_life_days=7.0)}
    if args.mode == "keyword":
        from .search import _keyword_search_fts5
        results = _keyword_search_fts5(engine, query=args.text, embed_type=args.type, limit=args.limit)
        return {"status": "ok", "mode": args.mode, "results": results}
    results = engine._search_similar(query_text=args.text, embed_type=args.type, limit=args.limit, exclude_project=exclude)
    return {"status": "ok", "mode": args.mode, "results": results}


def _dispatch(engine, args):
    """Route CLI command to engine method."""
    cmd = args.command
    engine.init_schema()
    if cmd == "init":
        return {"status": "ok", "message": "Schema initialised"}
    if cmd == "embed":
        if args.store:
            return {"status": "ok", "id": engine._store_embedding(
                text=args.text, embed_type=args.type, project_path=args.project, session_id=args.session)}
        v = engine.embed_text(args.text)
        return {"status": "ok", "dimensions": VECTOR_DIM, "bytes": len(v)}
    if cmd == "search":
        return _dispatch_search(engine, args)
    if cmd == "novelty":
        score, meta = engine._calculate_novelty(args.text)
        return {"status": "ok", "novelty_score": round(score, 4), "priority": engine._get_priority(score), "coherence": meta}
    if cmd == "queue":
        engine._queue_for_embedding(text=args.text, embed_type=args.type, priority=args.priority, project_path=args.project)
        return {"status": "ok", "message": "Queued for embedding"}
    if cmd == "process":
        return {"status": "ok", "processed": engine._process_queue(priority_filter=args.priority)}
    if cmd == "stats":
        return {"status": "ok", **engine._get_stats()}
    if cmd == "prune":
        engine._prune_old_embeddings(days=args.days, min_success=args.min_success)
        return {"status": "ok", "message": "Pruning complete"}
    if cmd == "checkpoint":
        cp = engine._detect_checkpoint(args.text, threshold=args.threshold)
        return {"status": "ok", "triggered": bool(cp), "checkpoint": cp}
    if cmd == "classify":
        return {"status": "ok", **engine._get_full_classification(args.text)}
    if cmd == "intent":
        return {"status": "ok", "intents": engine._classify_intent(args.text)}
    if cmd == "feedback":
        return {"status": "ok", "feedbacks": engine._classify_feedback(args.text)}
    if cmd == "match":
        fn = {"agent": engine._match_agent, "skill": engine._match_skill, "command": engine._match_command}[args.type]
        return {"status": "ok", "matches": fn(args.text)}
    return None


def main():
    parser = _build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    engine = EmbeddingEngine()
    try:
        result = _dispatch(engine, args)
        if result:
            print(json.dumps(result, indent=2))
    finally:
        engine.close()


if __name__ == "__main__":
    main()
