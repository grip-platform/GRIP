"""
grip_haiku.py — GRIP Genome Haiku

Turns the live genome fitness snapshot into a 3-line haiku + one-line health
verdict. No LLM, no network — deterministic from genome-shared.json.

Public API
----------
HaikuResult  — dataclass with haiku lines + metadata
generate(genome_path)  -> HaikuResult
render(result, colour=True)  -> str   (terminal-ready string)

CLI
---
  python3 -m lib.grip_haiku [--no-colour] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

# ---------------------------------------------------------------------------
# Haiku corpus — (5-syl, 7-syl, 5-syl) triplets keyed by mood tier
# Fitness bands: thriving>=0.95, healthy>=0.85, stressed>=0.70, struggling<0.70
# ---------------------------------------------------------------------------
_LINES: dict[str, list[tuple[str, str, str]]] = {
    "thriving": [
        ("genome sings in tune",    "every gene a bright lantern",  "no path leads astray"),
        ("all circuits alive",      "fitness blooms across the map", "the loop knows no end"),
        ("strength in every cell",  "recursive growth hums forward", "roots hold, branches reach"),
    ],
    "healthy": [
        ("most genes hold their light", "a few still find their footing", "the whole leans upward"),
        ("steady pulse tonight",       "knowledge layers on knowledge", "the river runs clear"),
        ("progress, not perfection",   "each wave adds one more insight", "the tide never stills"),
    ],
    "stressed": [
        ("some threads still fray here", "the loom keeps pulling them back", "pattern not yet set"),
        ("rough edges remain",          "the chisel has more work left",  "stone becoming form"),
        ("unfinished and real",         "the scaffold hides the building", "trust what lies beneath"),
    ],
    "struggling": [
        ("the root seeks water",   "deep soil holds what leaves forget", "patience, then the bloom"),
        ("friction is the cost",   "of learning what does not work",    "gold hides in the dross"),
        ("low tide reveals rocks", "the sailor charts them and sails",   "knowledge born of loss"),
    ],
}

_VERDICTS: dict[str, str] = {
    "thriving":   "GRIP is thriving — genome at peak fitness.",
    "healthy":    "GRIP is healthy — strong signal across most genes.",
    "stressed":   "GRIP is stressed — a few genes need a sprint.",
    "struggling": "GRIP is struggling — time to run /evo and converge.",
}

_COLOURS: dict[str, str] = {
    "thriving":   "\033[92m",   # bright green
    "healthy":    "\033[32m",   # green
    "stressed":   "\033[33m",   # yellow
    "struggling": "\033[31m",   # red
}
_RESET = "\033[0m"
_DIM   = "\033[2m"
_BOLD  = "\033[1m"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HaikuResult:
    lines: tuple[str, str, str]
    mood: str
    mean_fitness: float
    gene_count: int
    gen: int
    top_gene: str
    top_fitness: float
    bot_gene: str
    bot_fitness: float
    verdict: str


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------
def _mood(mean: float) -> str:
    if mean >= 0.95:
        return "thriving"
    if mean >= 0.85:
        return "healthy"
    if mean >= 0.70:
        return "stressed"
    return "struggling"


def _pick(pool: list, seed: int) -> tuple[str, str, str]:
    """Deterministic pick from pool using seed (gen number)."""
    return pool[seed % len(pool)]


def generate(genome_path: Path) -> HaikuResult:
    data = json.loads(genome_path.read_text())
    genes = data.get("genes", {})
    if not genes:
        raise ValueError(f"No genes found in {genome_path}")

    fitnesses = {k: v["fitness"] for k, v in genes.items()}
    mean_f    = sum(fitnesses.values()) / len(fitnesses)
    gen       = data.get("exported_from_gen", 0)
    mood      = _mood(mean_f)

    top_gene = max(fitnesses, key=fitnesses.get)
    bot_gene = min(fitnesses, key=fitnesses.get)

    lines   = _pick(_LINES[mood], gen)
    verdict = _VERDICTS[mood]

    return HaikuResult(
        lines        = lines,
        mood         = mood,
        mean_fitness = mean_f,
        gene_count   = len(genes),
        gen          = gen,
        top_gene     = top_gene,
        top_fitness  = fitnesses[top_gene],
        bot_gene     = bot_gene,
        bot_fitness  = fitnesses[bot_gene],
        verdict      = verdict,
    )


def render(result: HaikuResult, colour: bool = True) -> str:
    c  = _COLOURS[result.mood] if colour else ""
    r  = _RESET if colour else ""
    d  = _DIM   if colour else ""
    b  = _BOLD  if colour else ""

    bar = "─" * 42
    pct = f"{result.mean_fitness * 100:.1f}%"

    out: List[str] = [
        f"{d}{bar}{r}",
        f"  {b}GRIP Genome Haiku{r}  {d}Gen {result.gen} · {result.gene_count} genes · fitness {pct}{r}",
        "",
        f"  {c}{result.lines[0]}{r}",
        f"  {c}{result.lines[1]}{r}",
        f"  {c}{result.lines[2]}{r}",
        "",
        f"  {d}strongest  {r}{result.top_gene} ({result.top_fitness:.2f})",
        f"  {d}weakest   {r}{result.bot_gene} ({result.bot_fitness:.2f})",
        "",
        f"  {b}{result.verdict}{r}",
        f"{d}{bar}{r}",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _default_genome() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "genome-shared.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GRIP Genome Haiku")
    parser.add_argument("--no-colour", action="store_true", help="Plain text output")
    parser.add_argument("--json",      action="store_true", help="Emit JSON instead")
    parser.add_argument("--genome",    default=str(_default_genome()), help="Path to genome JSON")
    args = parser.parse_args(argv)

    try:
        result = generate(Path(args.genome))
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        import dataclasses
        print(json.dumps(dataclasses.asdict(result), indent=2))
    else:
        use_colour = not args.no_colour and sys.stdout.isatty()
        print(render(result, colour=use_colour))
    return 0


if __name__ == "__main__":
    sys.exit(main())
