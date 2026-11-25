"""happiverse_node_check — measure whether ONE machine is on the HAPPIverse.

The criterion V>> locked on 2026-09-22 (HITL decision matrix, D6) has four legs
per machine: a mesh heartbeat under 60 seconds old, a checkout level with
``origin/main``, a health probe that answers, and a star reading ``live`` on the
DEPLOYED public map. This module measures all four and returns a record.

Three properties are the whole point:

**A leg that could not be measured is UNKNOWN, never PASS.** The failure this
guards against is absence-of-signal read as success — the same class as a PR
with zero checks reported green, and a trial with zero claims sealing as
Accept. ``overall`` is PASS only when every leg is PASS; a single UNKNOWN makes
the overall UNKNOWN.

**Nothing is taken on a report's word.** The star leg reads the deployed map
over HTTPS, not the local file that was rendered here; the level leg asks the
node whether it holds main as the MEASURING machine fetched it, never the
node's own copy of main — that copy freezes silently when a node loses access
to GitHub, and a node judged against a frozen copy of main looks level.

**Every dependency is injected**, so the whole thing is testable without a
network, a mesh or a second machine — and so a stranger can run it against
their own fleet by supplying their own runner.
"""
from __future__ import annotations

import json
import shlex
import socket
import sqlite3
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
LEGS = ("mesh", "level", "health", "star")
HEARTBEAT_MAX_AGE_S = 60

# A commit counts against a machine once it has SETTLED — been on origin/main
# longer than this. Without a window the leg is unholdable on an active repo:
# measured 2026-09-22, two machines went PASS -> FAIL with nothing about them
# changing, purely because other sessions merged in the interval. A verdict that
# flips with no change to its subject is measuring the race, not the machine.
# It stays strict: the VPS's pre-merge state, weeks stale, scores 1324 settled
# commits behind and fails, while a tree missing only what landed in the last
# hour scores 0.
LEVEL_SETTLE_WINDOW = "1 hour ago"
DEFAULT_MAP_URL = "https://your-gateway.example.com/nodes.json"


def _norm(text: str) -> str:
    """Fold a nickname the way the registry keys it, so "the second Mac" on the map
    matches "the second Mac" on the command line."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    for curly in ("’", "‘", "ʼ", "´", "`"):
        text = text.replace(curly, "'")
    return " ".join(text.split()).casefold().strip(".,;:!?")


@dataclass
class Leg:
    name: str
    verdict: str
    evidence: str

    def as_dict(self) -> dict:
        return {"leg": self.name, "verdict": self.verdict, "evidence": self.evidence}


@dataclass
class Runner:
    """Runs a shell command locally, or on another machine over SSH.

    ``ssh`` is a destination like ``user@100.64.0.10``. There is no fallback to
    running remotely-intended commands locally: if the SSH destination cannot be
    reached the leg is UNKNOWN, because a local answer to a question about
    another machine is not a weak measurement, it is the wrong one."""

    ssh: str = ""
    timeout: int = 20

    # ssh exits 255 for its OWN transport failures — refused connection, wrong
    # key, unknown host. That is the command never running, not the command
    # failing, and scoring it as a failure claims a measurement that never
    # happened. Found by running this probe against the real fleet: an SSH
    # "Permission denied" was being reported as a FAILED health probe.
    _SSH_TRANSPORT = (
        "permission denied", "connection refused", "connection timed out",
        "no route to host", "host key verification failed", "could not resolve hostname",
        "operation timed out", "connection closed by",
    )

    def run(self, command: str) -> tuple:
        argv = (["ssh", "-o", "BatchMode=yes", f"-o", f"ConnectTimeout={self.timeout}",
                 self.ssh, command] if self.ssh else ["bash", "-lc", command])
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout + 10)
        except Exception as exc:  # unreachable host, timeout, missing ssh
            return None, "", f"{type(exc).__name__}: {exc}"
        rc, out, err = p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
        if self.ssh and rc == 255 and any(m in err.lower() for m in self._SSH_TRANSPORT):
            return None, "", err
        return rc, out, err


# Mesh agents RUNNING where the command runs. launchd lists a loaded job whether
# or not it is alive, so only rows carrying a numeric PID count: measured
# 2026-09-24 on ol ancient, a mesh client crash-looping on a missing global
# (PID "-", last exit 1) was being counted as a participating agent.
MESH_AGENTS_CMD = (
    "ls ~/.grip-session-mesh/presence.db >/dev/null 2>&1 && echo HAS_DB; "
    "(pm2 jlist 2>/dev/null | grep -c '\"name\":\"mesh-' || true); "
    "(launchctl list 2>/dev/null | awk '$1 ~ /^[0-9]+$/ && $3 ~ /^com[.]grip[.]mesh-/' | wc -l || true)"
)


def _machine_key(name: str) -> str:
    """Fold a machine name the way macOS varies it.

    Blackbook's sessions registered under five names in one day (2026-09-24):
    its DHCP-derived hostname moves with its address (192-168-0-66, -109, -114,
    -115) and its LocalHostName appears as "Vs-MacBook-Pro.local". Case and the
    ".local" suffix are presentation, not identity."""
    key = str(name or "").strip().casefold()
    return key[: -len(".local")] if key.endswith(".local") else key


def _mesh_agent_count(runner: "Runner") -> tuple:
    """(count, detail): mesh agents running where ``runner`` runs. The count is
    None when the machine could not be asked at all."""
    rc, out, err = runner.run(MESH_AGENTS_CMD)
    if rc is None:
        return None, err
    counts = [int(x) for x in out.split() if x.strip().isdigit()]
    return (max(counts) if counts else 0), out


# Two forms of evidence, because two kinds of machine participate differently:
#
# * the mesh AGENTS running — bus and relay up on the machine. A headless server
#   produces only this: the VPS runs mesh-bus and mesh-relay under pm2 and has
#   no presence database at all. A workstation running the agents participates
#   the same way, so the local machine is asked about its agents too — Blackbook
#   ran four on 2026-09-24 and failed this leg for want of a session heartbeat
#   under a hostname it no longer carried.
# * a SESSION heartbeat — an interactive GRIP session registering presence.
#
# Asking THIS machine's presence database about ANOTHER machine was an earlier
# bug: that database only holds local sessions, so it answered "no session" for
# every remote machine. A remote machine is asked about itself over SSH, and an
# unreachable one is UNKNOWN rather than a fabricated failure.
#
# A TCP connection from our relay to a machine's port remains inadmissible: it
# proves a socket, not a participating node. ``relay/mesh_relay.py:119`` reaches
# the peer with a bare ``asyncio.open_connection``; its ``_authenticate`` runs
# against the LOCAL bus on 127.0.0.1, never the peer (re-examined 2026-09-22).
def check_mesh(spec: "NodeSpec", presence_db, now: datetime, runner: "Runner") -> Leg:
    """Is this machine PARTICIPATING in the mesh? See the comment above."""
    if spec.ssh:
        return _remote_mesh_leg(runner)
    agents, _ = _mesh_agent_count(runner)
    if agents:
        return Leg("mesh", PASS, f"{agents} mesh agent(s) running on this machine")
    return _session_heartbeat_leg(spec.hostnames, presence_db, now)


def _remote_mesh_leg(runner: "Runner") -> Leg:
    agents, detail = _mesh_agent_count(runner)
    if agents is None:
        return Leg("mesh", UNKNOWN, f"could not reach the machine: {detail[:150]}")
    if agents > 0:
        return Leg("mesh", PASS, f"{agents} mesh agent(s) running on the machine itself")
    return Leg("mesh", FAIL, f"no mesh agents running on the machine: {detail[:120]!r}")


def _session_heartbeat_leg(hostnames: Sequence[str], presence_db, now: datetime) -> Leg:
    if not hostnames:
        return Leg("mesh", UNKNOWN, "no hostname given for this machine")
    try:
        con = sqlite3.connect(f"file:{presence_db}?mode=ro", uri=True)
        rows = con.execute(
            "select machine, max(last_heartbeat) from sessions group by machine"
        ).fetchall()
    except Exception as exc:
        return Leg("mesh", UNKNOWN, f"presence db unreadable: {exc}")
    # The database holds only THIS machine's sessions, so the name it carries
    # right now is one of its names too.
    wanted = {_machine_key(h) for h in (*hostnames, socket.gethostname())} - {""}
    best = _freshest_heartbeat(rows, wanted)
    if best is None:
        seen = ", ".join(sorted({str(m) for m, _ in rows})) or "none"
        return Leg("mesh", FAIL, f"no mesh session from {sorted(wanted)}; db has: {seen}")
    return _heartbeat_age_leg(best, now)


def _freshest_heartbeat(rows, wanted: set) -> Optional[tuple]:
    best: Optional[tuple] = None
    for machine, last in rows:
        if _machine_key(machine) in wanted and last and (best is None or str(last) > str(best[1])):
            best = (machine, last)
    return best


def _heartbeat_age_leg(best: tuple, now: datetime) -> Leg:
    try:
        stamp = datetime.fromisoformat(str(best[1]).replace("Z", "+00:00"))
    except ValueError:
        return Leg("mesh", UNKNOWN, f"unparseable heartbeat {best[1]!r}")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (now - stamp).total_seconds()
    verdict = PASS if 0 <= age <= HEARTBEAT_MAX_AGE_S else FAIL
    return Leg("mesh", verdict, f"{best[0]} session heartbeat {age:.0f}s ago (max {HEARTBEAT_MAX_AGE_S}s)")


@dataclass(frozen=True)
class CanonicalMain:
    """main as the MEASURING machine sees it.

    ``tip`` is origin/main's head; ``settled`` is the newest commit on it older
    than LEVEL_SETTLE_WINDOW. ``counter(a, b)`` counts ``a..b`` in the measuring
    repository, for when the measured machine lacks the commits to count."""
    tip: str
    settled: str
    counter: Optional[Callable[[str, str], Optional[int]]] = None


def canonical_main(repo: str, run: Callable = subprocess.run) -> Optional[CanonicalMain]:
    """Refresh main on THIS machine and read it; None when it cannot be read.
    The fetch is best effort: offline, the last successful fetch still stands."""
    def git(*args) -> tuple:
        try:
            p = run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60)
        except Exception:
            return 1, ""
        return p.returncode, (p.stdout or "").strip()

    git("fetch", "-q", "origin", "main")
    rc_tip, tip = git("rev-parse", "--verify", "--quiet", "origin/main^{commit}")
    rc_settled, settled = git("rev-list", "-1", f"--before={LEVEL_SETTLE_WINDOW}", "origin/main")
    if rc_tip or rc_settled or not tip or not settled:
        return None

    def counter(a: str, b: str) -> Optional[int]:
        rc, out = git("rev-list", "--count", f"{a}..{b}")
        return int(out) if rc == 0 and out.isdigit() else None

    return CanonicalMain(tip=tip, settled=settled, counter=counter)


# NOT BEHIND is the measure: the machine's HEAD contains main's settled commit.
# Being ahead is reported, never failed — the VPS keeps a snapshot commit of its
# own config, so after merging all of main its HEAD is a DESCENDANT of main, and
# an exact-SHA comparison called that "behind".
#
# main is the MEASURING machine's, passed in. The leg used to fetch on the
# target and count against the target's own origin/main, swallowing a failed
# fetch — so a machine that had lost GitHub access was judged against a copy of
# main frozen at its last good fetch. Measured 2026-09-24: the second Mac's copy
# dated from 2026-07-15 and the leg reported 115 commits behind when it was over
# 1,650; had HEAD equalled that frozen copy, it would have reported level.
#
# EVERY link before the probe loop is &&: with `;` a failed `cd` let the counts
# run in whatever directory the shell landed in, reporting a confident number
# about the WRONG repository ("14 behind, 26 ahead" for /nonexistent/.claude).
#
# The BRANCH is part of level. Being ahead stays allowed, but only on main: on
# 2026-10-17 Blackbook's shared tree, which 40 background jobs execute from, sat
# on a merged feature branch with commits on no remote. It was 0 settled commits
# behind, so this leg passed it, while grip-tree-sync could not bring it level.
# Every machine in the fleet runs main (measured the same night), so a tree on any
# other branch, or a detached HEAD, is not level whatever its counts say.
LEVEL_BRANCH = "main"


def check_level(repo_dir: str, runner: Runner, canonical: Optional[CanonicalMain] = None) -> Leg:
    """Is this machine's checkout missing anything settled on main?"""
    if not repo_dir:
        return Leg("level", UNKNOWN, "no repo directory given for this machine")
    if canonical is None:
        return Leg("level", UNKNOWN, "main could not be read on the measuring machine, "
                                     "so there is nothing to be level with")
    rc, out, err = runner.run(_level_command(repo_dir, canonical))
    if rc is None:
        return Leg("level", UNKNOWN, f"could not reach the machine: {err[:160]}")
    if rc != 0:
        return Leg("level", UNKNOWN, f"git failed (rc={rc}): {(err or out)[:160]}")
    parsed = _parse_level(out)
    if parsed is None:
        return Leg("level", UNKNOWN, f"unexpected git output: {out[:160]!r}")
    return _level_verdict(*parsed, canonical)


def _level_command(repo_dir: str, canonical: CanonicalMain) -> str:
    probes = " ".join(shlex.quote(c) for c in (canonical.settled, canonical.tip))
    return (
        f"cd {shlex.quote(repo_dir)} && git rev-parse HEAD && git rev-parse --abbrev-ref HEAD "
        f"&& for c in {probes}; do "
        f"if git cat-file -e \"${{c}}^{{commit}}\" 2>/dev/null; then "
        f"echo \"$(git rev-list --count HEAD..$c) $(git rev-list --count $c..HEAD)\"; "
        f"else echo MISSING; fi; done"
    )


def _parse_level(out: str) -> Optional[tuple]:
    """(head, branch, settled_pair, tip_pair); a pair is (behind, ahead), or None
    when the machine does not have that commit at all. ``branch`` is ``HEAD`` for
    a detached checkout, as git prints it."""
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    if len(lines) != 4:
        return None
    try:
        return lines[0], lines[1], _count_pair(lines[2]), _count_pair(lines[3])
    except ValueError:
        return None


def _count_pair(line: str) -> Optional[tuple]:
    if line == "MISSING":
        return None
    behind, ahead = line.split()
    return int(behind), int(ahead)


def _level_verdict(head: str, branch: str, settled: Optional[tuple], tip: Optional[tuple],
                   canonical: CanonicalMain) -> Leg:
    main_ref = f"main {canonical.tip[:9]}"
    if settled is None:
        n = canonical.counter(head, canonical.settled) if canonical.counter else None
        size = f"{n} settled commit(s) behind" if n is not None else "behind by an unknown count"
        return Leg("level", FAIL, f"{size} {main_ref}: the machine does not have main's "
                                  f"settled commit {canonical.settled[:9]} at all")
    raw = f"{tip[0]} raw" if tip else "its newest commits not yet received"
    extra = f", {tip[1]} ahead (local commits)" if tip and tip[1] else ""
    counts = f"{settled[0]} settled commit(s) behind {main_ref} ({raw}, window {LEVEL_SETTLE_WINDOW}){extra}"
    if branch != LEVEL_BRANCH:
        where = "a detached HEAD" if branch == "HEAD" else f"branch {branch!r}"
        return Leg("level", FAIL, f"checked out on {where}, not {LEVEL_BRANCH}: {counts}")
    return Leg("level", PASS if settled[0] == 0 else FAIL, counts)


def check_health(command: str, runner: Runner) -> Leg:
    """A configured health probe that exits zero. Not configured is UNKNOWN."""
    if not command:
        return Leg("health", UNKNOWN, "no health probe configured for this machine")
    rc, out, err = runner.run(command)
    if rc is None:
        return Leg("health", UNKNOWN, f"could not reach the machine: {err[:160]}")
    verdict = PASS if rc == 0 else FAIL
    return Leg("health", verdict, f"`{command[:60]}` exited {rc}: {(out or err)[:120]}")


def check_star(nickname: str, map_url: str, fetch: Callable[[str], str]) -> Leg:
    """Does this machine have a star on the DEPLOYED public map?

    Reading the locally rendered file would only prove that this machine rendered
    it. What the criterion claims is that the world can see it, so this fetches
    the published registry over the network.

    It asserts PRESENCE, not the star's status, and that is a correction. The
    first version required ``status == "live"`` — but a star's status is set
    FROM these very legs, so the leg depended on its own result and no machine
    could ever pass from a cold start. The circularity was invisible until the
    map was published and all four stars, correctly reading "joining", failed a
    leg that was waiting for them to read "live".

    Status is reported as evidence and reconciled separately by
    ``star_status_matches``: the published status is a CLAIM about a machine,
    and a claim that disagrees with the measurement is a thing to re-publish,
    not a leg to fail."""
    try:
        raw = fetch(map_url)
    except Exception as exc:
        return Leg("star", UNKNOWN, f"could not fetch {map_url}: {type(exc).__name__}: {exc}")
    try:
        nodes = json.loads(raw).get("nodes", [])
    except Exception as exc:
        return Leg("star", UNKNOWN, f"{map_url} is not a registry document: {exc}")
    want = _norm(nickname)
    for node in nodes:
        if _norm(node.get("display", "")) == want:
            status = str(node.get("status", "")).strip().lower()
            return Leg("star", PASS,
                       f"{node.get('node_id','?')} is on the deployed map, status {status!r}")
    shown = ", ".join(str(n.get("display", "")) for n in nodes) or "none"
    return Leg("star", FAIL, f"no star named {nickname!r} on the deployed map; it shows: {shown}")


@dataclass
class NodeSpec:
    nickname: str
    hostnames: Sequence[str] = field(default_factory=tuple)
    repo_dir: str = ""
    ssh: str = ""
    health_command: str = ""


def check_node(
    spec: NodeSpec,
    *,
    presence_db,
    map_url: str = DEFAULT_MAP_URL,
    now: Optional[datetime] = None,
    fetch: Optional[Callable[[str], str]] = None,
    runner: Optional[Runner] = None,
    canonical: Optional[CanonicalMain] = None,
) -> dict:
    """Measure all four legs and reduce them to one overall verdict. Without a
    ``canonical`` main the level leg is UNKNOWN: there is nothing to be level with."""
    now = now or datetime.now(timezone.utc)
    runner = runner if runner is not None else Runner(ssh=spec.ssh)
    if fetch is None:
        fetch = default_fetch

    legs = [
        check_mesh(spec, presence_db, now, runner),
        check_level(spec.repo_dir, runner, canonical),
        check_health(spec.health_command, runner),
        check_star(spec.nickname, map_url, fetch),
    ]
    record = {
        "nickname": spec.nickname,
        "checked_at": now.isoformat(),
        "map_url": map_url,
        "legs": [l.as_dict() for l in legs],
        "overall": reduce_legs(legs),
    }
    record["published_status_matches"] = star_status_matches(record)
    return record


def default_fetch(url: str) -> str:  # pragma: no cover — the network path
    """Fetch the deployed map.

    Carries an explicit User-Agent because the CDN in front of the public map
    answers 403 to a bare urllib request — which the star leg then reported as
    UNKNOWN for every machine. An honest UNKNOWN that is really "we asked
    wrongly" still costs a real measurement."""
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": "grip-happiverse-node-check/1.0"})
    with urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8")


def star_status_matches(record: dict) -> Optional[bool]:
    """Does the star's PUBLISHED status agree with what was measured?

    ``live`` claims the machine is on; anything else claims it is not. Returns
    None when there is no star to compare against. This is deliberately NOT a
    leg: a disagreement means the map needs re-publishing, which is an action,
    where a failed leg would mean the machine is not on the HAPPIverse."""
    star = next((l for l in record["legs"] if l["leg"] == "star"), None)
    if star is None or star["verdict"] != PASS:
        return None
    claims_on = "'live'" in star["evidence"]
    return claims_on == (record["overall"] == PASS)


def reduce_legs(legs: Sequence[Leg]) -> str:
    """PASS only when every one of the four legs passed.

    An EMPTY set of legs is UNKNOWN, not PASS. ``all([])`` is True, and a reducer
    that returns success for "nothing was measured" is the defect this function
    exists to avoid — the same shape as a verdict of Accept on a trial that ran
    nothing."""
    verdicts = {l.name: l.verdict for l in legs}
    missing = [name for name in LEGS if name not in verdicts]
    if missing:
        return UNKNOWN
    if any(v == UNKNOWN for v in verdicts.values()):
        return UNKNOWN
    return PASS if all(v == PASS for v in verdicts.values()) else FAIL
