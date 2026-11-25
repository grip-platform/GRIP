"""Resolve the references GRIP writes for a reader, so none is left bare.

GRIP replies cite issues and pull requests (``GRIP#9172``, ``HAL#797``) and
hypotheses (``H926``). A reader who comes back to an old session cannot open a
bare number and does not know what it names (V>> 2026-11-16). This module gives
every reference, with no model call:

* a link: the GitHub page of an issue or pull request, computed from the number
  so it never waits on the network, or the hypothesis's card on the Reference
  Desk, a private claude.ai page that opens at ``<desk>#H926``;
* a card: what the reference is and its state now, and, for a hypothesis, where
  it came from (registered when, by which session and model, sealed in which
  signed decision records) and how, when and by whom it was graded;
* a finding wherever a draft leaves a reference bare (``lint``), and what
  changed after a given time (``since``), so an old ending can be checked
  against today.

GitHub titles and states come from ``gh api`` and are cached for ten minutes in
``state/grip-ref/github.json``. A failed lookup keeps the computed link and says
the title is unknown. The command is ``bin/grip-ref``; ``--help`` lists the rest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from claude_dir import get_claude_dir  # noqa: E402
from hypothesis_engine import (  # noqa: E402
    HYPOTHESIS_FILE,
    PROVENANCE_NAME,
    VERIFICATION_FILE,
)

CLAUDE_DIR = get_claude_dir()
STATE_DIR = CLAUDE_DIR / "state" / "grip-ref"
GITHUB_CACHE = STATE_DIR / "github.json"
DESK_FILE = STATE_DIR / "desk.json"
DESK_DATA = STATE_DIR / "desk" / "data.json"
DESK_PAGE = _LIB.parent / "apps" / "reference-desk" / "index.html"
IDR_CHAIN = CLAUDE_DIR / "state" / "precog" / "idr.jsonl"

CACHE_TTL_S = 600
GH_TIMEOUT_S = 15
GH_WORKERS = 6
TITLE_CHARS = 72
DETAIL_CHARS = 400

# The repositories a reply cites by prefix. Any other prefix is left alone,
# because a guessed repository gives a link to the wrong page.
REPOS = {"GRIP": "grip-platform/GRIP", "HAL": "grip-platform/HAL"}

_REF = re.compile(
    r"(?<![\w/#.-])(?:(?:" + "|".join(REPOS) + r")#\d{1,6}|H\d{3,4})(?![\w-])"
)
_LOOSE = re.compile(r"^(?P<repo>[A-Za-z]+)[#-](?P<num>\d{1,6})$")
_HYP = re.compile(r"^[Hh]\d{3,4}$")
_HID = re.compile(r"\bH\d{3,4}\b")
# Code and existing links are not prose, so a reference inside them is not bare.
_MASKS = (
    re.compile(r"```.*?```", re.S),
    re.compile(r"`[^`\n]*`"),
    re.compile(r"\[[^\]\n]*\]\([^)\s]*\)"),
    re.compile(r"<https?://[^>\s]+>"),
)
_GH_JQ = ("{title, state, created_at, closed_at, updated_at, html_url,"
          " is_pull: (.pull_request != null),"
          " merged_at: (.pull_request.merged_at // null)}")


@dataclass
class Ref:
    """One reference, resolved: what it is, where it opens, and its state."""

    key: str
    kind: str
    url: str = ""
    title: str = ""
    state: str = "unknown"
    card: dict = field(default_factory=dict)


@dataclass
class Finding:
    """A reference a draft leaves bare, by line and column."""

    line: int
    col: int
    key: str


# --- small readers and writers ------------------------------------------------

def _json_or_empty(raw) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict:
    try:
        return _json_or_empty(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def _write_json(path: Path, data: dict) -> None:
    """Write through a temporary file, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _jsonl(path: Path) -> list[dict]:
    """Every JSON object in a JSONL file; an unreadable line is skipped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [row for row in map(_json_or_empty, lines) if row]


def _group(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("id", "")), []).append(row)
    return grouped


def _cut(text, limit: int = DETAIL_CHARS) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _parse_time(text) -> datetime:
    """An ISO 8601 time; a trailing Z and a bare date are accepted."""
    stamp = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _utc(text) -> str:
    try:
        return _parse_time(text).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return str(text or "")


def _after(stamp, cut: datetime) -> bool:
    try:
        return _parse_time(stamp) > cut
    except (TypeError, ValueError):
        return False


# --- keys ---------------------------------------------------------------------

def anchor(key: str) -> str:
    """The Reference Desk anchor of a key: ``GRIP#9172`` becomes ``GRIP-9172``."""
    return key.replace("#", "-")


def normalise(arg: str) -> str:
    """One command-line argument as a reference key, or ``""`` when it names none."""
    text = arg.strip()
    loose = _LOOSE.match(text)
    if loose and loose.group("repo").upper() in REPOS:
        return f"{loose.group('repo').upper()}#{loose.group('num')}"
    return text.upper() if _HYP.match(text) else ""


def parse(text: str) -> list[str]:
    """Every reference key in ``text``, in order of first appearance."""
    keys: list[str] = []
    for match in _REF.finditer(text):
        if match.group(0) not in keys:
            keys.append(match.group(0))
    return keys


# --- issues and pull requests -------------------------------------------------

def issue_url(key: str) -> str:
    """The GitHub page of an issue or pull request, from its key alone."""
    repo, num = key.split("#", 1)
    return f"https://github.com/{REPOS[repo]}/issues/{num}"


def _gh_issue(key: str) -> dict:
    """One issue or pull request as GitHub reports it, or {} when gh cannot."""
    repo, num = key.split("#", 1)
    cmd = ["gh", "api", f"repos/{REPOS[repo]}/issues/{num}", "--jq", _GH_JQ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=GH_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return _json_or_empty(out.stdout) if out.returncode == 0 else {}


def _usable(hit: dict, fresh: bool) -> bool:
    """A cached hit under ten minutes old, in the current shape (it has updated_at)."""
    recent = time.time() - hit.get("fetched", 0) < CACHE_TTL_S
    return bool(hit) and not fresh and recent and "updated_at" in hit


def fetch_issues(keys: list[str], fresh: bool = False,
                 fetch: Callable[[str], dict] = _gh_issue) -> dict[str, dict]:
    """GitHub facts per key: from the cache when under ten minutes old, else fetched."""
    cache = _read_json(GITHUB_CACHE)
    stale = [key for key in keys if not _usable(cache.get(key, {}), fresh)]
    if stale:
        with ThreadPoolExecutor(max_workers=GH_WORKERS) as pool:
            for key, facts in zip(stale, pool.map(fetch, stale)):
                if facts:
                    cache[key] = {**facts, "fetched": time.time()}
        _write_json(GITHUB_CACHE, cache)
    return {key: cache.get(key, {}) for key in keys}


def _issue_kind(facts: dict) -> str:
    if not facts:
        return "issue or pull request"
    return "pull request" if facts.get("is_pull") else "issue"


def issue_ref(key: str, facts: dict) -> Ref:
    """An issue or pull request; with no facts the computed link still opens it."""
    return Ref(
        key=key,
        kind=_issue_kind(facts),
        url=facts.get("html_url") or issue_url(key),
        title=facts.get("title") or "title not fetched: GitHub could not be reached",
        state="merged" if facts.get("merged_at") else facts.get("state") or "unknown",
        card={"opened": facts.get("created_at") or "",
              "closed": facts.get("closed_at") or "",
              "merged": facts.get("merged_at") or "",
              "updated": facts.get("updated_at") or ""},
    )


# --- hypotheses ---------------------------------------------------------------

def _agreement(row: dict) -> str:
    verdicts = {"true": "agreed", "false": "disagreed"}
    return verdicts.get(str(row.get("agreed")).lower(), "gave no verdict")


def _review_event(row: dict) -> dict:
    reviewer = row.get("reviewer") or "an unnamed reviewer"
    return {"at": row.get("reviewed_at", ""), "how": f"outside review by {reviewer}",
            "verdict": f"{_agreement(row)} with {row.get('proposed') or 'the proposed grade'}",
            "detail": _cut(row.get("reasoning"))}


def _override_event(row: dict) -> dict:
    return {"at": row.get("override_at", ""), "how": "recorded by hand",
            "verdict": row.get("override_status", ""),
            "detail": _cut(row.get("override_evidence"))}


def _measured_event(row: dict) -> dict:
    measured = f"measured {row.get('actual')} against a baseline of {row.get('baseline')}"
    return {"at": row.get("verified_at", ""), "how": "measured by the hypothesis engine",
            "verdict": row.get("status", ""), "detail": _cut(measured)}


def grading_event(row: dict) -> dict:
    """One row of the verification audit as a plain event."""
    if row.get("kind") == "external_review":
        return _review_event(row)
    if "override_status" in row:
        return _override_event(row)
    return _measured_event(row)


def seal_index(path: Optional[Path] = None) -> dict[str, list[dict]]:
    """The signed decision records that name each hypothesis, keyed by id."""
    index: dict[str, list[dict]] = {}
    try:
        handle = (path or IDR_CHAIN).open(encoding="utf-8")
    except OSError:
        return index
    with handle:
        for line in handle:
            if _HID.search(line):
                _add_seal(index, _json_or_empty(line))
    return index


def _add_seal(index: dict, record: dict) -> None:
    named = set(_HID.findall(f"{record.get('decision', '')} {record.get('inputs', '')}"))
    seal = {"id": record.get("id", ""), "kind": record.get("kind", ""),
            "at": record.get("ts", "")}
    for hid in sorted(named):
        index.setdefault(hid, []).append(seal)


def _pr_link(row: dict) -> Optional[dict]:
    num = str(row.get("pr_number") or "").strip()
    if not num.isdigit() or int(num) == 0:
        return None
    return {"key": f"GRIP#{num}", "url": issue_url(f"GRIP#{num}")}


@dataclass
class Ledger:
    """The hypothesis store with its grading audit, its seals and its authors."""

    rows: dict = field(default_factory=dict)
    grading: dict = field(default_factory=dict)
    registrations: dict = field(default_factory=dict)
    seals: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Ledger":
        audit = _group(_jsonl(VERIFICATION_FILE))
        authors = _group(_jsonl(HYPOTHESIS_FILE.with_name(PROVENANCE_NAME)))
        return cls(
            rows=_group(_jsonl(HYPOTHESIS_FILE)),
            grading={hid: sorted(map(grading_event, rows), key=lambda e: str(e["at"]))
                     for hid, rows in audit.items()},
            registrations={hid: rows[-1] for hid, rows in authors.items()},
            seals=seal_index(),
        )

    def card(self, hid: str) -> dict:
        """Everything known about one hypothesis, or {} when it is not stored."""
        rows = self.rows.get(hid)
        if not rows:
            return {}
        row = rows[-1]
        return {
            "id": hid, "status": row.get("status") or "unknown",
            "deadline": row.get("deadline", ""), "registered": row.get("registered", ""),
            "claim": row.get("claim", ""), "metric": row.get("metric", ""),
            "prediction": row.get("prediction", ""), "plan": row.get("measure_plan", ""),
            "notes": row.get("notes", ""), "pr": _pr_link(row),
            "registered_by": self.registrations.get(hid),
            "seals": self.seals.get(hid, []), "grading": self.grading.get(hid, []),
            "copies": len(rows),
        }


def desk_url() -> str:
    """The published Reference Desk, or ``""`` before its first publish."""
    return str(_read_json(DESK_FILE).get("url") or "")


def hypothesis_ref(hid: str, ledger: Ledger, desk: str) -> Ref:
    """A hypothesis; it links to its desk card once the desk is published."""
    card = ledger.card(hid)
    if not card:
        return Ref(key=hid, kind="hypothesis", state="not in the ledger",
                   title=f"no hypothesis {hid} in {HYPOTHESIS_FILE.name}")
    return Ref(key=hid, kind="hypothesis", url=f"{desk}#{hid}" if desk else "",
               title=card["claim"], state=card["status"], card=card)


def resolve(keys: list[str], fresh: bool = False,
            fetch: Callable[[str], dict] = _gh_issue) -> list[Ref]:
    """Resolve each key: issues through GitHub, hypotheses through the ledger."""
    facts = fetch_issues([key for key in keys if "#" in key], fresh, fetch)
    ledger = Ledger.load() if any("#" not in key for key in keys) else Ledger()
    desk = desk_url()
    return [issue_ref(key, facts.get(key, {})) if "#" in key
            else hypothesis_ref(key, ledger, desk) for key in keys]


# --- rendering ----------------------------------------------------------------

def _short(text, limit: int = TITLE_CHARS) -> str:
    flat = " ".join(str(text).split()).replace("[", "(").replace("]", ")")
    if len(flat) <= limit:
        return flat
    return flat[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def markdown(ref: Ref) -> str:
    """The reference as a markdown link whose words say what it is."""
    words = f"{_short(ref.title)} ({ref.key}, {ref.state})"
    if ref.url:
        return f"[{words}]({ref.url})"
    if ref.card:
        return f"{words} -- no page yet: run `grip-ref desk export` and publish the desk"
    return words


def _registration_line(card: dict) -> str:
    who = card.get("registered_by") or {}
    when = _utc(card.get("registered"))
    if not who:
        return (f"Registered {when}. Its session was not recorded: only "
                "registrations from 2026-11-16 on record one.")
    branch = f" on branch {who['branch']}" if who.get("branch") else ""
    model = who.get("model") or "an unrecorded model"
    return f"Registered {when} by {model} in session {who.get('session') or 'unknown'}{branch}."


def _origin_lines(card: dict) -> list[str]:
    lines = [f"  {_registration_line(card)}"]
    if card.get("pr"):
        lines.append(f"  Pull request {card['pr']['key']}: {card['pr']['url']}")
    if card.get("plan"):
        lines.append(f"  How it is measured: {_cut(card['plan'], 240)}")
    # "Named in", never "sealed in": a record can quote an id without sealing it. This
    # card does not verify signatures, so it never calls a record signed (Codex, GRIP#9256).
    seals = card.get("seals", [])
    lines += [f"  Named in decision record {s['id']} ({s['kind']}, {_utc(s['at'])})"
              for s in seals]
    if seals:
        lines.append("  This card lists those records; it does not check their signatures.")
    return lines


def _grading_lines(card: dict) -> list[str]:
    events = card.get("grading") or []
    if not events:
        return ["  Not graded yet."]
    return [f"  {_utc(e['at'])} · {e['how']} · {e['verdict']} · {e['detail']}"
            for e in events]


def _hypothesis_lines(ref: Ref) -> list[str]:
    card = ref.card
    if not card:
        return [f"{ref.key} · {ref.state}", ref.title]
    lines = [f"{ref.key} · hypothesis · {card['status']} · due {card['deadline'] or 'no date'}",
             f"Claim: {card['claim']}", f"Measured as: {card['metric']}",
             f"Predicted: {card['prediction']}", "", "Where it came from"]
    lines += _origin_lines(card) + ["", "How it was graded"] + _grading_lines(card)
    return lines + (["", f"Card: {ref.url}"] if ref.url else [])


def _issue_lines(ref: Ref) -> list[str]:
    opened = ref.card.get("opened")
    when = f"opened {_utc(opened)}" if opened else "opening date unknown"
    return [f"{ref.key} · {ref.kind} · {ref.state} · {when}", ref.title, ref.url]


def card_text(ref: Ref) -> str:
    """A plain card for a terminal."""
    lines = _hypothesis_lines(ref) if ref.kind == "hypothesis" else _issue_lines(ref)
    return "\n".join(lines)


# --- lint and since -------------------------------------------------------------

def _blank(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def _finding(text: str, match: re.Match) -> Finding:
    start = match.start()
    line_start = text.rfind("\n", 0, start) + 1
    return Finding(text.count("\n", 0, start) + 1, start - line_start + 1, match.group(0))


def lint(text: str) -> list[Finding]:
    """References a draft leaves bare in prose; code and existing links are skipped."""
    masked = text
    for pattern in _MASKS:
        masked = pattern.sub(_blank, masked)
    return [_finding(masked, match) for match in _REF.finditer(masked)]


def _issue_change(ref: Ref, cut: datetime) -> str:
    for label in ("merged", "closed", "opened"):
        stamp = ref.card.get(label, "")
        if stamp and _after(stamp, cut):
            return f"{label} since then, at {_utc(stamp)}; {ref.state}"
    updated = ref.card.get("updated", "")
    if not updated:
        return f"unknown, because GitHub gave no update time; {ref.state}"
    if _after(updated, cut):
        # The snapshot cannot say what changed: a comment, a label or a reopen.
        return f"updated since then, at {_utc(updated)}; {ref.state}"
    return f"no change since then; {ref.state}"


def _hypothesis_change(ref: Ref, cut: datetime, now: datetime) -> str:
    later = [e for e in ref.card.get("grading", []) if _after(e.get("at", ""), cut)]
    if later:
        last = later[-1]
        return f"graded since then: {last['verdict']} ({last['how']}, {_utc(last['at'])})"
    if _after(ref.card.get("registered", ""), cut):
        return f"registered since then, at {_utc(ref.card['registered'])}; {ref.state}"
    due = ref.card.get("deadline", "")
    if ref.state == "pending" and due and _after(due, cut) and not _after(due, now):
        return f"fell due on {due} and is still pending"
    return f"no change since then; {ref.state}"


def change_since(ref: Ref, cut: datetime, now: Optional[datetime] = None) -> str:
    """What happened to ``ref`` after ``cut``, in one plain sentence."""
    if ref.kind == "hypothesis":
        return _hypothesis_change(ref, cut, now or datetime.now(timezone.utc))
    return _issue_change(ref, cut)


# --- the Reference Desk ---------------------------------------------------------

def _hid_number(hid: str) -> int:
    digits = hid[1:]
    return int(digits) if digits.isdigit() else 0


def _counts(cards: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for card in cards:
        counts[card["status"]] = counts.get(card["status"], 0) + 1
    return counts


# The free-text fields a person could be named in. They pass the redaction
# shield before the data leaves this machine, because a desk link can be shared.
_SCRUBBED = ("claim", "metric", "prediction", "plan", "notes")


def public_scrub() -> Callable[[str], str]:
    """The redaction shield's public profile; it raises without its denylist."""
    from redaction_shield import Shield, load_denylist
    shield = Shield(load_denylist())
    return lambda text: shield.redact(text).text if text else text


def _scrub_ref(ref: Ref, scrub: Callable[[str], str]) -> dict:
    return {**asdict(ref), "title": scrub(ref.title)}


def _scrub_card(card: dict, scrub: Callable[[str], str]) -> dict:
    clean = {**card, **{name: scrub(card.get(name, "")) for name in _SCRUBBED}}
    clean["grading"] = [{**event, "detail": scrub(event.get("detail", ""))}
                        for event in card.get("grading", [])]
    who = card.get("registered_by")
    if who:  # a branch name can name a client (Codex, GRIP#9256)
        clean["registered_by"] = {**who, "branch": scrub(who.get("branch", ""))}
    return clean


def card_digest(card: dict) -> str:
    """A short hash of everything a hypothesis card shows, taken before scrubbing."""
    blob = json.dumps(card, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def export_desk(out: Optional[Path] = None, ledger: Optional[Ledger] = None,
                scrub: Optional[Callable[[str], str]] = None) -> dict:
    """Write the desk's data file: every hypothesis and every cached reference.

    Every free-text field passes ``scrub`` (the shield's public profile by
    default), and nothing is written when the shield cannot load.
    """
    scrub = scrub or public_scrub()
    ledger = ledger or Ledger.load()
    raw = [ledger.card(hid) for hid in sorted(ledger.rows, key=_hid_number) if hid]
    cards = [_scrub_card(card, scrub) for card in raw]
    references = [_scrub_ref(issue_ref(key, facts), scrub)
                  for key, facts in sorted(_read_json(GITHUB_CACHE).items())]
    data = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "counts": _counts(cards), "hypotheses": cards, "references": references}
    target = out or DESK_DATA
    _write_json(target, data)
    _write_json(DESK_FILE, {**_read_json(DESK_FILE), "exported_at": data["generated_at"],
                            "data": str(target),
                            "cards": {card["id"]: card_digest(card) for card in raw}})
    return data


def desk_gaps(refs: list[Ref]) -> list[str]:
    """Hypotheses the last desk export does not show as they stand now.

    Each card's digest at export is compared with its digest now, so any change
    counts: a grade, an amended note, a new seal or a recorded author (Codex,
    GRIP#9256), not only a new event.
    """
    exported = _read_json(DESK_FILE).get("cards") or {}
    return [ref.key for ref in refs
            if ref.kind == "hypothesis" and ref.card
            and exported.get(ref.key) != card_digest(ref.card)]


# --- command line ---------------------------------------------------------------

def _keys(values: list[str]) -> tuple[list[str], list[str]]:
    """Reference keys from command-line arguments, and the arguments naming none."""
    keys: list[str] = []
    unknown: list[str] = []
    for value in values:
        found = [normalise(value)] if normalise(value) else parse(value)
        if not found:
            unknown.append(value)
        keys += [key for key in found if key not in keys]
    return keys, unknown


def _resolved(refs: list[str], fresh: bool) -> Optional[list[Ref]]:
    keys, unknown = _keys(refs)
    for value in unknown:
        print(f"grip-ref: not a reference: {value!r} (try GRIP#9172, HAL#797 or H926)",
              file=sys.stderr)
    return resolve(keys, fresh) if keys else None


def _cmd_show(args) -> int:
    refs = _resolved(args.refs, args.fresh)
    if refs is None:
        return 2
    if args.json:
        print(json.dumps([asdict(ref) for ref in refs], indent=1))
    else:
        print("\n\n".join(card_text(ref) for ref in refs))
    return 0


def _cmd_links(args) -> int:
    refs = _resolved(args.refs, args.fresh)
    if refs is None:
        return 2
    print("\n".join(markdown(ref) for ref in refs))
    gaps = desk_gaps(refs)
    if gaps:
        stands = "it stands" if len(gaps) == 1 else "they stand"
        print(f"grip-ref: the desk does not show {', '.join(gaps)} as {stands} now; "
              "run `grip-ref desk export` and republish the desk", file=sys.stderr)
    missing = [ref.key for ref in refs if ref.kind == "hypothesis" and not ref.card]
    if missing:
        print(f"grip-ref: not in the hypothesis ledger, so no link: {', '.join(missing)}",
              file=sys.stderr)
    return 3 if missing else 0


def _cmd_lint(args) -> int:
    try:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"grip-ref: cannot read {args.file}: {exc}", file=sys.stderr)
        return 2
    findings = lint(text)
    for found in findings:
        print(f"{args.file}:{found.line}:{found.col}: bare {found.key}; "
              f"link it with: grip-ref links {found.key}")
    return 1 if findings else 0


def _cmd_since(args) -> int:
    try:
        cut = _parse_time(args.time)
    except ValueError:
        print(f"grip-ref: not a time: {args.time!r} (use 2026-11-16T20:41Z)", file=sys.stderr)
        return 2
    refs = _resolved(args.refs, args.fresh)
    if refs is None:
        return 2
    for ref in refs:
        print(f"{ref.key}: {change_since(ref, cut)}")
    return 0


def _desk_export(args) -> int:
    try:
        data = export_desk(Path(args.out) if args.out else None)
    except (RuntimeError, OSError, ValueError) as exc:  # no usable shield config: fail closed
        print(f"grip-ref: nothing exported; the redaction shield could not load: {exc}",
              file=sys.stderr)
        return 2
    print(f"wrote {args.out or DESK_DATA}: {len(data['hypotheses'])} hypotheses and "
          f"{len(data['references'])} issues or pull requests")
    print(f"page: {DESK_PAGE}")
    print(f"desk: {desk_url() or 'not published yet; publish both, then grip-ref desk set-url URL'}")
    return 0


def _desk_set_url(args) -> int:
    url = args.url.split("#", 1)[0]
    if not url.startswith("https://claude.ai/"):
        print("grip-ref: the desk is a claude.ai artifact; expected https://claude.ai/...",
              file=sys.stderr)
        return 2
    _write_json(DESK_FILE, {**_read_json(DESK_FILE), "url": url})
    print(f"desk: {url}")
    return 0


def _desk_url(_args) -> int:
    url = desk_url()
    print(url or "no desk yet: run grip-ref desk export, publish it, then grip-ref desk set-url URL")
    return 0 if url else 1


_DESK = {"export": _desk_export, "set-url": _desk_set_url, "url": _desk_url}


def _cmd_desk(args) -> int:
    return _DESK[args.desk_cmd](args)


_COMMANDS = {"show": _cmd_show, "links": _cmd_links, "lint": _cmd_lint,
             "since": _cmd_since, "desk": _cmd_desk}


def _ref_command(sub, name: str, text: str, lead: tuple = ()) -> argparse.ArgumentParser:
    """A subcommand taking references, after any leading positionals in ``lead``."""
    cmd = sub.add_parser(name, help=text)
    for dest, help_text in lead:
        cmd.add_argument(dest, help=help_text)
    cmd.add_argument("refs", nargs="+", help="GRIP#9172, HAL#797, H926, or text holding them")
    cmd.add_argument("--fresh", action="store_true", help="ignore the ten-minute GitHub cache")
    return cmd


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grip-ref",
        description="Open any GRIP reference: an issue, a pull request or a hypothesis.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    show = _ref_command(sub, "show", "a plain card for each reference (the default)")
    show.add_argument("--json", action="store_true", help="print the resolved references as JSON")
    _ref_command(sub, "links", "markdown links whose words say what each reference is")
    lint_cmd = sub.add_parser("lint", help="references a draft leaves bare (exit 1 when any)")
    lint_cmd.add_argument("file", nargs="?", default="-", help="the draft; - or none reads stdin")
    _ref_command(sub, "since", "what changed for each reference after a time",
                 lead=(("time", "an ISO time, such as 2026-11-16T20:41Z"),))
    desk =sub.add_parser("desk", help="the Reference Desk, a private claude.ai page")
    desk_sub = desk.add_subparsers(dest="desk_cmd", required=True)
    export = desk_sub.add_parser("export", help="write the desk's data file")
    export.add_argument("--out", help=f"where to write it (default {DESK_DATA})")
    set_url = desk_sub.add_parser("set-url", help="record the published desk's address")
    set_url.add_argument("url")
    desk_sub.add_parser("url", help="print the published desk's address")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    words = list(sys.argv[1:] if argv is None else argv)
    if words and words[0] not in _COMMANDS and not words[0].startswith("-"):
        words = ["show", *words]
    args = _parser().parse_args(words)
    return _COMMANDS[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
