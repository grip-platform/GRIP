"""Welcome memory entry generator for /activate-grip-tui (issue #2520).

Per Rule 20 (Closed-Loop Auto-Insight Propagation), the wizard's exit state
must be persistable so the NEXT session inherits the activation context.
This module generates the memory artefact + index line that auto-memory's
Stop hook will absorb.
"""

from __future__ import annotations

from datetime import datetime, timezone


# Step 5.85 of the wizard: the door the operator says they will run GRIP from.
# Only the terminal door goes through bin/grip and so applies the plan tier's
# launch defaults; the seed says which one was chosen, so a later session can
# explain why a setting did or did not apply.
DOORS = {
    "terminal": "the Terminal, with gg++ (the full GRIP launch: pin, effort, banner)",
    "vscode": "VS Code, with gg++-vscode (GRIP warms context; the account's default model applies)",
    "app": "the Claude Code desktop app or claude.ai (no GRIP launch seam; account defaults)",
    "harness": "a foreign harness, Codex or OpenCode, wired by grip-harness",
}


def welcome_entry(operator: str, first_skill: str = "/coffee", door: str | None = None) -> str:
    """Generate the welcome memory entry for a freshly-activated operator.

    Args:
        operator: Single uppercase letter (e.g., "V", "M").
        first_skill: The skill demoed at wizard exit (default `/coffee` for
            zero-side-effect demonstration of a real GRIP loop).
        door: Where the operator said they will run GRIP (a key of DOORS), or
            None when the wizard did not ask. An unknown door raises, so a typo
            in the wizard never writes a meaningless sentence into memory.

    Returns:
        Markdown-with-frontmatter content suitable for writing to
        `projects/<path>/memory/welcome_grip_first_session.md`.
    """
    if not isinstance(operator, str) or len(operator) != 1 or not ("A" <= operator <= "Z"):
        raise ValueError(
            f"operator must be a single uppercase letter A-Z, got {operator!r}"
        )
    if not isinstance(first_skill, str) or not first_skill.strip():
        raise ValueError(f"first_skill must be a non-empty string, got {first_skill!r}")
    if door is not None and door not in DOORS:
        raise ValueError(f"door must be one of {sorted(DOORS)} or None, got {door!r}")
    door_line = f"Runs GRIP most from {DOORS[door]}.\n\n" if door else ""

    iso_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    return (
        f"---\n"
        f"name: welcome_grip_first_session\n"
        f"description: First GRIP session for operator {operator}>> — activated via TUI++ wizard.\n"
        f"type: project\n"
        f"---\n\n"
        f"Operator {operator}>> activated GRIP at {iso_now}. First skill demo: {first_skill}.\n\n"
        f"{door_line}"
        f"This memory was seeded by `/activate-grip` (TUI++) at first run. Future sessions\n"
        f"will inherit this as the canonical activation record so memory recall on day two\n"
        f"already knows who the operator is and which skill they saw run live.\n\n"
        f"The wizard's three-act structure (Identity → Capability → First Run) is documented\n"
        f"in `skills/activate-grip-tui/SKILL.md`.\n\n"
        f"Hypothesis H-ACTIVATE-TUI-1 (registered at wizard exit) tests whether this flow\n"
        f"shipped a working environment in under 5 minutes wall-clock with positive operator\n"
        f"recall on the delight elements. Verification window 14 days.\n"
    )


def welcome_index_line(operator: str) -> str:
    """One-line MEMORY.md index entry pointing at the welcome memory file.

    Constraints: <=200 chars per Rule 20 / MEMORY.md hygiene.
    """
    if not isinstance(operator, str) or len(operator) != 1 or not ("A" <= operator <= "Z"):
        raise ValueError(
            f"operator must be a single uppercase letter A-Z, got {operator!r}"
        )
    return (
        f"- [Welcome — {operator}>> first GRIP session]"
        f"(welcome_grip_first_session.md) — "
        f"TUI++ wizard activation seed."
    )
