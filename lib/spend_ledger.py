"""Model spend over the last ten days, against the cap V>> set. Reports; never enforces.

V>> capped model spend at $25 per ten days on 2026-10-01 and split the cap into
allocations: AGORA's runs in CI, local calls, and a reservation for KERN. Prepaid
balances count inside the cap and are reported apart from new charges (Synthesis
Ladder plan v2, decision 1). On 2026-10-02 he chose to report spend against the
allocations before enforcing anything. AGORA alone had measured about $11 per ten
days at ordinary volume, nearly twice its $6 share, so a hard stop would have
switched the merge judge off for half of every window. This module therefore adds
up and never refuses a call. The allocations live in config/spend-allocations.json.

Each source is the record its own spend already leaves:

* AGORA writes ``<!-- agora-sitting-cost {...} -->`` into every verdict comment
  (``sitting_cost_marker`` in lib/agora_pr_review.py). Those comments are the CI
  ledger: durable, one per sitting, and readable from any machine with ``gh``.
  Only comments by the configured author count, so a quoted marker is not
  counted twice.
* Calls through GRIP's HAL adapter land in the usage chain
  (lib/llm_usage_ledger.py), priced by lib/hal/cost_tracker.
* A reservation is a configured forecast. It is shown apart from measured spend,
  so it is never read as money already gone.

What cannot be priced is counted rather than hidden: AGORA calls whose usage was
never reported, and chain records with no token counts. Measured spend is then a
floor, and the report says so. A source that cannot be read is named, never
counted as $0.

CLI::

    python3 -m lib.spend_ledger              # the report (bin/grip-spend)
    python3 -m lib.spend_ledger --json       # the same, as JSON
    python3 -m lib.spend_ledger --days 3     # a shorter window

Exit status: 0 when every source was read; 1 when one could not be, which says
nothing about the spend itself, since the report still prints and names the
missing source; 2 when the allocations file is unreadable or invalid.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import chain
from pathlib import Path
from typing import Callable, Iterable, Optional

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "spend-allocations.json"
SOURCES = ("agora", "usage_chain", "reservation")
UNCLASSIFIED = "unclassified"
_ROUNDING = 1e-5

# One line per AGORA sitting: when, who posted it, where, and the marker's JSON.
_MARKER_JQ = (
    r'.[] | select(.body | test("agora-sitting-cost")) | '
    r'[.created_at, .user.login, .html_url, '
    r'(.body | capture("agora-sitting-cost (?<j>\\{.*?\\}) -->").j)] | @tsv'
)


@dataclass(frozen=True)
class Charge:
    """One AGORA sitting or one local call: when, what it cost, and by which route."""

    when: datetime
    usd: float
    by_billing: dict = field(default_factory=dict)
    unreported: int = 0


class SourceUnavailable(Exception):
    """A source could not be read. The report names it rather than counting $0."""


# --- configuration -------------------------------------------------------------

def load_config(path: Optional[Path] = None) -> dict:
    """The cap, the window and the allocations, checked so a typo fails loudly."""
    where = Path(path or CONFIG_PATH)
    config = json.loads(where.read_text(encoding="utf-8"))
    problems = config_problems(config)
    if problems:
        raise ValueError(f"{where}: " + "; ".join(problems))
    return config


def config_problems(config: dict) -> list:
    """Everything wrong with a config, as plain sentences; empty when it is sound."""
    problems = []
    if not _amount_over(config.get("cap_usd"), 0):
        problems.append("cap_usd must be a number above 0")
    if not _whole_over(config.get("window_days"), 0):
        problems.append("window_days must be a whole number above 0")
    allocations = config.get("allocations")
    if not isinstance(allocations, list) or not allocations:
        return problems + ["allocations must be a non-empty list"]
    for index, allocation in enumerate(allocations):
        problems += [f"allocations[{index}]: {p}" for p in _allocation_problems(allocation)]
    return problems


def _allocation_problems(allocation) -> list:
    if not isinstance(allocation, dict):
        return ["must be an object"]
    problems = []
    if not str(allocation.get("name") or "").strip():
        problems.append("name is required")
    if not _amount_over(allocation.get("usd"), -1e-12):
        problems.append("usd must be 0 or more")
    if allocation.get("source") not in SOURCES:
        problems.append(f"source must be one of {', '.join(SOURCES)}")
    if allocation.get("source") == "agora" and not (allocation.get("repos") and allocation.get("author")):
        problems.append("an agora source needs repos and author")
    if "records_since" in allocation and not _is_moment(allocation["records_since"]):
        problems.append("records_since must be an ISO 8601 time with a zone")
    return problems


def _is_moment(text) -> bool:
    try:
        _moment(text)
    except (TypeError, ValueError):
        return False
    return True


def _amount_over(value, floor: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > floor


def _whole_over(value, floor: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > floor


# --- helpers ---------------------------------------------------------------------

def billing_of(name: str, table: dict) -> str:
    """The billing route for a provider or model name, by the longest matching prefix.

    A name no prefix matches is unclassified: reported as such, never assumed free.
    """
    low = (name or "").strip().lower()
    for prefix in sorted(table, key=len, reverse=True):
        if low.startswith(prefix.lower()):
            return str(table[prefix])
    return UNCLASSIFIED


def utc_z(moment: datetime) -> str:
    """An aware time as UTC with a trailing Z, safe inside a query string.

    An offset such as +02:00 is not: a query string reads "+" as a space. On
    2026-10-02 a fetch sent that way returned nothing from its first nine hours,
    and nothing said so.
    """
    if moment.tzinfo is None:
        raise ValueError("a naive datetime has no time zone")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(value) -> float:
    """A cost as a finite, non-negative float, else ValueError."""
    if isinstance(value, bool):
        raise ValueError("a cost cannot be a boolean")
    amount = float(value)
    if not math.isfinite(amount) or amount < 0:
        raise ValueError(f"not a cost: {value!r}")
    return amount


def _split(by_name: dict, usd: float, billing: dict) -> dict:
    """Spend by billing route. Any part of the total no name accounts for is unclassified.

    The marker rounds every figure to six places, so the parts can miss the total
    by a few millionths either way. That is rounding, not spend: only a remainder
    above _ROUNDING counts, and never a negative one.
    """
    routes: Counter = Counter()
    for name, cost in by_name.items():
        routes[billing_of(name, billing)] += cost
    rest = usd - sum(by_name.values())
    if rest > _ROUNDING:
        routes[UNCLASSIFIED] += rest
    return dict(routes)


def _moment(text) -> datetime:
    """An ISO 8601 time that names its zone, else ValueError."""
    moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"{text!r} names no time zone")
    return moment


# --- AGORA in CI -------------------------------------------------------------------

def fetch_agora(repo: str, author: str, since: datetime, billing: dict,
                run: Optional[Callable] = None) -> tuple:
    """AGORA's sittings on one repo since a moment, and how many lines were unreadable."""
    url = f"repos/{repo}/issues/comments?since={utc_z(since)}&per_page=100"
    try:
        proc = (run or subprocess.run)(["gh", "api", "--paginate", url, "--jq", _MARKER_JQ],
                                       capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SourceUnavailable(f"{repo}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        reason = f": {detail[-1][:160]}" if detail else ""
        raise SourceUnavailable(f"{repo}: gh exited {proc.returncode}{reason}")
    return parse_agora(proc.stdout.splitlines(), author, since, billing)


def parse_agora(lines: Iterable[str], author: str, since: datetime, billing: dict) -> tuple:
    """Charges from marker lines (created, author, url, json), plus the unreadable count."""
    charges, unreadable = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            charge = _agora_charge(line, author, since, billing)
        except (ValueError, KeyError, TypeError, AttributeError):
            unreadable += 1
            continue
        if charge is not None:
            charges.append(charge)
    return charges, unreadable


def _agora_charge(line: str, author: str, since: datetime, billing: dict) -> Optional[Charge]:
    created, login, _url, blob = line.split("\t")
    when = datetime.fromisoformat(created.replace("Z", "+00:00"))
    if login != author or when < since:
        return None
    data = json.loads(blob)
    usd = _money(data["usd"])
    by_model = {str(model): _money(cost) for model, cost in dict(data.get("by_model") or {}).items()}
    return Charge(when, usd, _split(by_model, usd, billing), int(data.get("unreported") or 0))


# --- local calls -------------------------------------------------------------------

def read_usage_chain(since: datetime, billing: dict, path: Optional[Path] = None) -> tuple:
    """Local calls through GRIP's HAL adapter since a moment, plus the unreadable count."""
    from lib import llm_usage_ledger

    usage_chain = Path(path or llm_usage_ledger.chain_path())
    if not usage_chain.exists():
        raise SourceUnavailable(f"no usage chain at {usage_chain}")
    charges, unreadable = [], 0
    for entry in llm_usage_ledger.entries(usage_chain):
        try:
            charge = _local_charge(entry, since, billing)
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
            unreadable += 1
            continue
        if charge is not None:
            charges.append(charge)
    return charges, unreadable


def _local_charge(entry: dict, since: datetime, billing: dict) -> Optional[Charge]:
    when = datetime.fromtimestamp(float(entry["ts"]), timezone.utc)
    if when < since:
        return None
    usd = _money(entry.get("cost_usd") or 0.0)
    route = billing_of(str(entry.get("provider") or ""), billing)
    if route == UNCLASSIFIED:
        route = billing_of(str(entry.get("model") or ""), billing)
    silent = not (entry.get("input_tokens") or entry.get("output_tokens"))
    return Charge(when, usd, {route: usd}, 1 if silent else 0)


# --- collecting and reporting ----------------------------------------------------

def _read_agora(allocation: dict, ctx: dict) -> tuple:
    charges, unreadable = [], 0
    for repo in allocation["repos"]:
        found, bad = fetch_agora(repo, allocation["author"], ctx["since"], ctx["billing"], ctx["run"])
        charges += found
        unreadable += bad
    return charges, unreadable


def _read_chain(_allocation: dict, ctx: dict) -> tuple:
    return read_usage_chain(ctx["since"], ctx["billing"], ctx["chain"])


def _read_reservation(_allocation: dict, _ctx: dict) -> tuple:
    return [], 0


_READERS = {"agora": _read_agora, "usage_chain": _read_chain, "reservation": _read_reservation}


def collect(config: dict, now: datetime, days: int, run: Optional[Callable] = None,
            usage_chain: Optional[Path] = None) -> dict:
    """Read every allocation's source over the window and build the report."""
    since = now - timedelta(days=days)
    ctx = {"since": since, "billing": dict(config.get("billing_by_prefix") or {}),
           "run": run, "chain": usage_chain}
    readings = []
    for allocation in config["allocations"]:
        reading = {"allocation": allocation, "charges": [], "unreadable": 0, "problem": None}
        try:
            reading["charges"], reading["unreadable"] = _READERS[allocation["source"]](allocation, ctx)
        except SourceUnavailable as exc:
            reading["problem"] = str(exc)
        readings.append(reading)
    return build_report(config, readings, now, since)


def agora_window(now: Optional[datetime] = None, config: Optional[dict] = None,
                 run: Optional[Callable] = None) -> dict:
    """The CI allocation's line for AGORA's own footer, or {} when none reads AGORA.

    Only the first agora allocation is read, so a sitting in CI never looks for
    a local usage chain it does not have. The line carries the window, the cap
    and the repos it counts, so the footer can say what it covers.

    Unless it is empty, the dict always has the keys AGORA's footer reads:
    ``problem`` (None when the source was read), ``measured_usd``, ``items``,
    ``unreported``, ``record_starts``, ``allocation_usd``, ``repos``, ``cap_usd``
    and ``days``. The footer still survives a dict that breaks this (GRIP#7138).
    """
    config = config or load_config()
    agora = [a for a in config["allocations"] if a["source"] == "agora"][:1]
    if not agora:
        return {}
    report = collect({**config, "allocations": agora}, now or datetime.now(timezone.utc),
                     config["window_days"], run=run)
    return {**report["allocations"][0], "repos": list(agora[0]["repos"]),
            "cap_usd": report["cap_usd"], "days": config["window_days"]}


def build_report(config: dict, readings: list, now: datetime, since: datetime) -> dict:
    """The report as plain data: one line per allocation, then the totals."""
    day_ago = now - timedelta(hours=24)
    lines = [_line(reading, day_ago, since) for reading in readings]
    measured = sum(line["measured_usd"] or 0.0 for line in lines)
    reserved = sum(line["reserved_usd"] for line in lines)
    cap = float(config["cap_usd"])
    return {
        "window": {"from": utc_z(since), "to": utc_z(now),
                   "days": round((now - since).total_seconds() / 86400, 2)},
        "cap_usd": cap,
        "enforced": False,
        "allocations": lines,
        "measured_usd": round(measured, 6),
        "reserved_usd": round(reserved, 6),
        "committed_usd": round(measured + reserved, 6),
        "committed_share": round((measured + reserved) / cap, 4),
        "measured_by_billing": _by_billing(readings),
        "last_24h_usd": round(sum(line["last_24h_usd"] or 0.0 for line in lines), 6),
        "unreported": sum(line["unreported"] for line in lines),
        "unreadable_sources": [line["name"] for line in lines if line["problem"]],
    }


def _record_starts(allocation: dict, since: datetime) -> Optional[str]:
    """When a source's record begins, if that is inside the window; else None.

    AGORA wrote no cost marker before GRIP#6852 (2026-10-01 17:15 UTC), so a
    window reaching further back would silently read a day as ten without this.
    """
    if "records_since" not in allocation:
        return None
    starts = _moment(allocation["records_since"])
    return utc_z(starts) if starts > since else None


def _line(reading: dict, day_ago: datetime, since: datetime) -> dict:
    allocation, charges = reading["allocation"], reading["charges"]
    unread = reading["problem"] is not None
    reservation = allocation["source"] == "reservation"
    return {
        "record_starts": _record_starts(allocation, since),
        "name": allocation["name"],
        "label": allocation.get("label") or allocation["name"],
        "source": allocation["source"],
        "scope": ", ".join(allocation.get("repos") or []),
        "allocation_usd": float(allocation["usd"]),
        "measured_usd": None if unread else round(sum(c.usd for c in charges), 6),
        "reserved_usd": float(allocation["usd"]) if reservation else 0.0,
        "items": len(charges),
        "unreported": sum(c.unreported for c in charges),
        "unreadable": reading["unreadable"],
        "last_24h_usd": None if unread else round(sum(c.usd for c in charges if c.when >= day_ago), 6),
        "problem": reading["problem"],
    }


def _by_billing(readings: list) -> dict:
    routes: Counter = Counter()
    for charge in chain.from_iterable(reading["charges"] for reading in readings):
        routes.update(charge.by_billing)
    return {route: round(usd, 6) for route, usd in sorted(routes.items())}


def render(report: dict) -> str:
    """The report as a few plain lines."""
    window = report["window"]
    out = [f"Model spend from {_clock(window['from'])} to {_clock(window['to'])} UTC, against a "
           f"${report['cap_usd']:.2f} cap. Reported, not enforced.", ""]
    out += [_render_line(line) for line in report["allocations"]]
    out += [""] + _render_totals(report)
    return "\n".join(out)


def _clock(stamp: str) -> str:
    return stamp[:16].replace("T", " ")


def _render_line(line: dict) -> str:
    head = f"  {line['label']:<14}"
    if line["problem"]:
        return f"{head} could not be read: {line['problem']}"
    if line["source"] == "reservation":
        return f"{head} ${line['reserved_usd']:.2f} reserved, a forecast rather than measured spend"
    share = f"{line['measured_usd'] / line['allocation_usd']:.0%}" if line["allocation_usd"] else "n/a"
    return f"{head} ${line['measured_usd']:.2f} of ${line['allocation_usd']:.2f} ({share}); {_what(line)}"


def _what(line: dict) -> str:
    agora = line["source"] == "agora"
    text = f"{line['items']} {'sittings' if agora else 'calls'}"
    if line["scope"]:
        text += f" on {line['scope']}"
    if line["record_starts"]:
        text += f" (its cost record starts {_clock(line['record_starts'])} UTC)"
    if line["unreported"]:
        text += f", {line['unreported']} {'calls' if agora else 'of them'} reported no usage"
    if line["unreadable"]:
        text += f", {line['unreadable']} records unreadable"
    return text


def _render_totals(report: dict) -> list:
    out = [f"Measured ${report['measured_usd']:.2f} and reserved ${report['reserved_usd']:.2f} make "
           f"${report['committed_usd']:.2f} of the ${report['cap_usd']:.2f} cap "
           f"({report['committed_share']:.0%})."]
    if report["measured_by_billing"]:
        parts = ", ".join(f"{route} ${usd:.2f}" for route, usd in report["measured_by_billing"].items())
        out.append(f"Measured spend by billing route: {parts}.")
    out.append(f"Last 24 hours: ${report['last_24h_usd']:.2f} measured.")
    if report["unreported"]:
        out.append("Calls that reported no usage are not priced, so measured spend is a floor.")
    if any(line["record_starts"] for line in report["allocations"]):
        out.append("A record that starts inside the window misses the time before it, "
                   "so its total covers less than the full window.")
    if report["unreadable_sources"]:
        out.append("Not counted, because their source could not be read: "
                   + ", ".join(report["unreadable_sources"]) + ".")
    return out


# --- command line ------------------------------------------------------------------

def _whole_days(text: str) -> int:
    days = int(text)
    if days < 1:
        raise argparse.ArgumentTypeError("days must be 1 or more")
    return days


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grip-spend",
        description="Model spend over the last ten days, against the cap and its "
                    "allocations. Reports; never enforces.")
    parser.add_argument("--days", type=_whole_days, help="window in days (default: the config's)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--config", type=Path,
                        help="allocations file (default: config/spend-allocations.json)")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"[grip-spend] {exc}", file=sys.stderr)
        return 2
    report = collect(config, datetime.now(timezone.utc), args.days or config["window_days"])
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 1 if report["unreadable_sources"] else 0


if __name__ == "__main__":
    sys.exit(main())
