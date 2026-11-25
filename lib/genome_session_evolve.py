#!/usr/bin/env python3
"""genome_session_evolve.py -- Autonomous genome evolution from session activity.

Evolves the genome one generation using session signals (git commits, invoked
skills) as convergence proxy. Auto-exports to data/genome-shared.json.

Called by: grip_save.py (step 2b), /save command
Standalone: python3 lib/genome_session_evolve.py

This is the autonomous evolution pathway — ensures the genome evolves every
session without requiring manual /evo invocation.
"""

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(__file__).resolve().parent.parent
_ROOT = CLAUDE_DIR
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _local_author_email(repo: Path) -> str:
    """The email this clone stamps on its commits, or "" when git cannot say.

    ``git var GIT_AUTHOR_IDENT`` answers with the identity the next commit
    would carry, configured or auto-detected, so it matches what this machine
    actually authored. It fails when ``user.useConfigOnly`` is set and no
    email is configured; that returns "".
    """
    try:
        result = subprocess.run(
            ["git", "var", "GIT_AUTHOR_IDENT"],
            capture_output=True, text=True, timeout=10, cwd=str(repo),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    start, end = result.stdout.find("<"), result.stdout.find(">")
    if result.returncode != 0 or start == -1 or end <= start + 1:
        return ""
    return result.stdout[start + 1:end]


def _session_commit_count(hours: int = 4, repo: Path = None) -> int:
    """Count non-merge commits THIS clone's author made in the last N hours.

    Without the author filter, a pull of teammates' commits counted as this
    session's evidence: ``/save`` then advanced the genome and re-exported the
    tracked ``data/genome-shared.json`` on a machine that had only pulled,
    and the next pull collided with that rewrite (fleet report 2026-10-02,
    finding A-6). Matching is exact: ``--fixed-strings`` with the angle
    brackets, so ``me@x`` never matches ``ame@x``. An identity git cannot
    resolve counts nothing, because a commit nobody can attribute to this
    machine is not evidence of its session.
    """
    repo = repo or CLAUDE_DIR
    email = _local_author_email(repo)
    if not email:
        return 0
    try:
        result = subprocess.run(
            ["git", "log", f"--since={hours} hours ago", "--no-merges",
             "--fixed-strings", f"--author=<{email}>", "--format=%H"],
            capture_output=True, text=True, timeout=10, cwd=str(repo),
        )
        if result.returncode == 0 and result.stdout.strip():
            return len(result.stdout.strip().splitlines())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return 0


def _usage_skill_names(path: Path, cutoff_ms: float):
    """Yield skill names recorded at or after ``cutoff_ms``, in file order.

    A byte that is not valid UTF-8 becomes U+FFFD, so it costs only its own
    line's name, never the rest of the ground-truth log.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = row.get("timestamp")
        name = row.get("skill") or ""
        if name and isinstance(ts, (int, float)) and ts >= cutoff_ms:
            yield name


def _read_skill_usage(
    gene_names: set, hours: int = 4, path: Path = None
) -> tuple[list, list]:
    """Recent skill invocations from the hook's record, split matched/unmatched.

    ``skill-usage.jsonl`` is the ONLY ground-truth signal of which skills were
    actually used: the harness writes it via hooks/skill-usage-tracker.py, so
    the model being scored never reports on itself. Everything else this module
    reads is a proxy.

    Unmatched names are returned rather than dropped, because a silently
    discarded name is indistinguishable from an unused skill. As at 2026-08-01
    only 16 of 31 recorded names (52%) matched a gene by exact string, and
    namespace drift that quietly halves the signal is the failure class this
    module is recovering from.

    The gap is mostly one namespace meeting another: the recorder writes the name
    the Skill tool was invoked with, often a COMMAND (``/recall``, ``/save``),
    while genes are keyed by SKILL DIRECTORY (``recall``,
    ``saving-state``). ``resolve_skill_to_gene`` closes that by reading the
    delegation each command file states about itself, lifting the join to 0.77 on
    the same history. It returns None wherever the answer is not settled, so an
    ambiguous name stays unmatched instead of crediting the wrong gene.

    Resolution reads the command file as it stands NOW, so a delegation that
    changed since a record was written would credit the new gene rather than the
    one in force at the time. The exposure is bounded by ``hours`` -- 4 in every
    production caller -- and a command's delegation changing inside a four-hour
    window is not a case worth carrying machinery for. Widening that window would
    make it worth revisiting.
    """
    from lib.skill_gene_resolver import resolve_skill_to_gene

    usage_path = path or (CLAUDE_DIR / "skill-usage.jsonl")
    commands_dir = CLAUDE_DIR / "commands"
    matched: list = []
    unmatched: list = []
    if not usage_path.exists():
        return matched, unmatched

    cutoff_ms = (datetime.now(timezone.utc).timestamp() - hours * 3600) * 1000
    # Memoised per call: the log yields one entry per invocation, so a skill used
    # ten times would otherwise re-read the same command file ten times. Measured
    # on the live log, 44 entries needed a disk read but only 15 were distinct --
    # 29 redundant reads per scan. Worth removing on a path that is already prone
    # to timing out under concurrent saves (issue #4310).
    seen: dict = {}
    for name in _usage_skill_names(usage_path, cutoff_ms):
        if name not in seen:
            seen[name] = resolve_skill_to_gene(name, gene_names, commands_dir)
        gene = seen[name]
        bucket = matched if gene else unmatched
        entry = gene or name
        if entry not in bucket:
            bucket.append(entry)
    return matched, unmatched


def _read_proxy(path: Path, tail: int, extract, gene_names: set) -> list:
    """Read a fallback source: last ``tail`` lines, ``extract(row) -> name``.

    A byte that is not valid UTF-8 costs only its own line, as above.
    """
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-tail:]
    except OSError:
        return []
    out: list = []
    for line in lines:
        try:
            name = extract(json.loads(line))
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
        if name and name in gene_names and name not in out:
            out.append(name)
    return out


def _metrics_skill(row: dict) -> str:
    if row.get("event") in ("skill_triggered", "skill_invoked"):
        return row.get("skill", "")
    return ""


def _session_invoked_skills(gene_names: set, hours: int = 4) -> list:
    """Gene names invoked this session, best available signal first.

    Order matters. ``skill-usage.jsonl`` is ground truth and is tried first;
    metrics.jsonl and insights.jsonl stay as proxies so a machine whose hook
    has not recorded anything yet degrades to its previous behaviour rather
    than to nothing.

    Returns [] when nothing is known. That empty list is a real answer, and
    callers must not substitute a constant for it.
    """
    invoked, unmatched = _read_skill_usage(gene_names, hours=hours)
    if unmatched:
        logger.info(
            "[session-evolve] %d recorded skill name(s) match no gene: %s",
            len(unmatched), ", ".join(unmatched[:8]),
        )
    if invoked:
        return invoked

    invoked = _read_proxy(
        CLAUDE_DIR / "metrics.jsonl", 50, _metrics_skill, gene_names
    )
    if invoked:
        return invoked

    return _read_proxy(
        CLAUDE_DIR / "learning" / "insights.jsonl", 20,
        lambda row: row.get("skill_name", ""), gene_names,
    )


def _auto_export() -> int:
    """Export genome to data/genome-shared.json. Returns gene count exported."""
    try:
        from lib.genome_export import export_genome
        return export_genome()["exported"]
    except Exception as e:
        logger.warning("[session-evolve] export failed: %s", e)
        return 0


def _expressed_window(genome, invoked: list) -> list:
    """Genes eligible for a fitness update this generation.

    ``update_gene_fitness`` returns early for any gene outside this list
    (converge_dna_fitness.py: ``if not was_expressed``), so a gene missing here
    cannot be scored at all. The historical value was ``list(genes.keys())[:50]``
    -- an insertion-ordered slice, which left 727 of 777 genes (93.6%) never
    evaluated once across 5,710 generations, and would have silently discarded
    any real invocation landing outside the first fifty keys.

    The sample is kept as the not-invoked control arm that ablation scoring
    needs, and every invoked gene is unioned in so evidence is never dropped.
    """
    window = list(genome.genes.keys())[:50]
    return window + [name for name in invoked if name not in window]


def _build_outcome(genome, commit_count: int, invoked: list):
    """Factory for session ConvergeOutcome (decoupled from concrete class)."""
    from lib.converge_dna_core import ConvergeOutcome
    return ConvergeOutcome(
        run_id=f"session-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        genome_id=genome.id,
        generation=genome.generation,
        expressed_genes=_expressed_window(genome, invoked),
        invoked_genes=invoked,
        satisfied=commit_count > 0,
        score_before=genome.avg_fitness,
        score_after=min(1.0, genome.avg_fitness + (0.01 * min(commit_count, 5))),
        depth_reached=min(commit_count, 11),
        context="session-evolve",
    )


def evolve_from_session() -> dict:
    """Evolve genome one generation from session activity signals.

    Single-flight (issue #4310): if another session is evolving right now,
    skip immediately instead of queueing. The evidence this module folds in is
    read from SHARED substrate (the shared tree's git log, the shared
    skill-usage.jsonl), so N concurrent runs fold near-identical evidence into
    N generations — the extra runs are generation-counter inflation, the exact
    fabrication the no-evidence guard below refuses. Skipping the contended
    runs is therefore more honest than serialising them, and it is what stops
    five stacked /save instances dying on grip_save's 45-second wall.

    Returns dict with: prev_gen, new_gen, gene_count, avg_fitness, commits,
    exported — or a ``skipped`` dict (no ``prev_gen`` when the skip happened
    before the genome was ever read).
    """
    from lib.file_lock import FileLock, LockTimeoutError
    try:
        with FileLock(_single_flight_lock_path(), timeout=0):
            return _evolve_from_session_exclusive()
    except LockTimeoutError:
        logger.info(
            "[session-evolve] another session is evolving the genome -- skipped"
        )
        return {"skipped": "another session is evolving the genome"}


def _single_flight_lock_path() -> Path:
    """Lock file beside the SHARED genome, not this module's checkout.

    Concurrent ``/save`` runs execute this module from different worktrees, so
    a ``Path(__file__)``-derived lock would give each session its own lock and
    dedupe nothing. The contention is on ``GENOME_PATH`` (issue #4310:
    instances queue on the genome lock and die on grip_save's 45s wall), so
    the single-flight guard must live beside that file.
    """
    from lib.converge_dna_core import GENOME_PATH
    return GENOME_PATH.with_name(GENOME_PATH.name + ".evolve.lock")


def _evolve_from_session_exclusive() -> dict:
    """The actual evolve pass; the caller holds the single-flight lock."""
    from lib.converge_dna import load_genome, save_genome, evolve_generation

    genome = load_genome()
    if genome is None:
        return {"error": "No genome found. Run: converge_dna.py bootstrap"}

    commit_count = _session_commit_count()
    invoked = _session_invoked_skills(set(genome.genes.keys()))

    # No evidence means no generation. Advancing the counter on an empty
    # session would record a decision GRIP never observed -- which is how the
    # genome reached generation 5708 while 93.6% of its genes had never been
    # measured once. An unrecorded generation is honest; a fabricated one is
    # not.
    if not invoked and commit_count == 0:
        logger.info("[session-evolve] no session evidence -- generation not advanced")
        return {
            "skipped": "no session evidence",
            "prev_gen": genome.generation, "new_gen": genome.generation,
            "gene_count": len(genome.genes),
            "avg_fitness": round(genome.avg_fitness, 3),
            "commits": 0, "invoked": 0, "exported": 0,
        }

    outcome = _build_outcome(genome, commit_count, invoked)

    prev_gen = genome.generation
    evolved = evolve_generation(genome, outcome)
    save_genome(evolved, reason="session-evolve")
    exported = _auto_export()

    logger.info(
        "[session-evolve] gen %d -> %d | %d genes | fitness=%.3f | commits=%d | invoked=%d",
        prev_gen, evolved.generation, len(evolved.genes), evolved.avg_fitness,
        commit_count, len(invoked),
    )
    return {
        "prev_gen": prev_gen, "new_gen": evolved.generation,
        "gene_count": len(evolved.genes), "avg_fitness": round(evolved.avg_fitness, 3),
        "commits": commit_count, "invoked": len(invoked), "exported": exported,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = evolve_from_session()
    if "error" in result:
        print(result["error"], file=sys.stderr)
        sys.exit(1)
    if "skipped" in result:
        gen = result.get("prev_gen")
        stays = f"generation stays at {gen}" if gen is not None else "generation unchanged"
        print(f"Session evolve: skipped ({result['skipped']}) | {stays}")
        sys.exit(0)
    print(
        f"Session evolve: gen {result['prev_gen']} -> {result['new_gen']} | "
        f"{result['gene_count']} genes | fitness={result['avg_fitness']} | "
        f"commits={result['commits']} | invoked={result['invoked']} | "
        f"exported={result['exported']}"
    )
