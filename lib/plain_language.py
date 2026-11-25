"""Plain language translation for GRIP numeric metrics and terminology.

Translates deterministic numeric values into human-readable labels,
and maps GRIP-internal jargon to plain-language equivalents.

Numeric translation: threshold-based, mechanical — handled here.
Terminology translation: string-based lookup via _TERMINOLOGY dict and
  translate_term() / translate_text() — also handled here.

Note: contextual reasoning (e.g. detecting jargon in free-form prose)
is still handled by the LLM through CLAUDE.md inference. This module
covers the deterministic cases where exact term matching is sufficient.

Acceptance test: Would a general human being understand this output
without asking for clarification? If no, rewrite.

Usage:
    from plain_language import explain, explain_session, explain_gate
    from plain_language import translate_term, translate_text
"""


def _match_threshold(value: float, thresholds: list) -> str:
    """Return label for the first threshold the value meets or exceeds."""
    for threshold, label in thresholds:
        if abs(value) >= threshold:
            return label
    return thresholds[-1][1]


# ── Threshold Tables ──────────────────────────────────────────────────

_FLOW = [
    (0.8, "Running smoothly, no blockages"),
    (0.5, "Some friction — retries or corrections needed"),
    (0.2, "Significant blockages slowing progress"),
    (0.0, "Stuck — blocked by errors or missing context"),
]

_HEALTH = [
    (0.8, "healthy"),
    (0.5, "functional but needs attention in places"),
    (0.3, "degraded — maintenance needed"),
    (0.0, "unhealthy — multiple areas failing"),
]

_EFFECT = [
    (0.8, "large measurable difference"),
    (0.5, "moderate difference"),
    (0.2, "small but real difference"),
    (0.0, "negligible difference"),
]

# Inverted-U metrics have quality that peaks in the middle — both extremes
# are bad. Examples: hypothesis confirmation rate (30-70% band),
# conversation entropy (2-4 bits band). See rules/scientific-method.md
# "entropy → inverted-U (extremes are bad)" for the shape's origin.
# _match_threshold() would pick the wrong label here (monotonic), so
# these metrics go through _inverted_u_band() instead.

# ── Metric Translators ────────────────────────────────────────────────


def _inverted_u_band(
    v: float,
    *,
    low: float,
    high: float,
    too_low_tmpl: str,
    band_tmpl: str,
    too_high_tmpl: str,
    value_fmt=lambda v: f"{v:.2f}",
) -> str:
    """Band-aware label for inverted-U quality metrics.

    Quality peaks in the healthy band [low, high]; both extremes are bad.
    Each template receives the formatted value as ``{v}``.

    Args:
        v: Raw metric value.
        low: Lower edge of healthy band (inclusive).
        high: Upper edge of healthy band (inclusive).
        too_low_tmpl: Template used when v < low (use ``{v}`` for value).
        band_tmpl: Template used when low <= v <= high.
        too_high_tmpl: Template used when v > high.
        value_fmt: Callable that formats v for display (default 2dp float).
    """
    display = value_fmt(v)
    if v > high:
        return too_high_tmpl.format(v=display)
    if v >= low:
        return band_tmpl.format(v=display)
    return too_low_tmpl.format(v=display)


def _confirmation_rate_label(v: float) -> str:
    """Band-aware label for hypothesis confirmation rate.

    30-70% is the healthy science band. >70% = predictions too easy
    (not really falsifiable). <30% = predictions too aggressive
    (unreliable, likely misframed).
    """
    return _inverted_u_band(
        v,
        low=0.30,
        high=0.70,
        value_fmt=lambda v: f"{v * 100:.1f}%",
        too_low_tmpl="{v} predictions came true — too few; predictions may be too aggressive or poorly framed (healthy range is 30-70%)",
        band_tmpl="{v} predictions came true — honest, in the useful-science band (30-70%)",
        too_high_tmpl="{v} predictions came true — suspiciously high, predictions may be too easy (healthy range is 30-70%)",
    )


def _entropy_label(v: float) -> str:
    """Band-aware label for Shannon conversation entropy in bits.

    Healthy band: 2.0-4.0 bits per message. <2 = repetitive / stuck in
    a loop. >4 = chaotic / topic drift. Source:
    ``lib/conversation_entropy.py`` + ``rules/scientific-method.md``
    inverted-U rule.
    """
    return _inverted_u_band(
        v,
        low=2.0,
        high=4.0,
        value_fmt=lambda v: f"{v:.2f} bits",
        too_low_tmpl="{v} — low variety, session may be repetitive or stuck in a loop (healthy range is 2-4 bits)",
        band_tmpl="{v} — productive variety, healthy exploration (2-4 bits band)",
        too_high_tmpl="{v} — chaotic, topic drift or incoherent output (healthy range is 2-4 bits)",
    )



def _velocity_label(v: float) -> str:
    """Translate velocity with baseline context (Alex feedback: '11 out of how many?')."""
    if v >= 10:
        return f"Completing about {v:.0f} tasks per hour (high — typical range is 3-15)"
    if v >= 5:
        return f"Completing about {v:.0f} tasks per hour (moderate — typical range is 3-15)"
    return f"Completing about {v:.0f} tasks per hour (low — typical range is 3-15)"


_TRANSLATORS = {
    "flow": lambda v: _match_threshold(v, _FLOW),
    "velocity": _velocity_label,
    "system_health": lambda v: f"System health: {_match_threshold(v, _HEALTH)} ({v:.0%})",
    "friction": lambda v: "No blockages" if v == 0 else f"{v:.1f}s spent retrying failed operations",
    "cohens_d": lambda v: _match_threshold(v, _EFFECT),
    "cv": lambda v: (
        f"Consistent across models (within {v * 100:.1f}%)" if v < 0.05
        else f"Varies by {v * 100:.1f}% across models"
    ),
    "recurrence_rate": lambda v: f"{v * 100:.0f}% of issues came back after correction",
    "precision": lambda v: f"Correct {v * 100:.0f}% of the time",
    "context_pct": lambda v: f"Context window {v:.0%} used",
    "confirmation_rate": _confirmation_rate_label,
    "hypothesis_confirmation_rate": _confirmation_rate_label,
    "entropy": _entropy_label,
    "conversation_entropy": _entropy_label,
}


# ── Terminology Map ───────────────────────────────────────────────────

_TERMINOLOGY = {
    "skill": "playbook",
    "skills": "playbooks",
    "agent": "worker",
    "agents": "workers",
    "mode": "preset",
    "modes": "presets",
    "gate": "guard",
    "gates": "guards",
    "hook": "automated check",
    "hooks": "automated checks",
    "RSI": "improvement cycle",
    "genome": "feature scorecard",
    "KONO": "knowledge base",
    "convergence": "iterative refinement",
    "converge": "iterative refinement",
    "Broly": "council",
    "broly": "council",
    "DELIGHT": "session quality score",
    "genome fitness": "feature effectiveness score",
    "gate fire": "quality check triggered",
    "convergence depth": "iteration limit",
    "embodiment": "skill maturity",
    # Installation and setup terminology
    "install.sh": "GRIP setup script",
    "git-crypt": "file encryption (protects private data)",
    "venv": "Python environment",
    "MCP server": "tool bridge (connects Claude to external services)",
    "MCP servers": "tool bridges (connect Claude to external services)",
    "PATH": "system command lookup list",
    "alias": "shortcut command",
    "source": "reload shell configuration",
    "gg++": "GRIP quick launch command",
    "gogrip": "GRIP quick launch function",
    "shell-functions.sh": "GRIP shell shortcuts",
    "worktree": "isolated working copy",
    # Guild-specific (Discord audience, not all developers)
    "Fibonacci wave": "work sprint with rest breaks",
    # Hypothesis engine terminology (canonicalised 2026-04-18 from ELI5 insight)
    "hypothesis": "falsifiable prediction",
    "hypotheses": "falsifiable predictions",
    "hypothesis engine": "prediction tracker",
    "confirmation rate": "honest prediction rate",
    "hypothesis confirmation": "prediction confirmation",
    "falsified": "disproven",
    "falsification": "disproving",
    "confirmed hypothesis": "prediction that came true",
    "refuted hypothesis": "prediction that was disproven",
    "inconclusive hypothesis": "prediction with no clear verdict yet",
}


def translate_term(term: str) -> str:
    """Translate GRIP jargon to plain language.

    Returns the plain-language equivalent if one exists,
    otherwise returns the original term unchanged.

    >>> translate_term("skills")
    'playbooks'
    >>> translate_term("unknown")
    'unknown'
    """
    return _TERMINOLOGY.get(term, term)


# Operator authenticity watermark. Lines containing this glyph are
# treated as V>>-authored and bypass the plain-language pass. The glyph
# is a palindrome (reads identically reversed) and is statistically
# invisible in naturally-produced text — its rarity IS its security.
# See skills/140-85/SKILL.md for the full voice-gate protocol.
VOICE_GATE = "V>>--<<V"


def translate_text(text: str) -> str:
    """Translate all known GRIP jargon in a text to plain language.

    Uses word-boundary matching to avoid partial replacements.
    Case-sensitive: 'KONO' translates but 'kono' in code paths does not.
    Longer phrases (e.g. 'genome fitness') are matched before shorter
    ones (e.g. 'genome') to prevent partial replacement.

    Lines containing the V>>--<<V voice-gate watermark are preserved
    verbatim so V>>-authored content keeps its authentic voice.

    >>> translate_text("Loading 3 skills and 2 agents")
    'Loading 3 playbooks and 2 workers'
    >>> translate_text("V>>--<<V · skills stay as skills here")
    'V>>--<<V · skills stay as skills here'
    """
    import re

    def _translate_line(line: str) -> str:
        if VOICE_GATE in line:
            return line
        result = line
        for jargon, plain in sorted(_TERMINOLOGY.items(), key=lambda x: -len(x[0])):
            result = re.sub(r'\b' + re.escape(jargon) + r'\b', plain, result)
        return result

    if "\n" in text:
        return "\n".join(_translate_line(line) for line in text.split("\n"))
    return _translate_line(text)


# Metrics that are meaningless without surrounding context.
# explain() appends a hint when these appear standalone.
_CONTEXT_REQUIRED = {"cohens_d", "cv", "precision", "recurrence_rate"}


def explain(metric: str, value: float, context: str = "") -> str:
    """Translate a numeric metric into plain English.

    Args:
        metric: Metric key (e.g. "flow", "velocity", "cohens_d").
        value: Numeric value to translate.
        context: Optional context sentence (e.g. "between Haiku and Opus").
            Required for ambiguous metrics; omitting it adds a hint.

    >>> explain("flow", 1.0)
    'Running smoothly, no blockages'
    >>> explain("velocity", 9.3)
    'Completing about 9 tasks per hour (moderate — typical range is 3-15)'
    >>> explain("cohens_d", 0.62, "between v1 and v2")
    'moderate difference between v1 and v2'
    """
    fn = _TRANSLATORS.get(metric)
    if fn is None:
        return f"{metric}: {value}"
    result = fn(value)
    if context:
        return f"{result} {context}"
    if metric in _CONTEXT_REQUIRED:
        return f"{result} (specify what is being compared for clarity)"
    return result


# ── Session Summary ───────────────────────────────────────────────────

def explain_session(data: dict) -> str:
    """Translate a full session snapshot into a 3-5 line plain summary.

    >>> explain_session({"flow": 1.0, "velocity": 9.3, "system_health": 0.75})
    'Session: Running well\\n  ...'
    """
    flow = data.get("flow", 0)
    vel = data.get("velocity", 0)
    health = data.get("system_health", 0)
    friction = data.get("friction", 0)
    vel_shadow = data.get("vel_shadow", 0)
    flow_shadow = data.get("flow_shadow", 0)

    lines = []

    if flow >= 0.8 and health >= 0.5:
        lines.append("Session: Running well")
    elif flow >= 0.5:
        lines.append("Session: Making progress with some friction")
    else:
        lines.append("Session: Struggling — consider a fresh start")

    if vel > 0:
        lines.append(f"  {explain('velocity', vel)}")

    lines.append(f"  {explain('system_health', health)}")

    if friction > 0:
        lines.append(f"  {explain('friction', friction)}")

    warnings = []
    if vel_shadow > 0.1:
        warnings.append(f"{vel_shadow:.0%} of completed work was later reverted")
    if flow_shadow > 0.1:
        warnings.append("repeated corrections suggest misunderstanding of the task")

    if warnings:
        lines.append(f"  Warning: {', '.join(warnings)}")
    else:
        lines.append("  No warning signs")

    return "\n".join(lines)


# ── Genome ("feature scorecard") Explanation ──────────────────────────
#
# The single most-misread GRIP number. "Fitness 0.429" looks like a grade
# (43% = bad). It is NOT. It is the MEAN usefulness score across ALL genes
# (~401 skills/behaviours), each on a 0-1 scale. Genes start at the 0.5
# NEUTRAL_FITNESS baseline, rise with proven use (cap 0.9), and decay when
# idle (lib/converge_dna_core.py, genome_breeder.py). Most genes sit dormant
# near/below 0.5, so a mean around 0.4-0.5 is the healthy steady-state — the
# long tail of unused skills drags the average down, the actively-used ones
# score higher. This explainer makes that legible so the raw number is never
# read as "GRIP is 43% healthy". Embodies rules/canonical-output-style.md
# Layer 2 (plain + lossless): exact numbers preserved, only prose is plain.

# Canonical trend glyphs from lib/digest_data.py::read_genome_summary.
_TREND_GLYPH_TO_WORD = {
    "▲": "rising",   # up triangle
    "▼": "falling",  # down triangle
    "▬": "flat",     # horizontal bar
}


def _genome_trend(fitness_history: list, *, eps: float = 0.002) -> str:
    """Classify the genome fitness trend as 'rising' | 'falling' | 'flat'.

    Mirrors the +/-0.002 band in lib/digest_data.py::read_genome_summary
    EXACTLY, so the plain word here always agrees with the digest's
    triangle/bar glyph (no contradictory signals to the operator).

    >>> _genome_trend([0.40, 0.41, 0.429])
    'rising'
    >>> _genome_trend([0.429, 0.429, 0.429])
    'flat'
    >>> _genome_trend([0.45, 0.43, 0.40])
    'falling'
    >>> _genome_trend([])
    'flat'
    """
    if not fitness_history:
        return "flat"
    latest = fitness_history[-1]
    prev = fitness_history[-2] if len(fitness_history) >= 2 else latest
    if latest > prev + eps:
        return "rising"
    if latest < prev - eps:
        return "falling"
    return "flat"


_GENOME_TREND_CLAUSE = {
    "rising": "rising — recently-used skills are proving more useful",
    "falling": (
        "slipping — some skills are being used less, or less effectively "
        "(worth a look)"
    ),
    "flat": (
        "holding steady — no skill became markedly more or less useful this "
        "round. This is the EXPECTED reading after verification, planning, or "
        "docs work, which changes no behaviour for the scorecard to react to"
    ),
}

# Concise one-word forms — MUST share keys with _GENOME_TREND_CLAUSE so the
# concise and full output styles can never disagree on the trend vocabulary
# (test_trend_dicts_share_keys enforces parity). Single source, not two
# (AGORA #3399 r2, Speaker C).
_GENOME_TREND_SHORT = {
    "rising": "improving",
    "falling": "slipping",
    "flat": "steady",
}


def explain_genome(
    generation,
    gene_count,
    fitness: float | None,
    trend=None,
    fitness_history: list | None = None,
    concise: bool = False,
) -> str:
    """Explain the genome ("feature scorecard") state in plain English.

    Lossless: the exact ``generation``, ``gene_count`` and ``fitness`` numbers
    are preserved verbatim (canonical-output-style precision-preservation
    invariant); only the surrounding prose is plain.

    Trend resolution order: ``fitness_history`` (richest) -> ``trend`` glyph
    (triangle/bar) or word -> default 'flat'.

    Args:
        generation: genome update count (gen N).
        gene_count: number of tracked skills/behaviours.
        fitness: the mean gene usefulness (0-1), i.e. genome.avg_fitness, or
            None when no scored cycle has run (reported as "no fitness yet",
            never fabricated as 0.000).
        trend: optional triangle/bar glyph or 'rising'/'falling'/'flat'.
        fitness_history: optional list of recent fitness values; if given,
            the trend is computed from it (wins over ``trend``).
        concise: one-line form for the compact /save digest; default is the
            full paragraph (for /evo summaries, /plain genome, on-demand).

    >>> "0.429" in explain_genome(4985, 401, 0.429, trend="▬")
    True
    >>> "grade" in explain_genome(4985, 401, 0.429, trend="▬")
    True
    """
    if fitness_history:
        word = _genome_trend(fitness_history)
    elif trend in _TREND_GLYPH_TO_WORD:
        word = _TREND_GLYPH_TO_WORD[trend]
    elif trend in _GENOME_TREND_CLAUSE:
        word = trend
    else:
        word = "flat"

    # No recorded fitness (empty/missing history) -> do NOT fabricate a 0.000
    # "average"; say so honestly. No data is not a score of zero (AGORA #3399).
    if fitness is None:
        if concise:
            return (
                f"plain: no fitness recorded yet across {gene_count} skills "
                f"(genome has run no scored cycles)"
            )
        return (
            f"Skill scorecard (update #{generation}): GRIP tracks {gene_count} "
            f"skills/behaviours, but no fitness score has been recorded yet — "
            f"the genome has completed no scored cycles, so there is no average "
            f"to report (this is not a score of zero)."
        )

    # `word` is always normalised to {rising,falling,flat} above; .get() is
    # belt-and-suspenders so a future change can never KeyError-crash a digest.
    if concise:
        short = _GENOME_TREND_SHORT.get(word, "steady")
        return (
            f"plain: {fitness:.3f} = average usefulness across {gene_count} "
            f"skills (0-1 scale, ~0.5 neutral — not a grade); {short}"
        )

    return (
        f"Skill scorecard (update #{generation}): GRIP tracks {gene_count} "
        f"skills/behaviours, each scored 0-1 on how useful it has proven. The "
        f"headline {fitness:.3f} is the AVERAGE across all {gene_count} of them "
        f"— not a grade out of 100%. Skills start at 0.5 (neutral), climb when "
        f"they earn their keep, and fade toward 0 when unused; most sit dormant, "
        f"so an average near 0.4-0.5 is the normal, healthy steady-state (the "
        f"few skills in active use score higher). "
        f"Trend: {_GENOME_TREND_CLAUSE.get(word, _GENOME_TREND_CLAUSE['flat'])}."
    )


# ── Gate Message Translation ──────────────────────────────────────────

_GATE_TEMPLATES = {
    "DRY": "Near-identical code appears {detail} — if these do the same thing, extract to a shared function. If they look similar but serve different purposes, consider whether the similarity is coincidental",
    "KISS": "Function '{name}' is too long ({length} lines). Split into smaller, focused functions",
    "BIG-O": "Nested loops could be slow on large inputs. Use a set or dictionary instead",
    "YAGNI": "Import '{name}' isn't used anywhere in this file — remove it",
    "CC": "This function has too many decision branches ({value}). Simplify by extracting branches into separate functions",
    "SOLID-ISP": "Class '{name}' does too many things ({count} public methods). Split into focused classes with fewer responsibilities",
    "SOLID-DIP": "Function '{fname}' creates '{cname}' directly — pass it as a parameter instead so it can be swapped or tested",
    "GRASP": "Method '{method}' uses '{param}' more than its own data — consider moving this logic to '{param}'s class",
}


def explain_gate(principle: str, raw_message: str) -> str:
    """Translate a quality gate message into plain English.

    Uses regex to extract key values from the raw message, then fills
    a plain-language template. Falls back to raw message if no match.
    """
    import re

    template = _GATE_TEMPLATES.get(principle)
    if not template:
        return raw_message

    if principle == "BIG-O":
        return template

    extractors = {
        "KISS": (r"'(\w+)' is (\d+) lines", lambda m: {"name": m[1], "length": m[2]}),
        "DRY": (r"appears (\d+ times)", lambda m: {"detail": m[1]}),
        "YAGNI": (r"'(\w+)'", lambda m: {"name": m[1]}),
        "CC": (r"(\d+)", lambda m: {"value": m[1]}),
        "SOLID-ISP": (r"'(\w+)' has (\d+)", lambda m: {"name": m[1], "count": m[2]}),
        "SOLID-DIP": (r"'(\w+)' in '(\w+)'", lambda m: {"cname": m[1], "fname": m[2]}),
        "GRASP": (r"'(\w+)' accesses '(\w+)'", lambda m: {"method": m[1], "param": m[2]}),
    }

    spec = extractors.get(principle)
    if spec:
        match = re.search(spec[0], raw_message)
        if match:
            return template.format(**spec[1](match))

    return raw_message


# ── Installation Explanation ─────────────────────────────────────────

_INSTALL_STEPS = [
    ("Clone the repository", "git clone https://github.com/grip-platform/GRIP.git ~/.claude"),
    ("Run the setup script", "~/.claude/scripts/install.sh"),
    ("Reload your shell", "source ~/.zshrc  (or source ~/.bashrc, or restart your terminal)"),
    ("Launch GRIP", "gg++"),
    ("Run the setup wizard", "Type /activate-grip when Claude Code starts"),
]


def explain_install(step: int | None = None) -> str:
    """Generate plain-language installation instructions.

    Args:
        step: If provided, explain a specific step (1-indexed).
            If None, return all steps.

    Returns:
        Human-readable installation guide.

    >>> explain_install(1)
    'Step 1: Clone the repository\\n  Run: git clone ...'
    >>> len(explain_install().splitlines()) > 5
    True
    """
    if step is not None:
        if step < 1 or step > len(_INSTALL_STEPS):
            return f"Unknown step {step}. There are {len(_INSTALL_STEPS)} steps."
        desc, cmd = _INSTALL_STEPS[step - 1]
        return f"Step {step}: {desc}\n  Run: {cmd}"

    lines = ["GRIP Installation Guide", "=" * 22, ""]
    for i, (desc, cmd) in enumerate(_INSTALL_STEPS, 1):
        lines.append(f"Step {i}: {desc}")
        lines.append(f"  Run: {cmd}")
        lines.append("")
    lines.append("After step 5, you are ready to use GRIP. Type gg++ any time to start.")
    return "\n".join(lines)


# ── Guild Post Formatting ────────────────────────────────────────────

_CHANNEL_LIMITS = {
    "general": 1500,
    "announcements": 1800,
    "experimentation-channel": 1800,
    "falsify-grip": 1500,
    "humblebrag-channel": 1200,
    "serious-shit": 1800,
    "ask-acg": 1200,
    "articles": 3600,
    "off-topic": 2000,
}

# Patterns stripped from Guild posts (whole phrases, not fragments)
_GRIP_JARGON_PATTERNS = [
    r'\bGen \d+\b',
    r'\bW\d+\b',
    r'\bRitual \d+\s*(?:territory)?',
    r'\b(?:lambda|mu|nu|omega)\s+(?:gate|lock|validation|challenge)\b',
    r'\bTRIAD\s*(?:protocol|Protocol)?\b',
    r'\bsibling\s+thread\w*\b',
    r'\bcontext\s+map\b',
    r'/(?:tldr|recall|save|converge|evo)\b',
    r'\bconvergence\s+depth\s*\d*\b',
    r'\bgenome\s+fitness\s*[\d.]*\b',
    r'\bGoodhart\s*(?:protection|guard)?\b',
    r'\bKONO\b',
    r'\bCohen\'?s?\s+d\s*[=:]\s*[\d.]+',
]


def format_guild_post(content: str, channel: str = "general") -> str:
    """Translate GRIP content into Guild-appropriate format.

    Applies jargon firewall, enforces channel character limits,
    and strips internal GRIP patterns.

    Args:
        content: Raw content (may contain GRIP jargon).
        channel: Discord channel name (without #).

    Returns:
        Guild-ready post content.

    >>> 'Gen 500' not in format_guild_post("Reached Gen 500 with 5 PRs")
    True
    >>> len(format_guild_post("x" * 2000, "general")) <= 1500
    True
    """
    import re

    result = translate_text(content)

    for pattern in _GRIP_JARGON_PATTERNS:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)

    # Clean up orphaned fragments from removals
    result = re.sub(r'\.\s*\.', '.', result)            # double dots
    result = re.sub(r',\s*\.', '.', result)              # comma before dot
    result = re.sub(r'\b(?:This|That|The|A|An)\s+(?:is|was|says?)\s*\.', '', result)  # orphaned stubs
    result = re.sub(r'(?:^|\.\s*)\w{1,8}\s*\.', '.', result)  # tiny orphaned sentences
    result = re.sub(r'  +', ' ', result)                 # double spaces
    result = re.sub(r'^\s+$', '', result, flags=re.MULTILINE)  # blank lines
    result = re.sub(r'\n{3,}', '\n\n', result)           # triple+ newlines
    result = re.sub(r'^\.\s*', '', result)               # leading dot
    result = result.strip()

    limit = _CHANNEL_LIMITS.get(channel, 1800)
    if len(result) > limit:
        result = result[:limit - 3].rsplit(' ', 1)[0] + '...'

    return result


# ── CLI: one-call plain-language genome status ────────────────────────
#
# `python lib/plain_language.py genome` reads cache/genome.json and prints the
# plain-English explanation. The one-call hook any session / agent / the /evo
# cron uses to get the genome state in plain language, so the raw fitness
# number is never surfaced bare. `--concise` for the one-liner.

def _genome_status_plain(concise: bool = False) -> str:
    """Read cache/genome.json and return explain_genome() output.

    Path-portable: CLAUDE_DIR env wins, else this file's repo root
    (lib/plain_language.py -> parent.parent). Fail-soft on any error.
    """
    import json
    import os
    from pathlib import Path

    root = Path(os.environ.get("CLAUDE_DIR") or Path(__file__).resolve().parent.parent)
    path = root / "cache" / "genome.json"
    try:
        g = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        # fail-soft on missing / unreadable / corrupt genome.json; a real bug
        # (NameError/TypeError) is NOT masked (AGORA #3399 r2, Speaker B).
        return f"genome status unavailable ({type(exc).__name__})"
    history = g.get("fitness_history", [])
    # No history -> fitness=None so explain_genome reports "no fitness yet"
    # rather than fabricating a misleading 0.000 average (AGORA #3399).
    fitness = history[-1] if history else None
    return explain_genome(
        generation=g.get("generation", 0),
        gene_count=len(g.get("genes", {})),
        fitness=fitness,
        fitness_history=history,
        concise=concise,
    )


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] == "genome":
        print(_genome_status_plain(concise="--concise" in argv))
    else:
        print("usage: python lib/plain_language.py genome [--concise]")
