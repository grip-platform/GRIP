"""Scorecard data for the AGORA council-picker flip-cards page (GRIP#9404, pick 1).

Pure data layer: reads the shadow-trial JSONL records
(``docs/measurements/2026-11-24-agora-judge-trial-300s.jsonl`` and
``2026-11-24-agora-speaker-trial.jsonl``, explained in
``docs/measurements/2026-11-24-agora-seat-trials.md``) and ``lib/hal_council_dispatch.py``'s
live roster, and builds one :class:`Candidate` per (seat, incumbent|challenger) -- six for the
three seats this pick covers: the judge fallback, the technical speaker and the quality
speaker. Every number on a card comes from a trial record; a model absent from its seat's file
gets ``scorecard=None`` ("no trial yet"), never an invented number.

No network, no writes -- the renderer (``lib/council_picker_page.py``) and the CLI
(``bin/grip-council-picker``) do the one write. Moving a seat stays a ``lib/hal_council_dispatch.py``
change V>> merges (GRIP#9404); this module only reads that file, through an injectable module
reference, to mark which card is seated today.
"""
from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
_MEASUREMENTS = CLAUDE_DIR / "docs" / "measurements"
JUDGE_TRIAL_FILE = _MEASUREMENTS / "2026-11-24-agora-judge-trial-300s.jsonl"
SPEAKER_TRIAL_FILE = _MEASUREMENTS / "2026-11-24-agora-speaker-trial.jsonl"

_VENDOR_NAMES = {
    "moonshot": "Moonshot", "xai": "xAI", "qwen": "Alibaba (DashScope)",
    "deepseek": "DeepSeek", "gemini": "Google", "google": "Google",
    "openai": "OpenAI", "groq": "Groq", "claude": "Anthropic", "anthropic": "Anthropic",
}
_DISPLAY_NAMES = {
    "kimi-k2.6": "Kimi K2.6", "kimi-k3.1-pro": "Kimi K3.1 Pro",
    "grok-4.3": "Grok 4.3", "grok-4.7": "Grok 4.7",
    "qwen3.5-flash": "Qwen3.5-Flash", "qwen4-max": "Qwen4-Max",
}


def bare_id(happi_provider: str) -> str:
    """The model id with any ``vendor/`` prefix removed."""
    return (happi_provider or "").rsplit("/", 1)[-1]


def vendor_of(happi_provider: str) -> str:
    """A human vendor name from a happi provider address's prefix; the prefix itself,
    title-cased, when it is not one of the known ones (honest, never a crash)."""
    prefix = (happi_provider or "").split("/", 1)[0]
    return _VENDOR_NAMES.get(prefix, prefix.title() or "Unknown")


def display_name(model_id: str) -> str:
    """A friendly model name; the bare id itself when none is known."""
    return _DISPLAY_NAMES.get(model_id, model_id)


@dataclass(frozen=True)
class Scorecard:
    """One model's shadow-trial numbers for one seat. Every field is read from a
    trial record; a field a seat's trial shape does not produce (e.g. a speaker's
    "agreement with the reference" -- there is no single reference reply) stays
    ``None`` rather than being invented."""

    source_file: str
    sittings: int
    answered: Optional[int] = None
    valid: Optional[int] = None
    agrees_with_reference: Optional[int] = None
    concerns: Optional[int] = None
    verified: Optional[int] = None
    unquotable: Optional[int] = None
    not_found: Optional[int] = None
    median_seconds: Optional[float] = None
    slowest_seconds: Optional[float] = None
    price_in: Optional[float] = None
    price_out: Optional[float] = None


@dataclass(frozen=True)
class Candidate:
    """One card: a model, in one role, for one seat."""

    seat: str          # "judge_fallback" | "technical" | "quality"
    seat_label: str     # "Judge fallback" | "Technical speaker" | "Quality speaker"
    role: str           # "incumbent" | "challenger"
    address: str        # full happi provider address, e.g. "moonshot/kimi-k3.1-pro"
    model_id: str        # bare id, e.g. "kimi-k3.1-pro"
    vendor: str
    display: str
    seated: bool
    scorecard: Optional[Scorecard]  # None => "no trial yet"


@dataclass(frozen=True)
class SeatSpec:
    """Which two models a seat's shadow trial compares, and where the rows live."""

    key: str
    label: str
    incumbent: str       # happi provider address
    challenger: str
    trial_file: Path
    kind: str             # "judge" | "speaker"
    seat_key: str = ""    # speaker rows key their seat by this ("technical"/"quality")


# The pairing mirrors scripts/agora_judge_trial.py's INCUMBENT/CHALLENGER and
# scripts/agora_speaker_trial.py's SEATS -- update both places together if a new
# shadow trial changes which models are compared.
DEFAULT_SEATS: tuple[SeatSpec, ...] = (
    SeatSpec("judge_fallback", "Judge fallback", "moonshot/kimi-k2.6",
             "moonshot/kimi-k3.1-pro", JUDGE_TRIAL_FILE, "judge"),
    SeatSpec("technical", "Technical speaker", "xai/grok-4.3",
             "xai/grok-4.7", SPEAKER_TRIAL_FILE, "speaker", "technical"),
    SeatSpec("quality", "Quality speaker", "qwen/qwen3.5-flash",
             "qwen/qwen4-max", SPEAKER_TRIAL_FILE, "speaker", "quality"),
)


def _read_jsonl(path: Path) -> list[dict]:
    """Every parsed row of ``path``; ``[]`` for a missing or empty file."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _same_verdict(left: Optional[str], right: Optional[str]) -> bool:
    """Whatever the spelling: the judge parser writes DEFER_CHANGES, the reference DEFER CHANGES."""
    if left is None or right is None:
        return False
    return left.replace("_", " ").strip().upper() == right.replace("_", " ").strip().upper()


_SIDES = ("incumbent", "challenger")


def _judge_row_cells(row: dict, side_model: str) -> list[tuple[dict, str]]:
    """The (cell, reference) pairs where ``side_model`` sat in this one judge row."""
    return [(row[side], row["reference"]) for side in _SIDES if row[side].get("judge") == side_model]


def _judge_cells(rows: list[dict], side_model: str) -> list[tuple[dict, str]]:
    """Every (cell, reference) pair where ``side_model`` sat as either side of a row."""
    cells: list[tuple[dict, str]] = []
    for row in rows:
        if "incumbent" in row and "challenger" in row:
            cells.extend(_judge_row_cells(row, side_model))
    return cells


def _speaker_row_cells(seat: dict, side_model: str) -> list[dict]:
    """The reply cells where ``side_model`` sat in this one seat's row."""
    return [seat[side] for side in _SIDES if seat.get(side, {}).get("model") == side_model]


def _speaker_cells(rows: list[dict], seat_key: str, side_model: str) -> list[dict]:
    """Every per-sitting reply cell where ``side_model`` sat as either side of ``seat_key``."""
    cells: list[dict] = []
    for row in rows:
        seat = row.get(seat_key)
        if seat:
            cells.extend(_speaker_row_cells(seat, side_model))
    return cells


def _median_seconds(seconds: list[float]) -> Optional[float]:
    return round(statistics.median(seconds), 1) if seconds else None


def _max_seconds(seconds: list[float]) -> Optional[float]:
    return round(max(seconds), 1) if seconds else None


def _judge_scorecard(rows: list[dict], side_model: str, source_name: str) -> Optional[Scorecard]:
    """One side's scorecard from judge-trial rows (each row has an incumbent and a
    challenger cell plus the posted reference verdict). ``None`` when ``side_model``
    names neither side of any row -- "no trial yet", never an invented number."""
    cells = _judge_cells(rows, side_model)
    if not cells:
        return None
    all_seconds = [cell["seconds"] for cell, _ref in cells]
    valid_seconds = [cell["seconds"] for cell, _ref in cells if cell["valid"]]
    agrees = sum(1 for cell, ref in cells if cell["valid"] and _same_verdict(cell["verdict"], ref))
    return Scorecard(
        source_file=source_name,
        sittings=len(cells),
        valid=sum(1 for cell, _ref in cells if cell["valid"]),
        agrees_with_reference=agrees,
        median_seconds=_median_seconds(all_seconds),
        slowest_seconds=_max_seconds(valid_seconds),
    )


def _speaker_scorecard(rows: list[dict], seat_key: str, side_model: str,
                        source_name: str) -> Optional[Scorecard]:
    """One side's scorecard from speaker-trial rows (each row carries a seat's
    incumbent and challenger reply, scored by AGORA's own quote gate). ``None``
    when ``side_model`` names neither side of any row at ``seat_key``."""
    cells = _speaker_cells(rows, seat_key, side_model)
    if not cells:
        return None
    seconds = [cell["seconds"] for cell in cells]
    return Scorecard(
        source_file=source_name,
        sittings=len(cells),
        answered=sum(1 for cell in cells if cell["answered"]),
        concerns=sum(cell["concerns"] for cell in cells),
        verified=sum(cell["verified"] for cell in cells),
        unquotable=sum(cell["unquotable"] for cell in cells),
        not_found=sum(cell["not_found"] for cell in cells),
        median_seconds=_median_seconds(seconds),
        slowest_seconds=_max_seconds(seconds),
    )


# Known never to have real pricing (lib/hal/cost_tracker's billed-at-a-guess rate) --
# a price resolving to exactly this is reported as unpriced, never shown as if real.
_UNPRICED_SENTINEL = (3.0, 15.0, 0.3, 3.75)


def _price_for(model_id: str) -> tuple[Optional[float], Optional[float]]:
    """(price-in, price-out) per million tokens for ``model_id``, from the cost
    tracker's own table; ``(None, None)`` when it has no real row for it."""
    from lib.hal import cost_tracker as ct
    rates = ct._resolve_pricing(model_id)
    if rates == _UNPRICED_SENTINEL:
        return None, None
    return rates[0], rates[1]


# AGORA's own round-robin role-chain algorithm -- a small pure copy of
# lib/agora_pr_review.py::_build_role_chains (that module is thousands of lines;
# this is the ~10-line part that matters here). Role i's chain starts at roster
# position i and wraps, exactly as AGORA assigns plain_language/quality/technical
# to the funded speaker ladder in lib/hal_council_dispatch.py. If the two ever
# drift, update both together.
_AGORA_ROLES = ("plain_language", "quality", "technical")


def _role_chains(roster) -> dict[str, list[tuple]]:
    available = [(e[0], e[1], e[2]) for e in roster if e[2]]
    chains: dict[str, list[tuple]] = {role: [] for role in _AGORA_ROLES}
    n = len(available)
    if n:
        for i, role in enumerate(_AGORA_ROLES):
            chains[role] = [available[(i + j) % n] for j in range(n)]
    return chains


def _chain_head(chain: list[tuple]) -> Optional[str]:
    """The bare id leading a role chain, or None when the chain is empty."""
    return bare_id(chain[0][1]) if chain else None


def seated_models(hcd=None) -> dict[str, Optional[str]]:
    """The bare id currently seated at each of the three seats, read live from
    ``lib/hal_council_dispatch.py``. ``hcd`` is injectable (any object carrying
    ``_JUDGE_FALLBACKS`` and ``_AGORA_SPEAKER_ROSTER``) so tests do not depend on
    which secrets happen to be present in this environment.

    The judge-fallback seat is the ladder's first rung (the one every shadow trial
    treats as the incumbent). The technical/quality seats are read by applying
    AGORA's own role-chain algorithm (``_role_chains`` above) to the live roster,
    rather than hardcoding today's seated model id here. A seat that cannot be
    read is None: with no provider credentials every role chain is empty
    (GRIP#9517), and then no card claims to be seated.
    """
    if hcd is None:
        from lib import hal_council_dispatch as hcd
    judges = hcd._JUDGE_FALLBACKS
    chains = _role_chains(hcd._AGORA_SPEAKER_ROSTER)
    return {
        "judge_fallback": bare_id(judges[0][1]) if judges else None,
        "technical": _chain_head(chains["technical"]),
        "quality": _chain_head(chains["quality"]),
    }


def _one_candidate(spec: SeatSpec, role: str, address: str, rows: list[dict],
                    seated: dict[str, Optional[str]]) -> Candidate:
    """Build one card for ``role`` ("incumbent"/"challenger") at ``spec``'s seat."""
    model_id = bare_id(address)
    if spec.kind == "judge":
        card = _judge_scorecard(rows, address, spec.trial_file.name)
    else:
        card = _speaker_scorecard(rows, spec.seat_key, address, spec.trial_file.name)
    if card is not None:
        price_in, price_out = _price_for(model_id)
        card = replace(card, price_in=price_in, price_out=price_out)
    return Candidate(
        seat=spec.key, seat_label=spec.label, role=role, address=address,
        model_id=model_id, vendor=vendor_of(address), display=display_name(model_id),
        seated=(model_id == seated.get(spec.key)), scorecard=card,
    )


def build_candidates(seats: tuple[SeatSpec, ...] = DEFAULT_SEATS,
                      seated: Optional[dict[str, Optional[str]]] = None) -> list[Candidate]:
    """Every card this page shows: two per seat (incumbent, challenger), in seat order."""
    seated = seated_models() if seated is None else seated
    candidates: list[Candidate] = []
    for spec in seats:
        rows = _read_jsonl(spec.trial_file)
        candidates.append(_one_candidate(spec, "incumbent", spec.incumbent, rows, seated))
        candidates.append(_one_candidate(spec, "challenger", spec.challenger, rows, seated))
    return candidates


def build_proposal_text(candidate: Candidate, seated_candidate: Candidate) -> str:
    """The exact, copyable roster-change proposal for locking ``candidate`` into its
    seat. Never code, never a write -- moving a seat stays the ``lib/`` PR V>> merges
    (GRIP#9404). ``seated_candidate`` is the candidate currently seated for the same seat."""
    if candidate.model_id == seated_candidate.model_id:
        return (
            f"{candidate.seat_label}: {candidate.display} is already seated -- "
            "no change proposed."
        )
    lines = [
        f"Proposed seat change -- {candidate.seat_label} (AGORA)",
        "File: lib/hal_council_dispatch.py",
        f"Current: {seated_candidate.address} ({seated_candidate.display})",
        f"Proposed: {candidate.address} ({candidate.display})",
    ]
    if candidate.scorecard is not None:
        lines.append(
            f"Based on: {candidate.scorecard.source_file} "
            f"({candidate.scorecard.sittings} sittings)")
    else:
        lines.append("Based on: no trial yet -- run the shadow trial before moving this seat")
    lines.append("This page only proposes -- V>> merges the lib/ change.")
    return "\n".join(lines)


__all__ = [
    "Scorecard", "Candidate", "SeatSpec", "DEFAULT_SEATS",
    "bare_id", "vendor_of", "display_name",
    "seated_models", "build_candidates", "build_proposal_text",
    "JUDGE_TRIAL_FILE", "SPEAKER_TRIAL_FILE",
]
