"""Seat assignment for /connect: a planner, a coder and a reviewer from three vendors.

The role rule is the one Fable 5.2 set out for /connect on 2026-11-11 (the
/ultraplan trio's position, sealed). Every planner x coder x reviewer triple in
``config/connect-roles.json`` is enumerated, a triple that breaks R1 to R5 is
dropped, and the survivors sort by planner rank, then coder rank, then reviewer
rank, with billing class breaking ties. The three seats are solved jointly
because a fallback ladder per seat can put two seats on one vendor.

- R1: two seats share a vendor, or the planner and the coder share a harness.
- R2: a never-rule breaks -- GPT off the ChatGPT subscription, Claude on the paid
  key, or Claude on the coder seat.
- R3: a metered or free model sits on a seat with no token bound, or a metered
  seat's worst case at list price exceeds the per-run cap.
- R4: the coder's harness has no verified sandbox.
- R5: the seat has no headroom now (the caller measured it, or it failed this run).

Billing enters through one question: can the conductor bound the seat's
consumption before the call? A planner or reviewer is one text-only call with a
prompt cap and an output cap, so its worst case is a number. The coder is an
agent loop, so its worst case is not, and metered or free models never take it.
Billing is a filter and a tie-break, never a weight against quality: a weight
would need an exchange rate between dollars and plan quality that nobody has
measured.

Money is integer micro-dollars, because a sealed record holds no floats. A price
in USD per million tokens is numerically the price in micro-dollars per token.
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass, fields
from pathlib import Path

ROLES = ("planner", "coder", "reviewer")
BILLING_CLASSES = ("subscription", "membership", "free", "metered")
#: Ties go flat-rate first, then free, then metered.
_TIE = {"subscription": 0, "membership": 0, "free": 1, "metered": 2}
#: The harness each transport runs in. R1 keeps the planner and coder apart.
_HARNESS = {"claude-p": "claude-code", "codex-agent": "codex", "council": "http"}
DEFAULT_ROLES = Path(__file__).resolve().parents[1] / "config" / "connect-roles.json"
MICRO = 1_000_000


class BudgetRefused(RuntimeError):
    """A call would break the per-run cap or its seat's prompt bound. Nothing was sent."""


@dataclass(frozen=True)
class Seat:
    """One row of the role file."""

    role: str
    id: str
    vendor: str
    transport: str
    model: str
    billing: str
    rank: int
    target: str = ""
    sandbox: bool = False
    price_in: float = 0.0
    price_out: float = 0.0
    max_in: int = 0
    max_out: int = 0

    @property
    def bounded(self) -> bool:
        """One call with a prompt cap and an output cap: its worst case is a number."""
        return self.role != "coder" and self.max_in > 0 and self.max_out > 0

    def worst_micro(self, prompt_bytes: int | None = None) -> int:
        """Worst-case cost of one call, in micro-dollars at list price.

        A prompt's byte count bounds its token count, because a byte-level
        tokenizer never makes a token from less than one byte. With no prompt
        in hand, the seat's own ``max_in`` stands in.
        """
        tokens_in = self.max_in if prompt_bytes is None else prompt_bytes
        return math.ceil(tokens_in * self.price_in + self.max_out * self.price_out)

    def public(self) -> dict:
        """What a sealed record says about the seat: no prices, no floats."""
        return {"id": self.id, "vendor": self.vendor, "model": self.model,
                "billing": self.billing, "transport": self.transport}


@dataclass(frozen=True)
class Roles:
    """The parsed role file."""

    seats: dict
    cap_micro: int
    step_seconds: dict
    repair_rounds: int


_SEAT_KEYS = frozenset(f.name for f in fields(Seat)) - {"role"}


def _seat(role: str, row: dict) -> Seat:
    """One seat, refusing an unknown key or billing class rather than guessing."""
    unknown = set(row) - _SEAT_KEYS
    if unknown:
        raise ValueError(f"{role} seat {row.get('id')!r}: unknown keys {sorted(unknown)}")
    if row.get("billing") not in BILLING_CLASSES:
        raise ValueError(f"{role} seat {row.get('id')!r}: billing must be one of {BILLING_CLASSES}")
    return Seat(role=role, **row)


def load_roles(path: Path | None = None) -> Roles:
    """Read the role file (``config/connect-roles.json`` by default)."""
    data = json.loads(Path(path or DEFAULT_ROLES).read_text(encoding="utf-8"))
    seats = {role: tuple(_seat(role, row) for row in data[role]) for role in ROLES}
    return Roles(seats=seats, cap_micro=round(float(data["per_run_cap_usd"]) * MICRO),
                 step_seconds={k: int(v) for k, v in data["step_seconds"].items()},
                 repair_rounds=int(data["repair_rounds"]))


# --- the five rules ---------------------------------------------------------------


def _r1(triple: tuple) -> str:
    if len({seat.vendor for seat in triple}) < len(triple):
        return "R1: two seats share a vendor"
    planner, coder = triple[0], triple[1]
    if _HARNESS.get(planner.transport, planner.transport) == _HARNESS.get(coder.transport, coder.transport):
        return "R1: the planner and the coder share a harness"
    return ""


def _r2(seat: Seat) -> str:
    if seat.vendor == "openai" and (seat.billing, seat.transport) != ("subscription", "codex-agent"):
        return f"R2: {seat.id} would run GPT off the ChatGPT subscription"
    if seat.vendor == "anthropic" and (seat.billing, seat.transport) != ("subscription", "claude-p"):
        return f"R2: {seat.id} would run Claude on the paid key"
    if seat.vendor == "anthropic" and seat.role == "coder":
        return f"R2: {seat.id} would put Claude on the unbounded coder seat"
    return ""


def _r3(seat: Seat, cap_micro: int) -> str:
    if seat.billing not in ("metered", "free"):
        return ""
    if not seat.bounded:
        return f"R3: {seat.id} is {seat.billing} on a seat with no token bound"
    if seat.billing == "metered" and seat.worst_micro() > cap_micro:
        return f"R3: {seat.id} could cost more than the per-run cap in one call"
    return ""


def _r4(seat: Seat) -> str:
    return f"R4: {seat.id} has no verified sandbox" if seat.role == "coder" and not seat.sandbox else ""


def _r5(seat: Seat, unavailable) -> str:
    return f"R5: {seat.id} has no headroom now" if seat.id in unavailable else ""


def _seat_faults(seat: Seat, cap_micro: int, unavailable) -> list:
    return [f for f in (_r2(seat), _r3(seat, cap_micro), _r4(seat), _r5(seat, unavailable)) if f]


def _pin_fault(triple: tuple, pinned) -> str:
    moved = [role for role, seat in zip(ROLES, triple) if role in (pinned or {}) and pinned[role] != seat.id]
    return f"a seat that already served would be replaced: {', '.join(moved)}" if moved else ""


def faults(triple: tuple, cap_micro: int, unavailable=frozenset(), pinned=None) -> list:
    """Every rule the triple breaks; an empty list means the triple may run."""
    found = list(itertools.chain.from_iterable(
        _seat_faults(seat, cap_micro, unavailable) for seat in triple))
    return found + [f for f in (_r1(triple), _pin_fault(triple, pinned)) if f]


def _key(triple: tuple) -> tuple:
    return tuple(seat.rank for seat in triple) + tuple(_TIE[seat.billing] for seat in triple)


def triples(roles: Roles) -> list:
    """Every planner x coder x reviewer triple, best first."""
    return sorted(itertools.product(*(roles.seats[role] for role in ROLES)), key=_key)


def assign(roles: Roles, *, unavailable=frozenset(), pinned=None) -> dict | None:
    """The best triple that breaks no rule, as ``{role: Seat}``, or None."""
    for triple in triples(roles):
        if not faults(triple, roles.cap_micro, unavailable, pinned):
            return dict(zip(ROLES, triple))
    return None


def explain(roles: Roles, *, unavailable=frozenset(), pinned=None) -> list:
    """``[(triple, faults)]`` best first, so a refusal can say what stood in the way."""
    return [(t, faults(t, roles.cap_micro, unavailable, pinned)) for t in triples(roles)]


class Budget:
    """The run's spend ceiling, in micro-dollars.

    A metered call reserves its worst case before it is sent, and the
    reservation stands: the transports report no token counts, so nothing is
    refunded on a guess. The bound holds where the vendor counts reasoning
    tokens inside the output cap the call sends.
    """

    def __init__(self, cap_micro: int) -> None:
        self.cap_micro = cap_micro
        self.reserved_micro = 0

    def reserve(self, seat: Seat, prompt_bytes: int) -> int:
        """Reserve one call's worst case, or raise BudgetRefused. Returns the reservation."""
        if seat.bounded and prompt_bytes > seat.max_in:
            raise BudgetRefused(f"{seat.id}: the prompt is {prompt_bytes} bytes, over its bound "
                                f"of {seat.max_in}")
        if seat.billing != "metered":
            return 0
        cost = seat.worst_micro(prompt_bytes)
        if self.reserved_micro + cost > self.cap_micro:
            raise BudgetRefused(f"{seat.id}: a worst case of {cost} micro-USD would pass the cap "
                                f"({self.reserved_micro} of {self.cap_micro} reserved)")
        self.reserved_micro += cost
        return cost
