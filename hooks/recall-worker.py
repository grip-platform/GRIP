#!/usr/bin/env python3
"""
Recall Worker - Subprocess-isolated KONO recall.

Runs in a child process so that lembed SIGSEGV crashes are contained.
The parent recall-hook.py survives and exits cleanly -- no macOS crash dialog.

Input: JSON on stdin with {message, limit, min_fitness, cache_file,
session_id, shadow}.

Output: ONE JSON object on stdout, always -- including for a recall that found
nothing, because a zero-result prompt is the most informative row the shadow
log can hold. The envelope separates the two concerns deliberately:

    text     the rendered [KONO RECALL] block, the only field that carries
             memory content, and empty in shadow mode so that content cannot
             cross the process boundary at all
    results  content-free per-result numbers for the shadow log

Shadow mode is read-only. Live mode dedups against the session cache, attests,
and writes the cache back, exactly as before.
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

# Ensure venv packages are available
VENV_SITE = Path.home() / ".claude" / "venv" / "lib"
if VENV_SITE.exists():
    for sp in sorted(VENV_SITE.glob("python*/site-packages")):
        if str(sp) not in sys.path:
            sys.path.insert(0, str(sp))

sys.path.insert(0, str(Path.home() / ".claude" / "lib"))


def load_cache(cache_file: str) -> dict:
    """Load session-scoped deduplication cache."""
    try:
        with open(cache_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {"session_id": "", "recalled_ids": [], "last_updated": ""}


def save_cache(cache: dict, cache_file: str) -> None:
    """Save deduplication cache."""
    try:
        Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(cache, f)
    except OSError:
        pass


def result_metrics(results: list) -> list:
    """Content-free per-result numbers.

    Memory content is deliberately absent: the shadow log is built from this
    list, so no code path can leak recalled text into it.

    ``distance`` is passed through as the substrate supplied it, which on this
    call is normally absent. The hook queries with expand_spatial=False, so
    recall() takes the hybrid-search path (kono_substrate._enrich_single_hit),
    which sets ``score`` but no ``distance``; only the spatial retrieve() path
    sets that key. Reporting None is the honest answer -- inventing a distance
    from the position tuple would be a number nobody measured.
    """
    return [
        {
            "id": r.get("id"),
            "dimension_name": r.get("dimension_name", "unknown"),
            "fitness": r.get("fitness"),
            "distance": r.get("distance"),
            "score": r.get("score"),
            "is_permanent": bool(r.get("is_permanent")),
        }
        for r in results
    ]


def emit(payload: dict) -> None:
    """Write the single stdout envelope the hook parses."""
    print(json.dumps(payload, ensure_ascii=False))


def _make_read_only(substrate) -> None:
    """Neutralise the write that recall() performs on its own results.

    recall() -> _finalize_results() -> attest_recalled() runs an UPDATE on
    every row it returns and ensures an instance exists. A shadow observation
    that attests would raise the fitness of the very rows it is measuring, so
    the denominator would be reading a substrate its own reads had altered.
    Bound on this instance only; nothing in lib/ changes.
    """
    substrate.attest_recalled = lambda *_args, **_kwargs: False


def dedup_against_session(results: list, params: dict) -> tuple[list, dict]:
    """Drop results already recalled this session; return survivors and cache."""
    cache = load_cache(params["cache_file"])
    session_id = params.get("session_id", "unknown")
    if cache.get("session_id") != session_id:
        cache = {"session_id": session_id, "recalled_ids": [], "last_updated": ""}
    cached_ids = set(cache.get("recalled_ids", []))
    return [r for r in results if r["id"] not in cached_ids], cache


def format_results(results: list) -> tuple[list[str], list]:
    """Format recall results into output lines and collected IDs."""
    lines = ["[KONO RECALL]"]
    recalled_ids = []
    for r in results:
        dim_name = r.get("dimension_name", "unknown")
        fitness = r.get("fitness", 0)
        perm = ", permanent" if r.get("is_permanent") else ""
        content = r.get("content", "").strip()
        if len(content) > 200:
            content = content[:200] + "..."
        lines.append(f"- ({dim_name}, {fitness:.2f}{perm}) {content}")
        recalled_ids.append(r["id"])
    return lines, recalled_ids


def _shadow_pass(results: list) -> dict:
    """Observe only: no dedup, no attestation, no cache write, no text."""
    return {
        "shadow": True,
        "deduped": False,
        "n_raw": len(results),
        "text": "",
        "results": result_metrics(results),
    }


def _live_pass(substrate, results: list, params: dict) -> dict:
    """Dedup, render the injectable block, attest, and persist the cache."""
    new_results, cache = dedup_against_session(results, params)
    payload = {
        "shadow": False,
        "deduped": True,
        "n_raw": len(results),
        "text": "",
        "results": result_metrics(new_results),
    }
    if not new_results:
        return payload

    lines, recalled_ids = format_results(new_results)
    payload["text"] = "\n".join(lines)

    # Attest recalled memories (closes feedback loop)
    for rid in recalled_ids:
        try:
            substrate.attest_recalled(rid)
        except (AttributeError, Exception):
            pass

    cache["recalled_ids"].extend(recalled_ids)
    cache["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_cache(cache, params["cache_file"])
    return payload


def recall_and_output(params: dict) -> None:
    """Execute recall against the KONO substrate and emit one JSON envelope.

    ``shadow`` defaults to True: a caller that forgets the flag observes rather
    than injects, so the failure mode of a stale caller is a missing log line,
    never an unreviewed memory in someone's context.
    """
    from kono_substrate import MemorySubstrate

    shadow = bool(params.get("shadow", True))
    substrate = MemorySubstrate()
    try:
        if shadow:
            _make_read_only(substrate)
        results = substrate.recall(
            query=params["message"],
            limit=params.get("limit", 3),
            min_fitness=params.get("min_fitness", 0.1),
            expand_spatial=False,
        )
        if shadow:
            emit(_shadow_pass(results))
        else:
            emit(_live_pass(substrate, results, params))
    finally:
        substrate.close()


if __name__ == "__main__":
    recall_and_output(json.load(sys.stdin))
