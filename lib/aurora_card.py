"""Overnight aurora — render the night's actual work as an ANSI aurora borealis.

V>> runs overnight RSI loops and wakes to dry metrics. This turns those
numbers into a moment of joy: an aurora whose FORM IS THE DATA. A quiet night
is a low calm green shimmer; a big night is tall vivid multi-colour curtains.
The art encodes the work — it is not decoration.

Design (the Goodhart-proof core):
- `render(metrics)` is PURE. The wave pattern is seeded DETERMINISTICALLY from
  the metric numbers (integer hashing + integer sine table — no float drift),
  so identical metrics -> BIT-IDENTICAL output (SHA-256-stable) and different
  metrics -> a visibly different aurora. No Date.now, no random, no time.
- `gather()` is IMPURE and fail-soft: any source erroring -> that metric is
  ``None`` (UNKNOWN), never a crash and never a silent zero.

Zero is not the same as unknown
-------------------------------
A counter has three outcomes, not two: it counted something, it counted
nothing, or it could not be asked at all. ``0`` means the second; ``None``
means the third, and the card prints ``?`` plus the reason rather than a
number. Collapsing "could not ask" into "counted nothing" is what made
/aurora tell the team "0 PRs merged" on the very nights `gh` was broken —
a confident, legible, wrong statement about somebody's week.

Pure stdlib. No network. No non-stdlib imports.

Used by:
- the /aurora slash command (skills/aurora/SKILL.md)
- ad-hoc: `python3 lib/aurora_card.py`              (gather + render)
          `python3 lib/aurora_card.py --metrics '{"prs":2,"commits":17}'`
"""

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --- ANSI ---------------------------------------------------------------------

_ESC = "\x1b["
RESET = f"{_ESC}0m"

# Night sky + star colours (256-colour, universally supported).
_SKY = 17       # deep navy
_STAR_DIM = 240
_STAR_BRIGHT = 255

# Classic aurora palette, top (high/cool-green) -> bottom (violet/magenta).
# Ordered so index 0 is the topmost band colour. 256-colour codes chosen to
# read as a green -> teal -> cyan -> violet -> magenta gradient.
_AURORA_PALETTE: Tuple[int, ...] = (
    48,    # spring green
    42,    # emerald
    43,    # teal-green
    44,    # teal
    45,    # cyan
    81,    # sky cyan
    111,   # periwinkle
    141,   # violet
    177,   # orchid
    171,   # magenta
)

# Block glyphs used for the curtains, lightest -> densest.
_BLOCKS: Tuple[str, ...] = ("░", "▒", "▓", "█")
# Vertical partial blocks for the shimmering top edge of each band.
_PARTIALS: Tuple[str, ...] = (" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")

_WIDTH = 56  # canvas width in cells

# Integer sine table (amplitude 1000), 0..359 degrees. Pure integers ->
# no floating-point drift -> bit-identical across machines/Python builds.
_SIN1000: Tuple[int, ...] = tuple(
    round(1000 * __import__("math").sin(__import__("math").radians(d)))
    for d in range(360)
)

# Poetic captions per intensity tier (curated, deterministic — NOT LLM-made).
_POEMS: Tuple[str, ...] = (
    "the sky kept watch over a quiet, resting night",          # 0 dormant
    "a soft green breath stirred above the sleeping hills",     # 1 calm
    "ribbons of light unfurled while the world slept on",       # 2 active
    "the heavens danced in full chorus through the dark",       # 3 brilliant
)
_TIER_NAMES: Tuple[str, ...] = ("dormant", "calm", "active", "brilliant")


_METRIC_KEYS: Tuple[str, ...] = ("prs", "commits", "tests", "hypotheses")


@dataclass(frozen=True)
class NightMetrics:
    """Normalised metrics for one night.

    Each field is a non-negative int, or ``None`` when that counter could not
    be measured at all. ``0`` and ``None`` are different facts and are rendered
    differently — see the module docstring.
    """

    prs: Optional[int] = 0
    commits: Optional[int] = 0
    tests: Optional[int] = 0
    hypotheses: Optional[int] = 0

    @property
    def total(self) -> int:
        # Weighted: a merged PR is a bigger event than a single commit.
        # An unmeasured counter contributes nothing to the ART, because you
        # cannot draw a number you do not have. The CAPTION still says so.
        return (
            _drawable(self.prs) * 5
            + _drawable(self.commits)
            + _drawable(self.tests) * 2
            + _drawable(self.hypotheses) * 3
        )

    @property
    def unmeasured(self) -> Tuple[str, ...]:
        """Names of the counters that could not be measured, in card order."""
        return tuple(k for k in _METRIC_KEYS if getattr(self, k) is None)

    def as_dict(self) -> Dict[str, Optional[int]]:
        return {k: getattr(self, k) for k in _METRIC_KEYS}


def _drawable(value: Optional[int]) -> int:
    """The value to draw for a counter — an unmeasured one draws as nothing."""
    return 0 if value is None else value


def _coerce(metrics: Dict) -> NightMetrics:
    """Coerce an arbitrary dict into NightMetrics.

    An ABSENT key means the caller never asked about that counter, which stays
    ``0`` — that is what ``render({"prs": 2})`` has always meant and the CLI
    relies on it. An EXPLICIT ``None`` means a source that was asked and could
    not answer, and is carried through as unknown rather than flattened to
    zero. Negatives and non-numeric junk still clamp to ``0``.
    """

    def _nn(key: str) -> Optional[int]:
        if key not in metrics:
            return 0
        raw = metrics[key]
        if raw is None:
            return None
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    return NightMetrics(**{k: _nn(k) for k in _METRIC_KEYS})


def _seed(m: NightMetrics) -> int:
    """Deterministic integer seed from the metric numbers."""
    key = f"{m.prs}:{m.commits}:{m.tests}:{m.hypotheses}".encode("utf-8")
    return int(hashlib.sha256(key).hexdigest(), 16)


def _tier(total: int) -> int:
    """Map total work to an intensity tier 0..3."""
    if total <= 0:
        return 0
    if total < 8:
        return 1
    if total < 25:
        return 2
    return 3


def _bands_for(tier: int) -> int:
    """Number of aurora bands (rows) by tier — taller curtains for big nights."""
    return (2, 3, 5, 7)[tier]


def _sin(deg: int) -> int:
    """Integer sine (amplitude 1000) for any integer degree."""
    return _SIN1000[deg % 360]


def _band_row(
    band_idx: int,
    bands: int,
    seed: int,
    tier: int,
) -> Tuple[str, int]:
    """Render one aurora band row. Returns (ansi_line, coloured_cell_count).

    The band's height profile across the width is an integer sine wave whose
    frequency + phase are derived from (seed, band_idx) — deterministic, so the
    curtain ripples reproducibly. Richer tiers add a second harmonic for fuller,
    more vivid curtains.
    """
    # Colour for this band: walk the palette top->bottom across the bands.
    if bands > 1:
        pal_idx = (band_idx * (len(_AURORA_PALETTE) - 1)) // (bands - 1)
    else:
        pal_idx = 0
    colour = _AURORA_PALETTE[pal_idx]

    # Wave parameters from the seed (kept small & integer).
    freq = 2 + ((seed >> (band_idx * 3)) & 0x3)        # 2..5 ripples
    phase = (seed >> (band_idx * 5)) % 360
    # Amplitude (in partial-glyph steps) grows with tier.
    amp = 1 + tier + (band_idx % 2)

    coloured = 0
    cells: List[str] = []
    for x in range(_WIDTH):
        angle = (x * freq * 360) // _WIDTH + phase
        wave = _sin(angle)
        if tier >= 2:
            # second harmonic for brilliance
            wave = (wave + _sin(angle * 2 + band_idx * 30)) // 2
        # height in 0..len(_PARTIALS)-1
        height = (len(_PARTIALS) - 1 + (wave * amp) // 1000)
        height = max(0, min(len(_PARTIALS) - 1, height))
        glyph = _PARTIALS[height]
        if glyph == " ":
            cells.append(" ")
            continue
        # Densest glyphs in the body of the curtain on richer tiers.
        if tier >= 3 and height >= len(_PARTIALS) - 2:
            glyph = _BLOCKS[3]
        coloured += 1
        cells.append(f"{_ESC}38;5;{colour}m{glyph}{RESET}")
    return "".join(cells), coloured


def _star_row(seed: int, row: int, density: int) -> Tuple[str, int]:
    """A row of night sky with deterministic scattered stars."""
    coloured = 0
    cells: List[str] = []
    for x in range(_WIDTH):
        h = (seed >> ((x + row * 7) % 64)) & 0xFF
        # density 0..3 -> threshold; more work = a few more stars.
        thresh = 250 - density * 6
        if h >= thresh and (x + row) % 3 == 0:
            star = "⋆" if (h & 1) else "·"
            colour = _STAR_BRIGHT if (h & 2) else _STAR_DIM
            cells.append(f"{_ESC}38;5;{colour}m{star}{RESET}")
            coloured += 1
        else:
            cells.append(" ")
    return "".join(cells), coloured


def coloured_cell_count(metrics: Dict) -> int:
    """Count of non-space, coloured cells in the rendered aurora.

    Pure helper used by tests to prove the art encodes the data (monotonic
    meaning): a bigger-work night yields strictly more coloured cells.
    """
    m = _coerce(metrics)
    seed = _seed(m)
    tier = _tier(m.total)
    bands = _bands_for(tier)
    total = 0
    # star rows (sky above the curtains)
    for r in range(2):
        _, c = _star_row(seed, r, tier)
        total += c
    for b in range(bands):
        _, c = _band_row(b, bands, seed, tier)
        total += c
    return total


def _cell(value: Optional[int]) -> str:
    """One counter as it appears in the tally — a number, or '?' if unmeasured.

    '?' is deliberately not a number: a reader can tell at a glance that this
    counter was never established, which a '0' actively hides.
    """
    return "?" if value is None else str(value)


def format_count(value: Optional[int], reason: str = "") -> str:
    """Render a counter for a plain-text audience (Slack, markdown, prose).

    ``0`` is a source that ran and counted nothing. ``None`` never borrows that
    rendering — it says so, and names the reason when one is available, so the
    reader has something to act on instead of a silently wrong number.
    """
    if value is not None:
        return str(value)
    return f"unknown ({reason})" if reason else "unknown"


def _unmeasured_lines(m: NightMetrics, reasons: Optional[Dict[str, str]]) -> List[str]:
    """Warning lines naming every counter that could not be measured.

    Empty when everything was measured — so a fully-measured card, including a
    genuinely quiet all-zero one, renders exactly as it always did.
    """
    missing = m.unmeasured
    if not missing:
        return []
    reasons = reasons or {}
    lines = [
        f"{_ESC}1;38;5;208m  ⚠ {len(missing)} of {len(_METRIC_KEYS)} counters "
        f"could not be measured — a '?' above is NOT a zero{RESET}"
    ]
    lines.extend(
        f"{_ESC}38;5;208m    · {key}: "
        f"{reasons.get(key) or 'no reason reported'}{RESET}"
        for key in missing
    )
    return lines


def _caption(m: NightMetrics) -> List[str]:
    """Two caption lines: the factual tally + a tier-poetic line."""
    tier = _tier(m.total)
    tally = (
        f"{_ESC}38;5;255m✦{RESET} While you slept, GRIP shipped "
        f"{_ESC}1;38;5;48m{_cell(m.prs)}{RESET} PRs "
        f"{_ESC}38;5;245m·{RESET} "
        f"{_ESC}1;38;5;45m{_cell(m.commits)}{RESET} commits "
        f"{_ESC}38;5;245m·{RESET} "
        f"{_ESC}1;38;5;141m{_cell(m.tests)}{RESET} tests "
        f"{_ESC}38;5;245m·{RESET} "
        f"{_ESC}1;38;5;177m{_cell(m.hypotheses)}{RESET} hypotheses "
        f"{_ESC}38;5;255m✦{RESET}"
    )
    poem = f"{_ESC}3;38;5;111m  {_POEMS[tier]}{RESET}"
    return [tally, poem]


def render(metrics: Dict, reasons: Optional[Dict[str, str]] = None) -> str:
    """PURE. Render the night's metrics as a full ANSI aurora string.

    Identical metrics -> bit-identical output. No randomness, no time, no I/O.

    ``metrics`` values may be ``None`` for a counter that could not be
    measured; ``reasons`` maps such a counter to a plain-English explanation
    that is printed beneath the tally. When nothing is unmeasured the output is
    byte-for-byte what it always was.
    """
    m = _coerce(metrics)
    seed = _seed(m)
    tier = _tier(m.total)
    bands = _bands_for(tier)

    lines: List[str] = []
    lines.append("")
    # Night sky with stars on top.
    for r in range(2):
        row, _ = _star_row(seed, r, tier)
        lines.append(row)
    # Aurora curtains (top band is coolest green, descending to magenta).
    for b in range(bands):
        row, _ = _band_row(b, bands, seed, tier)
        lines.append(row)
    # A thin horizon line beneath the curtains.
    lines.append(f"{_ESC}38;5;236m{'─' * _WIDTH}{RESET}")
    lines.append("")
    lines.extend(_caption(m))
    lines.extend(_unmeasured_lines(m, reasons))
    lines.append("")
    return "\n".join(lines)


# --- impure gather ------------------------------------------------------------


def _claude_dir() -> Path:
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class CommandOutcome:
    """What one shelled-out command did: whether it worked, and what it said.

    ``ok=False`` means the command could not be run, timed out, or exited
    non-zero — so whatever it would have told us is UNKNOWN. ``reason`` is a
    plain-English sentence a reader can act on.
    """

    ok: bool
    stdout: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Measured:
    """One counter: the number, or ``None`` when it could not be measured.

    ``value == 0`` is a source that ran and counted nothing — a genuinely quiet
    night. ``value is None`` is a source that could not be asked at all, and
    ``reason`` says why. These are different facts and never share a rendering.
    """

    value: Optional[int]
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None


def _failure_reason(cmd: List[str], returncode: int, stderr: str) -> str:
    """Plain-English reason for a command that exited non-zero."""
    reason = f"{cmd[0]} exited {returncode}"
    detail = (stderr or "").strip().splitlines()
    return f"{reason}: {detail[0][:120]}" if detail else reason


def _run(cmd: List[str], cwd: Path) -> CommandOutcome:
    """Run a command and report BOTH what it said and whether it worked.

    This used to return only stdout, with ``""`` on every failure, which made a
    command that could not run indistinguishable from one that ran and had
    nothing to say. Every caller then read that ``""`` as a count of zero, so a
    broken `gh` was published to the team as "0 PRs merged".
    """
    try:
        out = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError:
        return CommandOutcome(False, reason=f"{cmd[0]} is not installed")
    except subprocess.TimeoutExpired:
        return CommandOutcome(False, reason=f"{cmd[0]} timed out after 15s")
    except OSError as exc:
        return CommandOutcome(False, reason=f"{cmd[0]} could not run: {exc}")
    stdout = (out.stdout or "").strip()
    if out.returncode != 0:
        return CommandOutcome(
            False, stdout, _failure_reason(cmd, out.returncode, out.stderr)
        )
    return CommandOutcome(True, stdout)


def _since_iso(hours_back: int = 0) -> str:
    """Local time 'today since ~20:00' or N hours back, as a git --since arg."""
    now = _dt.datetime.now()
    if hours_back > 0:
        start = now - _dt.timedelta(hours=hours_back)
    else:
        # Overnight window: 20:00 of the previous calendar day if we are in the
        # morning, else 20:00 today.
        start = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if now.hour < 20:
            start = start - _dt.timedelta(days=1)
    return start.strftime("%Y-%m-%dT%H:%M:%S")


def _gather_commits(root: Path, since: str) -> Measured:
    """Commits that LANDED on origin/main in the window -- not local HEAD.

    Concurrent sessions ship via the temp-index plumbing pattern (commit-tree
    -> push a SHA), which never touches the working tree's HEAD, so a local
    `git log` sees zero of them. origin/main is the canonical landed-work ref.

    A window with no commits is a real ``0``; a `git log` that could not run is
    ``None``. Empty stdout alone cannot tell those apart, which is why the exit
    status is what decides.
    """
    res = _run(["git", "log", "origin/main", f"--since={since}", "--oneline"], root)
    if not res.ok:
        return Measured(None, res.reason)
    return Measured(len([ln for ln in res.stdout.splitlines() if ln.strip()]))


def _gather_tests(root: Path, since: str) -> Measured:
    """Lines added to tests/ on origin/main since the window (numstat additions).

    ``None`` when the `git log` itself failed; ``0`` when it ran and no test
    lines were added.
    """
    res = _run(
        ["git", "log", "origin/main", f"--since={since}", "--numstat", "--pretty=tformat:"],
        root,
    )
    if not res.ok:
        return Measured(None, res.reason)
    added = 0
    for ln in res.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) == 3 and parts[2].startswith("tests/"):
            try:
                added += int(parts[0])
            except ValueError:
                continue
    # Scale lines -> a "tests" magnitude (every ~25 added test lines ~= 1 unit).
    return Measured(added // 25)


def _gather_prs(root: Path, since: str) -> Measured:
    """Merged PRs in the window, or ``None`` when `gh` could not be asked.

    Window-aware: PRs merged on/after the window start. `since` can reach back
    to yesterday for an overnight / 24h window, so a bare `today` undercounts.

    `gh pr list --json number` prints ``[]`` when nothing matched, so a real
    zero always arrives as parseable JSON. Anything else — a non-zero exit, no
    output, unparseable output — is a count we never established.
    """
    since_date = since[:10]
    res = _run(
        [
            "gh", "pr", "list", "--state", "merged",
            "--search", f"merged:>={since_date}", "--json", "number",
        ],
        root,
    )
    if not res.ok:
        return Measured(None, res.reason)
    try:
        items = json.loads(res.stdout)
    except (json.JSONDecodeError, TypeError):
        return Measured(None, "gh printed output that is not valid JSON")
    if not isinstance(items, list):
        return Measured(None, "gh printed JSON that is not a list of PRs")
    return Measured(len(items))


def _touched_in_window(h: dict, since_date: str) -> bool:
    """True if any of a hypothesis's timestamp fields is on/after the window."""
    for key in ("registered", "verification_date", "created_at", "registered_at",
                "timestamp", "date", "verified_at", "updated_at"):
        stamp = str(h.get(key, ""))
        if stamp[:1].isdigit() and stamp[:10] >= since_date:
            return True
    return False


def _gather_hypotheses(root: Path, since: str) -> Measured:
    """Hypotheses registered OR resolved within the window -- both are work.

    The old version counted only confirmed-today, so a night that registered
    fresh falsifiable predictions (the common case) showed zero. Counts a
    hypothesis when any timestamp field lands on/after the window start.
    Reads the ledger directly (the engine's `status --json` is only a summary,
    with no per-hypothesis timestamps); dedupes by id so a register+verify pair
    counts once.

    A ledger that does not exist is a real ``0`` — nothing was ever registered.
    A ledger that exists but cannot be READ is ``None``: it may hold a hundred
    entries and we have no idea. A malformed line still contributes nothing,
    because the rest of the file was read successfully.
    """
    ledger = root / "learning" / "hypotheses.jsonl"
    if not ledger.exists():
        return Measured(0)
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return Measured(None, f"hypothesis ledger could not be read: {exc}")
    since_date = since[:10]  # YYYY-MM-DD; ISO timestamps compare lexically by date
    seen = set()
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and _touched_in_window(row, since_date):
            seen.add(str(row.get("id", "")) or raw[:24])
    return Measured(len(seen))


def safe_measure(label: str, fn) -> Measured:
    """Call a gatherer, turning a raised error into an honest unknown.

    The old blanket ``except Exception: metrics[key] = 0`` reintroduced the very
    conflation the gatherers now avoid, one level up: a gatherer that exploded
    was reported as a confident zero.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — a broken source must not crash the card
        return Measured(None, f"{label} could not be gathered: {str(exc)[:120]}")


def gather_measured(hours_back: int = 0) -> Dict[str, Measured]:
    """IMPURE. Collect tonight's metrics, each with its measured-or-why.

    Fail-soft: a source that errors yields ``Measured(None, reason)``, never a
    zero and never a crash.
    """
    root = _claude_dir()
    since = _since_iso(hours_back)
    # Best-effort refresh so origin/main reflects work other sessions just
    # pushed or merged. Fail-soft: offline / no remote -> count the ref as-is.
    _run(["git", "fetch", "origin", "main", "--quiet"], root)
    return {
        "commits": safe_measure("commits", lambda: _gather_commits(root, since)),
        "tests": safe_measure("test lines", lambda: _gather_tests(root, since)),
        "prs": safe_measure("merged PRs", lambda: _gather_prs(root, since)),
        "hypotheses": safe_measure(
            "hypotheses", lambda: _gather_hypotheses(root, since)
        ),
    }


def gather(hours_back: int = 0) -> Dict[str, Optional[int]]:
    """IMPURE. Tonight's metrics as plain numbers, ``None`` where unmeasured.

    The values-only view of :func:`gather_measured`, for callers that do not
    need the reasons. ``None`` is JSON-safe (``null``) and is never a zero.
    """
    return {key: m.value for key, m in gather_measured(hours_back).items()}


def reasons_of(measurements: Dict[str, Measured]) -> Dict[str, str]:
    """The failure reasons from a measurement map, keyed by counter name."""
    return {key: m.reason for key, m in measurements.items() if not m.known}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render the night's work as a deterministic ANSI aurora.",
    )
    parser.add_argument(
        "--metrics",
        help='render a given metrics dict, e.g. \'{"prs":2,"commits":17}\'',
    )
    parser.add_argument(
        "--hours-back",
        type=int,
        default=0,
        help="gather window in hours (default: overnight since ~20:00)",
    )
    args = parser.parse_args()
    if args.metrics:
        try:
            metrics = json.loads(args.metrics)
        except json.JSONDecodeError as exc:
            print(f"invalid --metrics JSON: {exc}")
            return 2
        print(render(metrics))
        return 0
    measurements = gather_measured(args.hours_back)
    print(render(
        {key: m.value for key, m in measurements.items()},
        reasons_of(measurements),
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
