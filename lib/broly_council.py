"""Broly per-role LLM assignment + criterion-mesh evaluation.

Each broly role (analyst, explorer, reviewer, pragmatist, devil, integrator)
maps to a specific HAL provider/model via BrolyRoleAssignment. The council
mode dispatches each role to its assigned LLM in parallel. The criterion-mesh
mode aggregates per-role verdicts into a weighted decision with devil's-veto.

Live HAL dispatch via _query_grip_infer. When the assigned model fails,
falls back to the econ-default per the billing mandate (never CCH for
Anthropic models — broly is class-B machinery). Test injection uses the
role_dispatch / role_voter parameters on evaluate_criterion_mesh.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from lib.hal_llm_adapter import _query_grip_infer, _resolve_econ_default

ROLES = ("analyst", "explorer", "reviewer", "pragmatist", "devil", "integrator")

DEVIL_VETO_THRESHOLD = 0.85
MET_MAJORITY_THRESHOLD = 0.66

DEFAULT_CONFIG_PATH = Path(
    os.environ.get(
        "GRIP_BROLY_CONFIG",
        str(Path.home() / ".claude" / "config" / "broly-roles.json"),
    )
)

_VERDICT_SCORES: dict[str, float] = {"met": 1.0, "inconclusive": 0.5, "unmet": 0.0}

# Without a role_voter the vote is read from free text, so each seat is asked for
# an explicit verdict line and that line wins. Without one, whole words decide:
# the old substring test read "unmet", "method" and "parameter" as "met"
# (GRIP#6651).
_VERDICT_INSTRUCTION = (
    "End your answer with exactly one line: "
    "VERDICT: MET, VERDICT: UNMET or VERDICT: INCONCLUSIVE."
)
_VERDICT_LINE = re.compile(r"^\s*verdict\s*:\s*(met|unmet|inconclusive)\b", re.IGNORECASE | re.MULTILINE)
_NOT_MET = re.compile(r"\bunmet\b|(?:\bnot|n't)\s+(?:\w+\s+){0,2}met\b", re.IGNORECASE)
_MET = re.compile(r"\bmet\b", re.IGNORECASE)


@dataclass(frozen=True)
class BrolyRoleAssignment:
    role: str
    model: str
    weight: float = 1.0


# Providers banned from broly billing (CCH = weekly-capped subscription bucket).
# Broly is class-B internal machinery; routing through CCH would let an overnight
# loop exhaust the operator's interactive cap. See docs/adr/0011-*.md.
_BROLY_BANNED_PROVIDERS = ("anthropic-cch",)


def validate_cch_billing_guard(
    assignments: list[BrolyRoleAssignment],
    *,
    _api_key_env: str = "",
) -> None:
    """Reject routes whose billing class cannot be proven before dispatch.

    A local API key does not prove that the HAL server will use that key. The
    server may satisfy an explicit ``anthropic/*`` model through CCH and report
    its billing provider only after the weekly-capped call has happened.
    Broly is class-B machinery, so it must use non-Anthropic assignments until
    the transport carries a pre-dispatch, enforceable billing-class directive.

    ``_api_key_env`` remains accepted for call-site compatibility but cannot
    weaken this mechanical guard.
    """
    _ = _api_key_env
    has_anthropic = any(a.model.startswith("anthropic/") for a in assignments)
    if not has_anthropic:
        return
    raise RuntimeError(
        "Broly council refuses anthropic/* assignments because HAL cannot "
        "prove the billing class before dispatch. CCH is forbidden for "
        "class-B machinery; configure non-Anthropic role providers."
    )


DEFAULT_ROLE_ASSIGNMENTS: list[BrolyRoleAssignment] = [
    # gemini seats repointed from gemini-2.5-flash on 2026-11-24 (GRIP#9389):
    # Google auto-routes that id to gemini-3.5-flash-lite from 2026-12-01.
    BrolyRoleAssignment("analyst", "gemini/gemini-3.5-flash-lite"),
    BrolyRoleAssignment("explorer", "xai/grok-4.20-0309-non-reasoning"),
    BrolyRoleAssignment("reviewer", "deepseek/deepseek-v4-pro", weight=1.5),
    BrolyRoleAssignment("pragmatist", "groq/openai/gpt-oss-120b"),
    BrolyRoleAssignment("devil", "xai/grok-4.20-0309-non-reasoning", weight=2.0),
    BrolyRoleAssignment("integrator", "gemini/gemini-3.5-flash-lite"),
]
# Diversity guard: gemini / xai / deepseek / groq — 4 provider families.


def load_assignments_from_config(path: Optional[Path] = None) -> list[BrolyRoleAssignment]:
    """Load role assignments from JSON config; fall back to DEFAULT_ROLE_ASSIGNMENTS."""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        data = json.loads(p.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return list(DEFAULT_ROLE_ASSIGNMENTS)
    return [
        BrolyRoleAssignment(
            role=item["role"],
            model=item["model"],
            weight=float(item.get("weight", 1.0)),
        )
        for item in data.get("assignments", [])
    ]


def render_assignments(assignments: list[BrolyRoleAssignment]) -> str:
    """Plain-text aligned table for printing in broly output."""
    role_w = max(len(a.role) for a in assignments)
    lines = ["[broly council]"]
    for a in assignments:
        weight_marker = f" [weight {a.weight}]" if a.weight != 1.0 else ""
        lines.append(f"  {a.role:<{role_w}}  ->  {a.model}{weight_marker}")
    return "\n".join(lines)


# BILLING MANDATE for dispatch_role (decided 2026-06-23):
# broly is class-B GRIP-internal machinery, so an ``anthropic/*`` seat MUST
# bill via the per-token API key (keychain ``grip-anthropic-external``,
# consumption) — NEVER via CCH (``lib/hal/utils/cch.py``: the OAuth token +
# ``x-anthropic-billing-header`` path that bills the Claude SUBSCRIPTION = the
# shared WEEKLY-CAPPED bucket, ``provider="anthropic-cch"``). Routing
# machinery through CCH would let an overnight broly-heavy loop silently
# exhaust the operator's interactive weekly cap. On api-key absence/over-
# budget, fall back to the econ-default NON-Anthropic ladder (groq/gemini),
# never to CCH — monotone-toward-safe: the fallback may only move AWAY from
# the weekly cap, never onto it. Cross-refs: H-HAL-ECON-DEFAULT (the class-B
# demote) + rules/exogenous-anchor-law.md (the anthropic+xai+groq diversity
# that makes a council verdict a valid exogenous anchor).


def _role_result(model: str, role: str, result: Optional[dict]) -> dict:
    """The dispatch record for one role. No result means the seat was unavailable."""
    if not result:
        return {"model": model, "role": role, "output": f"[broly:unavailable] role={role}",
                "tokens": 0, "latency_ms": 0}
    return {"model": model, "role": role, "output": result.get("text", ""),
            "tokens": (result.get("usage") or {}).get("total_tokens", 0), "latency_ms": 0}


def dispatch_role(role: str, prompt: str, assignments: list[BrolyRoleAssignment]) -> dict:
    """Dispatch a single role to its assigned LLM via HAL.

    Returns dict with keys: model, role, output, tokens, latency_ms.
    Tries the assigned model first. On failure, falls back to the
    econ-default (groq/gemini ladder), NEVER to CCH for Anthropic models:
    see the BILLING MANDATE comment above.
    """
    assignment = next((a for a in assignments if a.role == role), None)
    if assignment is None:
        raise ValueError(f"role {role!r} not found in assignments")

    # Must run before the first provider call. A post-response provider check
    # can detect CCH, but it cannot undo the weekly-capped call.
    validate_cch_billing_guard([assignment])
    result = _query_grip_infer(prompt, model=assignment.model, audit=False)
    if result:
        return _role_result(assignment.model, role, result)

    econ = _resolve_econ_default()
    fallback = _query_grip_infer(prompt, model=econ, audit=False)
    if fallback:
        return _role_result(econ, role, fallback)
    return _role_result(assignment.model, role, None)


# ---------------------------------------------------------------------------
# Criterion-mesh helpers (decomposed for CC < 10)
# ---------------------------------------------------------------------------


def _verdict_score(vote: str) -> float:
    """Map vote string to numeric weight. Unknown votes are inconclusive."""
    return _VERDICT_SCORES.get(vote, 0.5)


def _effective_score(vote: str, confidence: float) -> float:
    """The vote's score, pulled toward the neutral 0.5 by its uncertainty.

    Confidence sets how far a vote leans, never which way. The old weighting,
    confidence x score, read doubt as evidence against: a panel unsure at 0.6
    scored 0.3 and landed in UNMET, and with the fallback's fixed 0.5 no panel
    could ever reach MET (GRIP#6651). Confidence is clamped to [0, 1] so the
    score stays in the range verdict_shape_floor enforces.
    """
    c = min(1.0, max(0.0, float(confidence)))
    return 0.5 + c * (_verdict_score(vote) - 0.5)


def _parse_vote(output: str) -> str:
    """A seat's vote from free text: its last VERDICT line if any, else whole words."""
    lines = _VERDICT_LINE.findall(output)
    if lines:
        return lines[-1].lower()
    if _NOT_MET.search(output):
        return "unmet"
    return "met" if _MET.search(output) else "inconclusive"


def _collect_votes(
    assignments: list[BrolyRoleAssignment],
    claim: str,
    state: dict,
    role_dispatch: Callable,
    role_voter: Optional[Callable],
) -> list[dict]:
    """Return per-role vote records."""
    records = []
    for assignment in assignments:
        if role_voter is not None:
            vote, confidence = role_voter(assignment.role, assignment.model, claim, state)
        else:
            prompt = f"{claim}\n\nState: {state}\n\n{_VERDICT_INSTRUCTION}"
            result = role_dispatch(assignment.role, prompt, assignments)
            vote = _parse_vote(result["output"])
            confidence = 0.5
        records.append(
            {
                "role": assignment.role,
                "model": assignment.model,
                "vote": vote,
                "confidence": confidence,
                "weight": assignment.weight,
            }
        )
    return records


def _aggregate(per_role: list[dict]) -> tuple[float, bool]:
    """Return (weighted_met_score, devils_veto)."""
    total_weight = sum(r["weight"] for r in per_role) or 1.0
    weighted_met = sum(r["weight"] * _effective_score(r["vote"], r["confidence"]) for r in per_role) / total_weight
    devils_veto = any(
        r["role"] == "devil" and r["vote"] == "unmet" and r["confidence"] > DEVIL_VETO_THRESHOLD for r in per_role
    )
    return weighted_met, devils_veto


def _final_verdict(weighted_met: float, devils_veto: bool) -> tuple[str, str]:
    """Map aggregated score + devil's veto to (verdict, rationale)."""
    if devils_veto:
        return "unmet", "devil's veto (high-confidence unmet vote)"
    if weighted_met >= MET_MAJORITY_THRESHOLD:
        return "met", f"weighted met-score {weighted_met:.2f} >= {MET_MAJORITY_THRESHOLD}"
    if weighted_met <= (1 - MET_MAJORITY_THRESHOLD):
        return "unmet", f"weighted met-score {weighted_met:.2f} <= {1 - MET_MAJORITY_THRESHOLD}"
    return "inconclusive", f"weighted met-score {weighted_met:.2f} in inconclusive band"


def evaluate_criterion_mesh(
    claim: str,
    state: dict,
    assignments: list[BrolyRoleAssignment],
    *,
    role_dispatch: Callable = dispatch_role,
    role_voter: Optional[Callable] = None,
) -> dict:
    """Aggregate per-role verdicts into a weighted decision with devil's-veto.

    Aggregation rules:
    - Devil's veto: devil votes "unmet" with confidence > DEVIL_VETO_THRESHOLD
      => verdict is "unmet" regardless of majority.
    - Otherwise: weighted_met_score = sum(weight * effective) / sum(weight),
      where effective = 0.5 + confidence * (score - 0.5) and score is
      met=1.0, inconclusive=0.5, unmet=0.0. Confidence sets how far a vote
      leans, never which way, so an unsure panel is inconclusive, not unmet.
    - Verdict: met if score >= 0.66, unmet if <= 0.34, else inconclusive.

    role_voter injection: callable(role, model, claim, state) -> (vote, confidence).
    Absent: dispatch_role is called with a request for a VERDICT line, the vote
    is read by _parse_vote, and confidence is 0.5 (so the devil cannot veto).
    """
    per_role = _collect_votes(assignments, claim, state, role_dispatch, role_voter)
    weighted_met, devils_veto = _aggregate(per_role)
    verdict, rationale = _final_verdict(weighted_met, devils_veto)
    return {
        "verdict": verdict,
        "weighted_met_score": weighted_met,
        "per_role": per_role,
        "devils_veto": devils_veto,
        "rationale": rationale,
    }
