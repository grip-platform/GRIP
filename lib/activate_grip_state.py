"""TUI++ wizard state persistence — schema parity with legacy wizard (issue #2520).

The archived skill at skills/_archived/authenticating-with-claude/SKILL.md (v3.0.0) ships
a structured `.onboarded.json` whose schema downstream consumers (upgrade
detection, settings.local.json regeneration, hooks/session-start) depend on.

The TUI++ replacement MUST emit the SAME schema so post-wizard filesystem state
is indistinguishable — that's the parity verification target named in issue
#2520's W5 wave and acceptance criterion 3 ("post-state matches legacy").
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ONBOARDED_SCHEMA_VERSION = "6.4.0"
EXPECTED_STEPS: tuple[str, ...] = (
    "auth_check",
    "identity_setup",
    "persona_selection",
    "signature_system",
    "usage_mode",
    "extended_thinking",
    "budget_mode",
    "usage_tier",
    "output_tier",
    "embeddings_warm",
    "settings_local_generated",
    "confirm_save",
)


def build_onboarded_payload(
    operator: str,
    *,
    settings_local_generated: bool = True,
    identity_file: bool = True,
    extra_steps: list[str] | None = None,
    completed_at: str | None = None,
) -> dict[str, object]:
    """Build the dictionary written to `.onboarded.json` — pure function, no I/O."""
    if not isinstance(operator, str) or len(operator) != 1 or not ("A" <= operator <= "Z"):
        raise ValueError(
            f"operator must be a single uppercase letter A-Z, got {operator!r}"
        )

    timestamp = completed_at or datetime.now(timezone.utc).isoformat()
    steps = list(EXPECTED_STEPS) + (extra_steps or [])
    return {
        "version": ONBOARDED_SCHEMA_VERSION,
        "completed_at": timestamp,
        "operator": operator,
        "steps_completed": steps,
        "settings_local_generated": settings_local_generated,
        "identity_file": identity_file,
    }


def write_onboarded(
    grip_home: Path,
    operator: str,
    *,
    settings_local_generated: bool = True,
    identity_file: bool = True,
    extra_steps: list[str] | None = None,
) -> Path:
    """Write `.onboarded.json` with structured GRIP activation state.

    Returns the path written. Caller is responsible for ensuring grip_home
    exists; this function does not create the directory itself (so an
    operator typo doesn't silently shadow a real GRIP install).
    """
    payload = build_onboarded_payload(
        operator,
        settings_local_generated=settings_local_generated,
        identity_file=identity_file,
        extra_steps=extra_steps,
    )
    target = grip_home / ".onboarded.json"
    target.write_text(json.dumps(payload, indent=2) + "\n")
    return target


def _insert_step(steps: list, step: str, *, after: str = "", before: str = "") -> bool:
    """Idempotently insert `step` relative to an anchor. Returns True iff
    inserted (i.e. it was absent AND the anchor is present). Pure list
    mutation — no I/O. Extracted so each schema migration is a one-line
    declaration (KISS/CC: keeps migrate_onboarded_to_current small)."""
    if step in steps:
        return False
    if after and after in steps:
        steps.insert(steps.index(after) + 1, step)
        return True
    if before and before in steps:
        steps.insert(steps.index(before), step)
        return True
    return False


def migrate_onboarded_to_current(grip_home: Path) -> dict[str, object] | None:
    """Idempotent migration of a pre-current `.onboarded.json` to current schema.

    AGORA Council 2026-05-21, concern 4 (PR #3115): existing onboarded users
    had `.onboarded.json` without later steps in `steps_completed`. Without a
    migration path, the legacy parity test (W5) flags drift forever.

    Migrations applied (each idempotent — fires only when the step is absent):
      v6.2.0 → insert "output_tier" after "budget_mode"
      v6.3.0 → insert "usage_tier" before "output_tier" (plan-tier step,
               grip-tier feature 2026-05-28); anchored on output_tier so the
               order (budget_mode → usage_tier → output_tier) holds even if
               the v6.2.0 migration just inserted output_tier above.

    Returns the file's payload (migrated or not), or None if no file exists.
    Falsifier: running twice on the same file must produce identical output
    (test asserts idempotency).
    """
    target = grip_home / ".onboarded.json"
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text())
    except (OSError, ValueError):
        return None  # corrupt — leave alone, fresh wizard will overwrite

    steps = list(data.get("steps_completed", []))
    changed = _insert_step(steps, "output_tier", after="budget_mode")
    changed = _insert_step(steps, "usage_tier", before="output_tier") or changed
    # v6.4.0: opt-in always-warm embeddings step (grip embeddings-warm wizard)
    changed = _insert_step(steps, "embeddings_warm", after="output_tier") or changed

    if changed:
        data["steps_completed"] = steps

    # Version bump only if we actually migrated content
    if changed and data.get("version") != ONBOARDED_SCHEMA_VERSION:
        data["version"] = ONBOARDED_SCHEMA_VERSION

    if changed:
        target.write_text(json.dumps(data, indent=2) + "\n")
    return data


def build_identity_md(
    name: str,
    persona: str,
    signature: str,
    mode: str = "solo",
    extended_thinking: bool = False,
) -> str:
    """Build identity.md content — pure function, no I/O."""
    if not name or not name.strip():
        raise ValueError(f"name must be a non-empty string, got {name!r}")
    if not persona or not persona.strip():
        raise ValueError(f"persona must be a non-empty string, got {persona!r}")
    if not isinstance(signature, str):
        raise ValueError(f"signature must be a string, got {signature!r}")

    iso_today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"# GRIP Identity\n\n"
        f"name: {name}\n"
        f"persona: {persona}\n"
        f"signature: {signature}\n"
        f"mode: {mode}\n"
        f"extended_thinking: {str(extended_thinking).lower()}\n"
        f"created: {iso_today}\n"
    )


def write_identity(
    grip_home: Path,
    name: str,
    persona: str,
    signature: str,
    mode: str = "solo",
    extended_thinking: bool = False,
) -> Path:
    """Write `facts/identity.md` with operator metadata."""
    facts_dir = grip_home / "facts"
    facts_dir.mkdir(exist_ok=True)
    target = facts_dir / "identity.md"
    target.write_text(
        build_identity_md(name, persona, signature, mode, extended_thinking)
    )
    return target
