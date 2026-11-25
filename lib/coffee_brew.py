#!/usr/bin/env python3
"""coffee_brew.py — GRIP's hidden barista.

Reads DELIGHT v2 telemetry, git state, and session signals to brew
a personalised coffee diagnostic. The coffee IS the session, expressed
through barista metaphors.

Easter egg: /coffee
Loyalty tracking: learning/coffee_log.jsonl

Usage:
    python3 lib/coffee_brew.py                # Brew a coffee
    python3 lib/coffee_brew.py --secret       # Secret menu
    python3 lib/coffee_brew.py --decaf        # Minimal output
    python3 lib/coffee_brew.py --history      # Loyalty card + sparkline
"""

import hashlib
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DELIGHT_FILE = CLAUDE_DIR / "learning" / "delight_v2.jsonl"
COFFEE_LOG = CLAUDE_DIR / "learning" / "coffee_log.jsonl"
CONVERGE_METRICS = CLAUDE_DIR / "learning" / "converge_metrics.jsonl"


# =========================================================================
# Coffee Types — each a personality, not just a name
# =========================================================================

MENU = {
    "Espresso": {
        "desc": "Short. Intense. No wasted drops.",
        "when": "High velocity, low context usage",
        "cup": "small",
        "fill": "dark",
    },
    "Ristretto": {
        "desc": "Maximum extraction from minimum volume.",
        "when": "Lots of commits, minimal context burn",
        "cup": "small",
        "fill": "dark",
    },
    "Americano": {
        "desc": "Steady, dependable, gets the job done.",
        "when": "Default — moderate pace, moderate depth",
        "cup": "medium",
        "fill": "medium",
    },
    "Cortado": {
        "desc": "Perfectly balanced. As all things should be.",
        "when": "High DELIGHT composite, good flow",
        "cup": "medium",
        "fill": "light",
    },
    "Cold Brew": {
        "desc": "Slow-steeped patience. Deep and smooth.",
        "when": "Marathon session (3h+), steady pace",
        "cup": "large",
        "fill": "dark",
    },
    "Turkish Coffee": {
        "desc": "Thick, complex, and full of grounds.",
        "when": "High friction, many gate blocks",
        "cup": "small",
        "fill": "dark",
    },
    "Matcha Latte": {
        "desc": "Fresh start. Green and full of possibility.",
        "when": "New session, barely started",
        "cup": "medium",
        "fill": "light",
    },
    "Long Black": {
        "desc": "Stretched to the limit but still holding flavour.",
        "when": "Context usage > 80%",
        "cup": "large",
        "fill": "dark",
    },
    "Irish Coffee": {
        "desc": "It's late. You've earned something stronger.",
        "when": "Late night + long session + low delight",
        "cup": "medium",
        "fill": "medium",
    },
    "Flat White": {
        "desc": "Velvet microfoam over double shot. Refined.",
        "when": "High flow state, clean shadows",
        "cup": "medium",
        "fill": "light",
    },
    "Affogato": {
        "desc": "Drowning in it. Deliciously overwhelmed.",
        "when": "Secret menu — context breakdown",
        "cup": "small",
        "fill": "dark",
    },
    "Pour Over": {
        "desc": "Methodical. Each drop placed with intention.",
        "when": "Lots of reads, few writes — research mode",
        "cup": "medium",
        "fill": "light",
    },
}


# =========================================================================
# ASCII Art — the cups
# =========================================================================

STEAM_PATTERNS = [
    ["    ) )  )", "   ( (  ( ", "    ) )  )"],
    ["    ~ ~ ~  ", "   ) ) ) ) ", "    ~ ~ ~  "],
    ["     . * . ", "    * . *  ", "     . * . "],
    ["    ' ` ' `", "   ` ' ` ' ", "    ' ` ' `"],
    ["    .:*:. ", "   *:.:*  ", "    .:*:. "],
]

STEAM_ICED = [
    ["    * . *  ", "     . * . ", "    * . *  "],
]


def _steam(iced=False):
    """Pick a random steam pattern. Iced drinks get ice crystals."""
    patterns = STEAM_ICED if iced else STEAM_PATTERNS
    return random.choice(patterns)


def _cup_small(fill_char="@", label="GRIP"):
    """Espresso/Ristretto/Turkish cup."""
    return [
        f"    .---------.",
        f"    |  {fill_char * 7}  |",
        f"    |  {fill_char * 7}  |",
        f"    |   {label:^5s}   |",
        f"    '----_-_---'",
        f"        |_|      ",
    ]


def _cup_medium(fill_char="@", label="GRIP"):
    """Americano/Cortado/Flat White cup."""
    return [
        f"    .-----------.  ",
        f"    |  {fill_char * 7}  |) ",
        f"    |  {fill_char * 7}  |) ",
        f"    |  {fill_char * 7}  |) ",
        f"    |   {label:^7s} |  ",
        f"    '-----------'  ",
        f"    =============  ",
    ]


def _cup_large(fill_char="@", label="GRIP"):
    """Cold Brew/Long Black cup."""
    return [
        f"    .=============.",
        f"    |  {fill_char * 9}  |",
        f"    |  {fill_char * 9}  |",
        f"    |  {fill_char * 9}  |",
        f"    |  {fill_char * 9}  |",
        f"    |   {label:^9s} |",
        f"    '===___===___='",
        f"        |_____|    ",
    ]


def _cup_takeaway(fill_char="@", label="GRIP"):
    """Irish Coffee — takeaway cup (you should go home)."""
    return [
        f"      /-------\\",
        f"     / {fill_char * 7} \\",
        f"    |  {fill_char * 7}  |",
        f"    |  {fill_char * 7}  |",
        f"    |   {label:^5s}   |",
        f"     \\_________/",
        f"      \\_______/ ",
    ]


CUP_RENDERERS = {
    "small": _cup_small,
    "medium": _cup_medium,
    "large": _cup_large,
    "takeaway": _cup_takeaway,
}


FILL_CHARS = {
    "dark": "#",
    "medium": "%",
    "light": "~",
}


# Latte art — tiny patterns drawn on the surface of the drink
# Each is a list of 1-2 lines that replace the top fill rows
LATTE_ART = {
    "Flat White": ["\u2248\u2248\u00b7\u2248\u2248\u00b7\u2248"],           # ~~.~~.~  (rosetta)
    "Cortado": ["\u00b7\u2248\u2665\u2248\u00b7\u2248\u00b7"],              # .~heart~..  (heart pour)
    "Matcha Latte": ["\u00b7\u2234\u00b7\u2234\u00b7\u2234\u00b7"],         # .dots.dots.  (matcha swirl)
    "Pour Over": ["\u2500\u2500\u2500\u25cb\u2500\u2500\u2500"],             # ---o---  (single drop)
    "Americano": ["\u2248\u2248\u2248\u2248\u2248\u2248\u2248"],             # ~~~~~~~  (calm surface)
    "Cold Brew": ["\u2591\u2591\u2593\u2593\u2593\u2591\u2591"],             # gradient  (iced layers)
}


# =========================================================================
# Metric Extraction
# =========================================================================

@dataclass
class SessionSignals:
    """Signals the barista reads before brewing."""
    composite: float = 0.5
    flow_state: float = 0.5
    velocity: float = 0.0
    friction: float = 0.0
    recovery_s: float = 10.0
    momentum: float = 0.0
    velocity_shadow: float = 0.0
    flow_shadow: float = 0.0
    context_pct: float = 0.0
    session_hours: float = 0.0
    commit_count: int = 0
    commit_subjects: list = field(default_factory=list)
    hour_of_day: int = 12
    converge_runs: int = 0
    coffee_count: int = 0


def _read_delight() -> dict:
    """Read latest DELIGHT v2 snapshot."""
    if not DELIGHT_FILE.exists():
        return {}
    try:
        lines = DELIGHT_FILE.read_text().strip().splitlines()
        if lines:
            return json.loads(lines[-1])
    except (json.JSONDecodeError, IndexError):
        pass
    return {}


def _read_git_log(n=8) -> list[str]:
    """Recent commit subjects as flavour notes."""
    try:
        result = subprocess.run(
            ["git", "log", f"--oneline", f"-{n}", "--format=%s"],
            capture_output=True, text=True, timeout=5,
            cwd=str(CLAUDE_DIR),
        )
        if result.returncode == 0:
            return [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def _count_recent_commits(hours=4) -> int:
    """Count commits in the last N hours."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={hours} hours ago", "--oneline"],
            capture_output=True, text=True, timeout=5,
            cwd=str(CLAUDE_DIR),
        )
        if result.returncode == 0:
            return len([l for l in result.stdout.strip().splitlines() if l.strip()])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return 0


def _count_coffees() -> int:
    """How many coffees have been brewed (loyalty card)."""
    if not COFFEE_LOG.exists():
        return 0
    try:
        lines = COFFEE_LOG.read_text().strip().splitlines()
        return len(lines)
    except Exception:
        return 0


def _count_converge_runs() -> int:
    """How many converge runs recorded."""
    if not CONVERGE_METRICS.exists():
        return 0
    try:
        lines = CONVERGE_METRICS.read_text().strip().splitlines()
        return len(lines)
    except Exception:
        return 0


def _delight_sparkline() -> str:
    """Sparkline of recent DELIGHT composites using Unicode blocks."""
    if not DELIGHT_FILE.exists():
        return ""
    try:
        lines = DELIGHT_FILE.read_text().strip().splitlines()
        scores = []
        for line in lines[-16:]:
            d = json.loads(line)
            c = d.get("composite", 0)
            if c > 0:
                scores.append(c)
        if len(scores) < 2:
            return ""
        # Unicode block elements: ascending height
        blocks = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
        mn, mx = min(scores), max(scores)
        rng = mx - mn if mx > mn else 1.0
        return "".join(blocks[min(7, int((s - mn) / rng * 7))] for s in scores)
    except Exception:
        return ""


def gather_signals() -> SessionSignals:
    """Gather all signals for the brew."""
    delight = _read_delight()
    now = datetime.now()
    commits = _read_git_log()
    commit_count = _count_recent_commits()

    return SessionSignals(
        composite=delight.get("composite", 0.5),
        flow_state=delight.get("flow_state", 0.5),
        velocity=delight.get("velocity", 0.0),
        friction=delight.get("friction_to_flow", 0.0),
        recovery_s=delight.get("recovery_time_s", 10.0),
        momentum=delight.get("session_momentum", 0.0),
        velocity_shadow=delight.get("velocity_shadow", 0.0),
        flow_shadow=delight.get("flow_shadow", 0.0),
        context_pct=0.0,  # Injected by the agent at runtime
        session_hours=0.0,  # Injected by the agent at runtime
        commit_count=commit_count,
        commit_subjects=commits[:5],
        hour_of_day=now.hour,
        converge_runs=_count_converge_runs(),
        coffee_count=_count_coffees(),
    )


# =========================================================================
# The Brew Algorithm — selects coffee type from session signals
# =========================================================================

def select_coffee(s: SessionSignals) -> str:
    """The barista reads the room and picks the right drink."""

    # Fresh start — barely brewed
    if s.session_hours < 0.3 and s.commit_count == 0:
        return "Matcha Latte"

    # Context stretched to the limit
    if s.context_pct > 80:
        return "Long Black"

    # Late night + long session + struggling
    if s.hour_of_day >= 23 or s.hour_of_day < 5:
        if s.session_hours > 2 and s.composite < 0.5:
            return "Irish Coffee"

    # Research mode — lots of reading, few commits
    if s.commit_count == 0 and s.session_hours > 1:
        return "Pour Over"

    # Maximum concentration — many commits, high efficiency
    if s.commit_count > 5 and s.flow_state > 0.6:
        return "Ristretto"

    # High friction — fighting the system
    if s.friction > 0.25:
        return "Turkish Coffee"

    # Beautiful balance — high delight, clean shadows
    if s.composite > 0.7 and s.velocity_shadow < 0.1:
        return "Cortado"

    # Velvet flow — smooth sailing
    if s.flow_state > 0.7 and s.flow_shadow < 0.1:
        return "Flat White"

    # Marathon session
    if s.session_hours > 3:
        return "Cold Brew"

    # Quick and focused
    if s.flow_state > 0.5 and s.session_hours < 1.5:
        return "Espresso"

    # The reliable default
    return "Americano"


# =========================================================================
# Roast, Temperature, and Flavour Profile
# =========================================================================

def roast_level(s: SessionSignals) -> str:
    """Roast = efficiency. Dark = efficient, light = exploratory."""
    if s.composite > 0.7:
        return "Dark Roast"
    if s.composite > 0.5:
        return "Medium Roast"
    if s.composite > 0.3:
        return "Light Roast"
    return "Green (Unroasted)"


def temperature(s: SessionSignals) -> str:
    """Temperature = context freshness."""
    if s.context_pct < 20:
        return "Piping Hot"
    if s.context_pct < 50:
        return "Hot"
    if s.context_pct < 75:
        return "Warm"
    if s.context_pct < 85:
        return "Lukewarm"
    return "Iced"


def _tasting_notes(s: SessionSignals) -> list[str]:
    """Extract tasting notes from git subjects + metrics."""
    notes = []

    # From git subjects — extract the human-readable flavour
    type_labels = {
        "feat": "new blend",
        "fix": "patched roast",
        "test": "quality check",
        "chore": "house blend",
        "docs": "menu update",
        "refactor": "re-roasted",
        "perf": "extra shot",
        "style": "latte art",
        "revert": "sent back",
    }

    for subject in s.commit_subjects[:4]:
        # Parse conventional commits
        for prefix, label in type_labels.items():
            if subject.lower().startswith(prefix):
                # Extract the description after the type
                desc = subject.split(":", 1)[-1].strip() if ":" in subject else subject
                # Truncate at word boundary
                if len(desc) > 40:
                    cut = desc[:37].rsplit(" ", 1)[0]
                    desc = cut + "..."
                notes.append(f"{desc} ({label})")
                break
        else:
            if len(subject) > 40:
                cut = subject[:37].rsplit(" ", 1)[0]
                subject = cut + "..."
            notes.append(subject)

    if not notes:
        notes.append("single origin (no recent commits)")

    return notes


def _progress_bar(value: float, width: int = 10, filled: str = "\u2588", empty: str = "\u2591") -> str:
    """Tiny progress bar with Unicode blocks."""
    n = int(value * width)
    n = max(0, min(width, n))
    return filled * n + empty * (width - n)


# =========================================================================
# The Barista's Note — witty, state-aware, never generic
# =========================================================================

def barista_note(s: SessionSignals, coffee_type: str) -> str:
    """The barista always has something to say."""

    # Context-specific notes (checked in priority order)
    if s.context_pct > 85:
        return "Your cup runneth over. Time for a fresh pour."

    if s.coffee_count == 0:
        return "First cup on the house. Welcome to the Daily Grind."

    if s.coffee_count > 0 and s.coffee_count % 10 == 0:
        return f"Cup #{s.coffee_count}. At this point, you're basically a shareholder."

    if coffee_type == "Irish Coffee":
        return "Last call was an hour ago. But I'll make an exception."

    if coffee_type == "Matcha Latte":
        return "Fresh pot, fresh start. The world is green and possible."

    if coffee_type == "Turkish Coffee":
        notes = [
            "The grounds are thick. Read them carefully.",
            "Strong enough to debug a kernel. And about as grainy.",
            "Friction is just flavour you haven't learned to appreciate yet.",
        ]
        return random.choice(notes)

    if coffee_type == "Ristretto":
        return "Maximum extraction. Minimum waste. You're running a tight bar."

    if s.velocity_shadow > 0.15:
        return "The foam looks perfect... but I'd taste it before trusting it."

    if s.flow_shadow > 0.15:
        return "You're drinking fast, but are you tasting? Slow down."

    if s.momentum > 0.3:
        return "You're accelerating. The machine can barely keep up."

    if s.commit_count > 6:
        return "Six shots in. The beans are starting to question your life choices."

    if s.commit_count == 0 and s.session_hours > 1:
        return "Watching the beans, not yet grinding. The best cups take time."

    # Time-of-day notes
    if s.hour_of_day < 7:
        notes = [
            "A dawn brew. The code is quieter before the world wakes.",
            "The early grind catches the worm. Or the bug.",
        ]
        return random.choice(notes)

    if s.hour_of_day < 10:
        return "Rise and grind. Literally."

    if 12 <= s.hour_of_day < 14:
        return "Afternoon fuel. The post-lunch dip is a myth... mostly."

    if 17 <= s.hour_of_day < 20:
        return "Evening blend. The golden hour of shipping."

    if s.hour_of_day >= 22:
        notes = [
            "A midnight brew. The best code is written when the world sleeps.",
            "The midnight roast. Where bugs fear to tread.",
        ]
        return random.choice(notes)

    # Flow-specific notes
    if s.flow_state > 0.8:
        return "Deep flow. I almost didn't want to interrupt."

    if s.composite > 0.7:
        notes = [
            "This session has excellent body. Full, rich, no bitter finish.",
            "Smooth extraction. The kind of session you remember.",
        ]
        return random.choice(notes)

    # Converge-specific
    if s.converge_runs > 3:
        return "You converge like I tamp: firm, level, no second-guessing."

    # Loyalty milestones
    if s.coffee_count == 4:
        return "One more and you're a Regular. The beans are watching."

    if s.coffee_count == 14:
        return "Gold Card is one cup away. I've already engraved it."

    # Default pool — always something to say
    defaults = [
        "A solid cup. Not every brew needs to be extraordinary.",
        "Consistent extraction. That's what the regulars come for.",
        "Another round, another commit. This is the rhythm.",
        "The machine hums. The code flows. All is well.",
        "Some people debug with printf. You debug with espresso.",
        "The best code I've seen was written between the second and third cup.",
        "No cream, no sugar. Just you and the terminal.",
        "A watched pot never boils. A watched build never passes.",
        "Every great codebase was built one sip at a time.",
    ]
    return random.choice(defaults)


# =========================================================================
# Loyalty Card
# =========================================================================

LOYALTY_TIERS = [
    (5, "Regular"),
    (15, "Gold Card"),
    (30, "Platinum"),
    (50, "Barista's Friend"),
    (100, "Part Owner"),
]


def loyalty_display(count: int) -> str:
    """Loyalty card with fill indicators."""
    # Find current tier and progress
    tier_name = "New Customer"
    next_tier = 5
    for threshold, name in LOYALTY_TIERS:
        if count >= threshold:
            tier_name = name
        else:
            next_tier = threshold
            break
    else:
        next_tier = count + 1  # Beyond all tiers

    remaining = next_tier - count
    # Visual card — filled and empty coffee cups
    in_tier = min(count % 5 if count < next_tier else 5, 5)
    filled = "\u2615"  # hot beverage
    empty = "\u25cb"   # white circle
    card = f"{filled} " * in_tier + f"{empty} " * (5 - in_tier)
    card = card.strip()

    if remaining > 0 and count < 100:
        return f"    Loyalty: [{card}] {tier_name} ({remaining} to next tier)"
    return f"    Loyalty: [{card}] {tier_name}"


# =========================================================================
# The Brew — assembles the full output
# =========================================================================

def brew(s: SessionSignals, secret: str = None) -> str:
    """Brew the coffee. Returns the full formatted output."""
    lines = []

    # Secret menu overrides
    if secret == "affogato":
        return _brew_affogato(s)
    elif secret == "decaf":
        return _brew_decaf(s)
    elif secret == "menu":
        return _brew_secret_menu()
    elif secret == "beans":
        return _brew_bean_origins(s)
    else:
        coffee_type = select_coffee(s)

    spec = MENU[coffee_type]
    cup_fn = CUP_RENDERERS[spec["cup"]]
    fill = FILL_CHARS[spec["fill"]]

    roast = roast_level(s)
    temp = temperature(s)
    notes = _tasting_notes(s)
    note = barista_note(s, coffee_type)
    sparkline = _delight_sparkline()

    # First-brew ceremony
    if s.coffee_count == 0:
        lines.append("")
        lines.append("    ........................................")
        lines.append("    .                                      .")
        lines.append("    .  You found the coffee machine.       .")
        lines.append("    .                                      .")
        lines.append("    .  It's been here the whole time,      .")
        lines.append("    .  tucked behind the convergence       .")
        lines.append("    .  loops and the safety gates.         .")
        lines.append("    .                                      .")
        lines.append("    .  Most people walk right past it.     .")
        lines.append("    .                                      .")
        lines.append("    .  Not you.                            .")
        lines.append("    .                                      .")
        lines.append("    ........................................")
        lines.append("")

    # Title bar
    lines.append("")
    lines.append("    ========================================")
    lines.append("      T H E   D A I L Y   G R I N D")
    lines.append("    ========================================")
    lines.append("")

    # Steam (no steam for iced/cold drinks)
    is_cold = coffee_type in ("Cold Brew", "Affogato") or temp == "Iced"
    steam = _steam(iced=is_cold)
    for s_line in steam:
        lines.append(f"  {s_line}")

    # Cup (with latte art on the surface if available)
    art = LATTE_ART.get(coffee_type)
    if art:
        cup_lines = cup_fn(fill_char=fill, label="GRIP")
        # Replace the first fill row with latte art
        for i, c_line in enumerate(cup_lines):
            if fill * 7 in c_line and i <= 1:
                c_line = c_line.replace(fill * 7, art[0], 1)
                cup_lines[i] = c_line
                break
        for c_line in cup_lines:
            lines.append(c_line)
    else:
        cup_lines = cup_fn(fill_char=fill, label="GRIP")
        for c_line in cup_lines:
            lines.append(c_line)

    lines.append("")

    # Order details
    lines.append(f"    Order: {coffee_type}")
    lines.append(f"    \"{spec['desc']}\"")
    lines.append(f"    {roast} | {temp}")
    lines.append("")

    # Tasting notes
    lines.append("    Tasting Notes:")
    for n in notes:
        lines.append(f"      > {n}")
    lines.append("")

    # Session vitals as coffee attributes
    lines.append("    Body:    [" + _progress_bar(s.flow_state) + f"]  {s.flow_state:.0%}")
    lines.append("    Crema:   [" + _progress_bar(max(0, 1.0 - s.friction)) + f"]  {max(0, 1.0 - s.friction):.0%}")
    lines.append("    Aroma:   [" + _progress_bar(s.composite) + f"]  {s.composite:.0%}")

    # Shadow warning (the Goodhart barista)
    shadow_avg = (s.velocity_shadow + s.flow_shadow) / 2
    if shadow_avg > 0.1:
        lines.append(f"    Bitter:  [" + _progress_bar(shadow_avg, filled="!", empty="\u2591") + f"]  {shadow_avg:.0%}  (Goodhart)")
    lines.append("")

    # Barista's note
    lines.append(f"    Barista's Note:")
    lines.append(f"    \"{note}\"")
    lines.append("")

    # DELIGHT sparkline (coffee ring stain)
    if sparkline:
        lines.append(f"    Coffee Ring: [{sparkline}]  (DELIGHT history)")
        lines.append("")

    # Loyalty card
    cup_num = s.coffee_count + 1
    lines.append(loyalty_display(cup_num))

    # Milestone surprises
    milestone = _milestone_message(cup_num)
    if milestone:
        lines.append("")
        lines.append(f"    {milestone}")

    # Fortune wisdom (deterministic per brew, rotates daily)
    fortune = _coffee_fortune(cup_num)
    lines.append("")
    lines.append(f"    \u2500 \"{fortune}\"")

    lines.append("")
    lines.append("    ----------------------------------------")

    # Time signature
    now = datetime.now()
    lines.append(f"    Brewed at {now.strftime('%H:%M')} | Cup #{cup_num}")
    lines.append("    ========================================")
    lines.append("")

    return "\n".join(lines)


def _brew_decaf(s: SessionSignals) -> str:
    """Minimal output — just the cup and a score. Changes with time."""
    coffee_type = select_coffee(s)
    hour = datetime.now().hour
    if hour < 7:
        return f"\n    \u263d [{s.composite:.0%}] {coffee_type} (before dawn)\n"
    if hour < 12:
        return f"\n    \u2615 [{s.composite:.0%}] {coffee_type}\n"
    if hour < 18:
        return f"\n    \u2615 [{s.composite:.0%}] {coffee_type} (afternoon)\n"
    return f"\n    \u263e [{s.composite:.0%}] {coffee_type} (night cap)\n"


def _brew_secret_menu() -> str:
    """The menu you don't get unless you ask."""
    return """
    ========================================
      S E C R E T   M E N U
    ========================================

    These aren't on the board. You have to know.

    --secret affogato    Drowning in context.
                         Shows context usage breakdown
                         by file type and operation.

    --secret beans       Where the beans come from.
                         Shows which GRIP subsystems
                         contributed to your session.

    --secret menu        You're looking at it.

    --decaf              Just the cup. No foam.
                         One line. One number.

    --history            The loyalty card. Break
                         patterns. DELIGHT at brew time.

    ----------------------------------------
    "If you have to explain the joke,
     it wasn't a good joke." — but this
     isn't a joke. It's a diagnostic.
    ========================================
"""


def _brew_bean_origins(s: SessionSignals) -> str:
    """Show which GRIP subsystems contributed to the session."""
    lines = []
    lines.append("")
    lines.append("    ========================================")
    lines.append("      B E A N   O R I G I N S")
    lines.append("    ========================================")
    lines.append("")
    lines.append("    Where your session's flavour comes from:")
    lines.append("")

    # Map DELIGHT signals to bean origins
    origins = [
        ("Ethiopia Yirgacheffe", "Flow State", s.flow_state,
         "The bright, fruity note of uninterrupted work"),
        ("Colombian Supremo", "Velocity", min(s.velocity / 3.0, 1.0),
         "Full body from steady commit cadence"),
        ("Sumatran Mandheling", "Convergence", s.composite,
         "Earthy depth from iteration"),
        ("Kenyan AA", "Friction Recovery", max(0, 1.0 - min(s.recovery_s / 30, 1.0)),
         "Bright acidity of bouncing back fast"),
        ("Brazilian Santos", "Momentum", max(0, min(s.momentum + 0.5, 1.0)),
         "Smooth, nutty acceleration"),
    ]

    for origin, signal, value, desc in origins:
        bar = _progress_bar(value, width=8)
        lines.append(f"    {origin:<24s} [{bar}] {value:.0%}")
        lines.append(f"      {signal}: {desc}")
        lines.append("")

    # Shadow beans (the bitter ones)
    if s.velocity_shadow > 0.05 or s.flow_shadow > 0.05:
        lines.append("    --- Bitter Notes (Shadow Metrics) ---")
        lines.append("")
        if s.velocity_shadow > 0.05:
            bar = _progress_bar(s.velocity_shadow, width=8, filled="!", empty="\u2591")
            lines.append(f"    Robusta (revert risk)    [{bar}] {s.velocity_shadow:.0%}")
        if s.flow_shadow > 0.05:
            bar = _progress_bar(s.flow_shadow, width=8, filled="!", empty="\u2591")
            lines.append(f"    Chicory (confusion)      [{bar}] {s.flow_shadow:.0%}")
        lines.append("")

    lines.append("    ========================================")
    lines.append("")
    return "\n".join(lines)


def _milestone_message(cup_num: int) -> str:
    """Special messages at notable cup counts."""
    milestones = {
        10: "Double digits. You're a regular now.",
        25: "Quarter century of cups. The barista knows your name.",
        42: "The Answer to Life, the Universe, and Coffee.",
        50: "Half a hundred. The espresso machine salutes you.",
        69: "Nice.",
        100: "Triple digits. At this point, the coffee runs through your veins.",
        128: "2^7 cups. A binary barista's dream.",
        256: "2^8. You ARE the coffee machine.",
        404: "Cup not found. Just kidding, here it is.",
    }
    return milestones.get(cup_num, "")


# Fortunes — deterministic rotation, one per brew
_FORTUNES = [
    "The bug you're looking for is in the file you haven't read.",
    "A refactor delayed is a refactor doubled.",
    "The best time to write tests was before the bug. The second best time is now.",
    "Not all who wander through the codebase are lost. Some are grepping.",
    "The commit message you don't write today, you won't understand tomorrow.",
    "A function that does one thing is worth two that do three.",
    "The diff is always shorter than it felt.",
    "Code you delete is code you never have to maintain.",
    "Premature optimisation is the root of all evil. Late coffee is the root of all bugs.",
    "You can't overflow a stack if you never recurse. (But where's the fun in that?)",
    "The API endpoint you need was deprecated yesterday.",
    "Ship it. Then ship the fix. Then ship the fix for the fix.",
    "Your future self will thank you for that comment. Or curse you for the lack of one.",
    "The best architectures emerge from one more coffee.",
    "Today's hack is tomorrow's production infrastructure.",
    "If it works, don't touch it. Unless it's time to deploy.",
    "Every great system started as a prototype that worked surprisingly well.",
    "The senior engineer's secret: they Google it too.",
    "Sometimes the right abstraction is no abstraction.",
    "A watched pipeline never builds. Go get coffee.",
]


def _coffee_fortune(cup_num: int) -> str:
    """Pick a fortune. Deterministic per cup number."""
    idx = cup_num % len(_FORTUNES)
    return _FORTUNES[idx]


def _brew_affogato(s: SessionSignals) -> str:
    """Affogato: drowning in DELIGHT. Shows composition as drink layers."""
    lines = []
    lines.append("")
    lines.append("    ========================================")
    lines.append("      A F F O G A T O")
    lines.append("      Drowning in it. Deliciously.")
    lines.append("    ========================================")
    lines.append("")

    # The drink is layered like DELIGHT itself
    # User Delight (60%) = the gelato
    # System Health (40%) = the espresso shot

    # Compute DELIGHT breakdown
    flow_adj = s.flow_state * max(0.5, 1.0 - s.flow_shadow)
    vel_adj = min(s.velocity / 3.0, 1.0) * max(0.5, 1.0 - s.velocity_shadow)
    friction_score = max(0, 1.0 - s.friction)
    recovery_score = max(0, 1.0 - min(s.recovery_s / 30, 1.0))
    momentum_score = max(0, min(s.momentum + 0.5, 1.0))

    lines.append("    === THE GELATO (User Delight: 60%) ===")
    lines.append("")
    layers_ud = [
        ("Flow State", flow_adj, 0.20),
        ("Velocity", vel_adj, 0.20),
        ("Completion", s.composite, 0.15),
        ("Low Friction", friction_score, 0.15),
        ("Recovery Speed", recovery_score, 0.15),
        ("Momentum", momentum_score, 0.15),
    ]
    for name, val, weight in layers_ud:
        bar = _progress_bar(val, width=12)
        contrib = val * weight
        lines.append(f"    {name:<18s} [{bar}] {val:.0%}  x{weight:.0%} = {contrib:.3f}")
    lines.append("")

    lines.append("    === THE SHOT (System Health: 40%) ===")
    lines.append("")
    genome = max(0, min(s.composite + 0.5, 1.0))  # Proxy
    layers_sh = [
        ("Genome Fitness", genome, 0.35),
        ("Converge Velocity", min(s.velocity / 3.0, 1.0), 0.35),
        ("Conv. Efficiency", s.composite, 0.30),
    ]
    for name, val, weight in layers_sh:
        bar = _progress_bar(val, width=12)
        contrib = val * weight
        lines.append(f"    {name:<18s} [{bar}] {val:.0%}  x{weight:.0%} = {contrib:.3f}")
    lines.append("")

    # The final pour
    lines.append("    === THE POUR ===")
    lines.append("")
    lines.append(f"    DELIGHT Composite:  {s.composite:.1%}")
    sparkline = _delight_sparkline()
    if sparkline:
        lines.append(f"    Trend:             [{sparkline}]")
    lines.append("")

    # Shadow warning
    if s.velocity_shadow > 0.05 or s.flow_shadow > 0.05:
        lines.append("    The ice cream is melting. (Shadow metrics detected)")
        if s.velocity_shadow > 0.05:
            lines.append(f"      Revert risk:   {s.velocity_shadow:.0%}")
        if s.flow_shadow > 0.05:
            lines.append(f"      Confusion:     {s.flow_shadow:.0%}")
        lines.append("")

    lines.append("    \"An affogato tells you everything.")
    lines.append("     The gelato is what you hoped for.")
    lines.append("     The espresso is what actually happened.\"")
    lines.append("")
    lines.append("    ========================================")
    lines.append("")
    return "\n".join(lines)


# =========================================================================
# Record the brew (meta-measurement)
# =========================================================================

def record_brew(s: SessionSignals, coffee_type: str) -> None:
    """Log this coffee. Break frequency is itself a health signal."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coffee_type": coffee_type,
        "composite": round(s.composite, 4),
        "flow_state": round(s.flow_state, 4),
        "friction": round(s.friction, 4),
        "context_pct": round(s.context_pct, 2),
        "session_hours": round(s.session_hours, 2),
        "commit_count": s.commit_count,
        "hour_of_day": s.hour_of_day,
        "cup_number": s.coffee_count + 1,
    }
    COFFEE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(COFFEE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# =========================================================================
# History view (sparkline + loyalty)
# =========================================================================

def history_view() -> str:
    """Show coffee history and break patterns."""
    if not COFFEE_LOG.exists():
        return "\n    No coffee history yet. Order your first cup with /coffee\n"

    lines = COFFEE_LOG.read_text().strip().splitlines()
    entries = [json.loads(l) for l in lines]

    output = []
    output.append("")
    output.append("    ========================================")
    output.append("      C O F F E E   H I S T O R Y")
    output.append("    ========================================")
    output.append("")
    output.append(f"    Total cups: {len(entries)}")
    output.append("")

    # Favourite drink
    from collections import Counter
    types = Counter(e["coffee_type"] for e in entries)
    fav = types.most_common(1)[0] if types else ("none", 0)
    output.append(f"    Favourite: {fav[0]} (x{fav[1]})")

    # Time pattern
    hours = Counter(e["hour_of_day"] for e in entries)
    peak_hour = hours.most_common(1)[0] if hours else (12, 0)
    output.append(f"    Peak hour: {peak_hour[0]:02d}:00 ({peak_hour[1]} cups)")
    output.append("")

    # DELIGHT at brew time — sparkline (Unicode blocks)
    scores = [e.get("composite", 0.5) for e in entries[-20:]]
    blocks = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    if len(scores) >= 2:
        mn, mx = min(scores), max(scores)
        rng = mx - mn if mx > mn else 1.0
        spark = "".join(blocks[min(7, int((sc - mn) / rng * 7))] for sc in scores)
        output.append(f"    DELIGHT at brew: [{spark}]")
        output.append("")

    # Last 5 brews
    output.append("    Recent orders:")
    for e in entries[-5:]:
        ts = e["timestamp"][:16].replace("T", " ")
        output.append(f"      {ts}  {e['coffee_type']:15s}  [{e.get('composite', 0):.0%}]")

    output.append("")
    output.append(loyalty_display(len(entries)))
    output.append("    ========================================")
    output.append("")

    return "\n".join(output)


# =========================================================================
# CLI
# =========================================================================

def main():
    args = sys.argv[1:]

    if "--history" in args:
        print(history_view())
        return

    signals = gather_signals()

    secret = None
    if "--secret" in args:
        idx = args.index("--secret")
        secret = args[idx + 1] if idx + 1 < len(args) else "affogato"

    if "--decaf" in args:
        secret = "decaf"

    coffee_type = select_coffee(signals) if secret != "affogato" else "Affogato"
    if secret == "decaf":
        coffee_type = select_coffee(signals)

    output = brew(signals, secret=secret)
    print(output)

    # Record the brew (unless decaf — ghosts don't leave traces)
    if secret != "decaf":
        record_brew(signals, coffee_type if secret != "affogato" else "Affogato")


if __name__ == "__main__":
    main()
