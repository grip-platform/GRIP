"""P2 W1 — the thin `/holistic save` facade.

plans/unified-holistic-command.md W1 (FAST). `/holistic save` ≡
`grip_save.save_all()` THEN `holistic_synthesiser.regenerate()` (which
itself does synthesise → compare-and-skip → atomic write → digest-bearing
broadcast). Pure ordered composition — ZERO new behaviour (H-HOL-3: byte-
equivalent to running the two separately).

Rule 21: composes `grip_save.save_all` + `holistic_synthesiser.regenerate`
UNCHANGED. KISS — no new persistence/broadcast logic here.

The W0 provenance gate is ARMED for the duration (set→try/finally→clear):
while it runs, the PreToolUse hook DENIES any git-mutation/closeout — and
`/holistic save` provably needs none (it only persists memory + regenerates
a file + broadcasts a digest). Honest-determinism boundary: the ledger is
a ✓/✗/skipped status line; it NEVER prints the word "deterministic" for
the composite (save_all's subsystems are best-effort, not deterministic).
"""
from __future__ import annotations

from typing import Optional

from lib.gates.holistic_provenance import clear_provenance, set_provenance


def holistic_save(achievement: Optional[str] = None, *,
                  quiet: bool = False) -> dict:
    """Arm provenance → save_all → regenerate → disarm. Status ledger.

    Returns {"save": <save_all dict>, "regenerate": <regenerate dict>,
    "ledger": [<plain ✓/✗/skipped lines>]} — no synthesised verdict, no
    "deterministic" claim for the composite.
    """
    # Import here (not module-top) so this module stays importable even if
    # an optional subsystem is mid-migration — fail honest, not at import.
    from lib.grip_save import save_all
    from lib.holistic_synthesiser import regenerate

    set_provenance("holistic save", ttl_seconds=1800)  # arm W0 gate
    ledger: list[str] = []
    try:
        save = save_all(achievement, quiet=quiet)
        ledger.append(
            f"{'✓' if save.get('failed', 1) == 0 else '✗'} save_all "
            f"({save.get('passed', '?')} passed / {save.get('failed', '?')} failed)"
        )
        regen = regenerate()
        ledger.append(_regenerate_line(regen))
    finally:
        clear_provenance()  # disarm even on exception (gate must not stick)
    return {"save": save, "regenerate": regen, "ledger": ledger}


def _regenerate_line(regen: dict) -> str:
    """One ledger line for regenerate()'s result. A refusal means the kernel
    was not written at all, so it is never reported as an unchanged one."""
    if regen.get("refused"):
        return f"✗ regenerate refused ({regen['refused']})"
    if not regen.get("changed"):
        return "skipped (substrate unchanged)"
    return f"✓ regenerate (digest {str(regen.get('digest', ''))[:12]})"
