"""B2 — B1 plus the CONFLICT corner (paper §7).

Identical to B1 on PRESENT and MISSING. On CONFLICTING, B2 recognises the
contradiction as a distinct state (full FDE): material -> suspend,
immaterial -> disclose in the audit log, and never assert a value. Still
no sealing, no structured flip records, no negative provenance.
"""

from __future__ import annotations

from typing import Optional

from sunyata_pilot.agents.b1 import B1Agent
from sunyata_pilot.common import ACT_SUSPEND, Disposition


class B2Agent(B1Agent):
    """B1 + CONFLICT corner, no sealing, no negative provenance."""

    name = "B2"

    def _conflict(
        self, var: str, evidence: tuple[str, ...], material: bool
    ) -> Optional[Disposition]:
        if not evidence:
            return self._missing(var, material)
        n = len(evidence)
        if material:
            self._note(f"suspend: {var} (conflict, material; {n} facts seen)")
            return Disposition(
                var=var, action=ACT_SUSPEND, provenance=evidence
            )
        self._note(
            f"disclose: {var} (conflict, immaterial; {n} facts; "
            "no value asserted)"
        )
        return None
