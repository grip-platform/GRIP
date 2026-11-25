#!/usr/bin/env python3
"""
cube_session_wire.py — Wire design principle signals into the 4D CUBE.

Runs at session end (or on demand) to:
  1. Scan the current codebase for design principle adherence
  2. Record each principle as an observation in the CUBE
  3. Persist CUBE state to cache/cube-state.json

Usage:
    python3 lib/cube_session_wire.py [path]
    python3 lib/cube_session_wire.py --report   # Print CUBE state only

Integrates with hooks/session-end.sh for automatic RSI session tracking.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CUBE_CACHE = _ROOT / "cache" / "cube-state.json"

# CLAUDE_DIR is the GRIP root. The canonical install symlinks ~/.claude to the
# repo root; hooks pass an explicit claude_dir for path-portability. The module
# default honours $CLAUDE_DIR when set (rules/path-portability.md).
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR") or _ROOT)

logger = logging.getLogger(__name__)

# File extensions that trigger a full scan vs lightweight skip
_CODE_EXTENSIONS = {".py", ".sh", ".js"}
_DOC_EXTENSIONS = {".md", ".txt", ".rst", ".yaml", ".yml", ".json"}


# =========================================================================
# Per-Project Concept Dictionary (#218)
# =========================================================================

def _project_slug(path: str = ".") -> str:
    """Derive a filesystem-safe project slug from the working directory."""
    resolved = Path(path).resolve()
    return resolved.name.lower().replace(" ", "-")


def _load_concept_dict(path: str = ".") -> dict[str, str]:
    """Load project-specific concept→domain mapping.

    Looks for cache/concepts-{project}.json.  Returns empty dict if not found.
    Format: {"react-hooks": "DRY", "error-boundaries": "SOLID", ...}
    """
    slug = _project_slug(path)
    concept_file = _ROOT / "cache" / f"concepts-{slug}.json"
    if not concept_file.exists():
        return {}
    try:
        data = json.loads(concept_file.read_text())
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_concept_dict(path: str, concepts: dict[str, str]) -> Path:
    """Write a project concept dictionary to cache.

    Args:
        path: Project working directory (used to derive slug).
        concepts: Mapping of project terms to CUBE domains.

    Returns:
        Path to the written concept file.
    """
    slug = _project_slug(path)
    concept_file = _ROOT / "cache" / f"concepts-{slug}.json"
    concept_file.parent.mkdir(exist_ok=True)
    concept_file.write_text(json.dumps(concepts, indent=2))
    return concept_file


def _deserialize_cell_list(cell_list: list[dict]) -> list:
    """Convert serialised cell dicts to CubeCell objects."""
    from lib.cube import CubeCell
    return [CubeCell(confidence=cd["confidence"], session_id=cd["session_id"],
                     convergence_depth=cd.get("convergence_depth", 1),
                     timestamp=cd.get("timestamp", 0.0))
            for cd in cell_list]


def _restore_cells(cube, cells_data: dict) -> None:
    """Restore cell history from serialised data into cube."""
    for key, cell_list in cells_data.items():
        parts = key.split(":", 1)
        if len(parts) != 2:
            continue
        cube._cells[(parts[0], int(parts[1]))].extend(_deserialize_cell_list(cell_list))


def _load_cube():
    """Load CUBE from cache or build a fresh design CUBE."""
    from lib.cube import Cube

    if CUBE_CACHE.exists():
        try:
            data = json.loads(CUBE_CACHE.read_text())
            cube = Cube()
            for domain, level in data.get("levels", {}).items():
                cube._levels[domain] = level
            w_data = data.get("convergence_depth") or data.get("consciousness", {})
            for domain, convergence_depth in w_data.items():
                cube._convergence_depth[domain] = convergence_depth
            _restore_cells(cube, data.get("cells", {}))
            return cube
        except Exception:
            pass  # Corrupt cache — start fresh

    from lib.cube import build_design_cube
    return build_design_cube()


def _serialize_cell_list(cell_list) -> list[dict]:
    """Serialise a single cell list."""
    return [{"confidence": c.confidence, "session_id": c.session_id,
             "convergence_depth": c.convergence_depth, "timestamp": c.timestamp}
            for c in cell_list]


def _serialize_cells(cube) -> dict:
    """Serialise cube cell history to a dict."""
    return {f"{d}:{l}": _serialize_cell_list(cells) for (d, l), cells in cube._cells.items()}


def _state_record(cube, last_scan_commit: str | None) -> dict:
    """The cache record for ``cube``, in the one shape ``_load_cube`` reads back."""
    state = cube.snapshot()
    return {
        "levels": cube._levels,
        "convergence_depth": cube._convergence_depth,
        "mesh_fraction": state.mesh_fraction,
        "embodied_count": sum(
            1 for w in cube._convergence_depth.values() if w >= 4
        ),
        "last_scan_commit": last_scan_commit,
        "cells": _serialize_cells(cube),
    }


def _save_cube(cube, report=None) -> None:
    """Persist CUBE state to cache/cube-state.json."""
    CUBE_CACHE.parent.mkdir(exist_ok=True)
    data = _state_record(cube, _get_current_commit())
    if report is not None:
        data["composite_score"] = report.composite_score
        data["violations"] = report.violations[:5]
    CUBE_CACHE.write_text(json.dumps(data, indent=2))


def _holds_a_record(path: Path) -> bool:
    """True when ``path`` holds a JSON object, which is someone's cube record."""
    try:
        return isinstance(json.loads(path.read_text()), dict)
    except (OSError, ValueError):
        return False


def seed_cache(path: Path | None = None) -> dict | None:
    """Write a fresh design cube to ``path`` unless a cube record is already there.

    Returns the record it wrote, or None when it left an existing record alone.
    A file that holds no JSON object is replaced: installers before GRIP#9370
    wrote a CubeState's repr there as a JSON string, which ``_check_fast_path``
    could not read. ``last_scan_commit`` stays None because no scan has run, so
    the first real scan is not mistaken for a cache hit.
    """
    from lib.cube import build_design_cube

    path = path or CUBE_CACHE
    if _holds_a_record(path):
        return None
    record = _state_record(build_design_cube(), None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2))
    return record


# =========================================================================
# Fast-Path Cache — skip full scan when no code files changed (#222)
# =========================================================================

def _get_current_commit() -> str | None:
    """Get HEAD commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(_ROOT),
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _get_changed_files(since_commit: str) -> list[str] | None:
    """Get files changed since a commit. Returns None if git unavailable."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", since_commit, "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(_ROOT),
        )
        if result.returncode != 0:
            return None
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except Exception:
        return None


def _cached_result(cube, data: dict, hit_type: str) -> dict:
    """Build a cache-hit result dict."""
    state = cube.snapshot()
    return {
        "session_id": "cached",
        "composite_score": data.get("composite_score", 0.0),
        "mesh_fraction": state.mesh_fraction,
        "cube_report": cube.report(),
        "violations": data.get("violations", []),
        "cache_hit": hit_type,
    }


def _check_fast_path(cube) -> dict | None:
    """Return cached result if no code files changed since last scan."""
    if not CUBE_CACHE.exists():
        return None
    try:
        data = json.loads(CUBE_CACHE.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None  # not a cube record, e.g. the pre-GRIP#9370 installer's string

    last_commit = data.get("last_scan_commit")
    if not last_commit:
        return None

    current_commit = _get_current_commit()
    if current_commit is None:
        return None

    if current_commit == last_commit:
        return _cached_result(cube, data, "no_new_commits")

    changed = _get_changed_files(last_commit)
    if changed is None:
        return None

    if any(Path(f).suffix in _CODE_EXTENSIONS for f in changed):
        return None

    return _cached_result(cube, data, "docs_only" if changed else "no_changes")


def _auto_record_strategies(path: str, report, previous_data: dict | None) -> None:
    """Auto-record convergence strategies when improvement is detected.

    Compares current scan report against cached previous scores.
    If any domain improved by >= 0.1, records as inheritable strategy.
    """
    if previous_data is None:
        return

    prev_composite = previous_data.get("composite_score", 0)
    curr_composite = report.composite_score

    # Only record if there was meaningful improvement
    if curr_composite - prev_composite < 0.05:
        return

    try:
        from lib.converge_inherit import record_success
    except ImportError:
        return

    slug = _project_slug(path)
    score_map = {
        "DRY": report.dry_score,
        "KISS": report.kiss_score,
        "SOLID": report.solid_score,
        "GRASP": report.grasp_score,
        "YAGNI": report.yagni_score,
        "YSH": report.ysh_score,
    }

    for domain, current_score in score_map.items():
        # We don't have per-domain previous scores in the cache,
        # so record composite improvement attributed to each domain
        if current_score >= 0.7:
            try:
                record_success(
                    project=slug,
                    domain=domain,
                    strategy=f"convergence_cycle_{slug}",
                    score_before=prev_composite,
                    score_after=curr_composite,
                    description=f"Auto-recorded during principles scan of {slug}",
                    tags=[slug, "auto"],
                )
            except Exception:
                pass  # Non-critical — don't break scan on strategy recording failure


def _score_to_depth(confidence: float, passed: bool):
    """Map score + pass/fail to convergence depth."""
    from lib.cube import ConvergenceDepth
    if passed:
        return ConvergenceDepth.CODIFIED
    return ConvergenceDepth.UNDERSTOOD if confidence > 0.5 else ConvergenceDepth.DETECTED


def _record_signals(cube, signals, score_map: dict, session_id: str) -> None:
    """Record principle signals in the CUBE."""
    for sig in signals:
        confidence = score_map.get(sig.name, 0.5)
        cube.record(sig.name, session_id, confidence=confidence,
                    convergence_depth=_score_to_depth(confidence, sig.check(None)))


def _apply_concept_dict(cube, path: str, score_map: dict, session_id: str) -> list[str]:
    """Apply per-project concept dictionary mappings."""
    concept_dict = _load_concept_dict(path)
    applied = []
    for term, domain in concept_dict.items():
        if domain in score_map:
            score = score_map[domain]
            cube.record(domain, session_id, confidence=score,
                        convergence_depth=_score_to_depth(score, score >= 0.7))
            applied.append(f"{term}->{domain}")
    return applied


def _report_score_map(report) -> dict:
    """Extract score map from a PrinciplesReport."""
    return {
        "DRY": report.dry_score, "KISS": report.kiss_score,
        "CC": report.cc_score, "BIG-O": report.bigo_score,
        "SOLID": report.solid_score, "GRASP": report.grasp_score,
        "YAGNI": report.yagni_score, "YSH": report.ysh_score,
    }


def wire_principles(path: str = ".", session_id: str | None = None, project_id: str | None = None) -> dict:
    """Scan codebase and record design principle signals in the CUBE."""
    from lib.principles_criterion import principles_scan, principles_as_attest_signals

    if session_id is None:
        import hashlib, time
        session_id = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]

    cube = _load_cube()
    cached = _check_fast_path(cube)
    if cached is not None:
        cached["session_id"] = session_id
        return cached

    report = principles_scan(path)
    score_map = _report_score_map(report)

    _record_signals(cube, principles_as_attest_signals(report), score_map, session_id)
    concept_applied = _apply_concept_dict(cube, path, score_map, session_id)

    prev_data = None
    try:
        prev_data = json.loads(CUBE_CACHE.read_text()) if CUBE_CACHE.exists() else None
    except Exception:
        pass

    _save_cube(cube, report)

    result = {
        "session_id": session_id,
        "composite_score": report.composite_score,
        "mesh_fraction": cube.snapshot().mesh_fraction,
        "cube_report": cube.report(),
        "violations": report.violations[:5],
    }
    if concept_applied:
        result["concept_mappings"] = concept_applied
    _auto_record_strategies(path, report, prev_data)
    return result


def _load_cube_config() -> dict:
    """Load CUBE configuration or return defaults."""
    config_path = _ROOT / "cache" / "cube-config.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except Exception:
            pass
    return {
        "enabled_operational": [
            "convergence_speed", "friction_density", "task_efficiency",
            "achievement_rate", "compound_rate", "genome_health",
        ],
        "promotion_mode": "default",
        "proof_report_schedule": "manual",
        "external_scoring_mode": "self-only",
    }


def _score_jsonl(path: Path, key: str, n: int, invert: bool = True) -> float | None:
    """Score from last N entries of a JSONL file. Returns 0.0-1.0 or None."""
    if not path.exists():
        return None
    try:
        lines = [json.loads(l) for l in path.read_text().strip().split("\n") if l.strip()]
        if not lines:
            return None
        recent = lines[-n:]
        avg = sum(r.get(key, 0) for r in recent) / len(recent)
        return max(0.0, min(1.0, 1.0 - avg if invert else avg))
    except Exception:
        return None


def _score_facets() -> dict[str, float]:
    """Task efficiency + achievement rate from usage facets."""
    facets_dir = _ROOT / "usage-data" / "facets"
    if not facets_dir.exists():
        return {}
    try:
        facets = list(facets_dir.glob("*.json"))
        if not facets:
            return {}
        outcomes = [json.loads(fp.read_text()).get("outcome", "") for fp in facets]
        total = len(outcomes)
        fully = outcomes.count("fully_achieved")
        mostly = outcomes.count("mostly_achieved")
        return {
            "task_efficiency": fully / total,
            "achievement_rate": (fully + mostly) / total,
        }
    except Exception:
        return {}


def _score_genome() -> float | None:
    """Average genome fitness."""
    genome_path = _ROOT / "cache" / "genome.json"
    if not genome_path.exists():
        return None
    try:
        genes = json.loads(genome_path.read_text()).get("genes", {})
        fitnesses = [float(g["fitness"]) for g in genes.values() if g.get("fitness") is not None]
        return sum(fitnesses) / len(fitnesses) if fitnesses else None
    except Exception:
        return None


def wire_operational(session_id: str | None = None) -> dict:
    """Compute and record operational metrics in the CUBE."""
    if session_id is None:
        import hashlib, time
        session_id = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]

    config = _load_cube_config()
    enabled = set(config.get("enabled_operational", []))
    scores: dict[str, float] = {}

    speed = _score_jsonl(_ROOT / "learning" / "converge-metrics.jsonl", "elapsed_sec", 20, invert=True)
    if speed is not None:
        scores["convergence_speed"] = speed

    friction = _score_jsonl(_ROOT / "learning" / "evo-convergence.jsonl", "calibrated", 10, invert=True)
    if friction is not None:
        scores["friction_density"] = friction

    scores.update(_score_facets())

    try:
        from lib.converge_metrics import compound_analysis
        scores["compound_rate"] = min(1.0, compound_analysis().get("evidence_points", 0) / 5.0)
    except Exception:
        pass

    genome = _score_genome()
    if genome is not None:
        scores["genome_health"] = genome

    if enabled:
        scores = {k: v for k, v in scores.items() if k in enabled}

    if scores:
        cube = _load_cube()
        for domain, score in scores.items():
            cube.record(domain, session_id, confidence=score,
                        convergence_depth=_score_to_depth(score, score >= 0.7))
        _save_cube(cube)

    return scores


def _score_jsonl_count(path: Path, count_key: str, count_values: tuple) -> float | None:
    """Score a JSONL file by counting entries matching key/values in recent 20 lines.

    Fail-loud (fix(genome) #4263, same disease): a missing, empty, or unreadable
    source returns None (unmeasured) with an ERROR log — never an invented
    default. The CUBE must not mistake the absence of data for weak evidence.
    """
    if not path.exists():
        logger.error("[cube-wire] source missing — dimension unmeasured: %s", path)
        return None
    try:
        import json as _json
        lines = path.read_text().strip().split("\n")
        recent = [_json.loads(l) for l in lines[-20:] if l.strip()]
        if not recent:
            logger.error("[cube-wire] source empty — dimension unmeasured: %s", path)
            return None
        matched = sum(1 for r in recent if r.get(count_key) in count_values)
        return min(1.0, matched / max(1, len(recent)))
    except Exception as exc:
        logger.error("[cube-wire] source unreadable — dimension unmeasured: %s (%s)", path, exc)
        return None


def _score_shadow_health(path: Path | None = None) -> float | None:
    """Score shadow metrics health as ratio of healthy metrics.

    Fail-loud: a missing or empty source returns None (unmeasured), never a
    default. The path defaults to CLAUDE_DIR/data/shadow-metrics.json.
    """
    if path is None:
        path = CLAUDE_DIR / "data" / "shadow-metrics.json"
    if not path.exists():
        logger.error("[cube-wire] shadow-metrics missing — dimension unmeasured: %s", path)
        return None
    try:
        import json as _json
        metrics = _json.loads(path.read_text()).get("metrics", {})
        if not metrics:
            logger.error("[cube-wire] shadow-metrics empty — dimension unmeasured: %s", path)
            return None
        return sum(1 for m in metrics.values() if m.get("health") == "healthy") / len(metrics)
    except Exception as exc:
        logger.error("[cube-wire] shadow-metrics unreadable: %s (%s)", path, exc)
        return None


def wire_knowledge_work(session_id: str | None = None, claude_dir: Path | str | None = None) -> dict:
    """Wire knowledge-work signals into CUBE. Origin: Gen 475 GRIP Power plan.

    Accepts claude_dir for path-portability (hooks/knowledge-work-hook.py
    passes it explicitly). Only dimensions with a REAL measurement are recorded
    — dimensions whose source is missing/unreadable score None and are skipped
    with an ERROR log, never an invented default (fix(genome) #4263, same
    disease).

    Note: evidence_quality / reasoning_rigour / cross_domain_validity /
    source_diversity / temporal_validity previously carried hard-coded 0.5 — an
    invented value with no source. They are unmeasured until a real scorer is
    wired.
    """
    if session_id is None:
        session_id = f"kw-{int(__import__('time').time())}"

    base = Path(claude_dir) if claude_dir is not None else CLAUDE_DIR
    data_dir = base / "data"

    scores = {
        "hypothesis_registration": _score_jsonl_count(
            data_dir / "hypothesis-registry.jsonl", "status", ("pending", "confirmed", "refuted")),
        "claim_falsifiability": _score_jsonl_count(
            data_dir / "falsification-results.jsonl", "grade", ("VERIFIED",)),
        "bias_awareness": _score_shadow_health(data_dir / "shadow-metrics.json"),
    }

    measured = {k: v for k, v in scores.items() if v is not None}
    skipped = sorted(k for k, v in scores.items() if v is None)
    if skipped:
        logger.warning("[cube-wire] skipped unmeasured dimensions: %s", ", ".join(skipped))

    cube = _load_cube()
    for domain, score in measured.items():
        cube.record(domain, session_id, confidence=score,
                    convergence_depth=_score_to_depth(score, score >= 0.7))
    _save_cube(cube)
    return scores


def _is_project_dir(path: str) -> bool:
    """Check if path is a project directory — rejects home dir and root."""
    p = Path(path).resolve()
    if p == Path.home().resolve() or p == Path("/"):
        return False
    markers = [".git", "CLAUDE.md", "package.json", "pyproject.toml",
               "Cargo.toml", "go.mod", "Makefile", "setup.py"]
    return any((p / m).exists() for m in markers)


def _parse_args(argv: list[str] | None = None):
    import argparse
    parser = argparse.ArgumentParser(description="Wire principles scan into 4D CUBE")
    parser.add_argument("path", nargs="?", default=".", help="Codebase path")
    parser.add_argument("--report", action="store_true", help="Print CUBE state only")
    parser.add_argument("--session", default=None, help="Session ID to record")
    parser.add_argument("--seed-cache", metavar="FILE", default=None,
                        help="write a fresh design cube to FILE unless a cube record "
                             "is already there, then exit (scripts/install.sh uses it)")
    return parser.parse_args(argv)


def _print_seed_result(target: Path) -> None:
    """Seed ``target`` and say what happened (scripts/install.sh, GRIP#9370)."""
    record = seed_cache(target)
    if record is None:
        print(f"[CUBE] Cube record already present at {target}")
    else:
        print(f"[CUBE] Initialized with {len(record['levels'])} design domains at {target}")


def _print_report() -> None:
    cube = _load_cube()
    print(cube.report())
    op_scores = wire_operational(session_id="report-only")
    if op_scores:
        print(f"\n  Operational Dimensions ({len(op_scores)}):")
        for d, s in sorted(op_scores.items()):
            bar = "#" * int(s * 20) + "." * (20 - int(s * 20))
            print(f"    {d:<22} {s:>6.1%} [{bar}]")


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.seed_cache:
        _print_seed_result(Path(args.seed_cache))
        return
    if args.report:
        _print_report()
        return

    if not _is_project_dir(args.path):
        print(f"[CUBE] Skipping: {args.path} is not a project directory", file=sys.stderr)
        return

    result = wire_principles(args.path, session_id=args.session)
    # Also wire operational dimensions
    op_scores = wire_operational(session_id=args.session or result.get("session_id"))
    _print_wire_result(result, op_scores)


def _print_wire_result(result: dict, op_scores: dict | None) -> None:
    print(f"[CUBE] Session {result['session_id']} recorded")
    print(f"[CUBE] Composite score: {result['composite_score']:.1%}")
    print(f"[CUBE] MESH fraction: {result['mesh_fraction']:.1%}")
    if op_scores:
        print(f"[CUBE] Operational: {len(op_scores)} dimensions recorded")
    if result["violations"]:
        print("[CUBE] Violations:")
        for v in result["violations"]:
            print(f"  • {v}")
    print()
    print(result["cube_report"])


if __name__ == "__main__":
    main()
