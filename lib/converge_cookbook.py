#!/usr/bin/env python3
"""Convergence Cookbook -- auto-generate reusable recipes from successful patterns.

Extracts templates from historical convergence runs that consistently produce
high scores, ranks them, and suggests the best recipe for a given task.

Usage:
    python3 -m lib.converge_cookbook recipes [--min-samples N] [--sort-by FIELD]
    python3 -m lib.converge_cookbook recipe <task_type>
    python3 -m lib.converge_cookbook best [--top-k N]
    python3 -m lib.converge_cookbook suggest <description>
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Recipe:
    """A reusable template extracted from convergence history."""

    id: str
    task_type: str
    gene_combination: list[str]
    avg_depth: float
    avg_score: float
    success_rate: float
    sample_size: int
    strategies: list[str]
    description: str


def _parse_run(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise a raw JSONL run dict. Returns None if unusable."""
    if not isinstance(raw, dict):
        return None
    task_type = raw.get("task_type")
    if not task_type:
        return None
    genes = sorted(raw.get("expressed_genes") or [])
    if not genes:
        return None
    scores = raw.get("final_dimension_scores") or {}
    if not scores:
        return None
    avg_score = sum(scores.values()) / len(scores) if scores else 0.0
    strategies: list[str] = []
    for d in raw.get("depths") or []:
        strat = d.get("strategy")
        if strat and strat not in strategies:
            strategies.append(strat)
    return {
        "task_type": task_type,
        "genes": tuple(genes),
        "satisfied": bool(raw.get("satisfied", False)),
        "depth": raw.get("depth_reached", 0),
        "score": avg_score,
        "strategies": strategies,
        "task": raw.get("task", ""),
    }


def _group_key(run: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    return (run["task_type"], run["genes"])


def _make_recipe_id(index: int) -> str:
    return f"R{index:03d}"


def extract_recipes(
    runs: list[dict[str, Any]], min_samples: int = 2
) -> list[Recipe]:
    """Group runs by task_type + gene_combination and compute stats."""
    parsed = [r for raw in runs if (r := _parse_run(raw)) is not None]
    groups: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
    for run in parsed:
        key = _group_key(run)
        groups.setdefault(key, []).append(run)

    recipes: list[Recipe] = []
    idx = 1
    for (task_type, genes), members in sorted(groups.items()):
        if len(members) < min_samples:
            continue
        successes = [m for m in members if m["satisfied"]]
        success_rate = len(successes) / len(members) * 100.0
        avg_depth = sum(m["depth"] for m in members) / len(members)
        avg_score = sum(m["score"] for m in members) / len(members)
        all_strategies: list[str] = []
        for m in members:
            for s in m["strategies"]:
                if s not in all_strategies:
                    all_strategies.append(s)
        gene_list = list(genes)
        desc = (
            f"{task_type} tasks using {' + '.join(gene_list)} "
            f"({len(members)} runs, {len(successes)} succeeded)"
        )
        recipes.append(
            Recipe(
                id=_make_recipe_id(idx),
                task_type=task_type,
                gene_combination=gene_list,
                avg_depth=round(avg_depth, 1),
                avg_score=round(avg_score, 1),
                success_rate=round(success_rate, 1),
                sample_size=len(members),
                strategies=all_strategies,
                description=desc,
            )
        )
        idx += 1
    return recipes


def rank_recipes(
    recipes: list[Recipe], sort_by: str = "success_rate"
) -> list[Recipe]:
    """Sort recipes by the given field (descending)."""
    valid_fields = {"success_rate", "avg_score", "avg_depth", "sample_size"}
    if sort_by not in valid_fields:
        raise ValueError(
            f"sort_by must be one of {valid_fields}, got '{sort_by}'"
        )
    return sorted(recipes, key=lambda r: getattr(r, sort_by), reverse=True)


def best_recipes(recipes: list[Recipe], top_k: int = 5) -> list[Recipe]:
    """Return the top K recipes by success rate."""
    ranked = rank_recipes(recipes, sort_by="success_rate")
    return ranked[:top_k]


def recipes_for_type(
    recipes: list[Recipe], task_type: str
) -> list[Recipe]:
    """Filter recipes by task type."""
    return [r for r in recipes if r.task_type == task_type]


def format_recipe(recipe: Recipe) -> str:
    """Format a single recipe as a human-readable card."""
    genes_str = " + ".join(recipe.gene_combination)
    strategies_str = ", ".join(f'"{s}"' for s in recipe.strategies)
    lines = [
        f"Recipe #{recipe.id} -- {recipe.task_type.title()} "
        f"(success: {recipe.success_rate}%, n={recipe.sample_size})",
        f"Genes: {genes_str}",
        f"Avg depth: {recipe.avg_depth} | Avg score: {recipe.avg_score}",
    ]
    if recipe.strategies:
        lines.append(f"Strategies: {strategies_str}")
    return "\n".join(lines)


def format_playbook(recipes: list[Recipe]) -> str:
    """Format multiple recipes as a playbook document."""
    if not recipes:
        return "No recipes available."
    header = f"Convergence Playbook ({len(recipes)} recipes)"
    separator = "=" * len(header)
    sections = [f"{header}\n{separator}"]
    for recipe in recipes:
        sections.append(format_recipe(recipe))
    return "\n\n".join(sections)


def _tokenise(text: str) -> set[str]:
    """Split text into lowercase tokens for keyword matching."""
    return {w.lower().strip(".,;:!?\"'()[]{}") for w in text.split() if len(w) > 2}


def suggest_recipe(
    task_description: str, recipes: list[Recipe]
) -> Recipe | None:
    """Match a task description to the best recipe by keyword overlap.

    Scoring: overlap count weighted by success_rate / 100.
    """
    if not recipes or not task_description.strip():
        return None
    task_tokens = _tokenise(task_description)
    if not task_tokens:
        return None
    best: Recipe | None = None
    best_score = -1.0
    for recipe in recipes:
        recipe_tokens = _tokenise(recipe.description)
        for gene in recipe.gene_combination:
            recipe_tokens |= _tokenise(gene)
        for strat in recipe.strategies:
            recipe_tokens |= _tokenise(strat)
        recipe_tokens |= _tokenise(recipe.task_type)
        overlap = len(task_tokens & recipe_tokens)
        if overlap == 0:
            continue
        weighted = overlap * (recipe.success_rate / 100.0)
        if weighted > best_score:
            best_score = weighted
            best = recipe
    return best


def load_runs(path: Path) -> list[dict[str, Any]]:
    """Load runs from a JSONL file."""
    runs: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                runs.append(json.loads(line))
    return runs


def _default_runs_path() -> Path:
    return Path.home() / ".claude" / "data" / "convergence-runs.jsonl"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="converge_cookbook",
        description="Auto-generate reusable recipes from convergence patterns.",
    )
    parser.add_argument(
        "--runs-file",
        type=Path,
        default=None,
        help="Path to JSONL runs file (default: ~/.claude/data/convergence-runs.jsonl)",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=2,
        help="Minimum sample size to include a recipe (default: 2)",
    )
    sub = parser.add_subparsers(dest="command")

    recipes_cmd = sub.add_parser("recipes", help="List all recipes")
    recipes_cmd.add_argument(
        "--sort-by",
        choices=["success_rate", "avg_score", "avg_depth", "sample_size"],
        default="success_rate",
        help="Sort field (default: success_rate)",
    )

    recipe_cmd = sub.add_parser("recipe", help="Show recipes for a task type")
    recipe_cmd.add_argument("task_type", help="Task type to filter by")

    best_cmd = sub.add_parser("best", help="Show top K recipes")
    best_cmd.add_argument(
        "--top-k", type=int, default=5, help="Number of top recipes (default: 5)"
    )

    suggest_cmd = sub.add_parser("suggest", help="Suggest a recipe for a task")
    suggest_cmd.add_argument("description", help="Task description")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    runs_path = args.runs_file or _default_runs_path()
    if not runs_path.exists():
        print(f"Runs file not found: {runs_path}", file=sys.stderr)
        return 1

    runs = load_runs(runs_path)
    all_recipes = extract_recipes(runs, min_samples=args.min_samples)

    if args.command == "recipes":
        ranked = rank_recipes(all_recipes, sort_by=args.sort_by)
        print(format_playbook(ranked))
    elif args.command == "recipe":
        filtered = recipes_for_type(all_recipes, args.task_type)
        if not filtered:
            print(f"No recipes found for task type: {args.task_type}")
            return 0
        ranked = rank_recipes(filtered, sort_by="success_rate")
        print(format_playbook(ranked))
    elif args.command == "best":
        top = best_recipes(all_recipes, top_k=args.top_k)
        print(format_playbook(top))
    elif args.command == "suggest":
        recipe = suggest_recipe(args.description, all_recipes)
        if recipe:
            print("Suggested recipe:\n")
            print(format_recipe(recipe))
        else:
            print("No matching recipe found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
