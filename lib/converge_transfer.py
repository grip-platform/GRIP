"""
converge_transfer.py — Cross-project convergence inheritance and marketplace.

Combines two concerns:
  1. Strategy inheritance: record, find, apply, and report on proven
     convergence strategies across projects.
  2. Strategy marketplace: export/import portable strategy packages for
     cross-organisation sharing.

When a project achieves high convergence scores, its successful strategies
become transferable to new projects. This is the mechanism by which GRIP
recursively improves not just one codebase, but ALL codebases it touches.

Pattern: converge(project_A) -> strategies_A
         converge(project_B, inherit=strategies_A) -> faster convergence

Export flow:
    strategies = find_transferable(domains=["DRY", "SOLID"])
    package = export_package(strategies, author="team-a")

Import flow:
    package = load_package("convergence-strategies-team-a.json")
    report = import_package(package, trust_level=0.8)
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path.home() / ".claude"
STRATEGY_STORE = _ROOT / "cache" / "convergence-strategies.json"
MARKETPLACE_DIR = _ROOT / "cache" / "marketplace"

# Package format version for forward compatibility
PACKAGE_VERSION = 1


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------


@dataclass
class ConvergenceStrategy:
    """A proven convergence strategy from a successful project."""

    domain: str                    # CUBE domain (DRY, SOLID, etc.)
    strategy_name: str             # Human-readable name
    description: str               # What this strategy does
    source_project: str            # Where it was proven
    score_before: float            # Domain score before applying
    score_after: float             # Domain score after applying
    improvement: float = 0.0      # score_after - score_before
    confidence: float = 0.0       # How reliable (0-1)
    times_applied: int = 1        # How many projects used this
    times_succeeded: int = 1      # How many succeeded (score improved)
    tags: list[str] = field(default_factory=list)  # Project type tags
    created_at: str = ""
    content_hash: str = ""        # SHA-256 of strategy_name + domain

    def __post_init__(self):
        self.improvement = self.score_after - self.score_before
        self.confidence = self.times_succeeded / max(self.times_applied, 1)
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.content_hash:
            raw = f"{self.domain}:{self.strategy_name}".lower()
            self.content_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load_strategies() -> list[dict]:
    """Load strategy store from disk."""
    if not STRATEGY_STORE.exists():
        return []
    try:
        data = json.loads(STRATEGY_STORE.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_strategies(strategies: list[dict]) -> None:
    """Persist strategy store to disk."""
    STRATEGY_STORE.parent.mkdir(parents=True, exist_ok=True)
    STRATEGY_STORE.write_text(json.dumps(strategies, indent=2))


def record_success(
    project: str,
    domain: str,
    strategy: str,
    score_before: float,
    score_after: float,
    description: str = "",
    tags: Optional[list[str]] = None,
) -> ConvergenceStrategy:
    """Record a successful convergence strategy for future inheritance.

    If the strategy already exists (same domain + name), increment
    its success count and update confidence.
    """
    cs = ConvergenceStrategy(
        domain=domain,
        strategy_name=strategy,
        description=description or f"{strategy} applied to {domain} in {project}",
        source_project=project,
        score_before=score_before,
        score_after=score_after,
        tags=tags or [],
    )

    strategies = _load_strategies()

    # Check for existing strategy (content-addressable)
    for i, existing in enumerate(strategies):
        if existing.get("content_hash") == cs.content_hash:
            # Update existing: increment counters
            existing["times_applied"] = existing.get("times_applied", 1) + 1
            if score_after > score_before:
                existing["times_succeeded"] = existing.get("times_succeeded", 1) + 1
            existing["confidence"] = (
                existing["times_succeeded"] / existing["times_applied"]
            )
            # Keep best improvement
            new_improvement = score_after - score_before
            if new_improvement > existing.get("improvement", 0):
                existing["improvement"] = new_improvement
                existing["score_before"] = score_before
                existing["score_after"] = score_after
            strategies[i] = existing
            _save_strategies(strategies)
            return ConvergenceStrategy(**{
                k: v for k, v in existing.items()
                if k in ConvergenceStrategy.__dataclass_fields__
            })

    # New strategy
    strategies.append(asdict(cs))
    _save_strategies(strategies)
    return cs


def find_transferable(
    target_domains: Optional[list[str]] = None,
    min_confidence: float = 0.5,
    min_improvement: float = 0.1,
    tags: Optional[list[str]] = None,
    limit: int = 10,
) -> list[ConvergenceStrategy]:
    """Find transferable strategies ranked by confidence and improvement.

    Filters by domain, confidence threshold, and minimum improvement.
    Returns strategies sorted by (confidence * improvement) descending.
    """
    strategies = _load_strategies()
    results = []

    for s in strategies:
        # Domain filter
        if target_domains and s.get("domain") not in target_domains:
            continue

        # Confidence filter
        confidence = s.get("confidence", 0)
        if confidence < min_confidence:
            continue

        # Improvement filter
        improvement = s.get("improvement", 0)
        if improvement < min_improvement:
            continue

        # Tag filter (if provided, require at least one matching tag)
        if tags:
            s_tags = set(s.get("tags", []))
            if not s_tags.intersection(tags):
                continue

        results.append(s)

    # Sort by effectiveness = confidence * improvement
    results.sort(
        key=lambda s: s.get("confidence", 0) * s.get("improvement", 0),
        reverse=True,
    )

    return [
        ConvergenceStrategy(**{
            k: v for k, v in s.items()
            if k in ConvergenceStrategy.__dataclass_fields__
        })
        for s in results[:limit]
    ]


def apply_inheritance(
    target_project: str,
    strategies: list[ConvergenceStrategy],
) -> dict:
    """Apply inherited strategies to a target project.

    Records the application attempt in the strategy store.
    Returns a report of which strategies were applied.

    The actual improvement is project-specific -- this function
    records intent. Call record_success() later with actual results.
    """
    applied = []
    for strategy in strategies:
        # Record the application attempt
        store = _load_strategies()
        for s in store:
            if s.get("content_hash") == strategy.content_hash:
                s["times_applied"] = s.get("times_applied", 1) + 1
                break
        _save_strategies(store)

        applied.append({
            "domain": strategy.domain,
            "strategy": strategy.strategy_name,
            "confidence": round(strategy.confidence, 2),
            "expected_improvement": round(strategy.improvement, 2),
            "source": strategy.source_project,
        })

    return {
        "target_project": target_project,
        "strategies_applied": len(applied),
        "details": applied,
    }


def inheritance_report() -> dict:
    """Generate a report on the strategy inheritance network.

    Shows which strategies are most proven, most transferred,
    and which domains have the richest inheritance chains.
    """
    strategies = _load_strategies()

    if not strategies:
        return {"total_strategies": 0, "domains": {}, "top_strategies": []}

    # Domain breakdown
    domains: dict[str, list] = {}
    for s in strategies:
        domain = s.get("domain", "unknown")
        if domain not in domains:
            domains[domain] = []
        domains[domain].append(s)

    domain_summary = {}
    for domain, strats in domains.items():
        domain_summary[domain] = {
            "count": len(strats),
            "avg_confidence": round(
                sum(s.get("confidence", 0) for s in strats) / len(strats), 2
            ),
            "avg_improvement": round(
                sum(s.get("improvement", 0) for s in strats) / len(strats), 2
            ),
            "total_applications": sum(s.get("times_applied", 1) for s in strats),
        }

    # Top strategies by effectiveness
    all_sorted = sorted(
        strategies,
        key=lambda s: s.get("confidence", 0) * s.get("improvement", 0),
        reverse=True,
    )

    return {
        "total_strategies": len(strategies),
        "domains": domain_summary,
        "top_strategies": [
            {
                "name": s.get("strategy_name"),
                "domain": s.get("domain"),
                "confidence": round(s.get("confidence", 0), 2),
                "improvement": round(s.get("improvement", 0), 2),
                "applications": s.get("times_applied", 1),
            }
            for s in all_sorted[:5]
        ],
    }


# ---------------------------------------------------------------------------
# Marketplace
# ---------------------------------------------------------------------------


@dataclass
class StrategyPackage:
    """A portable collection of convergence strategies."""

    name: str
    author: str
    strategies: list[dict] = field(default_factory=list)
    description: str = ""
    version: int = PACKAGE_VERSION
    created_at: str = ""
    package_hash: str = ""
    strategy_count: int = 0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        self.strategy_count = len(self.strategies)
        if not self.package_hash:
            raw = json.dumps(self.strategies, sort_keys=True)
            self.package_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]


def export_package(
    strategies: list[ConvergenceStrategy],
    name: str = "exported-strategies",
    author: str = "unknown",
    description: str = "",
    min_confidence: float = 0.5,
    save_to_marketplace: bool = True,
) -> StrategyPackage:
    """Export strategies as a portable JSON package.

    Filters strategies by minimum confidence before packaging.
    Strips local-only fields (source_project paths) and adds
    package metadata for provenance tracking.

    Args:
        strategies: List of ConvergenceStrategy to export.
        name: Human-readable package name.
        author: Author/team identifier.
        description: Package description.
        min_confidence: Minimum confidence threshold for inclusion.
        save_to_marketplace: If True, save to local marketplace dir.

    Returns:
        StrategyPackage ready for sharing.
    """
    exportable = []
    for s in strategies:
        if s.confidence < min_confidence:
            continue
        entry = {
            "domain": s.domain,
            "strategy_name": s.strategy_name,
            "description": s.description,
            "score_before": s.score_before,
            "score_after": s.score_after,
            "improvement": s.improvement,
            "confidence": round(s.confidence, 3),
            "times_applied": s.times_applied,
            "times_succeeded": s.times_succeeded,
            "tags": s.tags,
            "content_hash": s.content_hash,
        }
        exportable.append(entry)

    package = StrategyPackage(
        name=name,
        author=author,
        strategies=exportable,
        description=description or f"{len(exportable)} strategies from {author}",
    )

    if save_to_marketplace:
        MARKETPLACE_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{name}.json"
        filepath = MARKETPLACE_DIR / filename
        filepath.write_text(json.dumps(asdict(package), indent=2))

    return package


def load_package(path: str) -> Optional[StrategyPackage]:
    """Load a strategy package from a JSON file.

    Args:
        path: Path to package JSON file.

    Returns:
        StrategyPackage or None if file is invalid.
    """
    filepath = Path(path)
    if not filepath.exists():
        # Try marketplace dir
        filepath = MARKETPLACE_DIR / path
        if not filepath.exists():
            filepath = MARKETPLACE_DIR / f"{path}.json"

    if not filepath.exists():
        return None

    try:
        data = json.loads(filepath.read_text())
        return StrategyPackage(
            name=data.get("name", "unknown"),
            author=data.get("author", "unknown"),
            strategies=data.get("strategies", []),
            description=data.get("description", ""),
            version=data.get("version", 1),
            created_at=data.get("created_at", ""),
            package_hash=data.get("package_hash", ""),
        )
    except (json.JSONDecodeError, OSError):
        return None


def import_package(
    package: StrategyPackage,
    trust_level: float = 0.8,
    overwrite: bool = False,
) -> dict:
    """Import strategies from a package into the local strategy store.

    Imported strategies have their confidence discounted by trust_level
    to reflect uncertainty about external data quality.

    Args:
        package: The StrategyPackage to import.
        trust_level: Confidence multiplier (0.0 to 1.0). Imported strategies
            get confidence * trust_level to discount external claims.
        overwrite: If True, overwrite existing strategies with same hash.

    Returns:
        Dict with: imported (count), skipped (count), details.
    """
    trust_level = max(0.0, min(1.0, trust_level))
    store = _load_strategies()
    existing_hashes = {s.get("content_hash") for s in store}

    imported = []
    skipped = []

    for entry in package.strategies:
        content_hash = entry.get("content_hash", "")

        if content_hash in existing_hashes and not overwrite:
            skipped.append(entry.get("strategy_name", "?"))
            continue

        # Discount confidence by trust level
        original_confidence = entry.get("confidence", 0.5)
        discounted_confidence = round(original_confidence * trust_level, 3)

        strategy_dict = {
            "domain": entry.get("domain", "unknown"),
            "strategy_name": entry.get("strategy_name", "unknown"),
            "description": entry.get("description", ""),
            "source_project": f"import:{package.author}/{package.name}",
            "score_before": entry.get("score_before", 0.0),
            "score_after": entry.get("score_after", 0.0),
            "improvement": entry.get("improvement", 0.0),
            "confidence": discounted_confidence,
            "times_applied": entry.get("times_applied", 1),
            "times_succeeded": entry.get("times_succeeded", 1),
            "tags": entry.get("tags", []) + [f"imported:{package.author}"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": content_hash,
        }

        if content_hash in existing_hashes and overwrite:
            # Replace existing
            store = [s for s in store if s.get("content_hash") != content_hash]

        store.append(strategy_dict)
        existing_hashes.add(content_hash)
        imported.append(entry.get("strategy_name", "?"))

    _save_strategies(store)

    return {
        "package": package.name,
        "author": package.author,
        "imported": len(imported),
        "skipped": len(skipped),
        "trust_level": trust_level,
        "imported_names": imported,
        "skipped_names": skipped,
    }


def list_packages() -> list[dict]:
    """List all strategy packages in the local marketplace directory.

    Returns:
        List of dicts with: name, author, strategy_count, created_at, path.
    """
    if not MARKETPLACE_DIR.exists():
        return []

    packages = []
    for filepath in sorted(MARKETPLACE_DIR.glob("*.json")):
        try:
            data = json.loads(filepath.read_text())
            packages.append({
                "name": data.get("name", filepath.stem),
                "author": data.get("author", "unknown"),
                "strategy_count": data.get("strategy_count", len(data.get("strategies", []))),
                "created_at": data.get("created_at", ""),
                "path": str(filepath),
            })
        except (json.JSONDecodeError, OSError):
            continue

    return packages


def export_genome_package(
    org_slug: str,
    min_fitness: float = 0.6,
    description: str = "",
) -> Optional[StrategyPackage]:
    """Export the local genome as a marketplace strategy package.

    Bridges the genome (Gene objects) into the marketplace (StrategyPackage)
    via the adapter in converge_dna. Reuses export_package() for packaging,
    dedup, and storage.
    """
    from converge_dna import genome_to_marketplace, load_genome

    genome = load_genome()
    if not genome:
        return None
    strategies = genome_to_marketplace(genome, org_slug, min_fitness)
    if not strategies:
        return None
    pkg_name = f"{org_slug}-genome-gen-{genome.generation}"
    return export_package(
        strategies,
        name=pkg_name,
        author=org_slug,
        description=description or f"{org_slug} genome (gen {genome.generation})",
    )


# Ceiling on any aggregated confidence. Distinctness is observable from the
# provenance field; independence is not. Confirmations from different projects
# can still share an operator, a codebase lineage, or a copied strategy, so the
# aggregate is never allowed to assert certainty no matter how many agree.
CONFIDENCE_CEILING = 0.99

# Entry fields naming where a confirmation came from, most specific first.
_PROVENANCE_FIELDS = ("source_project", "source")


def _source_key(entry: dict) -> str:
    """Normalised provenance key for one entry, or "" when the source is unknown."""
    for name in _PROVENANCE_FIELDS:
        value = entry.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _combine_confirmations(entries: list[dict]) -> float:
    """Combine one strategy's confirmations, counting distinct sources only.

    Repeated confirmations from a single source are re-reports of one
    observation, not new evidence, so only that source's strongest reading is
    kept. Entries with no identified source cannot establish distinctness at
    all: they may all be the same project, so they raise the floor (the best
    observed value) without ever adding an independent term.
    """
    best_by_source: dict[str, float] = {}
    floor = 0.0

    for entry in entries:
        conf = entry.get("confidence", 0.0)
        floor = max(floor, conf)
        key = _source_key(entry)
        if key:
            best_by_source[key] = max(best_by_source.get(key, 0.0), conf)

    # Fewer than two identified sources: independence is not established, so
    # abstain from any lift and report the strongest single observation.
    if len(best_by_source) < 2:
        # Ceiling applies here too: a lone 1.0 confirmation is still a single
        # observation, and times_succeeded/times_applied is 1.0 for any strategy
        # recorded once and succeeded once, so this path is reachable in normal
        # use. Without the clamp the module's stated invariant -- never assert
        # certainty -- would hold only on the multi-source branch.
        return round(min(CONFIDENCE_CEILING, floor), 3)

    product = 1.0
    for conf in best_by_source.values():
        product *= (1.0 - conf)
    return round(min(CONFIDENCE_CEILING, max(floor, 1.0 - product)), 3)


def aggregate_confidence(strategies: list[dict]) -> dict:
    """Aggregate confidence across DISTINCT sources for the same strategy.

    When the same strategy (by content_hash) is confirmed several times,
    confirmations from *different* source projects raise confidence; repeated
    confirmations from the *same* project do not. Repetition is not evidence.

    Sources are established distinct through each entry's ``source_project``
    (or ``source``) field, which ``record_success`` and ``import_package``
    both populate. When fewer than two distinct sources are identified this
    abstains, returning the strongest single observation rather than asserting
    a combined confidence. That mirrors
    ``converge_dna_core.AblationRecord.ablation_lift``, which returns the
    neutral 0.0 until both arms carry enough evidence to support a claim.

    The previous implementation combined every confirmation regardless of
    provenance, so ten re-reports from one project reached 1.000 from a true
    value of 0.700 -- manufacturing certainty out of repetition.

    Returns:
        Dict mapping content_hash -> aggregated_confidence.
    """
    hash_entries: dict[str, list[dict]] = defaultdict(list)

    for s in strategies:
        ch = s.get("content_hash", "")
        conf = s.get("confidence", 0.0)
        if ch and conf > 0:
            hash_entries[ch].append(s)

    return {
        ch: _combine_confirmations(entries)
        for ch, entries in hash_entries.items()
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI for convergence inheritance and marketplace operations."""
    import sys

    if len(sys.argv) < 2:
        print(
            "Usage: converge_transfer.py "
            "<report|find|record|export|import|list|genome-export|aggregate> [options]"
        )
        sys.exit(1)

    cmd = sys.argv[1]

    # -- Inheritance commands --

    if cmd == "report":
        report = inheritance_report()
        print(json.dumps(report, indent=2))

    elif cmd == "find":
        domains = sys.argv[2:] if len(sys.argv) > 2 else None
        results = find_transferable(target_domains=domains)
        for s in results:
            print(
                f"  [{s.domain}] {s.strategy_name} "
                f"(confidence={s.confidence:.0%}, improvement=+{s.improvement:.2f}) "
                f"from {s.source_project}"
            )
        if not results:
            print("No transferable strategies found.")

    elif cmd == "record":
        if len(sys.argv) < 7:
            print("Usage: converge_transfer.py record <project> <domain> <strategy> <before> <after>")
            sys.exit(1)
        cs = record_success(
            project=sys.argv[2],
            domain=sys.argv[3],
            strategy=sys.argv[4],
            score_before=float(sys.argv[5]),
            score_after=float(sys.argv[6]),
        )
        print(f"Recorded: {cs.strategy_name} ({cs.domain}) improvement=+{cs.improvement:.2f}")

    # -- Marketplace commands --

    elif cmd == "list":
        packages = list_packages()
        if not packages:
            print("No packages in marketplace.")
        for p in packages:
            print(f"  {p['name']} by {p['author']} ({p['strategy_count']} strategies)")

    elif cmd == "export":
        name = sys.argv[2] if len(sys.argv) > 2 else "exported"
        author = sys.argv[3] if len(sys.argv) > 3 else "local"
        strategies = find_transferable()
        if not strategies:
            print("No strategies to export.")
            sys.exit(0)
        pkg = export_package(strategies, name=name, author=author)
        print(f"Exported {pkg.strategy_count} strategies to {name}.json")

    elif cmd == "import":
        if len(sys.argv) < 3:
            print("Usage: converge_transfer.py import <package-path> [trust_level]")
            sys.exit(1)
        path = sys.argv[2]
        trust = float(sys.argv[3]) if len(sys.argv) > 3 else 0.8
        pkg = load_package(path)
        if not pkg:
            print(f"Could not load package: {path}")
            sys.exit(1)
        result = import_package(pkg, trust_level=trust)
        print(f"Imported {result['imported']}, skipped {result['skipped']}")

    elif cmd == "genome-export":
        org = sys.argv[2] if len(sys.argv) > 2 else "local"
        min_fit = float(sys.argv[3]) if len(sys.argv) > 3 else 0.6
        pkg = export_genome_package(org, min_fitness=min_fit)
        if not pkg:
            print("No genome found or no genes above threshold.")
            sys.exit(1)
        print(f"Exported {pkg.strategy_count} genes as strategies to {pkg.name}.json")

    elif cmd == "aggregate":
        strategies = _load_strategies()
        agg = aggregate_confidence(strategies)
        print(f"Aggregated {len(agg)} strategies")
        for ch, conf in sorted(agg.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  {ch}: {conf:.1%}")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
