#!/usr/bin/env python3
"""GRIP Genome Trading Card renderer — TUI display with ANSI styling.

Usage:
    python3 trading_card.py                    # Show GRIP card (default)
    python3 trading_card.py toopie             # Show Toopie's card
    python3 trading_card.py data/genomes/x.json  # Show any genome card
    python3 trading_card.py --all              # Show all cards
"""

import json
import os
import re
import sys
import textwrap
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# ── Colour palette ──────────────────────────────────────────────────────
C = {
    "r":     "\033[0m",
    "bold":  "\033[1m",
    "dim":   "\033[2m",
    "gold":  "\033[38;5;220m",
    "orange":"\033[38;5;208m",
    "amber": "\033[38;5;214m",
    "red":   "\033[38;5;196m",
    "purple":"\033[38;5;135m",
    "blue":  "\033[38;5;33m",
    "cyan":  "\033[38;5;45m",
    "green": "\033[38;5;48m",
    "grey":  "\033[38;5;242m",
    "dark":  "\033[38;5;236m",
    "white": "\033[97m",
}

RARITY_COLOURS = {
    "MYTHIC":    {"primary": "gold",   "accent": "amber",  "glow": "orange", "border": "gold"},
    "LEGENDARY": {"primary": "orange", "accent": "amber",  "glow": "gold",   "border": "orange"},
    "EPIC":      {"primary": "purple", "accent": "blue",   "glow": "purple", "border": "purple"},
    "RARE":      {"primary": "blue",   "accent": "cyan",   "glow": "blue",   "border": "blue"},
    "COMMON":    {"primary": "grey",   "accent": "white",  "glow": "grey",   "border": "grey"},
}

# Curated stat display names per card (keyed by operator_name)
CARD_STATS = {
    "grip": [
        ("Mech Guardrails",  1.00),
        ("Self-Improvement", 0.98),
        ("Hallucin Resist",  0.97),
        ("Session Contin",   0.95),
        ("Context Effic",    0.92),
    ],
    "toopie": [
        ("Pair Debugging",   1.00),
        ("Cheerfulness",     0.99),
        ("Late-Night Hero",  0.95),
        ("PR Review Stamina",0.92),
        ("BParty Knowledge", 1.00),
    ],
    "thomas": [
        ("Meta-Agent Arch",  1.00),
        ("Context Compress", 0.98),
        ("Recursive Reason", 0.95),
        ("Spatial Memory",   1.00),
        ("Cross-Domain Map", 0.85),
    ],
}

W = 51  # inner content width between borders
_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

# Decorative elements
RARITY_ICON = {"MYTHIC": "◆", "LEGENDARY": "◇", "EPIC": "●", "RARE": "○", "COMMON": "·"}
GENE_BADGE = {"MYTHIC": "◆", "LEGENDARY": "◇", "EPIC": "●", "RARE": "○"}

# Curated gene display names (short enough for card)
GENE_DISPLAY = {
    "mechanical-guardrails": "Mechanical Guardrails",
    "recursive-self-improvement": "Recursive Self-Improve",
    "inverse-scheherazade": "Inverse Scheherazade",
    "toopie-prime": "Toopie Prime",
    "bparty-whisperer": "BParty Whisperer",
    "genome-patient-zero": "Genome Patient Zero",
    "compressed-information-language": "Compressed Info Lang",
    "four-dimensional-memory": "4D Memory",
    "eth-framework": "ETH Framework",
}


def _s(name):
    """Get ANSI escape sequence by name."""
    return C.get(name, "")


def _vlen(text):
    """Visible length of text after stripping ANSI codes."""
    return len(_ANSI_RE.sub('', text))


def _row(content, bc="gold"):
    """Render a bordered row. Padding auto-computed from visible content length."""
    vl = _vlen(content)
    pad = W - vl
    return f"  {_s(bc)}║{_s('r')}{content}{' ' * max(0, pad)}{_s(bc)}║{_s('r')}"


def _centre(text, width=W):
    """Centre text within width, accounting for ANSI codes."""
    vl = _vlen(text)
    pad = width - vl
    left = pad // 2
    right = pad - left
    return " " * left + text + " " * right


def _divider(bc="gold", style="solid"):
    """Section divider with tee connectors."""
    if style == "dots":
        pattern = ("·─" * ((W + 1) // 2))[:W]
        return f"  {_s(bc)}╠{_s('grey')}{pattern}{_s(bc)}╣{_s('r')}"
    if style == "sparkle":
        # Build exactly W chars: ─·✦·─·✦·... with colour on ✦
        parts = []
        unit = "─·✦·"
        full = (unit * ((W + 3) // 4))[:W]
        for ch in full:
            if ch == "✦":
                parts.append(f"{_s(bc)}{ch}")
            else:
                parts.append(f"{_s('dark')}{ch}")
        return f"  {_s(bc)}╠{''.join(parts)}{_s(bc)}╣{_s('r')}"
    return f"  {_s(bc)}╠{_s('dark')}{'─' * W}{_s(bc)}╣{_s('r')}"


def _bar(value, width=16):
    """Gradient stat bar: green → amber → orange."""
    filled = int(value * width)
    out = ""
    for i in range(width):
        if i < filled:
            ratio = i / width
            if ratio < 0.5:
                out += f"{_s('green')}█"
            elif ratio < 0.75:
                out += f"{_s('amber')}█"
            else:
                out += f"{_s('orange')}█"
        else:
            out += f"{_s('dark')}░"
    return out + _s("r")


def _extract_card_fields(card_data):
    """Parse structured fields from the plain ASCII card array."""
    fields = {"class": "Unknown", "special": "", "flavour": "", "collector": "???"}

    for i, line in enumerate(card_data):
        if "Class:" in line:
            fields["class"] = line.split("Class:")[1].strip().rstrip("|").strip()
        elif "Collector" in line:
            try:
                fields["collector"] = line.split("#")[1].strip().rstrip("|").strip()
            except IndexError:
                pass

    # Extract special ability text (lines after SPECIAL ABILITY: until empty/FLAVOUR)
    _collect_section(card_data, "SPECIAL ABILITY:", fields, "special")
    _collect_section(card_data, "FLAVOUR TEXT:", fields, "flavour")

    return fields


def _collect_section(card_data, marker, fields, key):
    """Collect wrapped text from a card section."""
    collecting = False
    parts = []
    for line in card_data:
        if marker in line:
            collecting = True
            continue
        if collecting:
            stripped = line.strip()
            if not stripped.startswith("|"):
                break
            text = stripped.strip("|").strip()
            if not text or any(kw in text for kw in ["FLAVOUR", "STATS", "Collector", "CodeTonight"]):
                if key == "special" and "FLAVOUR" in text:
                    break
                if not text:
                    break
                break
            parts.append(text)
    fields[key] = " ".join(parts)


def _extract_ascii_header(card_data):
    """Pull ASCII art header lines from card data."""
    header = []
    in_header = False
    for line in card_data:
        inner = line.strip().strip("|")
        if any(ch in inner for ch in ["____", "/ _", "\\__", "|_"]) and not in_header:
            in_header = True
        if in_header:
            if inner.strip() == "" or "G E N O M E" in inner:
                break
            clean = line.strip()
            if clean.startswith("|"):
                clean = clean[1:]
            if clean.endswith("|"):
                clean = clean[:-1]
            header.append(clean.rstrip())
    return header


def render_card(data):
    """Render a genome trading card with full ANSI styling."""
    genes = data.get("genes", {})
    card_data = data.get("_easter_egg", {}).get("card", [])
    operator = data.get("operator", "?>>")
    name = data.get("operator_name", "unknown")
    fingerprint = data.get("genome_fingerprint", "???")
    gen = data.get("exported_from_gen", "?")

    # Rarity from first gene
    first_gene = next(iter(genes.values())) if genes else {}
    rarity = first_gene.get("parameters", {}).get("rarity", "COMMON")
    scheme = RARITY_COLOURS.get(rarity, RARITY_COLOURS["COMMON"])
    bc, pc, ac, gc = scheme["border"], scheme["primary"], scheme["accent"], scheme["glow"]

    fields = _extract_card_fields(card_data)
    ascii_header = _extract_ascii_header(card_data)

    # Stats: use curated if available, else top 5 from domain_fitness
    stats = CARD_STATS.get(name, None)
    if not stats:
        all_stats = {}
        for gene in genes.values():
            for domain, fitness in gene.get("domain_fitness", {}).items():
                if domain not in all_stats or fitness > all_stats[domain]:
                    all_stats[domain] = fitness
        stats = [(k.replace("-", " ").replace("_", " ").title()[:18], v)
                 for k, v in sorted(all_stats.items(), key=lambda x: -x[1])[:5]]

    rarity_stars = "★" * {"MYTHIC": 5, "LEGENDARY": 4, "EPIC": 3, "RARE": 2}.get(rarity, 1)

    # Divider style: sparkle for MYTHIC, dots for LEGENDARY, solid otherwise
    div_style = "sparkle" if rarity == "MYTHIC" else "dots" if rarity == "LEGENDARY" else "solid"
    icon = RARITY_ICON.get(rarity, "·")

    # ── Build lines ──────────────────────────────────────────────────────
    L = []
    L.append("")

    # Top border with rarity-coloured glow line
    if rarity == "MYTHIC":
        glow_pattern = f"{_s(gc)}{'· ' * (W // 2)}{_s('r')}"
        L.append(f"  {glow_pattern}")
    L.append(f"  {_s(bc)}╔{'═' * W}╗{_s('r')}")
    L.append(_row("", bc))

    # ASCII name header
    for aline in ascii_header:
        L.append(_row(f" {_s(pc)}{_s('bold')}{aline}{_s('r')}", bc))

    # Title
    L.append(_row("", bc))
    title = f"{_s(gc)}{_s('bold')}G E N O M E   T R A D I N G   C A R D{_s('r')}"
    L.append(_row(_centre(title), bc))
    L.append(_row("", bc))

    # ── Rarity ───────────────────────────────────────────────────────────
    L.append(_divider(bc, div_style))
    L.append(_row("", bc))
    rarity_text = f"{_s(pc)}{_s('bold')}{rarity_stars} {rarity} {rarity_stars}{_s('r')}"
    L.append(_row(_centre(rarity_text), bc))
    L.append(_row("", bc))

    # ── Identity ─────────────────────────────────────────────────────────
    L.append(_row(f"  {_s('white')}Operator{_s('r')}   {_s(pc)}{_s('bold')}{operator}{_s('r')}  {_s('grey')}({name.upper()}){_s('r')}", bc))
    L.append(_row(f"  {_s('white')}Class{_s('r')}      {_s(ac)}{fields['class']}{_s('r')}", bc))
    L.append(_row(f"  {_s('white')}Gen{_s('r')}        {_s(ac)}{gen}{_s('r')}  {_s('grey')}│{_s('r')}  {_s('dim')}fp:{fingerprint}{_s('r')}", bc))

    # ── Stats ────────────────────────────────────────────────────────────
    L.append(_row("", bc))
    L.append(_divider(bc, "dots"))
    L.append(_row("", bc))
    L.append(_row(f"  {_s('white')}{_s('bold')}STATS{_s('r')}", bc))
    L.append(_row("", bc))

    for stat_name, stat_val in stats:
        padded_name = stat_name.ljust(18)
        # Gold highlight for perfect scores
        score_colour = pc if stat_val >= 1.0 else 'white'
        score_bold = _s('bold') if stat_val >= 1.0 else ""
        line = f"  {_s('grey')}{padded_name}{_s('r')} {_bar(stat_val)} {score_bold}{_s(score_colour)}{stat_val:.2f}{_s('r')}"
        L.append(_row(line, bc))

    # ── Genes ────────────────────────────────────────────────────────────
    L.append(_row("", bc))
    L.append(_divider(bc, "dots"))
    L.append(_row("", bc))
    L.append(_row(f"  {_s('white')}{_s('bold')}GENES{_s('r')}", bc))
    L.append(_row("", bc))

    for gene_name, gene in genes.items():
        g_rarity = gene.get("parameters", {}).get("rarity", "COMMON")
        g_fitness = gene.get("fitness", 0)
        g_icon = GENE_BADGE.get(g_rarity, "·")
        g_colour = RARITY_COLOURS.get(g_rarity, RARITY_COLOURS["COMMON"])["primary"]

        display = GENE_DISPLAY.get(gene_name, gene_name.replace("-", " ").title()[:22])

        badge = f"  {_s(g_colour)}{g_icon}{_s('r')} {_s('white')}{display}{_s('r')}"
        meta = f"{_s('dim')}{g_rarity:>9s}{_s('r')} {_s(g_colour)}{g_fitness:.2f}{_s('r')}"
        badge_vlen = _vlen(badge)
        meta_vlen = _vlen(meta)
        gap = W - badge_vlen - meta_vlen
        L.append(_row(f"{badge}{' ' * max(1, gap)}{meta}", bc))

    # ── Special Ability ──────────────────────────────────────────────────
    L.append(_row("", bc))
    L.append(_divider(bc, div_style))
    L.append(_row("", bc))
    L.append(_row(f"  {_s(pc)}{_s('bold')}SPECIAL ABILITY{_s('r')}", bc))

    if fields["special"]:
        for wline in textwrap.wrap(fields["special"], width=W - 4):
            L.append(_row(f"  {_s(ac)}{wline}{_s('r')}", bc))

    # ── Flavour Text ─────────────────────────────────────────────────────
    L.append(_row("", bc))
    L.append(_divider(bc, "solid"))
    L.append(_row("", bc))

    if fields["flavour"]:
        clean = fields["flavour"].strip("'\"")
        wrapped = textwrap.wrap(clean, width=W - 6)
        for i, wline in enumerate(wrapped):
            if i == 0 and len(wrapped) == 1:
                L.append(_row(f"  {_s(gc)}{_s('bold')}\"{wline}\"{_s('r')}", bc))
            elif i == 0:
                L.append(_row(f"  {_s(gc)}{_s('bold')}\"{wline}{_s('r')}", bc))
            elif i == len(wrapped) - 1:
                L.append(_row(f"  {_s(gc)}{_s('bold')} {wline}\"{_s('r')}", bc))
            else:
                L.append(_row(f"  {_s(gc)}{_s('bold')} {wline}{_s('r')}", bc))

    # ── Footer ───────────────────────────────────────────────────────────
    L.append(_row("", bc))
    L.append(_divider(bc, div_style))  # sparkle for footer
    L.append(_row("", bc))

    coll = f"{_s(pc)}{_s('bold')}{icon} Collector's Edition #{fields['collector']} {icon}{_s('r')}"
    L.append(_row(_centre(coll), bc))
    org = f"{_s('grey')}CodeTonight SA  ·  Gen {gen}{_s('r')}"
    L.append(_row(_centre(org), bc))

    L.append(_row("", bc))
    L.append(f"  {_s(bc)}╚{'═' * W}╝{_s('r')}")

    # Glow line + pulse for MYTHIC
    if rarity == "MYTHIC":
        glow_pattern = f"{_s(gc)}{'· ' * (W // 2)}{_s('r')}"
        L.append(f"  {glow_pattern}")

    pulse = f"  {_s(pc)}░▒▓█▓▒░{_s('r')}  {_s('dim')}{fingerprint}{_s('r')}"
    L.append(pulse)
    L.append("")

    return L


def load_genome(name_or_path):
    path = Path(name_or_path)
    if not path.exists():
        path = CLAUDE_DIR / "data" / "genomes" / f"{name_or_path}.json"
    if not path.exists():
        print(f"  {_s('red')}Genome not found: {name_or_path}{_s('r')}")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def list_genomes():
    genomes_dir = CLAUDE_DIR / "data" / "genomes"
    return sorted(
        [p for p in genomes_dir.glob("*.json") if not p.name.startswith("_")],
        key=lambda p: p.name,
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        for gpath in list_genomes():
            data = json.load(open(gpath))
            if "_easter_egg" in data:
                for line in render_card(data):
                    print(line)
    elif len(sys.argv) > 1:
        data = load_genome(sys.argv[1])
        for line in render_card(data):
            print(line)
    else:
        data = load_genome("grip")
        for line in render_card(data):
            print(line)
