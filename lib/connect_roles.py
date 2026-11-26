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
- R3: a metered or free model sits on a seat with no token bound, a metered seat
  has no list price recorded, or a metered seat's worst case at list price
  exceeds the per-run cap.
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

The operator may dictate any seat (``dictate``): a seat id from the role file, or
any HAL model address in ``config/model_catalog.yaml``. A dictated role keeps that
one seat only, so it is never swapped for another, and R1 to R5 still decide: a
dictated seat that breaks a rule leaves no triple, and the refusal names the rule.
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

ROLES = ("planner", "coder", "reviewer")
BILLING_CLASSES = ("subscription", "membership", "free", "metered")
#: Ties go flat-rate first, then free, then metered.
_TIE = {"subscription": 0, "membership": 0, "free": 1, "metered": 2}
#: The harness each transport runs in. R1 keeps the planner and coder apart.
_HARNESS = {"claude-p": "claude-code", "codex-agent": "codex", "council": "http", "hal": "http"}
DEFAULT_ROLES = Path(__file__).resolve().parents[1] / "config" / "connect-roles.json"
DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "config" / "model_catalog.yaml"
MICRO = 1_000_000


class BudgetRefused(RuntimeError):
    """A call would break the per-run cap or its seat's prompt bound. Nothing was sent."""


class DictateRefused(ValueError):
    """A dictated seat cannot be built: it names nothing, its maker is unknown, or the catalogue is unreadable."""


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
    #: Bounds per role and list prices per HAL address, for dictated HAL seats.
    dictated: dict = field(default_factory=dict)


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
                 repair_rounds=int(data["repair_rounds"]), dictated=dict(data.get("dictated") or {}))


# --- dictated seats ---------------------------------------------------------------


def _catalog_entries(path: Path | None = None) -> dict:
    """``{address: entry}`` for every model in the HAL catalogue; an unreadable catalogue is a refusal."""
    import yaml

    where = Path(path or DEFAULT_CATALOG)
    try:
        data = yaml.safe_load(where.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise DictateRefused(f"cannot read the HAL catalogue at {where}: {exc}") from exc
    lists = [data] if isinstance(data, list) else [v for v in data.values() if isinstance(v, list)]
    return {row["address"]: row for row in itertools.chain.from_iterable(lists)
            if isinstance(row, dict) and row.get("address")}


def _billing(entry: dict) -> str:
    """A dictated HAL seat's billing class, failing closed: free on a local tier, else metered.

    Neither catalogue field decides. ``paid`` marks open weights, which are often
    served through a metered endpoint (mistral/mistral-small-2603 through
    api.mistral.ai). A ``subscription`` tier names how GRIP usually reaches a model
    through its own harness (claude-p, codex-agent), not what a HAL call bills. So
    every non-local HAL call is metered and R3 demands a list price; a flat-rate
    plan reached through HAL over-reserves against the cap, the safe direction.
    """
    return "free" if entry.get("tier") == "local" else "metered"


def _list_price(roles: Roles, address: str) -> dict:
    """The operator's price row for ``address``, else the published price HAL bills, else nothing."""
    row = (roles.dictated.get("prices") or {}).get(address)
    if row:
        return row
    from lib.hal.cost_tracker import list_price

    published = list_price(address)
    return {"price_in": published[0], "price_out": published[1]} if published else {}


def known_vendor(model: str) -> str:
    """The maker of a model id (any provider prefix dropped), or "" when the classifier does not know it.

    One classification serves both the dictated seat and the conductor's check on
    what actually answered, so R1, R2 and that check cannot disagree: a provider
    is not always the maker (qwen serves Alibaba's models, openrouter anyone's).
    """
    from lib.roundtable import VENDOR, vendor_of

    bare = str(model).rsplit("/", 1)[-1]
    vendor = vendor_of(bare)
    return "" if vendor == bare and bare not in VENDOR else vendor


def _hal_seat(roles: Roles, role: str, address: str, entries: dict) -> Seat:
    """A seat for a HAL model address, bounded and priced from the role file's ``dictated`` table.

    The maker comes from the shared classifier only. A model whose maker it cannot
    name is refused, never guessed from the provider: a provider name stands in for
    no maker (ollama serves Google's Gemma, openrouter serves anyone's), and a guess
    would let two seats from one maker pass R1.
    """
    entry = entries.get(address)
    if entry is None:
        raise DictateRefused(f"{address!r} is neither a {role} seat in the role file nor a model "
                             f"in the HAL catalogue")
    vendor = known_vendor(address)
    if not vendor:
        raise DictateRefused(f"{address!r}: the vendor classifier (lib/model_attribution.py) cannot name this "
                             f"model's maker, so R1 cannot be checked; add the model family there first")
    price = _list_price(roles, address)
    bound = (roles.dictated.get("bounds") or {}).get(role) or {}
    return Seat(role=role, id=address, vendor=vendor, transport="hal",
                model=address, billing=_billing(entry), rank=0,
                price_in=float(price.get("price_in", 0.0)), price_out=float(price.get("price_out", 0.0)),
                max_in=int(bound.get("max_in", 0)), max_out=int(price.get("max_out", bound.get("max_out", 0))))


def dictate(roles: Roles, choices: dict, catalog: Path | None = None) -> Roles:
    """The role file with each dictated role reduced to the one seat the operator named.

    A choice is a seat id from the role file or a HAL model address from the
    catalogue. Nothing is waved through: a dictated seat that breaks R1 to R5
    leaves no triple, and the refusal names the rule. Raises DictateRefused for a
    choice that names neither, a model whose maker is unknown, or an unreadable
    catalogue.
    """
    named = {role: spec for role, spec in choices.items() if spec}
    entries = _catalog_entries(catalog) if any("/" in spec for spec in named.values()) else {}
    seats = dict(roles.seats)
    for role, spec in named.items():
        match = next((seat for seat in roles.seats[role] if seat.id == spec), None)
        seats[role] = (match or _hal_seat(roles, role, spec, entries),)
    return replace(roles, seats=seats) if named else roles


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
    if seat.billing == "metered" and not (seat.price_in > 0 and seat.price_out > 0):
        return f"R3: {seat.id} is metered with no list price recorded for both input and output"
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
