"""
evolution_march.py — The Ascent of GRIP

Renders GRIP's genome as an ASCII "March of Progress" — the iconic
knuckle-walking-ape → upright-human-with-a-laptop silhouette — where each
figure's POSTURE is a deterministic projection of a gene's REAL measured
fitness. A low-fitness gene renders as a hunched ape; a high-fitness gene
stands upright and carries the MacBook. Sorted weakest → strongest, the
genome marches across the page, standing on a ground line.

The march is crowned with a live GRIP SELF-MONITOR header (generation, git
branch, dirty-tree count, genome health) and a full-name LEGEND beneath —
every gene's complete name, fitness, and a fitness bar — so the art is GRIP
watching itself, with nothing ellipsized.

Why this is delight bound to TRUTH (not theatre)
------------------------------------------------
The posture cannot flatter. If the genome's fitness is flat, every figure
shares one posture (an honest flat march); if a gene is weak, its figure
stays hunched no matter what. This is the shadow-metric / falsify-self
principle made visual — a delight surface that REFUSES to lie about how far
GRIP has actually evolved. The legend prints each gene's real name + fitness,
so every claim on screen is verifiable against the genome file.

No LLM, no network for the genome path — pure stdlib, deterministic. Same
genome in → same march out (SHA-stable). The self-monitor header reads live
git state (fail-soft) and is the only non-deterministic part, by design.

Public API
----------
Stage              — frozen (name, fitness)
AscentResult       — frozen render-ready result
Monitor            — frozen live self-monitoring snapshot (branch, dirty, …)
band_for(f)        — fitness → posture band (monotone)
posture_rank(f)    — fitness → 0..3 posture rank (monotone, for tests)
build(...)         — stages + genome metadata → AscentResult (sorts ascending)
render(result, colour, monitor) — AscentResult → terminal string
load_stages_from_genome(path, k) — real genome JSON → representative ladder
sample_representative(pairs, k)   — evenly-spaced ascent ladder incl. endpoints
gather_monitor(repo) — live git self-monitoring snapshot (fail-soft)

CLI
---
  python3 -m lib.evolution_march [--colour|--no-colour] [--json] [--genome PATH] [--figures K]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

# ---------------------------------------------------------------------------
# Posture bands — thresholds mirror lib/grip_haiku.py so the two delight
# surfaces describe the same genome with the same vocabulary.
# Ordered high → low; band_for returns the first threshold the fitness meets.
# ---------------------------------------------------------------------------
_BANDS: Tuple[Tuple[str, float], ...] = (
    ("thriving", 0.95),
    ("healthy", 0.85),
    ("stressed", 0.70),
    ("struggling", 0.0),
)

# Posture rank: struggling=0 (most hunched) … thriving=3 (upright + laptop).
_RANK: Dict[str, int] = {"struggling": 0, "stressed": 1, "healthy": 2, "thriving": 3}

# The laptop the upright figure carries — V>>'s March-of-Progress image made
# literal. Present iff thriving; its presence/absence is a tested invariant.
LAPTOP_GLYPH = "[#]"

# ASCII figures, one per band. Raw art — _normalize() pads each to a common
# width/height at render time, so exact column counts here are not load-bearing.
# The progression is the point: head-down knuckle-walk → crouch → spine forms
# → upright carrying the laptop.
FIGURES: Dict[str, List[str]] = {
    "struggling": [
        "  __   ",
        " <oo>  ",
        " /|\\__ ",
        " _/ \\_ ",
    ],
    "stressed": [
        "  __   ",
        " (oo)  ",
        " /|_   ",
        " / \\   ",
    ],
    "healthy": [
        " (oo)  ",
        " /|\\_  ",
        "  |    ",
        " / \\   ",
    ],
    "thriving": [
        " (oo)  ",
        " /|\\" + LAPTOP_GLYPH,
        "  |    ",
        " / \\   ",
    ],
}

_FIG_H = 4  # every figure normalises to this many rows (feet on the last row)


def _fig_width(fig: List[str]) -> int:
    """Widest line in a figure (single-level scan)."""
    return max(len(line) for line in fig)


# Precomputed once at import over the constant FIGURES — keeps render-time
# width maths a flat dict lookup instead of a nested scan.
_FIG_WIDTHS: Dict[str, int] = {band: _fig_width(fig) for band, fig in FIGURES.items()}

_VERDICTS: Dict[str, str] = {
    "thriving": "GRIP stands upright — the genome reached the MacBook.",
    "healthy": "GRIP is rising — most genes near upright, a few still stooped.",
    "stressed": "GRIP is mid-climb — the spine is forming, weak genes drag.",
    "struggling": "GRIP is hunched — the ascent has barely begun.",
}

_COLOURS: Dict[str, str] = {
    "thriving": "\033[92m",   # bright green
    "healthy": "\033[32m",    # green
    "stressed": "\033[33m",   # yellow
    "struggling": "\033[31m",  # red
}
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"

_WIDTH = 62  # banner / rule width


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Stage:
    """One figure in the march: a real gene and its real measured fitness."""

    name: str
    fitness: float


@dataclass(frozen=True)
class AscentResult:
    stages: Tuple[Stage, ...]      # sorted ascending by fitness
    gen: int
    gene_count: int                # total genes in the genome, not just shown
    mean_fitness: float
    weakest: Stage
    strongest: Stage
    mood: str                      # band of the mean fitness
    verdict: str


@dataclass(frozen=True)
class Monitor:
    """A cheap live GRIP self-monitoring snapshot, woven into the header."""

    branch: str
    dirty: int
    hypotheses: str = ""


# ---------------------------------------------------------------------------
# Core logic — posture is a pure, monotone function of fitness
# ---------------------------------------------------------------------------
def band_for(fitness: float) -> str:
    """Map a fitness value to a posture band. Monotone non-decreasing."""
    for name, threshold in _BANDS:
        if fitness >= threshold:
            return name
    return "struggling"


def posture_rank(fitness: float) -> int:
    """Map fitness to a posture rank 0..3 (higher = more upright)."""
    return _RANK[band_for(fitness)]


def _even_indices(n: int, k: int) -> List[int]:
    """k evenly-spaced indices over range(n), always incl. first and last.

    Deterministic. Collisions from rounding are de-duplicated, so the result
    may be shorter than k for small n — acceptable (we never invent figures).
    """
    if k <= 0 or n <= 0:
        return []
    if k >= n:
        return list(range(n))
    if k == 1:
        return [0]
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def sample_representative(
    pairs: Sequence[Tuple[str, float]], k: int
) -> List[Stage]:
    """Pick a representative ascent ladder from (name, fitness) pairs.

    Sorts ascending by fitness, then samples k evenly-spaced genes including
    the weakest (index 0) and strongest (index -1). Every returned figure is
    a REAL gene with its REAL fitness — sampling chooses which real genes to
    show, it never fabricates values.
    """
    ordered = sorted(pairs, key=lambda p: p[1])
    return [Stage(ordered[i][0], ordered[i][1]) for i in _even_indices(len(ordered), k)]


def build(
    stages: Sequence[Stage],
    gen: int,
    gene_count: int,
    mean_fitness: float,
) -> AscentResult:
    """Assemble an AscentResult. Sorts stages ascending so the march reads
    weakest → strongest regardless of input order."""
    if not stages:
        raise ValueError("at least one stage is required to render the ascent")
    ordered = tuple(sorted(stages, key=lambda s: s.fitness))
    mood = band_for(mean_fitness)
    return AscentResult(
        stages=ordered,
        gen=gen,
        gene_count=gene_count,
        mean_fitness=mean_fitness,
        weakest=ordered[0],
        strongest=ordered[-1],
        mood=mood,
        verdict=_VERDICTS[mood],
    )


# ---------------------------------------------------------------------------
# Rendering — pure string composition; colour added after width maths so ANSI
# codes never corrupt column alignment. All iteration is kept flat (one level)
# via small helpers + a zip transpose for the figure grid.
# ---------------------------------------------------------------------------
def _normalize(fig: List[str], width: int) -> List[str]:
    """Pad a figure to exactly _FIG_H rows and `width` columns (feet last)."""
    rows = list(fig)
    while len(rows) < _FIG_H:
        rows.insert(0, "")          # top-pad so feet stay on the baseline
    rows = rows[-_FIG_H:]
    return [line.ljust(width) for line in rows]


def _colourize(lines: List[str], band: str, colour: bool) -> List[str]:
    """Wrap each (already width-normalised) line in the band's colour."""
    if not colour:
        return lines
    code = _COLOURS[band]
    return [code + line + _RESET for line in lines]


def _column(stage: Stage, colour: bool, col_w: int) -> List[str]:
    """One figure as a list of _FIG_H ready-to-emit lines."""
    band = band_for(stage.fitness)
    return _colourize(_normalize(FIGURES[band], col_w), band, colour)


def _centre(text: str, width: int) -> str:
    return text.center(width)


def _bar(fitness: float, width: int = 10) -> str:
    """A fitness bar: filled blocks proportional to fitness, rest shaded."""
    filled = max(0, min(width, round(fitness * width)))
    return "█" * filled + "░" * (width - filled)


def _column_width(result: AscentResult) -> int:
    widths = [_FIG_WIDTHS[band_for(s.fitness)] for s in result.stages]
    widths.append(6)
    return max(widths)


def _figure_rows(result: AscentResult, colour: bool, col_w: int) -> List[str]:
    columns = [_column(s, colour, col_w) for s in result.stages]
    return ["   " + "  ".join(cells) for cells in zip(*columns)]


def _index_row(n: int, col_w: int, colour: bool) -> str:
    d = _DIM if colour else ""
    r = _RESET if colour else ""
    cells = [_centre(str(i + 1), col_w) for i in range(n)]
    return f"   {d}" + "  ".join(cells) + r


def _ground_line(n: int, col_w: int, colour: bool) -> str:
    d = _DIM if colour else ""
    r = _RESET if colour else ""
    span = n * col_w + (n - 1) * 2
    return f"   {d}" + "▁" * span + r


def _self_monitor_lines(result: AscentResult, monitor: Monitor | None, colour: bool) -> List[str]:
    d = _DIM if colour else ""
    r = _RESET if colour else ""
    pct = f"{result.mean_fitness * 100:.1f}%"
    gen_line = f"  {d}self-monitor{r}  Gen {result.gen}"
    if monitor is not None:
        gen_line += f" · {monitor.branch} · {monitor.dirty} dirty"
        if monitor.hypotheses:
            gen_line += f" · {monitor.hypotheses}"
    genome_line = f"  {d}genome{r}        {result.gene_count} genes · mean {pct} · {result.mood}"
    return [gen_line, genome_line]


def _legend_line(idx: int, stage: Stage, name_w: int, colour: bool) -> str:
    band = band_for(stage.fitness)
    c = _COLOURS[band] if colour else ""
    r = _RESET if colour else ""
    d = _DIM if colour else ""
    num = str(idx).rjust(2)
    name = stage.name.ljust(name_w)          # FULL name, never truncated
    # name + bar + band-label all wear the gene's figure colour, so each legend
    # row colour-matches its silhouette in the march above.
    return f"  {d}{num}{r}  {c}{name}{r}  {stage.fitness:.2f}  {c}{_bar(stage.fitness)}{r}  {c}{band}{r}"


def _legend(result: AscentResult, colour: bool) -> List[str]:
    name_w = max(len(s.name) for s in result.stages)
    return [_legend_line(i + 1, s, name_w, colour) for i, s in enumerate(result.stages)]


def render(result: AscentResult, colour: bool = True, monitor: Monitor | None = None) -> str:
    b = _BOLD if colour else ""
    d = _DIM if colour else ""
    r = _RESET if colour else ""

    col_w = _column_width(result)
    n = len(result.stages)
    banner = " THE ASCENT OF GRIP ".center(_WIDTH, "═")
    rule = "═" * _WIDTH

    out: List[str] = [f"{b}{banner}{r}"]
    out.extend(_self_monitor_lines(result, monitor, colour))
    out.append(f"  {d}from knuckle-drag to MacBook — posture = real measured fitness{r}")
    out.append("")
    out.extend(_figure_rows(result, colour, col_w))
    out.append(_index_row(n, col_w, colour))
    out.append(_ground_line(n, col_w, colour))
    out.append("")
    out.append(f"  {d}the climb — weakest → strongest (full names, no truncation){r}")
    out.extend(_legend(result, colour))
    out.extend(
        [
            "",
            f"  {d}weakest  {r}{result.weakest.name} ({result.weakest.fitness:.2f}) {d}← next sprint{r}",
            f"  {d}strongest{r} {result.strongest.name} ({result.strongest.fitness:.2f})",
            f"  {b}{result.verdict}{r}",
            f"{d}{rule}{r}",
        ]
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# HTML rendering — colour that survives outside the terminal (open in a browser)
# ---------------------------------------------------------------------------
_ANSI_TO_HTML: Dict[str, str] = {
    "\033[92m": '<span class="thriving">',
    "\033[32m": '<span class="healthy">',
    "\033[33m": '<span class="stressed">',
    "\033[31m": '<span class="struggling">',
    "\033[2m": '<span class="dim">',
    "\033[1m": '<span class="bold">',
    "\033[0m": "</span>",
}

_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>The Ascent of GRIP</title>
<style>
  body { background:#0b0e14; color:#c9d1d9; margin:0; padding:28px;
         font:14px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  pre  { margin:0; white-space:pre; }
  .struggling { color:#ff6a5f; }
  .stressed   { color:#e3b341; }
  .healthy    { color:#3fb950; }
  .thriving   { color:#56d364; }
  .dim        { color:#6e7681; }
  .bold       { font-weight:700; color:#ffffff; }
</style></head>
<body><pre>__BODY__</pre></body></html>
"""


def ansi_to_html(text: str) -> str:
    """Translate the known ANSI colour codes to <span> tags, HTML-escaping the
    surrounding text. The code set is small + fixed (see _COLOURS) and the
    renders never nest codes, so a flat open/close translation is sound."""
    import html
    import re

    pattern = "\033" + r"\[[0-9]+m"   # ESC char + the rest (escape-safe match)
    out: List[str] = []
    pos = 0
    for match in re.finditer(pattern, text):
        out.append(html.escape(text[pos:match.start()]))
        out.append(_ANSI_TO_HTML.get(match.group(0), ""))
        pos = match.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def render_html(result: AscentResult, monitor: Monitor | None = None) -> str:
    """A self-contained colour HTML document — the march that survives a browser."""
    body = ansi_to_html(render(result, colour=True, monitor=monitor))
    return _HTML_TEMPLATE.replace("__BODY__", body)


# ---------------------------------------------------------------------------
# Genome loading + live self-monitoring (I/O boundary, thin + fail-soft)
# ---------------------------------------------------------------------------
def _fitness_pairs(genes: Dict) -> List[Tuple[str, float]]:
    return [
        (name, float(v["fitness"]))
        for name, v in genes.items()
        if isinstance(v, dict) and "fitness" in v
    ]


def load_stages_from_genome(
    genome_path: Path, figures: int = 7
) -> Tuple[List[Stage], int, int, float]:
    """Load a genome JSON and return (sample, gen, gene_count, mean_fitness).

    Reads every gene's real fitness, samples a representative ascent ladder of
    `figures` genes, and reports the gen + total gene count + mean over ALL
    genes (not just the shown sample) so the header is honest.
    """
    data = json.loads(genome_path.read_text())
    genes = data.get("genes", data)
    pairs = _fitness_pairs(genes if isinstance(genes, dict) else {})
    if not pairs:
        raise ValueError(f"no genes with fitness found in {genome_path}")
    gen = int(data.get("generation", data.get("exported_from_gen", 0)) or 0)
    mean = sum(f for _, f in pairs) / len(pairs)
    return sample_representative(pairs, figures), gen, len(pairs), mean


def gather_monitor(repo: Path | None = None) -> Monitor:
    """Collect a cheap live self-monitoring snapshot (git branch + dirty count).

    Fail-soft: any git error degrades to a safe placeholder rather than raising,
    so the delight surface never breaks in a non-git or restricted environment.
    """
    import subprocess

    root = str(repo or Path(__file__).resolve().parent.parent)

    def _git(args: List[str]) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", root, *args],
                capture_output=True, text=True, timeout=3,
            )
            return proc.stdout.strip() if proc.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]) or "?"
    porcelain = _git(["status", "--porcelain"])
    dirty = sum(1 for line in porcelain.splitlines() if line.strip())
    return Monitor(branch=branch, dirty=dirty)


def _distinct_fitness(path: Path) -> int:
    """Count distinct (3-dp) fitness values in a genome file; -1 on error."""
    try:
        genes = json.loads(path.read_text()).get("genes", {})
    except (OSError, ValueError):
        return -1
    pairs = _fitness_pairs(genes if isinstance(genes, dict) else {})
    return len({round(f, 3) for _, f in pairs})


def _default_genome() -> Path:
    """Pick the most ascent-worthy real genome: most distinct fitness values.

    Prefers a genome with genuine spread (a varied march) over a flat export,
    skipping `_`-prefixed bred/temp genomes. Falls back to genome-shared.json.
    Deterministic (sorted candidates, max-spread wins, ties → first path).
    """
    base = Path(__file__).resolve().parent.parent / "data"
    shared = base / "genome-shared.json"
    candidates = sorted(p for p in base.glob("genomes/*.json") if not p.name.startswith("_"))
    candidates.append(shared)
    best: Path | None = None
    best_spread = -1
    for path in candidates:
        spread = _distinct_fitness(path)
        if spread > best_spread:
            best_spread, best = spread, path
    return best or shared


# ---------------------------------------------------------------------------
# Colour resolution (pure + testable) — completes the colour input domain:
# force on, force off, the NO_COLOR / FORCE_COLOR conventions, else TTY detect.
# ---------------------------------------------------------------------------
def resolve_colour(*, force_on: bool, force_off: bool, env: Dict, isatty: bool) -> bool:
    """Decide whether to emit ANSI colour. First match wins:

    1. --no-colour                                  → off
    2. --colour                                     → on
    3. NO_COLOR present + non-empty (no-color.org)  → off
    4. FORCE_COLOR truthy                           → on
    5. otherwise                                    → stdout.isatty()
    """
    if force_off:
        return False
    if force_on:
        return True
    if env.get("NO_COLOR"):
        return False
    if str(env.get("FORCE_COLOR", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return bool(isatty)


# ---------------------------------------------------------------------------
# Open-in-editor — the march displays cleanly in a monospace text editor
# (no ANSI escapes, no terminal line-wrap). Cross-platform, fail-soft.
# ---------------------------------------------------------------------------
def editor_command(path: Path | str, system: str) -> List[str]:
    """The platform command that opens `path` in the default TEXT editor.

    macOS `open -t` and Windows `notepad` target a text editor specifically;
    Linux uses xdg-open (the user's default .txt handler).
    """
    p = str(path)
    if system.startswith("darwin"):
        return ["open", "-t", p]
    if system.startswith("win"):
        return ["notepad", p]
    return ["xdg-open", p]


def browser_command(path: Path | str, system: str) -> List[str]:
    """The platform command that opens `path` in the default app (a browser for HTML)."""
    p = str(path)
    if system.startswith("darwin"):
        return ["open", p]
    if system.startswith("win"):
        return ["cmd", "/c", "start", "", p]
    return ["xdg-open", p]


def _launch(cmd: List[str], runner=None) -> List[str]:
    """Run an opener command fail-soft; return the command either way."""
    import subprocess

    run = runner or (lambda c: subprocess.run(c, timeout=10))
    try:
        run(cmd)
    except (OSError, subprocess.SubprocessError):
        pass
    return cmd


def open_in_editor(path: Path | str, *, system: str | None = None, runner=None) -> List[str]:
    """Open `path` in the default text editor (TextEdit / notepad / xdg-open). Fail-soft."""
    return _launch(editor_command(path, system or sys.platform), runner)


def open_in_browser(path: Path | str, *, system: str | None = None, runner=None) -> List[str]:
    """Open `path` in the default app — a browser for .html. Fail-soft."""
    return _launch(browser_command(path, system or sys.platform), runner)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _emit(result: AscentResult, args, genome: Path) -> None:
    """Render `result` to the output the flags selected (json / html / open /
    terminal). Kept separate so main() stays a thin coordinator (KISS)."""
    if args.json:
        import dataclasses

        payload = dataclasses.asdict(result)
        payload["genome"] = str(genome)
        print(json.dumps(payload, indent=2, default=str))
        return
    if args.html:
        doc = render_html(result, monitor=gather_monitor())
        out_path = Path(tempfile.gettempdir()) / "grip-ascent.html"
        out_path.write_text(doc, encoding="utf-8")
        cmd = open_in_browser(out_path)
        print(f"The Ascent of GRIP (colour HTML) → {out_path}")
        print(f"opening in your browser: {' '.join(cmd)}")
        return
    if args.open:
        # Plain text (no ANSI) so it renders cleanly in any text editor.
        text = render(result, colour=False, monitor=gather_monitor())
        out_path = Path(tempfile.gettempdir()) / "grip-ascent.txt"
        out_path.write_text(text, encoding="utf-8")
        cmd = open_in_editor(out_path)
        print(f"The Ascent of GRIP → {out_path}")
        print(f"opening in your text editor: {' '.join(cmd)}")
        return
    use_colour = resolve_colour(
        force_on=args.colour, force_off=args.no_colour,
        env=os.environ, isatty=sys.stdout.isatty(),
    )
    print(render(result, colour=use_colour, monitor=gather_monitor()))


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The Ascent of GRIP — genome as March of Progress")
    colour = parser.add_mutually_exclusive_group()
    colour.add_argument(
        "--colour", "--color", dest="colour", action="store_true",
        help="Force colour on (e.g. piping to `less -R` or any non-TTY)",
    )
    colour.add_argument(
        "--no-colour", "--no-color", dest="no_colour", action="store_true",
        help="Force plain text (no colour)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of art")
    parser.add_argument("--genome", default=None, help="Path to genome JSON (default: richest available)")
    parser.add_argument("--figures", type=int, default=7, help="Number of figures in the march (default 7)")
    parser.add_argument(
        "--open", action="store_true",
        help="Write the art to a temp .txt and open it in the default text editor (clean, no terminal mangling)",
    )
    parser.add_argument(
        "--html", action="store_true",
        help="Write a colour HTML version and open it in your browser (colour + clean alignment)",
    )
    args = parser.parse_args(argv)

    genome = Path(args.genome) if args.genome else _default_genome()
    try:
        stages, gen, gene_count, mean = load_stages_from_genome(genome, args.figures)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    _emit(build(stages, gen, gene_count, mean), args, genome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
