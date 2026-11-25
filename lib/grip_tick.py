#!/usr/bin/env python3
"""grip_tick -- the always-on tick: a deterministic floor, no model, report-only.

``com.grip.tick`` runs this every five minutes. It reads what the other
always-on agents and the sessions already write, decides with code alone, and
writes one JSON line per tick to ``logs/grip-tick.jsonl``. The checks:

- ``fleet``       the fleet watch (``lib/fleet_watch.py``): a transcript that
                  grew more than the hot line in one tick, or repeats one tool
                  error, in any session. The error's text stays on the machine;
                  the report carries a short digest of it.
- ``hitl``        a session whose last assistant record asked an
                  ``AskUserQuestion`` that nobody answered, older than the
                  threshold (``GRIP_TICK_HITL_THRESHOLD_H``, default 4 hours).
                  Transcripts written in the last 48 hours are checked every
                  tick, and every transcript of the last 30 days once an hour,
                  so a question in a session that stopped writing is still
                  found. A transcript whose size and write time have not
                  changed since it was last read is not read again: its answer
                  is cached in the state file.
- ``hypotheses``  a pending hypothesis past its deadline in
                  ``learning/hypotheses.jsonl``.
- ``launchd``     a ``com.grip``, ``com.hal`` or ``com.codetonight`` agent whose
                  last exit status was not 0.
- ``ideation``    an ``/ultraideate`` shortlist (``logs/ultraideate-shortlists.jsonl``)
                  older than a day with no picks row carrying its ``run_id``
                  (``logs/ultraideate-picks.jsonl``): a round the operator never
                  came back to. The report names the run id and the age, never
                  the topic.
- ``spend``       the weekly utilization per organization in
                  ``$HAL_HOME/logs/cache-loss-ledger.jsonl`` crossing a 0.05 step
                  upward. A step that falls is a weekly reset: it re-arms the
                  organization's reported crossings.
- ``cert``        a registered MCP tunnel deployment's (``lib/mcp_tunnel.py``)
                  server certificate within 30 days of expiry, or expired.
                  Silent with no registered deployment.
- ``error``       a check that raised. It is reported once, by check and
                  exception type, and the tick's verdict is ERROR, so a floor
                  that stopped working never looks quiet.

A new finding is reported once, as one WhatsApp message through
``lib/whatsapp_transport`` (session ids, counts, a question's header and an
error digest, never a body), and ``state/grip-tick/state.json`` remembers what
was reported. A finding whose message did not go out is reported again next
tick, and a spend crossing is held back the same way.

What the tick does on its own about a question or a shortlist it has
reported: once either has been open for a day, counted from when the question
was asked or the shortlist shown, and its first notice has gone out on an
earlier tick, the tick escalates once, a second WhatsApp message and a macOS
notification on the operator's own Mac (``lib/grip_notify``), and from then on
every open item of any check appears in one daily digest line. A question
found already a day old is therefore escalated on the tick after its notice. It never answers a question,
never closes or expires a round, and never stops, signals or expires anything it
did not start. Beyond its own state (a stale lock of its own, its own logs past
10 MB, its own notices) it acts only under a grant the operator signed
(``lib/tick_grant.py``): the recovery checkpoint (``lib/tick_checkpoint.py``),
the throttle flag (``lib/tick_throttle.py``) and an approved experiment for an
overdue hypothesis (``lib/tick_experiment.py``), the one act that starts a
process, sandboxed and killed at its timeout or its write cap, and never for a
hypothesis id the store holds in two distinct records. No model is called on
any path.

The wall budget covers the whole tick, the report included. Exit: 0 quiet,
1 findings, 2 the tick overran its budget or a check raised. Under
``--quiet``, the launchd mode, findings exit 0 as well, because they are
reported on their own channel and launchd would otherwise record every ACT
tick as a failed run, which the launchd check would then report; an ERROR or
OVERRUN still exits 2, so the tick watches itself. A second instance that
meets a live lock exits 0 without a tick. ``--dry-run`` reads everything and
writes nothing, the fleet baseline included.

CLI::

    grip-tick [--dry-run] [--no-send] [--json] [--quiet] [--threshold-hours H]
    grip-tick --install     # write ~/Library/LaunchAgents/com.grip.tick.plist and load it
    grip-tick --uninstall   # unload and remove it
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

_LIB = Path(__file__).resolve().parent
for _p in (_LIB, _LIB.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from claude_dir import get_claude_dir  # noqa: E402
import fleet_watch  # noqa: E402
from _compat import windows_pid_alive  # noqa: E402
import tick_checkpoint  # noqa: E402
import tick_experiment  # noqa: E402
import tick_grant  # noqa: E402
import tick_throttle  # noqa: E402
import grip_tick_plain  # noqa: E402

CLAUDE_DIR = get_claude_dir()
LABEL = "com.grip.tick"
DEFAULT_THRESHOLD_S = 4 * 3600
TAIL_BYTES = 262144
RECENT_S = 48 * 3600
FULL_SCAN_S = 3600
FULL_SCAN_WINDOW_S = 30 * 86400
SPEND_STEP = 0.05
LOCK_STALE_S = 600
LOG_MAX_BYTES = 10 * 1024 * 1024
REPORTED_KEEP_S = 30 * 86400
BUDGET_S = 20.0
# An act starts only while the tick has used less than this share of its budget. A module constant by
# design, not configuration: the one knob the operator holds is the grant, and a share that could be set
# per machine would let a slow tick be talked into its own overrun; lib/CLAUDE.md names it by this name, never
# by its value, so a change here changes the deferral everywhere at once (GRIP#8833).
ACT_BUDGET_SHARE = 0.5
AGENT_PREFIXES = ("com.grip.", "com.hal.", "com.codetonight.")
IDEATION_THRESHOLD_S = 24 * 3600
ESCALATE_AFTER_S = 24 * 3600
DIGEST_S = 24 * 3600
ESCALATED_CHECKS = ("hitl", "ideation")
AT_FORMAT = "%Y-%m-%d %H:%MZ"
DEFAULT_ORG = "default"


@dataclass
class Finding:
    key: str
    check: str
    text: str
    age_s: Optional[float] = None  # how long the thing itself has been open, where the check knows


@dataclass
class TickResult:
    at: str
    seconds: float = 0.0
    findings: List[Finding] = field(default_factory=list)
    new: List[Finding] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)
    overran: bool = False
    sent: Optional[str] = None
    escalated: List[Finding] = field(default_factory=list)
    digested: int = 0

    @property
    def verdict(self) -> str:
        if self.overran:
            return "OVERRUN"
        if self.errors:
            return "ERROR"
        return "ACT" if self.findings else "QUIET"

    @property
    def exit_code(self) -> int:
        return {"OVERRUN": 2, "ERROR": 2, "ACT": 1}.get(self.verdict, 0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict
        return d


@dataclass
class Config:
    root: Path
    state_path: Path
    fleet_state: Path
    lock: Path
    log: Path
    ledger: Path
    hypotheses: Path
    shortlists: Optional[Path] = None
    picks: Optional[Path] = None
    threshold_s: int = DEFAULT_THRESHOLD_S
    budget_s: float = BUDGET_S
    grants_dir: Optional[Path] = None   # the operator's signed grants; None: no act is ever granted
    grant_key: Optional[Path] = None    # this checkout's copy of the verifying key, reported when it drifts from main
    pin_fetch: Optional[Callable[[], Optional[bytes]]] = None  # how the published key is fetched; None: gh api
    checkpoints_dir: Optional[Path] = None  # where the granted checkpoint act writes; None: the act is off
    throttle_flag: Optional[Path] = None  # where the granted throttle act writes its flag; None: the act is off
    experiments_dir: Optional[Path] = None  # the experiment act's manifests and its runs/ folder; None: the act is off
    experiment_runner: Optional[Callable[..., dict]] = None  # how a run is spawned; None: tick_experiment.run_sandboxed
    tree: Optional[Path] = None         # the GRIP tree an experiment runs in and resolves its paths against; None: CLAUDE_DIR
    idr_path: Optional[Path] = None     # the tick's own act ledger; None: state/grip-tick/acts.idr.jsonl
    context_path: Optional[Path] = None  # the paired memory-chain file; None: state/grip-tick/acts.context.jsonl
    context_head: Optional[Path] = None  # its head pointer; None: state/grip-tick/acts.context.head
    dogfood_dir: Optional[Path] = None  # the tree stack-dogfood writes its reports under; None: the dogfood check is off
    cert_registry: Optional[Path] = None  # lib/mcp_tunnel.py's deployments registry; None: the cert check is off
    send: bool = True
    dry_run: bool = False
    ps_text: Optional[str] = None
    launchctl_text: Optional[str] = None


def state_dir() -> Path:
    return CLAUDE_DIR / "state" / "grip-tick"


def ledger_path() -> Path:
    return Path(os.environ.get("HAL_HOME") or Path.home() / ".hal") / "logs" / "cache-loss-ledger.jsonl"


def threshold_from_env(value: Optional[str]) -> int:
    """GRIP_TICK_HITL_THRESHOLD_H as seconds; anything that is not a positive number keeps the default."""
    try:
        hours = float(value) if value else 0.0
    except ValueError:
        return DEFAULT_THRESHOLD_S
    return int(hours * 3600) if hours > 0 else DEFAULT_THRESHOLD_S


def default_config() -> Config:
    threshold = threshold_from_env(os.environ.get("GRIP_TICK_HITL_THRESHOLD_H"))
    return Config(root=fleet_watch.projects_root(), state_path=state_dir() / "state.json",
                  fleet_state=state_dir() / "fleet-sizes.json", lock=state_dir() / "lock",
                  log=CLAUDE_DIR / "logs" / "grip-tick.jsonl", ledger=ledger_path(),
                  hypotheses=CLAUDE_DIR / "learning" / "hypotheses.jsonl",
                  shortlists=CLAUDE_DIR / "logs" / "ultraideate-shortlists.jsonl",
                  picks=CLAUDE_DIR / "logs" / "ultraideate-picks.jsonl", threshold_s=threshold,
                  grants_dir=tick_grant.grants_dir(), grant_key=tick_grant.verifying_key_path(),
                  checkpoints_dir=tick_checkpoint.checkpoints_dir(),
                  throttle_flag=tick_throttle.flag_path(), experiments_dir=tick_experiment.experiments_dir(),
                  dogfood_dir=CLAUDE_DIR,
                  # mcp_tunnel.registry_path(), spelled here so building the config imports
                  # no mcp_tunnel; tests/test_mcp_tunnel.py pins the two together.
                  cert_registry=CLAUDE_DIR / "state" / "mcp-tunnel" / "deployments.json")


# ----------------------------------------------------------------------------- checks

def digest(text: str) -> str:
    """Eight hex characters that name an error without carrying any of it."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]


def incidents(report: fleet_watch.Report) -> List[dict]:
    """This tick's hot and stuck transcripts, one entry per transcript with every cause; the error text never leaves.

    The findings keep the report's grain, one line per session and signal; the act keys on the
    transcript path, so a session's main and subagent transcripts are preserved separately.
    """
    out = []
    for t in report.transcripts:
        signals = []
        if t.hot:
            signals.append(("fleet:hot", f"fleet:hot:{t.owner}:{report.at[:13]}",
                            f"session {t.owner} wrote {t.growth / 1048576:.1f} MB in one tick (hot)"))
        if t.stuck:
            signals.append(("fleet:stuck", f"fleet:stuck:{t.owner}:{digest(t.stuck)}",
                            f"session {t.owner} repeats one tool error (digest {digest(t.stuck)})"))
        if signals:
            out.append({"path": t.path, "owner": t.owner, "kinds": [k for k, _, _ in signals],
                        "keys": [key for _, key, _ in signals], "texts": [text for _, _, text in signals]})
    return out


def fleet_findings(report: fleet_watch.Report) -> List[Finding]:
    """A hot transcript is reported once an hour, a stuck one once per error; the error text never leaves the machine."""
    return [Finding(key, "fleet", text) for i in incidents(report) for key, text in zip(i["keys"], i["texts"])]


def _records(text: str) -> List[dict]:
    out = []
    for line in text.splitlines():
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _iso_s(ts: object) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _has_tool_result(content: list, tool_use_id: str) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == tool_use_id
               for b in content)


def _answered(records: List[dict], tool_use_id: str) -> bool:
    for r in records:
        if r.get("type") != "user":
            continue
        if _has_tool_result((r.get("message") or {}).get("content") or [], tool_use_id):
            return True
    return False


def _ask_block(record: dict) -> Optional[dict]:
    """The AskUserQuestion tool_use block of an assistant record, if it has one."""
    content = (record.get("message") or {}).get("content") or []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
            return block
    return None


def _header(ask: dict) -> str:
    """The first question's header, the one short label the report may carry; never the question text."""
    questions = (ask.get("input") or {}).get("questions") or []
    first = questions[0] if questions and isinstance(questions[0], dict) else {}
    return str(first.get("header") or "")


def pending_question(records: List[dict], now: float) -> Optional[Tuple[str, str, float]]:
    """(tool_use_id, header, age_s) when the last assistant record asked a question nobody answered."""
    last = max((i for i, r in enumerate(records) if r.get("type") == "assistant"), default=None)
    ask = _ask_block(records[last]) if last is not None else None
    if ask is None or _answered(records[last + 1:], ask.get("id") or ""):
        return None
    asked = _iso_s(records[last].get("timestamp"))
    return ask.get("id") or "", _header(ask), (now - asked) if asked else 0.0


def _tail(path: Path, nbytes: int) -> str:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - nbytes))
        return fh.read().decode("utf-8", errors="replace")


def transcripts(root: Path, now: float, recent_s: Optional[int]) -> Iterable[Path]:
    """Session transcripts, all of them when ``recent_s`` is None, else those written within it; subagent files never match."""
    for path in sorted(root.glob("*/*.jsonl")):
        try:
            if recent_s is None or now - path.stat().st_mtime <= recent_s:
                yield path
        except OSError:
            continue


def _pending_cached(path: Path, now: float, cache: Dict[str, dict]) -> Optional[Tuple[str, str, float]]:
    """The transcript's pending question, read from the file only when its size or write time changed."""
    st = path.stat()
    entry = cache.get(str(path))
    if entry is None or entry.get("size") != st.st_size or entry.get("mtime") != st.st_mtime:
        hit = pending_question(_records(_tail(path, TAIL_BYTES)), now)
        entry = {"size": st.st_size, "mtime": st.st_mtime,
                 "ask": None if hit is None else {"id": hit[0], "header": hit[1], "asked": now - hit[2]}}
        cache[str(path)] = entry
    ask = entry.get("ask")
    return None if not ask else (ask["id"], ask["header"], now - float(ask["asked"]))


def _hitl_finding(path: Path, hit: Tuple[str, str, float]) -> Finding:
    tool_use_id, header, age = hit
    label = f" ({header})" if header else ""
    return Finding(f"hitl:{path.stem}:{tool_use_id}", "hitl",
                   f"session {path.stem[:8]} has a question waiting {age / 3600:.1f} h{label}", age_s=age)


def hitl_findings(root: Path, now: float, threshold_s: int, recent_s: Optional[int] = RECENT_S,
                  cache: Optional[Dict[str, dict]] = None) -> List[Finding]:
    """Pending questions in the transcripts visited this tick, plus those the cache still holds from the last full scan."""
    out, visited = [], set()
    cache = {} if cache is None else cache
    for path in transcripts(root, now, FULL_SCAN_WINDOW_S if recent_s is None else recent_s):
        visited.add(str(path))
        try:
            hit = _pending_cached(path, now, cache)
        except OSError:
            continue
        if hit and hit[2] >= threshold_s:
            out.append(_hitl_finding(path, hit))
    for path_str in [p for p in cache if p not in visited]:
        if not Path(path_str).exists():
            del cache[path_str]  # the transcript is gone, so is its question
            continue
        ask = cache[path_str].get("ask")
        if ask and now - float(ask["asked"]) >= threshold_s:
            out.append(_hitl_finding(Path(path_str), (ask["id"], ask["header"], now - float(ask["asked"]))))
    return out


def hypothesis_findings(lines: Iterable[str], today: date) -> List[Finding]:
    """The last row per id decides; a pending one past its deadline is a finding."""
    last: Dict[str, dict] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("id"):
            last[str(row["id"])] = row
    out = []
    for hid, row in last.items():
        due = _date(row.get("deadline"))
        if row.get("status") == "pending" and due and due < today:
            out.append(Finding(f"hyp:{hid}", "hypotheses", f"{hid} is {(today - due).days} days past its deadline with no verdict"))
    return out


def _date(value: object) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def launchd_findings(listing: str) -> List[Finding]:
    """`launchctl list` rows are PID, last exit status, label; a non-zero status on a job not running is a finding."""
    out = []
    for line in listing.splitlines():
        parts = line.split("\t") if "\t" in line else line.split()
        if len(parts) < 3 or not parts[2].startswith(AGENT_PREFIXES):
            continue
        pid, status, label = parts[0], parts[1], parts[2]
        if pid == "-" and status not in ("0", "-"):
            out.append(Finding(f"launchd:{label}:{status}", "launchd", f"{label} last exited with status {status}"))
    return out


def _rows(lines: Iterable[str]) -> Iterator[dict]:
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def ideation_findings(shortlist_lines: Iterable[str], pick_lines: Iterable[str], now: float,
                      threshold_s: int = IDEATION_THRESHOLD_S) -> List[Finding]:
    """A shortlist older than the threshold whose run_id no picks row carries is a round nobody came back to."""
    picked = {str(r.get("run_id")) for r in _rows(pick_lines) if r.get("run_id")}
    out = []
    for row in _rows(shortlist_lines):
        run_id, ts = str(row.get("run_id") or ""), row.get("ts")
        if not run_id or run_id in picked or not isinstance(ts, (int, float)):
            continue
        age = now - float(ts)
        if age >= threshold_s:
            out.append(Finding(f"ideation:{run_id}", "ideation",
                               f"ideation shortlist {run_id} has waited {age / 3600:.0f} h for a pick", age_s=age))
    return out


def grant_findings(cfg: Config, state: dict, now: float) -> List[Finding]:
    """Verify every grant file. A grant that fails is a finding, never a permission; every valid grant seen is logged.

    It writes four state keys and no other: ``grants_valid`` and ``grants_active`` (this tick's view),
    ``grants_seen`` (every valid grant ever, by hash) and ``pin_cache`` (through ``tick_grant.pinned_key``).
    """
    state["grants_valid"], state["grants_active"] = {}, {}
    if cfg.grants_dir is None:
        return []
    pem, _ = tick_grant.pinned_key(state["pin_cache"], now, fetch=cfg.pin_fetch)
    valid, problems = tick_grant.load_grants(cfg.grants_dir, pem, now)
    at = time.strftime(AT_FORMAT, time.gmtime(now))
    for act, entry in valid.items():
        state["grants_seen"].setdefault(entry["hash"], {"act": act, "first_seen": at, "file": entry["file"],
                                                         "expires": entry["grant"]["expires"]})
        state["grants_valid"][act] = entry["hash"]
        state["grants_active"][act] = {"hash": entry["hash"], "triggers": entry["grant"]["triggers"],
                                       "limits": entry["grant"]["limits"], "expires": entry["grant"]["expires"],
                                       "grant": entry["grant"]}
        state.setdefault("first_grant_at", at)  # H843's fourteen days count from here
    out = [Finding(f"grant:{digest or name}", "grant", f"grant file {name} failed verification ({reason})")
           for name, digest, reason in problems]
    return out + _pin_drift(cfg, pem)


def _pin_drift(cfg: Config, published: Optional[bytes]) -> List[Finding]:
    """This checkout's copy of the verifying key is not what grants verify against; say so when it differs."""
    local = tick_grant.read_verifying_key(cfg.grant_key)
    if published is None or local is None or local.strip() == published.strip():
        return []
    return [Finding("grant:pin-drift", "grant", "this checkout's grant-verifying.pub differs from the key published on "
                                                "main; grants verify against the published key")]


def active_grants(state: dict) -> List[Tuple[str, dict]]:
    """(hash, seen entry) for every grant that verified on this tick, for the digest and for the acts."""
    seen = state.get("grants_seen", {})
    return [(h, seen[h]) for h in state.get("grants_valid", {}).values() if h in seen]


def latest_weekly(tail: str) -> Dict[str, float]:
    """The newest weekly utilization per organization in the ledger's tail."""
    out: Dict[str, float] = {}
    for line in reversed(tail.splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        weekly = row.get("seven_day_utilization")
        org = str(row.get("org_id") or DEFAULT_ORG)
        if isinstance(weekly, (int, float)) and not isinstance(weekly, bool) and org not in out:
            out[org] = float(weekly)
    return out


def _step(weekly: float) -> float:
    """The 0.05 step at or below ``weekly``: 0.22 is 0.20, and exactly 0.20 is 0.20, the small epsilon guarding the
    float division so 0.15 / 0.05 (2.9999...) still lands on 0.15."""
    return round(int(weekly / SPEND_STEP + 1e-9) * SPEND_STEP, 2)


def spend_findings(weekly: Dict[str, float], steps: Dict[str, float]) -> Tuple[List[Finding], Dict[str, float], List[str]]:
    """Per organization: a finding on an upward 0.05 crossing, the step to store, and the organizations that reset.

    A value that is not a number is ignored and its organization keeps its stored step.
    """
    findings, new_steps, resets = [], dict(steps), []
    for org, value in weekly.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        step, prev = _step(value), steps.get(org)
        if prev is not None and step > prev:
            findings.append(Finding(f"spend:{org}:{step:.2f}", "spend", f"weekly spend ({org}) crossed {step:.0%} (now {value:.0%})"))
        if prev is not None and step < prev:
            resets.append(org)
        new_steps[org] = step
    return findings, new_steps, resets


def cert_findings(registry: Optional[Path] = None) -> List[Finding]:
    """A registered MCP tunnel deployment's certificate within 30 days of expiry, or expired.

    Reads the deployments listed in ``registry``, by default mcp_tunnel's own.
    The tick passes ``cfg.cert_registry`` and runs this check only when it is
    set, so a config built without it, as every test builds one, reads no
    machine's registry (GRIP#9387, the shape GRIP#9383 fixed for the dogfood
    check). Silent (no findings, no error) with no registered deployment --
    lazily imports ``mcp_tunnel`` so a plain ``import grip_tick`` never requires
    ``cryptography`` or ``PyYAML`` (rules/never-system-python-for-grip.md).
    """
    import mcp_tunnel

    return [Finding(f"cert:{d}", "cert", line)
            for d in mcp_tunnel.registered_deployments(registry)
            if (line := mcp_tunnel.cert_status_one(d)) is not None]


def _load_dogfood_tick_findings():
    """The stack-dogfood tick-findings reader, under whichever import name works
    (repo-root path in tests, lib/ path when grip_tick runs as a file)."""
    for modname in ("lib.stack_dogfood.report", "stack_dogfood.report"):
        try:
            mod = __import__(modname, fromlist=["tick_findings"])
            return mod.tick_findings
        except Exception:
            continue
    return None


def dogfood_findings(tree: Path) -> List[Finding]:
    """Surface stack-dogfood residuals and failed checks from the persisted
    reports (lib/stack_dogfood/report.py) under ``tree``. The tick passes
    ``cfg.dogfood_dir``, which ``default_config`` sets to CLAUDE_DIR -- the same
    dir report.write_report writes to -- NOT cfg.root (fleet_watch.projects_root()),
    which differs and would make these findings silently never appear. A config
    built without it, as every test builds one, reads no machine's reports. Reads
    only, never probes. Fail-open: a broken import yields no findings rather than
    taking the tick down."""
    reader = _load_dogfood_tick_findings()
    if reader is None:
        return []
    return [Finding(f.key, "dogfood", f.text) for f in reader(tree)]


# ----------------------------------------------------------------------------- state, lock, log

def load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.setdefault("reported", {})
    state.setdefault("spend_steps", {})
    state.setdefault("full_scan_at", 0.0)
    state.setdefault("hitl_cache", {})
    state.setdefault("pending", [])
    state.setdefault("escalated", {})
    state.setdefault("digest_at", None)
    state.setdefault("grants_seen", {})
    state.setdefault("grants_valid", {})
    state.setdefault("pin_cache", {})
    state.setdefault("grants_active", {})
    state.setdefault("checkpoints", {})
    return state


def prune_reported(state: dict, now: float, keep_s: int = REPORTED_KEEP_S) -> int:
    """Forget reported keys older than ``keep_s``, so the state file cannot grow without bound; returns how many."""
    cutoff = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(now - keep_s))
    old = [k for k, at in state["reported"].items() if str(at) < cutoff]
    for k in old:
        del state["reported"][k]
    stale = [p for p, e in state.get("hitl_cache", {}).items() if now - float(e.get("mtime") or 0) > FULL_SCAN_WINDOW_S]
    for p in stale:
        del state["hitl_cache"][p]
    return len(old) + len(stale)


def save_state(path: Path, state: dict) -> None:
    state.pop("incidents", None)  # this tick's input to the act, never persisted
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")), encoding="utf-8")  # compact: the cache has one entry per transcript
    os.replace(tmp, path)


def split_new(findings: List[Finding], state: dict) -> List[Finding]:
    """Findings not yet reported, the ones whose message did not go out last time first; one entry per key."""
    carried = [Finding(**p) for p in state.get("pending", []) if isinstance(p, dict)]
    out, seen = [], set()
    for f in carried + findings:
        if f.key in state["reported"] or f.key in seen:
            continue
        seen.add(f.key)
        out.append(f)
    return out


def rearm_spend(state: dict, orgs: List[str]) -> None:
    """A weekly reset forgets the organization's reported crossings, so the next crossing is new again."""
    prefixes = tuple(f"spend:{org}:" for org in orgs)
    for key in [k for k in state["reported"] if prefixes and k.startswith(prefixes)]:
        del state["reported"][key]


def _at_epoch(at: str) -> Optional[float]:
    try:
        return datetime.strptime(at, AT_FORMAT).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def due_escalations(findings: List[Finding], state: dict, now: float) -> List[Finding]:
    """A question or a shortlist open for a day, noticed on an earlier tick, not yet escalated.

    The day is counted from when the thing itself was asked or shown where the check
    knows that, else from the first notice; the notice must be older than this tick.
    """
    out = []
    for f in findings:
        if f.check not in ESCALATED_CHECKS or f.key in state["escalated"]:
            continue
        first = _at_epoch(str(state["reported"].get(f.key, "")))
        if first is None or now - first < 60:
            continue
        age = f.age_s if f.age_s is not None else now - first
        if age >= ESCALATE_AFTER_S:
            out.append(f)
    return out


def digest_due(findings: List[Finding], state: dict, now: float) -> Optional[List[Finding]]:
    """Once a day: every finding already reported and still true, or an empty list when nothing is open; None when not due."""
    last = state.get("digest_at")
    if last is not None and now - float(last) < DIGEST_S:
        return None
    return [f for f in findings if f.key in state["reported"]]


def notify_mac(title: str, body: str) -> bool:
    """A notification on the operator's own Mac through lib/grip_notify; False when it cannot be shown. Never raises."""
    try:
        from grip_notify import MacOSLocalBackend, Notification
        result = MacOSLocalBackend().send(Notification(title=title, body=body))
        return bool(getattr(result, "ok", result))
    except Exception:  # the Mac is outside this process
        return False


def _alive(pid: int) -> bool:
    """True while the process holding the tick lock still runs.

    On Windows ``os.kill(pid, 0)`` is a console Ctrl+C call, not a check
    (GRIP#8883), so a live holder could read as gone and a second tick start beside
    it. There the Windows PID is looked up with ``windows_pid_alive``; when the
    lookup cannot tell, the answer is True, and the lock still goes stale after
    ``stale_s``. Off Windows any OSError means the holder has gone, as before.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            return windows_pid_alive(pid)
        except OSError:
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _lock_holder(path: Path, now: float, stale_s: int) -> Optional[int]:
    """The live pid holding the lock, or None when the lock is absent, stale, or holds no pid at all."""
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip() or "0")
    except ValueError:
        return None  # garbage in the lock is a crashed writer, not a holder
    return pid if now - path.stat().st_mtime < stale_s and _alive(pid) else None


def acquire_lock(path: Path, now: float, stale_s: int = LOCK_STALE_S) -> str:
    """"acquired", "held" by a live tick, or "error" when the lock cannot be read or written.

    A stale lock (dead pid, older than ``stale_s``, or unreadable content) is removed first.
    """
    try:
        if _lock_holder(path, now, stale_s) is not None:
            return "held"
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return "acquired"
    except OSError:
        return "error"


def release_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def rotate_log(path: Path, max_bytes: int = LOG_MAX_BYTES) -> bool:
    """Rotate the tick's own log and the two launchd stream files beside it once any passes ``max_bytes``."""
    rotated = False
    for p in (path, path.parent / "grip-tick-stdout.log", path.parent / "grip-tick-stderr.log"):
        try:
            if p.stat().st_size > max_bytes:
                os.replace(p, p.with_name(p.name + ".1"))
                rotated = True
        except OSError:
            continue
    return rotated


def append_log(path: Path, result: TickResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(result.to_dict(), sort_keys=True) + "\n")


# ----------------------------------------------------------------------------- report

def report_text(new: List[Finding]) -> str:
    """The WhatsApp message for this tick's new findings, in plain words (``lib/grip_tick_plain.py``)."""
    return grip_tick_plain.report(new)


def send_whatsapp(text: str) -> Optional[str]:
    """The operator's WhatsApp through the local bridge: the message id, or "sent" when the bridge reported ok without one, or None."""
    from wa_identities import operator_jid
    from whatsapp_transport import post
    jid = operator_jid()
    if not jid:
        return None
    result = post(recipient=jid, text=text)
    if not result.get("ok"):
        return None
    return str(result.get("message_id") or "sent")


# ----------------------------------------------------------------------------- the tick

def _guard(name: str, fn, errors: Dict[str, str]) -> List[Finding]:
    try:
        return fn()
    except Exception as exc:  # one failing check never stops the others, and never looks quiet
        errors[name] = f"{type(exc).__name__}: {exc}"
        return [Finding(f"error:{name}:{type(exc).__name__}", "error", f"check {name} failed ({type(exc).__name__})")]


def _read_text(path: Path, tail_bytes: Optional[int] = None) -> str:
    if not path.exists():
        return ""
    return _tail(path, tail_bytes) if tail_bytes else path.read_text(encoding="utf-8", errors="replace")


class LedgerUnreadable(RuntimeError):
    """The ledger has rows but none the tick can read: its format moved, and that must not pass as no spend."""


def _read_weekly(ledger: Path, weekly: Dict[str, float]) -> List[Finding]:
    tail = _read_text(ledger, TAIL_BYTES)
    weekly.update(latest_weekly(tail))
    if tail.strip() and not weekly:
        raise LedgerUnreadable(f"no seven_day_utilization in the last {TAIL_BYTES} bytes of {ledger.name}")
    return []


def _require_root(root: Path) -> List[Finding]:
    """The projects root must be a readable directory, or both transcript floors would scan nothing and look quiet."""
    if not root.is_dir() or not os.access(root, os.R_OK):
        raise FileNotFoundError(f"projects root is not a readable directory: {root}")
    return []


def _launchctl(cfg: Config) -> str:
    if cfg.launchctl_text is not None:
        return cfg.launchctl_text
    return subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5, check=True).stdout


def _fleet_state_for(cfg: Config) -> Path:
    """The real baseline, or for a dry run a scratch copy of it, so a dry run advances nothing."""
    if not cfg.dry_run:
        return cfg.fleet_state
    scratch = Path(tempfile.mkdtemp(prefix="grip-tick-dry-")) / "fleet-sizes.json"
    if cfg.fleet_state.exists():
        shutil.copyfile(cfg.fleet_state, scratch)
    return scratch


def collect(cfg: Config, now: float, state: dict, errors: Dict[str, str]) -> Tuple[List[Finding], Dict[str, float]]:
    """Every check's findings, with a check that raised recorded in ``errors`` and reported as a finding."""
    full_scan = now - float(state.get("full_scan_at") or 0.0) >= FULL_SCAN_S
    recent_s = None if full_scan else RECENT_S
    findings: List[Finding] = []
    findings += _guard("root", lambda: _require_root(cfg.root), errors)
    def _fleet() -> List[Finding]:
        report = fleet_watch.scan(root=cfg.root, state=_fleet_state_for(cfg), now=now, ps_text=cfg.ps_text)
        state["incidents"] = incidents(report)
        return fleet_findings(report)
    state["incidents"] = []
    findings += _guard("fleet", _fleet, errors)
    findings += _guard("hitl", lambda: hitl_findings(cfg.root, now, cfg.threshold_s, recent_s, state["hitl_cache"]), errors)
    findings += _guard("hypotheses", lambda: hypothesis_findings(
        _read_text(cfg.hypotheses).splitlines(), date.fromtimestamp(now)), errors)
    findings += _guard("launchd", lambda: launchd_findings(_launchctl(cfg)), errors)
    findings += _guard("grant", lambda: grant_findings(cfg, state, now), errors)
    if cfg.shortlists is not None and cfg.picks is not None:
        findings += _guard("ideation", lambda: ideation_findings(
            _read_text(cfg.shortlists).splitlines(), _read_text(cfg.picks).splitlines(), now), errors)
    weekly: Dict[str, float] = {}
    findings += _guard("spend", lambda: _read_weekly(cfg.ledger, weekly), errors)
    if cfg.cert_registry is not None:
        findings += _guard("cert", lambda: cert_findings(cfg.cert_registry), errors)
    if cfg.dogfood_dir is not None:
        findings += _guard("dogfood", lambda: dogfood_findings(cfg.dogfood_dir), errors)
    if full_scan and "hitl" not in errors:
        state["full_scan_at"] = now
    return findings, weekly


def _send_text(cfg: Config, text: str, errors: Dict[str, str]) -> Tuple[Optional[str], bool]:
    """One message: (message id or "sent", True) when it counts as delivered, else (None, False)."""
    if not cfg.send:
        return None, True
    try:
        sent = send_whatsapp(text)
    except Exception as exc:  # the bridge is outside this process
        errors["send"] = f"{type(exc).__name__}: {exc}"
        return None, False
    return sent, sent is not None


def _deliver(cfg: Config, new: List[Finding], errors: Dict[str, str]) -> Tuple[Optional[str], bool]:
    """Send the new findings once; (message id or "sent", True) when they count as reported, else (None, False)."""
    if not new:
        return None, False
    return _send_text(cfg, report_text(new), errors)


def _escalate(cfg: Config, result: TickResult, state: dict, now: float, errors: Dict[str, str]) -> None:
    """The tick's own act on a question or a shortlist still open after a day: one more message, one Mac notification."""
    due = due_escalations(result.findings, state, now)
    if not due:
        return
    _, delivered = _send_text(cfg, grip_tick_plain.reminder(due), errors)
    if delivered:
        notify_mac("GRIP reminder", grip_tick_plain.reminder_notice(len(due)))
        for f in due:
            state["escalated"][f.key] = result.at
        result.escalated = due


def _digest(cfg: Config, result: TickResult, state: dict, now: float, errors: Dict[str, str]) -> None:
    """One line a day with every open item and every grant in force; nothing is sent when there is neither, but the day turns."""
    open_items = digest_due(result.findings, state, now)
    if open_items is None:
        return
    grants = active_grants(state)
    if not open_items and not grants:
        state["digest_at"] = now
        return
    _, delivered = _send_text(cfg, grip_tick_plain.daily(open_items, grants), errors)
    if delivered:
        state["digest_at"] = now
        result.digested = len(open_items)


def _eligible(inc: dict, grant: dict, state: dict) -> bool:
    """An incident of a kind the grant names, with a key no checkpoint of that transcript has recorded yet."""
    recorded = set(state["checkpoints"].get(inc["path"], {}).get("keys", []))
    return bool(set(inc["kinds"]) & set(grant["triggers"])) and bool(set(inc["keys"]) - recorded)


def _act(cfg: Config, state: dict, now: float, errors: Dict[str, str], elapsed_s: float = 0.0,
         weekly: Optional[Dict[str, float]] = None, overdue: Optional[List[str]] = None) -> List[Finding]:
    """The granted acts on this tick's findings, behind every clause of their gates; never on a dry run.

    The checkpoint act's eligibility is the current incidents against the checkpoint state, never the
    report's dedupe, so a transcript reported before a grant existed, skipped for a full cap, or failed
    once, is tried again while its trouble lasts; and a transcript in new trouble later (a new hot hour,
    a different error) is a new incident with its own checkpoint. The throttle act raises the flag when
    an organization's weekly spend reaches the grant's soft cap and no flag is set, and clears a flag it
    raised itself once every organization is below the cap again. The experiment act runs, once per
    approval, the measurement the operator approved for a hypothesis in ``overdue`` (the ids this tick's
    hypotheses check found past their deadline), at most one run a tick and last, so it starts only if the
    tick is still inside the share below once the other acts are done.

    The acts are efficiency-gated: they start only while this tick has used less than ACT_BUDGET_SHARE
    of its budget, so a slow tick defers them to the next run instead of overrunning. The deferral is one
    finding a day, reported like any other, and only when an act was waiting: a slow tick with nothing
    to do says nothing.
    """
    if cfg.dry_run:
        return []
    started = time.monotonic()
    grants = state.get("grants_active", {})
    checkpoint, throttle, experiment = grants.get("checkpoint"), grants.get("throttle"), grants.get("experiment")
    eligible = _checkpoint_eligible(cfg, state, checkpoint)
    measured = bool(weekly) and "spend" not in errors
    pending, out = _throttle_decision(cfg, throttle, weekly or {}, measured, now, errors)
    chosen = _experiment_decision(cfg, experiment, overdue or [], now, out, errors)
    waiting = len(eligible) + (1 if pending else 0) + (1 if chosen else 0)
    if not waiting:
        return out
    if elapsed_s > cfg.budget_s * ACT_BUDGET_SHARE:
        return out + [_deferred(cfg, now, waiting, elapsed_s)]
    out += _checkpoints(cfg, state, eligible, checkpoint, now, errors)
    if pending:
        out.append(_throttle_one(cfg, throttle, weekly or {}, pending, now, errors))
    if chosen:
        out.append(_experiment_one(cfg, experiment, chosen, now, errors, elapsed_s + time.monotonic() - started))
    return out


def _checkpoint_eligible(cfg: Config, state: dict, grant: Optional[dict]) -> List[dict]:
    """The current incidents the checkpoint grant covers and no checkpoint has recorded yet."""
    if not grant or cfg.checkpoints_dir is None:
        return []
    _reconcile_checkpoints(cfg, state)
    return [inc for inc in state.get("incidents", []) if _eligible(inc, grant, state)]


def _checkpoints(cfg: Config, state: dict, eligible: List[dict], grant: Optional[dict], now: float,
                 errors: Dict[str, str]) -> List[Finding]:
    """One checkpoint per eligible incident; a failure is one finding and never takes the tick down."""
    out: List[Finding] = []
    for inc in eligible:
        try:
            out.append(_checkpoint_one(cfg, state, inc, grant, now))
        except Exception as exc:  # noqa: BLE001  the act must never take the tick down; the failure is reported
            errors["act"] = type(exc).__name__
            out.append(Finding(f"act:error:{tick_checkpoint.transcript_id(inc['path'])}:{type(exc).__name__}", "act",
                               f"checkpoint for session {inc['owner']} failed ({type(exc).__name__})"))
    return out


def _deferred(cfg: Config, now: float, waiting: int, elapsed_s: float) -> Finding:
    """The day's one deferral finding: the acts waiting stay for the next tick inside its budget."""
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    return Finding(f"act:deferred:{day}", "act", f"acts deferred ({waiting} waiting): the tick had used "
                   f"{elapsed_s:.1f} s of its {cfg.budget_s:.0f} s budget before acting")


def _act_ledgers(cfg: Config) -> Tuple[Path, Path, Path]:
    """The tick's act ledgers: the decision leaves, the memory nodes and their head pointer."""
    return (cfg.idr_path or tick_checkpoint.acts_ledger(), cfg.context_path or tick_checkpoint.acts_context(),
            cfg.context_head or tick_checkpoint.acts_context_head())


def _experiment_decision(cfg: Config, grant: Optional[dict], overdue: List[str], now: float, out: List[Finding],
                         errors: Dict[str, str]) -> Optional[dict]:
    """The approved experiment this tick may run, or None (``_experiment_choice``). A failure while choosing is one
    finding and never takes the tick down (Codex on GRIP#8856)."""
    if cfg.experiments_dir is None:
        return None
    try:
        return _experiment_choice(cfg, grant, overdue, now, out)
    except Exception as exc:  # noqa: BLE001  the experiment act's choice must never take the tick down
        errors["act"] = type(exc).__name__
        out.append(Finding(f"act:error:experiment:choice:{type(exc).__name__}", "act",
                           f"the experiment act could not choose a run ({type(exc).__name__}); a later tick tries again"))
        return None


def _experiment_choice(cfg: Config, grant: Optional[dict], overdue: List[str], now: float,
                       out: List[Finding]) -> Optional[dict]:
    """First the tick's own debris goes (a run folder never reserved), the engine rows a settled run is still owed are
    written, failures reported, and a run left reserved is reported once; then a grant naming the trigger picks the
    first overdue hypothesis it approves with no run yet under this approval. A manifest or an input that changed
    since the approval, or an id the hypothesis store holds in two distinct records, runs nothing and is one finding
    per hypothesis and grant."""
    runs = tick_experiment.runs_dir(cfg.experiments_dir)
    tick_experiment.drop_incomplete(runs, now)
    out += [Finding(f"act:experiment-engine:{folder}:{error}", "act", f"experiment {folder} is sealed but its engine "
                    f"row could not be written ({error}); a later tick tries again")
            for folder, error in tick_experiment.record_pending(runs)]
    out += [_unsettled_finding(result) for result in tick_experiment.unsettled(runs, now)]
    if not grant or tick_experiment.TRIGGER not in (grant.get("triggers") or []):
        return None
    approved = (grant.get("limits") or {}).get("experiments") or {}
    ambiguous = _ambiguous(cfg) & set(overdue) & set(approved)
    out += [_ambiguous_finding(hid, grant["hash"]) for hid in sorted(ambiguous)]
    chosen, skipped = tick_experiment.select(cfg.experiments_dir, approved, [h for h in overdue if h not in ambiguous],
                                             grant["hash"], cfg.tree or CLAUDE_DIR)
    out += [Finding(f"act:experiment-drift:{hid}:{grant['hash']}", "act", f"experiment for {hid} not run: {why}")
            for hid, why in skipped]
    return chosen


def _ambiguous(cfg: Config) -> set:
    """Each hypothesis id the store holds in more than one distinct record: a run under it could be attributed to no
    one claim, so it never runs (Codex on GRIP#8856). The engine's own test, over the lines the tick reads."""
    import hypothesis_engine
    return hypothesis_engine.divergent_ids(_read_text(cfg.hypotheses).splitlines())


def _ambiguous_finding(hid: str, grant_hash: str) -> Finding:
    return Finding(f"act:experiment-ambiguous:{hid}:{grant_hash}", "act",
                   f"experiment for {hid} not run: the hypothesis store holds more than one distinct record under "
                   f"{hid}, so a run could not be attributed to one claim; settle the id first")


def _unsettled_finding(result: dict) -> Finding:
    folder = result.get("folder")
    return Finding(f"act:experiment-unsettled:{folder}", "act",
                   f"experiment {folder} for {result.get('hypothesis')} stopped between its reservation and its record, "
                   f"so whether its seal reached the ledger is not known; it will not run again, and "
                   f"grip-tick-experiment remove {folder} allows one more run")


def _experiment_one(cfg: Config, grant: dict, chosen: dict, now: float, errors: Dict[str, str],
                    spent_s: float) -> Finding:
    """Run the chosen experiment if the tick is still inside the share after the other acts, the grant is still in
    force at this moment (``now + spent_s``, the tick's wall clock moved on by what it has spent) and the
    hypothesis id still names one record, then write its engine row; a failure is one finding and never takes the
    tick down. The report names sizes and hashes, never output."""
    hid = chosen["hypothesis"]
    if spent_s > cfg.budget_s * ACT_BUDGET_SHARE:
        return _deferred(cfg, now, 1, spent_s)
    live, why = tick_grant.in_force(grant["grant"], now + spent_s)
    if not live:
        return Finding(f"act:experiment-lapsed:{hid}:{grant['hash']}", "act",
                       f"experiment for {hid} not run: its grant was {why} by the time the other acts were done")
    runs = tick_experiment.runs_dir(cfg.experiments_dir)
    try:
        if hid in _ambiguous(cfg):  # checked again at the spawn, as the grant's window is
            return _ambiguous_finding(hid, grant["hash"])
        result = tick_experiment.run_experiment(runs, chosen, grant["grant"], grant["hash"], cfg.tree or CLAUDE_DIR, now,
                                                *_act_ledgers(cfg), runner=cfg.experiment_runner)
    except Exception as exc:  # noqa: BLE001  the act must never take the tick down; the failure is reported
        errors["act"] = type(exc).__name__
        return _experiment_failed(hid, chosen, grant["hash"], runs, exc)
    owed = "; its engine row is still owed" if tick_experiment.record_pending(runs) else ""
    uncertain = ("; its record is incomplete (the leaf has no memory node and no roll-back), so the run is kept and "
                 "will not run again" if result.get("record") == "uncertain" else "")
    return Finding(f"act:experiment:{result['folder']}", "act",
                   f"experiment for {hid} ran under grant {grant['hash']}: exit {result['exit']}"
                   f"{tick_experiment.ending(result)} in {result['milliseconds']} ms, stdout "
                   f"{result['stdout']['bytes']} bytes sha256 {result['stdout']['sha256'][:12]} (leaf {result['idr_leaf']})"
                   f"{owed}{uncertain}; the verdict is yours")


def _experiment_failed(hid: str, chosen: dict, grant_hash: str, runs: Path, exc: Exception) -> Finding:
    """A run that raised: it runs again on a later tick, unless it was reserved before the failure, in which case it
    never runs again under this approval and the report says so (Codex on GRIP#8856)."""
    reserved = tick_experiment.run_key(hid, chosen["sha256"], grant_hash) in tick_experiment.run_keys(runs)
    then = ("its run was reserved before the failure, so it will not run again; grip-tick-experiment list shows it"
            if reserved else "it runs again on a later tick")
    return Finding(f"act:error:experiment:{hid}:{type(exc).__name__}", "act",
                   f"experiment for {hid} failed ({type(exc).__name__}); {then}")


def _grant_cap(grant: dict) -> float:
    """The soft cap a verified throttle grant binds, as a share; ValueError on a malformed limit."""
    try:
        return int(grant["limits"]["soft_cap_pct"]) / 100.0
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"the throttle grant's soft_cap_pct is malformed ({type(exc).__name__})") from exc


def _throttle_decision(cfg: Config, grant: Optional[dict], weekly: Dict[str, float], measured: bool, now: float,
                       errors: Dict[str, str]) -> Tuple[Optional[Tuple[str, float]], List[Finding]]:
    """The throttle act's pending transition and the findings deciding it produced. A flag of the tick's own
    that records no leaf (a kill between the write and the seal) is removed first and reported, so the act is
    decided afresh; a malformed grant is one finding, never an exception out of the tick."""
    out: List[Finding] = []
    if cfg.throttle_flag is None:
        return None, out
    dropped = tick_throttle.drop_unsealed(cfg.throttle_flag)
    if dropped is not None:
        out.append(Finding(f"act:throttle-unsealed:{tick_throttle.iso(now)}", "act",
                           f"an unsealed throttle flag (written {dropped.get('at', '?')}, never sealed) was removed; "
                           f"the act is decided afresh"))
    try:
        return _throttle_pending(cfg, grant, weekly, measured), out
    except Exception as exc:  # noqa: BLE001  the act must never take the tick down; the failure is reported
        errors["act"] = type(exc).__name__
        out.append(Finding(f"act:error:throttle:{type(exc).__name__}", "act", f"throttle act skipped ({exc})"[:300]))
        return None, out


def _throttle_pending(cfg: Config, grant: Optional[dict], weekly: Dict[str, float],
                      measured: bool) -> Optional[Tuple[str, float]]:
    """``("raise", cap)`` when a grant naming the spend trigger is in force, an organization is at or over its
    soft cap and no flag is set; ``("clear", cap)`` when the tick's own flag is set and every organization is
    below the cap, the grant's while one is in force and the flag's own once it has expired, so an expired
    grant never strands its flag; else None. Both need this tick to have MEASURED the weekly spend: a missing
    or unreadable ledger proves nothing, so it neither raises nor clears."""
    if cfg.throttle_flag is None or not measured:
        return None
    armed = bool(grant) and tick_throttle.SPEND_TRIGGER in (grant.get("triggers") or [])
    if not cfg.throttle_flag.exists():
        if armed and tick_throttle.over_cap(weekly, _grant_cap(grant)):
            return "raise", _grant_cap(grant)
        return None
    flag = tick_throttle.read_flag(cfg.throttle_flag)
    if not tick_throttle.is_own(flag):
        return None
    cap = _grant_cap(grant) if grant else tick_throttle.flag_cap(flag)
    if cap is None or tick_throttle.over_cap(weekly, cap):
        return None
    return "clear", cap


def _throttle_one(cfg: Config, grant: Optional[dict], weekly: Dict[str, float], pending: Tuple[str, float],
                  now: float, errors: Dict[str, str]) -> Finding:
    """Raise or clear the flag and seal it; a failure is one finding and never takes the tick down. Every
    transition carries its own timestamp in the finding key, so a raise, a clear and a second raise on one
    day are three reports, never one."""
    transition, cap = pending
    paths = _act_ledgers(cfg)
    try:
        if transition == "raise":
            flag = tick_throttle.raise_flag(cfg.throttle_flag, weekly, cap, str((grant or {}).get("hash", "")), now,
                                            *paths)
            org, value = max(flag["weekly_pct"].items(), key=lambda kv: kv[1])
            return Finding(f"act:throttle:{flag['at']}", "act",
                           f"throttle flag raised under grant {flag['grant_hash']}: weekly spend ({org}) {value}% is at "
                           f"or over the {cap:.0%} cap; headless GRIP runs refuse until the weekly reset (leaf {flag['leaf']})")
        cleared = tick_throttle.clear_flag(cfg.throttle_flag, weekly, cap, now, *paths,
                                           grant_hash=str((grant or {}).get("hash", "")))
        worst = max((float(v) for v in weekly.values()), default=0.0)
        return Finding(f"act:throttle-cleared:{tick_throttle.iso(now)}", "act",
                       f"throttle flag cleared: weekly spend is below the {cap:.0%} cap again (highest {worst:.0%}; "
                       f"leaf {cleared['leaf']})")
    except Exception as exc:  # noqa: BLE001  the act must never take the tick down; the failure is reported
        errors["act"] = type(exc).__name__
        return Finding(f"act:error:throttle:{type(exc).__name__}", "act", f"throttle {transition} failed ({type(exc).__name__})")


def _reconcile_checkpoints(cfg: Config, state: dict) -> None:
    """The state follows the folders: a folder the operator removed frees its incidents, and a sealed folder the
    state never learned of (a crash between the seal and the state write) is imported, so no incident is copied twice."""
    for path, entry in list(state["checkpoints"].items()):
        kept = [f for f in entry.get("folders", []) if (cfg.checkpoints_dir / str(f)).is_dir()]
        if not kept:
            del state["checkpoints"][path]
        else:
            entry["folders"] = kept
    for index in tick_checkpoint.list_checkpoints(cfg.checkpoints_dir):
        source, folder, keys = index.get("source"), index.get("folder"), index.get("incident_keys") or []
        if not source or not folder:
            continue
        entry = state["checkpoints"].setdefault(source, {"folders": [], "keys": []})
        if folder not in entry["folders"]:
            entry["folders"].append(folder)
        entry["keys"] = sorted(set(entry["keys"]) | set(keys))


def _checkpoint_one(cfg: Config, state: dict, inc: dict, grant: dict, now: float) -> Finding:
    cap_mb = int(grant["limits"]["max_mb"])
    cap = cap_mb * 1048576
    tid = tick_checkpoint.transcript_id(inc["path"])
    size = tick_checkpoint.last_complete_offset(Path(inc["path"]))
    held = tick_checkpoint.folder_bytes(cfg.checkpoints_dir)
    skip = Finding(f"act:cap:{tid}:{grant['hash']}", "act",
                   f"checkpoint for session {inc['owner']} skipped: the grant's cap of {cap_mb} MB is full "
                   f"({held / 1048576:.2f} MB held, {size / 1048576:.2f} MB needed)")
    if held + size + tick_checkpoint.METADATA_RESERVE > cap:
        return skip
    try:
        index = tick_checkpoint.write_checkpoint(cfg.checkpoints_dir, inc, grant["grant"], now, cap, held,
                                                 cfg.idr_path, cfg.context_path, cfg.context_head)
    except tick_checkpoint.CapExceeded:
        return skip
    entry = state["checkpoints"].setdefault(inc["path"], {"folders": [], "keys": []})
    entry["folders"].append(index["folder"])
    entry["keys"] = sorted(set(entry["keys"]) | set(inc["keys"]))
    entry.update(kinds=inc["kinds"], grant_sha256=index["grant_sha256"], at=index["created"])
    return Finding(f"act:checkpoint:{index['folder']}", "act",
                   f"checkpoint {index['folder']} written for session {inc['owner']} ({index['bytes'] / 1048576:.2f} MB, "
                   f"sha256 {index['sha256'][:12]}, leaf {index['idr_leaf']}, memory {index['memory_node']}) "
                   f"under grant {grant['hash']}")


def _settle_spend(state: dict, steps: Dict[str, float], spend: List[Finding], reported: bool) -> None:
    """Store the new steps, except an organization's crossing that was not reported, so it is found again next tick."""
    held = {f.key.split(":")[1] for f in spend} if not reported else set()
    state["spend_steps"] = {org: step for org, step in steps.items() if org not in held} | {
        org: state["spend_steps"][org] for org in held if org in state["spend_steps"]}


def run(cfg: Config, now: Optional[float] = None) -> TickResult:
    """One tick: collect, dedupe against the state, report what is new, log the tick; the budget covers all of it."""
    now = time.time() if now is None else now
    at = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(now))
    started = time.monotonic()
    state = load_state(cfg.state_path)
    errors: Dict[str, str] = {}
    findings, weekly = collect(cfg, now, state, errors)
    spend, steps, resets = spend_findings(weekly, state["spend_steps"])
    rearm_spend(state, resets)
    findings += spend
    overdue = [f.key.split(":", 1)[1] for f in findings if f.check == "hypotheses"]
    findings += _act(cfg, state, now, errors, elapsed_s=time.monotonic() - started, weekly=weekly, overdue=overdue)
    new = split_new(findings, state)
    result = TickResult(at, 0.0, findings, new, errors)
    if cfg.dry_run:
        result.seconds = round(time.monotonic() - started, 3)
        result.overran = result.seconds > cfg.budget_s
        return result
    result.sent, reported = _deliver(cfg, new, errors)
    if reported:
        for f in new:
            state["reported"][f.key] = at
    state["pending"] = [] if reported else [asdict(f) for f in new]  # undelivered findings outlive their evidence
    if state.get("digest_at") is None:
        state["digest_at"] = now  # the day starts at install; the first digest is a day later
    _escalate(cfg, result, state, now, errors)
    _digest(cfg, result, state, now, errors)
    _settle_spend(state, steps, spend, reported or not new)
    prune_reported(state, now)
    result.seconds = round(time.monotonic() - started, 3)
    result.overran = result.seconds > cfg.budget_s
    save_state(cfg.state_path, state)
    append_log(cfg.log, result)
    return result


def render(result: TickResult) -> str:
    lines = [f"== grip-tick {result.at} == verdict {result.verdict} ({result.seconds:.2f}s)"]
    lines += [f"  {f.check:10s} {f.text}" for f in result.findings]
    lines.append(f"  new findings reported: {len(result.new)}" + (f" (message {result.sent})" if result.sent else ""))
    lines += [f"  error in {k}: {v}" for k, v in result.errors.items()]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- install

def plist_paths() -> Tuple[Path, Path]:
    return CLAUDE_DIR / "config" / "tick" / f"{LABEL}.plist", Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def install() -> int:
    src, dst = plist_paths()
    if not src.exists():
        print(f"grip-tick: plist not found: {src}", file=sys.stderr)
        return 2
    home = Path.home()
    dst.parent.mkdir(parents=True, exist_ok=True)
    (CLAUDE_DIR / "logs").mkdir(parents=True, exist_ok=True)  # launchd does not create the stream paths' folder
    content = src.read_text(encoding="utf-8").replace("$CLAUDE_DIR", str(CLAUDE_DIR)).replace("$HOME", str(home))
    dst.write_text(content, encoding="utf-8")
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(dst)], capture_output=True)
    done = subprocess.run(["launchctl", "bootstrap", domain, str(dst)], capture_output=True, text=True)
    if done.returncode != 0:
        print(f"grip-tick: launchctl bootstrap failed: {done.stderr.strip()}", file=sys.stderr)
        return 2
    print(f"grip-tick: installed {dst}, every 300 s; remove with grip-tick --uninstall")
    return 0


def uninstall() -> int:
    _, dst = plist_paths()
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(dst)], capture_output=True)
    if dst.exists():
        dst.unlink()
    print(f"grip-tick: removed {dst}")
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="The always-on tick: a deterministic floor, no model, report-only.")
    ap.add_argument("--dry-run", action="store_true", help="run the checks, write nothing, send nothing")
    ap.add_argument("--no-send", action="store_true", help="log and mark findings, send no message")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true",
                    help="the launchd mode: print nothing on a QUIET tick, and exit 0 on ACT as well, because findings "
                         "are reported on their own channel and a non-zero exit would read as the tick itself failing")
    ap.add_argument("--threshold-hours", type=float, default=None, help="a pending question older than this is reported")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    cfg = default_config()
    cfg.dry_run, cfg.send = args.dry_run, not args.no_send
    if args.threshold_hours is not None:
        cfg.threshold_s = int(args.threshold_hours * 3600)
    now = time.time()
    lock = "acquired" if cfg.dry_run else acquire_lock(cfg.lock, now)
    if lock == "held":
        return 0
    if lock == "error":
        print(f"grip-tick: cannot take the lock at {cfg.lock}", file=sys.stderr)
        return 2
    try:
        if not cfg.dry_run:
            rotate_log(cfg.log)
        result = run(cfg, now)
    finally:
        if not cfg.dry_run:
            release_lock(cfg.lock)
    if not (args.quiet and result.verdict == "QUIET"):
        print(json.dumps(result.to_dict(), indent=1) if args.json else render(result))
    if args.quiet and result.verdict == "ACT":
        return 0  # under launchd, exit 1 would make the next tick report this one as a failed agent
    return result.exit_code


if __name__ == "__main__":
    # The checkpoint act seals IDR leaves, so this tool must run under an interpreter that
    # can dual-sign before any write (rules/never-system-python-for-grip.md).
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(Path(__file__))
    raise SystemExit(main())
