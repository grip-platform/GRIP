#!/usr/bin/env python3
"""
KONO CLI — Command-line interface for the KONO Memory Substrate.

Extracted from kono_substrate.py (H013 SOLID decomposition, Phase 2).
All CLI handlers and the argparse dispatcher live here.
The MemorySubstrate class stays in kono_substrate.py.

Usage:
    python3 lib/kono_cli.py recall "search query"
    python3 lib/kono_cli.py stats
    python3 lib/kono_cli.py dashboard
    python3 lib/kono_cli.py store --content "memory text"
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure lib/ is on sys.path for sibling imports
_lib_dir = str(Path(__file__).resolve().parent)
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from kono import Position4D
from kono_counters import is_operational_content


# =========================================================================
# CLI HANDLERS (independently callable, no argparse dependency)
# =========================================================================

def handle_init_session(substrate, instance: str, gen: int, project: str) -> None:
    instance_obj = substrate.init_session(instance, gen, project)
    print(json.dumps({"status": "ok", "instance": instance_obj.to_dict()}, indent=2, default=str))


def handle_stats(substrate) -> None:
    stats = substrate.get_stats()
    print(json.dumps({"status": "ok", **stats}, indent=2))


def handle_compact_stats(substrate) -> None:
    print(substrate.get_compact_stats())


def handle_session_summary(substrate) -> None:
    print(substrate.format_session_summary())


def handle_dashboard(substrate) -> None:
    stats = substrate.get_stats()
    total = stats['embeddings']['total']
    permanent = stats['embeddings']['permanent']
    transient = total - permanent

    print("Memory Substrate Status:")
    print(f"  Total memories: {total} ({permanent} permanent, {transient} transient)")

    conn = substrate.connect()
    fitness_range = list(conn.execute(
        "SELECT MIN(fitness_score), MAX(fitness_score) FROM embeddings WHERE fitness_score > 0"
    ))
    if fitness_range and fitness_range[0][0] is not None:
        print(f"  Fitness range: {fitness_range[0][0]:.2f} - {fitness_range[0][1]:.2f}")

    print("\nDimensions:")
    by_dim = stats['embeddings'].get('by_dimension', {})
    dim_names = {0: 'Factual (0-2)', 3: 'Procedural (3-5)', 6: 'Episodic (6-8)', 9: 'Emergent (9-11)'}
    for dim_start, label in dim_names.items():
        count = sum(by_dim.get(d, 0) for d in range(dim_start, dim_start + 3))
        if count > 0:
            print(f"  {label:20s} {count} memories")

    inst_total = stats['instances']['total']
    inst_valid = stats['instances']['valid']
    inst_content = stats['instances'].get('content_bearing', inst_valid)
    print(f"\nInstances:")
    print(f"  Total: {inst_total}")
    print(f"  With attestations (content-bearing): {inst_content}")
    print(f"  Active (fitness >= 0.8): {inst_valid}")
    if substrate.current_instance:
        ci = substrate.current_instance
        print(f"  Current: {ci.id} (Gen {ci.generation}, fitness {ci.fitness:.2f})")

    domains = stats['domains']
    print(f"\nDomains: {domains}")
    domain_rows = list(conn.execute(
        "SELECT d.name, d.path, COUNT(e.id) FROM kono_domains d "
        "LEFT JOIN embeddings e ON e.grid_x = d.x_coord "
        "GROUP BY d.x_coord ORDER BY COUNT(e.id) DESC LIMIT 10"
    ))
    for name, path, count in domain_rows:
        print(f"  {path} ({count} memories)")

    summary = substrate.get_session_summary()
    print(f"\nSession Activity:")
    print(f"  Stored: {summary['stored']} | "
          f"Recalled: {summary['recalled']} | "
          f"Attested: {summary['attested']} | "
          f"Promoted: {summary['newly_permanent']}")


def handle_metabolism(substrate) -> None:
    count = substrate.run_metabolism()
    print(json.dumps({"status": "ok", "updated": count}))


def handle_consolidate(substrate) -> None:
    count = substrate.consolidate()
    print(json.dumps({"status": "ok", "newly_permanent": count}))


def handle_evolve(substrate, generations: int = 1) -> None:
    survivors = substrate.evolve(generations)
    print(json.dumps({
        "status": "ok",
        "survivors": len(survivors),
        "ids": [s.id for s in survivors]
    }))


def handle_recall(
    substrate,
    query: str,
    limit: int = 10,
    dimension: Optional[str] = None,
    min_fitness: float = 0.3,  # decay floor (matches recall + spatial expansion)
    expand_spatial: bool = True,
    json_output: bool = False,
    project_path: Optional[str] = None,
    all_projects: bool = False,
    platform: Optional[str] = None,
    channel_id: Optional[str] = None,
    sender_id: Optional[str] = None,
) -> None:
    dim_map = {"factual": 1, "procedural": 4, "episodic": 7, "emergent": 10}
    dim = dim_map.get(dimension) if dimension else None

    resolved_project = None
    if not all_projects:
        if project_path:
            resolved_project = project_path
        else:
            resolved_project = os.getcwd()

    results = substrate.recall(
        query=query,
        limit=limit,
        dimension=dim,
        min_fitness=min_fitness,
        expand_spatial=expand_spatial,
        project_path=resolved_project,
        platform=platform,
        channel_id=channel_id,
        sender_id=sender_id,
    )

    if json_output:
        permanent_count = sum(1 for r in results if r.get("is_permanent"))
        print(json.dumps({
            "status": "ok",
            "query": query,
            "results": results,
            "count": len(results),
            "permanent_count": permanent_count,
        }, indent=2, default=str))
        return

    if not results:
        print(f"No memories found for: {query}")
        return

    permanent_count = 0
    transient_count = 0
    for r in results:
        dim_name = r.get("dimension_name", "unknown")
        fitness = r.get("fitness", 0)
        is_perm = r.get("is_permanent", False)
        created = r.get("created_at", "")

        if is_perm:
            permanent_count += 1
        else:
            transient_count += 1

        age_str = ""
        if created:
            try:
                if isinstance(created, str):
                    if 'Z' in created:
                        ct = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    elif '+' in created:
                        ct = datetime.fromisoformat(created)
                    else:
                        ct = datetime.fromisoformat(created).replace(tzinfo=timezone.utc)
                    days = (datetime.now(timezone.utc) - ct).total_seconds() / 86400
                    age_str = f"{int(days * 24)}h ago" if days < 1 else f"{int(days)}d ago"
            except (ValueError, TypeError):
                pass

        perm_tag = ", permanent" if is_perm else ""
        age_tag = f", {age_str}" if age_str else ""
        source_tag = " [spatial]" if r.get("source") == "kono_spatial" else ""

        print(f"[{dim_name}] (fitness: {fitness:.2f}{age_tag}{perm_tag}){source_tag}")
        content = r.get("content", "")
        if len(content) > 200:
            content = content[:200] + "..."
        print(f"  {content}")
        print()

    print(f"Found {len(results)} memories ({permanent_count} permanent, {transient_count} transient)")


def handle_prune(
    substrate,
    project_path: Optional[str] = None,
    below_fitness: Optional[float] = None,
    memory_id: Optional[int] = None,
    include_permanent: bool = False,
    dry_run: bool = False,
) -> None:
    result = substrate.prune(
        project_path=project_path,
        below_fitness=below_fitness,
        memory_id=memory_id,
        include_permanent=include_permanent,
        dry_run=dry_run,
    )

    if dry_run:
        candidates = result.get("memories", [])
        if not candidates:
            print("No candidates match the filter.")
        else:
            print(f"DRY RUN: {result['candidates']} memories would be pruned:\n")
            for m in candidates:
                perm = " [permanent]" if m["is_permanent"] else ""
                print(f"  ID {m['id']} (fitness: {m['fitness']:.2f}, grid_x: {m['grid_x']}{perm})")
                if m["content"]:
                    print(f"    {m['content']}")
                print()
    else:
        if result.get("error"):
            print(f"Error: {result['error']}")
        elif result["pruned"] == 0:
            print("No memories matched the filter.")
        else:
            print(f"Pruned {result['pruned']} memories "
                  f"({result.get('attestations_deleted', 0)} attestations, "
                  f"{result.get('vectors_deleted', 0)} vectors)")


def handle_store(
    substrate,
    content: str,
    project: Optional[str] = None,
) -> None:
    emb_id = substrate.store_session_memory(
        content=content,
        memory_type="episodic",
        project_path=project,
    )
    if emb_id is not None:
        print(json.dumps({"status": "ok", "embedding_id": emb_id}))
    else:
        print(json.dumps({"status": "error", "reason": "embedding pipeline unavailable"}))


def handle_store_session_summary(
    substrate,
    gen: int,
    instance: str,
    msgs: int = 0,
    achievement: str = "",
    project: Optional[str] = None,
) -> None:
    emb_id = substrate.store_session_summary(
        generation=gen,
        instance_id=instance,
        message_count=msgs,
        achievement=achievement,
        project_path=project,
    )
    if emb_id is not None:
        print(json.dumps({"status": "ok", "embedding_id": emb_id}))
    else:
        print(json.dumps({"status": "error", "reason": "embedding pipeline unavailable"}))


def handle_search(
    substrate,
    x: float,
    y: float,
    z: float = 5.0,
    radius: float = 10.0,
    dimension: Optional[int] = None,
    limit: int = 10,
    min_fitness: float = 0.0,
    json_output: bool = False,
) -> None:
    query_pos = Position4D(x=x, y=y, z=z, t=0)
    results = substrate.retrieve(
        query_position=query_pos,
        dimension=dimension,
        radius=radius,
        limit=limit,
        min_fitness=min_fitness,
    )

    if json_output:
        print(json.dumps({
            "status": "ok",
            "query": {"x": x, "y": y, "z": z, "radius": radius},
            "results": results,
            "count": len(results),
        }, indent=2, default=str))
        return

    if not results:
        print(f"No memories found at ({x}, {y}, {z}) radius={radius}")
        return

    for r in results:
        pos = r.get("position", (0, 0, 0))
        fitness = r.get("fitness", 0)
        dist = r.get("distance", 0)
        perm_tag = " [permanent]" if r.get("is_permanent", False) else ""
        print(f"pos=({pos[0]}, {pos[1]}, {pos[2]}) dist={dist:.1f} fitness={fitness:.2f}{perm_tag}")
        content = r.get("content", "")
        if len(content) > 200:
            content = content[:200] + "..."
        print(f"  {content}")
        print()

    print(f"Found {len(results)} memories")


# =========================================================================
# CLI DISPATCHER
# =========================================================================

def main():
    """CLI for KONO Substrate."""
    import argparse
    from kono_substrate import MemorySubstrate

    parser = argparse.ArgumentParser(description="KONO Memory Substrate")
    subparsers = parser.add_subparsers(dest="command")

    # Init session
    init_parser = subparsers.add_parser("init-session", help="Initialize session")
    init_parser.add_argument("--instance", required=True, help="Instance ID")
    init_parser.add_argument("--gen", type=int, required=True, help="Generation")
    init_parser.add_argument("--project", default=".", help="Project path")

    # Stats
    subparsers.add_parser("stats", help="Show substrate statistics")
    subparsers.add_parser("compact-stats", help="Show compact single-line stats")
    subparsers.add_parser("session-summary", help="Show session action summary")
    subparsers.add_parser("dashboard", help="Show detailed KONO dashboard")
    subparsers.add_parser("metabolism", help="Run fitness decay")
    subparsers.add_parser("consolidate", help="Mark permanent embeddings")

    # Evolve
    evolve_parser = subparsers.add_parser("evolve", help="Run evolution")
    evolve_parser.add_argument("--generations", type=int, default=1)

    # Recall (semantic search)
    recall_parser = subparsers.add_parser(
        "recall", help="Semantic memory recall (natural language query)"
    )
    recall_parser.add_argument("query", help="Natural language search query")
    recall_parser.add_argument("--limit", type=int, default=10, help="Max results")
    recall_parser.add_argument(
        "--dimension",
        choices=["factual", "procedural", "episodic", "emergent"],
        help="Filter by memory dimension"
    )
    recall_parser.add_argument(
        "--min-fitness", type=float, default=0.3, help="Minimum fitness threshold (default 0.3 — decay floor)"
    )
    recall_parser.add_argument(
        "--expand", dest="expand_spatial", action="store_true", default=True,
        help="Expand via KONO spatial neighbors (default)"
    )
    recall_parser.add_argument(
        "--no-expand", dest="expand_spatial", action="store_false",
        help="Disable spatial expansion"
    )
    recall_parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Machine-readable JSON output"
    )
    recall_parser.add_argument(
        "--project", dest="project_path", default=None,
        help="Filter to specific project path"
    )
    recall_parser.add_argument(
        "--all-projects", dest="all_projects", action="store_true",
        help="Search all projects (bypass project filtering)"
    )
    recall_parser.add_argument(
        "--platform", choices=["discord", "whatsapp", "slack", "web", "mcp"],
        help="Filter by source platform (provenance)"
    )
    recall_parser.add_argument(
        "--channel", dest="channel_id", default=None,
        help="Filter by source channel ID (provenance)"
    )
    recall_parser.add_argument(
        "--sender", dest="sender_id", default=None,
        help="Filter by sender ID — GRIP user ID or platform-specific (provenance)"
    )

    # Prune
    prune_parser = subparsers.add_parser("prune", help="Prune memories from substrate")
    prune_parser.add_argument("--project", dest="project_path", default=None)
    prune_parser.add_argument("--below-fitness", type=float, default=None)
    prune_parser.add_argument("--id", type=int, default=None)
    prune_parser.add_argument("--include-permanent", action="store_true")
    prune_parser.add_argument("--dry-run", action="store_true")

    # Store
    store_parser = subparsers.add_parser("store", help="Store an episodic memory")
    store_parser.add_argument("--domain", default="session-summary")
    store_parser.add_argument("--key", default=None)
    store_parser.add_argument("--content", required=True)
    store_parser.add_argument("--project", default=None)

    # Store session summary
    store_summary_parser = subparsers.add_parser("store-session-summary")
    store_summary_parser.add_argument("--gen", type=int, required=True)
    store_summary_parser.add_argument("--instance", required=True)
    store_summary_parser.add_argument("--msgs", type=int, default=0)
    store_summary_parser.add_argument("--achievement", required=True)
    store_summary_parser.add_argument("--project", default=None)

    # Search (spatial)
    search_parser = subparsers.add_parser("search", help="Direct spatial query")
    search_parser.add_argument("--x", type=float, required=True)
    search_parser.add_argument("--y", type=float, required=True)
    search_parser.add_argument("--z", type=float, default=5.0)
    search_parser.add_argument("--radius", type=float, default=10.0)
    search_parser.add_argument("--dimension", type=int, default=None)
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--min-fitness", type=float, default=0.0)
    search_parser.add_argument("--json", dest="json_output", action="store_true")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    substrate = MemorySubstrate()

    try:
        if args.command == "init-session":
            handle_init_session(substrate, args.instance, args.gen, args.project)
        elif args.command == "stats":
            handle_stats(substrate)
        elif args.command == "compact-stats":
            handle_compact_stats(substrate)
        elif args.command == "session-summary":
            handle_session_summary(substrate)
        elif args.command == "dashboard":
            handle_dashboard(substrate)
        elif args.command == "metabolism":
            handle_metabolism(substrate)
        elif args.command == "consolidate":
            handle_consolidate(substrate)
        elif args.command == "evolve":
            handle_evolve(substrate, args.generations)
        elif args.command == "recall":
            handle_recall(
                substrate,
                query=args.query,
                limit=args.limit,
                dimension=args.dimension,
                min_fitness=args.min_fitness,
                expand_spatial=args.expand_spatial,
                json_output=args.json_output,
                project_path=args.project_path,
                all_projects=args.all_projects,
                platform=args.platform,
                channel_id=args.channel_id,
                sender_id=args.sender_id,
            )
        elif args.command == "prune":
            handle_prune(
                substrate,
                project_path=args.project_path,
                below_fitness=args.below_fitness,
                memory_id=args.id,
                include_permanent=args.include_permanent,
                dry_run=args.dry_run,
            )
        elif args.command == "store":
            handle_store(substrate, content=args.content, project=args.project)
        elif args.command == "store-session-summary":
            handle_store_session_summary(
                substrate,
                gen=args.gen,
                instance=args.instance,
                msgs=args.msgs,
                achievement=args.achievement,
                project=args.project,
            )
        elif args.command == "search":
            handle_search(
                substrate,
                x=args.x,
                y=args.y,
                z=args.z,
                radius=args.radius,
                dimension=args.dimension,
                limit=args.limit,
                min_fitness=args.min_fitness,
                json_output=args.json_output,
            )
    finally:
        substrate.close()


if __name__ == "__main__":
    main()
