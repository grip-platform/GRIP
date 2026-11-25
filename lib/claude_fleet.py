"""Claude tokens across the operator's machines: what is where, and making it so.

One deterministic tool, run from the machine that holds the tokens (the
"source"). For every machine in a local list and every account label it can:

- ``check``     survey each machine and say which cells hold the right token,
                and which label each machine's relay bills on.
                Passive: no call to the vendor, and nothing is written anywhere.
- ``identify``  make one tiny call per machine and label, from that machine,
                to show the token works from there.
- ``converge``  plan, and with ``--apply`` do, what brings every cell to "the
                right token, in a private file": copy the token to a machine
                that lacks it, then look again, until nothing more can be done
                without the operator.
- ``strays``    list the tokens lying in each machine's shell files, by file
                and line number. With ``--live``, ask the service whether each
                still works. With ``--remove --machine NAME``, delete those
                lines on the machines named and on no other. With ``--deep``,
                also look in what each harness keeps (prompt history, session
                records, shell snapshots, its database) and say what was
                looked in. Nothing there can be removed: a copy in a record is
                settled when the service refuses the token.
- ``keychain``  say what the keychain items that hold a Claude token hold, by
                digest (``lib/claude_keychain.py`` names them and says who
                reads them). With ``--live``, ask the service about each. With
                ``--set ITEM=LABEL``, put the token stored under LABEL into
                ITEM, on the machine this runs on or the ones named. ``check``
                says so when an item holds anything but one of the fleet's own
                tokens, and what stops when that token is removed on the
                vendor's side: the readers of the item, or nobody.
- ``forget``    remove a kept copy (a label such as ``laurie-old``) from every
                machine that stores it, once its token is dead. Each machine
                asks the service about its own copy first, the way that kind
                of credential needs; only a copy the service refuses is
                removed, through the store's own command there. One that
                still works, could not be checked, or sits on a machine that
                did not answer is kept. Never one of the fleet's own labels.

WHAT "THE RIGHT TOKEN" MEANS. The token stored under the same label on the
source machine. Two files with the same digest hold the same token, so a cell
is verified by comparing digests. No token and no vendor call is needed for it.

THE TWO GATES ON A COPY. A token is copied onto a machine only when

1. the token canary passes there, run fresh for this copy (no token moves onto
   a machine until the canary passes there), and
2. no token-shaped text is lying in that machine's shell start-up or history
   files, because the fence the canary proves does not cover those files.

``--allow-strays`` lifts the second gate. Nothing lifts the first.

HOW A MACHINE IS ASKED. Over ``ssh`` in batch mode, or directly for the source.
The survey is a Python program sent on standard input and made of this
checkout's own modules, so the answer does not depend on which version of GRIP
is installed there. A token being copied travels on the connection's standard
input into the remote ``grip-relay-bill store``: it is never an argument, never
a file on the way, and never printed.

WHAT A COPY DOES TO THE TOKEN THAT WAS THERE. The store on that machine never
overwrites a different token: it keeps it as ``<label>-old``, and a relay that
was billing on the label moves to the new token. What the store said is shown
under the copy. A relay that bills on anything but one of the fleet's labels is
listed as waiting on the operator, because it stops when that token is removed
on the vendor's side. The store that does this is the one installed on that
machine: one older than this behaviour overwrites the label and leaves its
relay on the old token, and the next survey then lists that relay as waiting.

THE LOOP. ``converge`` is one improvement step (act, then survey again) handed
to the DFI-FIB kernel in ``lib/improve_kernel.py``. The kernel stops it when
every cell is verified, when a step changes nothing (what is left needs the
operator), or at its depth cap. Its record of fixed points is kept for one run
only: a fleet that could not move yesterday is surveyed again today.

THE MACHINE LIST is the operator's and holds host names, so it lives outside
every repository: ``${XDG_CONFIG_HOME:-~/.config}/grip/claude-fleet.json``.

NOT COVERED, and said rather than solved. The source is always the machine
this runs on. A machine that lies about itself can get a token copied to it;
it is one of the operator's own machines either way. The stray-token survey
reads the shell start-up and history files named in ``claude_fleet_probe``,
and with ``--deep`` the harness records named there and in the machine list:
a token lying anywhere else is not seen. Removing a stray line from a file does
not switch the token off: that is done on the vendor's side, and ``strays
--live`` shows whether it was.
"""
from __future__ import annotations

import itertools
import json
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import claude_as  # noqa: E402
import claude_keychain  # noqa: E402
import claude_token_store  # noqa: E402
from improve_kernel import ImproveResult, InnerspeakerGo, fingerprint  # noqa: E402

CONFIG_ENV = "GRIP_CLAUDE_FLEET_CONFIG"
SSH = ("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
PROBE_SECONDS, COPY_SECONDS, CANARY_SECONDS = 240, 120, 900

#: A live check of strays makes one call per token, each with its own time
#: limit on the machine, so this is only a backstop for a machine that hangs.
LIVE_SECONDS = 3600

#: Every cell verified. The kernel compares the step's metric against this.
THRESHOLD = {"verified": ("ge", 1.0)}

#: A cell in one of these states is put right by copying the source's token.
NEEDS_A_COPY = ("missing", "differs", "unsafe")

#: The modules each action needs on the machine that carries it out.
_PASSIVE = ("claude_token_store", "claude_as", "claude_keychain", "claude_fleet_probe")
_CALLING = (*_PASSIVE, "claude_token_mint")
_MODULES = {"survey": _PASSIVE, "identify": _CALLING, "strays": _CALLING, "remove_strays": _PASSIVE,
            "records": _CALLING, "keychain_lives": _CALLING, "keychain_put": _PASSIVE,
            "stored_lives": _CALLING}

# The program a machine is sent. It unpacks the modules into a directory of its
# own, imports them the ordinary way, prints one line of JSON, and removes the
# directory. The directory holds source code and nothing else.
_PROGRAM = """\
import json, pathlib, shutil, sys, tempfile
sources = json.loads({sources!r})
home = pathlib.Path(tempfile.mkdtemp(prefix="grip-fleet-"))
try:
    for name, text in sources.items():
        (home / (name + ".py")).write_text(text, encoding="utf-8")
    sys.path.insert(0, str(home))
    import claude_fleet_probe
    print(json.dumps(claude_fleet_probe.run({action!r}, {args!r})))
finally:
    shutil.rmtree(home, ignore_errors=True)
"""

EXAMPLE = """\
{
  "labels": ["laurie", "teammate"],
  "machines": [
    {"name": "laptop"},
    {"name": "server", "ssh": "grip@server.example.net",
     "canary": "GRIP_RELAY_HARNESS=opencode ~/.claude/bin/grip-token-canary --json"},
    {"name": "spare", "ssh": "jane@spare.example.net", "python": "/opt/homebrew/bin/python3"}
  ]
}"""


class ConfigError(Exception):
    """The machine list is missing or does not make sense."""


@dataclass(frozen=True)
class Machine:
    """One machine. ``ssh`` is how to reach it; without one it is the source."""
    name: str
    ssh: str | None = None
    python: str | None = None
    grip_dir: str = "~/.claude"
    canary: str | None = None
    records: tuple[str, ...] = ()

    def run_python(self) -> str:
        """The command that reads a program on standard input, on this machine."""
        local = shlex.quote(sys.executable)
        return f"{self.python or (local if self.ssh is None else 'python3')} -"

    def run_canary(self) -> str:
        return self.canary or f"{self.grip_dir}/bin/grip-token-canary --json"

    def store(self, label: str) -> str:
        return f"{self.grip_dir}/bin/grip-relay-bill store {label}"

    def forget(self, label: str) -> str:
        return f"{self.grip_dir}/bin/grip-relay-bill forget {label}"

    def argv(self, command: str) -> list[str]:
        """How one shell command is run on this machine from here."""
        return ["/bin/sh", "-c", command] if self.ssh is None else [*SSH, self.ssh, command]


@dataclass(frozen=True)
class Fleet:
    """The labels every machine should hold, and the machines."""
    labels: tuple[str, ...]
    machines: tuple[Machine, ...]

    @property
    def source(self) -> Machine:
        return next(machine for machine in self.machines if machine.ssh is None)

    @property
    def cells(self) -> list[tuple[Machine, str]]:
        """Every machine paired with every label: the fleet is this grid."""
        return list(itertools.product(self.machines, self.labels))


def config_path(env=None) -> Path:
    """Where the machine list is: beside the token store, outside any repository."""
    env = os.environ if env is None else env
    named = (env.get(CONFIG_ENV) or "").strip()
    return Path(named).expanduser() if named else (
        claude_token_store.store_dir(env).parent / "claude-fleet.json")


def _records(entry: dict) -> tuple[str, ...]:
    """The extra places one machine keeps harness records in, from its entry.
    Leaving the key out means none. Anything but a list of paths is refused,
    the wrong kind of nothing included (an empty text, a zero)."""
    places = [] if entry.get("records") is None else entry["records"]
    if not (isinstance(places, list) and all(isinstance(place, str) and place.strip() for place in places)):
        raise ConfigError(f"'records' of {entry['name']!r} must be a list of paths, such as "
                          '[".codex-two", "/var/scratch"]')
    return tuple(places)


def _machine(entry) -> Machine:
    if not isinstance(entry, dict) or claude_token_store.label_problem(str(entry.get("name") or "")):
        raise ConfigError(f"every machine needs a plain one-word name; got {entry!r}")
    target = str(entry.get("ssh") or "")
    if target.startswith("-") or len(target.split()) > 1:
        # It is handed to ssh as the host. One that starts with a dash would be
        # read as an option instead (-oProxyCommand=... runs a command here).
        raise ConfigError(f"the ssh target of {entry['name']!r} must be one word that does not "
                          "start with a dash, such as user@host")
    known = {key: entry[key] for key in ("name", "ssh", "python", "grip_dir", "canary") if entry.get(key)}
    return Machine(**known, records=_records(entry))


def _read_list(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        raise ConfigError(f"there is no machine list at {path}. It looks like this:\n{EXAMPLE}") from None
    except ValueError as exc:
        raise ConfigError(f"{path} is not valid JSON ({exc})") from None
    return raw if isinstance(raw, dict) else {}


def load(path: Path) -> Fleet:
    """Read and check the machine list. Raises ``ConfigError`` in plain words."""
    raw = _read_list(path)
    labels = tuple(raw.get("labels") or ())
    bad = list(filter(claude_token_store.label_problem, map(str, labels)))
    machines = tuple(map(_machine, raw.get("machines") or ()))
    names = [machine.name for machine in machines]
    sources = [machine.name for machine in machines if machine.ssh is None]
    problems = [
        (not labels, "it names no labels"),
        (bool(bad), f"these cannot be labels: {bad}"),
        (len(set(names)) != len(names), "two machines share a name"),
        (len(sources) != 1, "exactly one machine must have no ssh target: the one this "
                            f"runs on, which is the source of the tokens (found {len(sources)})"),
    ]
    wrong = [why for failed, why in problems if failed]
    if wrong:
        raise ConfigError(f"{path}: {wrong[0]}")
    return Fleet(labels=labels, machines=machines)


# --- asking a machine ------------------------------------------------------------

def _start(argv: list[str], stdin: str, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def program(action: str, args: list) -> str:
    """The program sent to a machine for one action: this checkout's own
    modules, then one call."""
    sources = {name: (_LIB / f"{name}.py").read_text(encoding="utf-8") for name in _MODULES[action]}
    return _PROGRAM.format(sources=json.dumps(sources), action=action, args=list(args))


def _last_json(text: str | None):
    lines = [line for line in (text or "").splitlines() if line.strip()]
    try:
        return json.loads(lines[-1]) if lines else None
    except ValueError:
        return None


UNREADABLE = "gave an answer that could not be read"


def _readable(answer: dict) -> bool:
    """Every action answers with a table of named things. An answer that says
    it worked and carries anything else is not one this tool can go on."""
    return not answer["ok"] or isinstance(answer.get("data"), dict)


def ask(machine: Machine, action: str, *args, start=_start, timeout: float = PROBE_SECONDS) -> dict:
    """Carry out one probe action on one machine. Never raises: a machine that
    cannot be reached, gives no answer, or says it worked and carries nothing
    readable (``UNREADABLE``) is an answer too, with the reason in ``why``."""
    try:
        done = start(machine.argv(machine.run_python()), program(action, list(args)), timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "why": f"could not be reached ({type(exc).__name__})"}
    answer = _last_json(done.stdout)
    if isinstance(answer, dict) and "ok" in answer:
        return answer if _readable(answer) else {"ok": False, "why": UNREADABLE}
    said = claude_token_store.scrubbed(done.stderr or done.stdout, 200)
    return {"ok": False, "why": f"gave no answer (exit {done.returncode}): {said}"}


def survey_all(fleet: Fleet, start=_start) -> dict:
    """The state of the whole fleet: one survey per machine, by name."""
    return {machine.name: ask(machine, "survey", machine.grip_dir, start=start)
            for machine in fleet.machines}


# --- reading the state -------------------------------------------------------------

def _stored(state: dict, name: str) -> dict:
    answer = state.get(name) or {}
    return answer["data"]["store"]["labels"] if answer.get("ok") else {}


def cell(fleet: Fleet, state: dict, name: str, label: str) -> str:
    """Where one machine stands for one label: ``verified``, ``missing``,
    ``differs``, ``unsafe``, ``unreachable``, or ``no-source`` when the source
    itself holds no token under that label."""
    wanted = (_stored(state, fleet.source.name).get(label) or {}).get("digest")
    if not wanted:
        return "no-source"
    if not (state.get(name) or {}).get("ok"):
        return "unreachable"
    entry = _stored(state, name).get(label)
    if not entry:
        return "missing"
    if entry["digest"] != wanted:
        return "differs"
    return "unsafe" if entry["problem"] else "verified"


def matrix(fleet: Fleet, state: dict) -> dict:
    """Every cell, keyed by ``(machine name, label)``: where it stands."""
    return {(machine.name, label): cell(fleet, state, machine.name, label)
            for machine, label in fleet.cells}


def verified_share(cells: dict) -> float:
    """The share of cells that are verified, from 0.0 to 1.0."""
    every = list(cells.values())
    return every.count("verified") / len(every) if every else 0.0


def copies_needed(fleet: Fleet, state: dict) -> list[tuple[str, str]]:
    """The ``(machine name, label)`` cells that a copy would put right. The
    source is never among them: nothing can be copied onto the machine the
    copies come from."""
    source = fleet.source.name
    return [where for where, status in matrix(fleet, state).items()
            if status in NEEDS_A_COPY and where[0] != source]


# --- changing it ---------------------------------------------------------------------

@dataclass
class Notes:
    """What one run did and what stopped it, for the report."""
    copied: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    #: What the store on a machine said it did besides storing, by ``(machine, label)``.
    said: dict = field(default_factory=dict)
    blocked: dict = field(default_factory=dict)
    canaries: dict = field(default_factory=dict)
    moved: bool = False


def _canary_verdict(done: subprocess.CompletedProcess) -> str:
    """``PASS`` only when the canary both says so and exits 0. Anything else
    comes back as words that can never be mistaken for a pass."""
    answer = _last_json(done.stdout)
    verdict = answer.get("verdict") if isinstance(answer, dict) else None
    if verdict == "PASS" and done.returncode == 0:
        return "PASS"
    return f"{verdict or 'no verdict'}, exit {done.returncode}"


def canary(machine: Machine, notes: Notes, start=_start) -> str:
    """Run the token canary on a machine, once per run. Returns ``PASS``, or
    what it said instead, or why it could not be run."""
    if machine.name not in notes.canaries:
        try:
            done = start(machine.argv(machine.run_canary()), "", CANARY_SECONDS)
            notes.canaries[machine.name] = _canary_verdict(done)
        except (OSError, subprocess.SubprocessError) as exc:
            notes.canaries[machine.name] = f"could not be run ({type(exc).__name__})"
    return notes.canaries[machine.name]


def _named(files: list[str], most: int = 3) -> str:
    """A few file names, and how many more there are."""
    rest = len(files) - most
    return ", ".join(files[:most]) + (f" and {rest} more" if rest > 0 else "")


def survey_blocker(survey: dict, allow_strays: bool) -> str | None:
    """Why no token may be copied onto a machine, as far as its survey alone
    says. Costs nothing to ask, so a plan can already say it."""
    missing = [name for name in ("grip-relay-bill", "grip-token-canary") if not survey["tools"].get(name)]
    if missing:
        return f"GRIP there has no {' or '.join(missing)}: bring its tree level with main first"
    if survey["strays"] and not allow_strays:
        return (f"token-shaped text is lying in {_named(sorted(survey['strays']))} there, which the "
                "fence does not cover: see them with 'grip-claude-fleet strays' and remove them, "
                "or pass --allow-strays")
    return None


def gate(machine: Machine, survey: dict, allow_strays: bool, notes: Notes, start=_start) -> str | None:
    """Why no token may be copied onto this machine now, or None when one may.
    Runs the canary there, unless the survey already says no."""
    why = survey_blocker(survey, allow_strays)
    if why:
        return why
    verdict = canary(machine, notes, start)
    return None if verdict == "PASS" else f"the token canary did not pass there ({verdict})"


def copy(machine: Machine, label: str, start=_start) -> tuple[str | None, list[str]]:
    """Copy the source's token for ``label`` onto ``machine`` through the
    store's own command there. Returns why it failed (None when it did not),
    and what the store there said it did besides: kept the token the label
    had as a way back, moved the relay to the new one."""
    try:
        token = claude_as.read_label(label)
        done = start(machine.argv(machine.store(label)), token + "\n", COPY_SECONDS)
    except claude_as.Refused as why:
        return str(why), []
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the copy could not be made ({type(exc).__name__})", []
    if done.returncode == 0:
        return None, claude_token_store.besides(done.stdout)
    said = claude_token_store.scrubbed(done.stderr or done.stdout, 200)
    return f"the store there refused it: {said}", []


def improve(fleet: Fleet, state: dict, notes: Notes, *, apply: bool, allow_strays: bool,
            start=_start) -> dict:
    """One round: check the gates of every machine that needs a token, then
    copy where they are open. Returns the fleet's state afterwards, surveyed
    again if anything was tried.

    Without ``apply`` it changes nothing and runs no canary. It still notes
    what the surveys alone say would stop a copy, so a plan is honest about
    which copies cannot happen yet."""
    needed = copies_needed(fleet, state)
    machines = {machine.name: machine for machine in fleet.machines}
    for name in dict.fromkeys(name for name, _ in needed):
        survey = state[name]["data"]
        why = (gate(machines[name], survey, allow_strays, notes, start) if apply
               else survey_blocker(survey, allow_strays))
        if why:
            notes.blocked[name] = why
    open_cells = [where for where in needed if apply and where[0] not in notes.blocked]
    for name, label in open_cells:
        failure, notes.said[name, label] = copy(machines[name], label, start)
        (notes.failed if failure else notes.copied).append(
            (name, label, failure) if failure else (name, label))
    return survey_all(fleet, start) if open_cells else state


def converge(fleet: Fleet, *, apply: bool, allow_strays: bool = False, start=_start,
             max_depth: int = InnerspeakerGo.DEFAULT_MAX_DEPTH) -> tuple[dict, Notes, str]:
    """Run the loop. Returns the final state, what happened, and why it
    stopped: ``criterion`` (every cell verified), ``waiting`` (a round changed
    nothing: what is left needs the operator) or ``depth-budget``."""
    notes = Notes()

    def step(state: dict, depth: int) -> ImproveResult:
        after = improve(fleet, state, notes, apply=apply, allow_strays=allow_strays, start=start)
        notes.moved = fingerprint(after) != fingerprint(state)
        return ImproveResult(state=after, metrics={"verified": verified_share(matrix(fleet, after))},
                             depth=depth, fingerprint=fingerprint(after))

    with tempfile.TemporaryDirectory(prefix="grip-fleet-run-") as run_dir:
        kernel = InnerspeakerGo(converge=step, threshold=THRESHOLD, max_depth=max_depth,
                                fixed_points_path=Path(run_dir) / "fixed-points.jsonl",
                                telemetry_path=Path(run_dir) / "steps.jsonl")
        result = kernel(survey_all(fleet, start))
    done = result.passes(THRESHOLD) if result.metrics else False
    return result.state, notes, "criterion" if done else ("depth-budget" if notes.moved else "waiting")


# --- saying it ------------------------------------------------------------------------

def _unanswered(fleet: Fleet, state: dict) -> list[str]:
    answers = ((machine.name, state.get(machine.name) or {}) for machine in fleet.machines)
    return [f"{name}: {answer.get('why', 'no answer')}" for name, answer in answers
            if not answer.get("ok")]


def waiting_on(fleet: Fleet, state: dict, notes: Notes) -> list[str]:
    """What is left, as lines an operator can act on."""
    cells, source = matrix(fleet, state), fleet.source.name
    lines = [f"{name}: {why}" for name, why in sorted(notes.blocked.items())]
    lines += [f"{name}: the copy of {label!r} failed: {why}" for name, label, why in notes.failed]
    lines += _unanswered(fleet, state)
    lines += [f"{source}: the token file for {label!r} here is not private; store it again "
              "with grip-relay-bill" for label in fleet.labels if cells[source, label] == "unsafe"]
    lines += [f"{label}: no token is stored under this label on {source}; mint it with: "
              f"grip-account mint <slot> --as {label}"
              for label in fleet.labels if cells[source, label] == "no-source"]
    return lines + relay_waiting(fleet, state) + keychain_waiting(fleet, state)


def relay_bills_on(fleet: Fleet, state: dict, name: str) -> tuple[str | None, list[str]]:
    """The digest of the token the relay on one machine bills on, and the
    labels that token is stored under there, the fleet's own first. No digest
    means the machine has no relay token, or did not answer."""
    answer = state.get(name) or {}
    mark = answer["data"]["store"]["active"]["digest"] if answer.get("ok") else None
    labels = [label for label, entry in _stored(state, name).items() if mark and entry["digest"] == mark]
    return mark, sorted(labels, key=lambda label: label not in fleet.labels)


def _relay_words(fleet: Fleet, state: dict, name: str) -> list[str]:
    """For a machine's row: the label its relay bills on, when it has a relay token."""
    mark, labels = relay_bills_on(fleet, state, name)
    if not mark:
        return []
    return [f"relay: {labels[0]}" if labels else "relay: a token stored under no label"]


def _relay_waiting(fleet: Fleet, state: dict, name: str) -> list[str]:
    """The line for one relay that bills on anything but the token one of the
    fleet's labels holds on that machine. A label that is about to be replaced
    is not such a case: the copy puts it right and the relay follows it."""
    mark, labels = relay_bills_on(fleet, state, name)
    if not mark or set(labels) & set(fleet.labels):
        return []
    what = f"the token stored there as {labels[0]!r}" if labels else "a token stored under no label there"
    return [f"{name}: the relay there bills on {what}; it stops when that token is removed on the "
            "vendor's side. Point it at a stored one, on that machine: grip-relay-bill LABEL"]


def relay_waiting(fleet: Fleet, state: dict) -> list[str]:
    """Lines for the grid: each relay that would stop when an old token is
    removed on the vendor's side, while there is still time to move it."""
    return list(itertools.chain.from_iterable(
        _relay_waiting(fleet, state, machine.name) for machine in fleet.machines))


def _row(fleet: Fleet, machine: Machine, state: dict, cells: dict, width: int) -> str:
    data = (state.get(machine.name) or {}).get("data") or {}
    strays = data.get("strays") or {}
    tokens = len(set(itertools.chain.from_iterable(strays.values())))
    others = sorted(set(_stored(state, machine.name)) - set(fleet.labels))
    extra = [f"claude {data['claude']['version']}"] if data else []
    extra += ["source"] if machine.ssh is None else []
    extra += _relay_words(fleet, state, machine.name)
    extra += [f"also holds: {', '.join(others)}"] if others else []
    extra += [f"{tokens} stray token(s) in {len(strays)} shell file(s)"] if strays else []
    statuses = "".join(cells[machine.name, label].ljust(14) for label in fleet.labels)
    return machine.name.ljust(width) + statuses + "  ".join(extra)


def _copied_rows(notes: Notes) -> list[str]:
    """One line per copy, and under it what the store there said it did besides."""
    rows = ([f"copied {label!r} to {name}",
             *(f"  {name}: {line}" for line in notes.said.get((name, label), []))]
            for name, label in notes.copied)
    return list(itertools.chain.from_iterable(rows))


def render(fleet: Fleet, state: dict, notes: Notes | None = None) -> str:
    """The grid and what is waiting, as text. Digests only, never a token."""
    notes = notes or Notes()
    cells = matrix(fleet, state)
    width = max(len(machine.name) for machine in fleet.machines) + 2
    rows = ["".ljust(width) + "".join(label.ljust(14) for label in fleet.labels)]
    rows += [_row(fleet, machine, state, cells, width) for machine in fleet.machines]
    rows += _copied_rows(notes)
    waiting = waiting_on(fleet, state, notes)
    rows += ["", "Waiting on the operator:", *(f"  - {line}" for line in waiting)] if waiting else []
    rows.append(f"\n{list(cells.values()).count('verified')} of {len(cells)} cells verified.")
    return "\n".join(rows)


# --- one live call per cell ------------------------------------------------------------

def identify_all(fleet: Fleet, state: dict, labels: list[str], start=_start) -> dict:
    """One tiny live call for every machine and label that holds a token, made
    on that machine.

    ``labels`` says which, and may name any stored label, the fleet's own or
    not: an older token kept as a way back is checked this way, and so is one
    that was removed on the vendor's side and should now be refused. Empty
    means the fleet's own labels. A label named twice gets one call."""
    wanted = tuple(dict.fromkeys(labels)) or fleet.labels
    held = [(machine, label) for machine, label in itertools.product(fleet.machines, wanted)
            if label in _stored(state, machine.name)]
    return {(machine.name, label): ask(machine, "identify", label, start=start)
            for machine, label in held}


def works(answer: dict) -> bool:
    """True when the service accepted the token and said whose usage it is."""
    data = answer.get("data") or {}
    return bool(answer.get("ok") and not data.get("refused") and data.get("fingerprint"))


def _said(answer: dict) -> str:
    data = answer.get("data") or {}
    if not answer.get("ok"):
        return str(answer.get("why"))
    if data.get("refused"):
        return f"REFUSED by the service: {data.get('failure')}"
    seen = data.get("fingerprint")
    if not seen:
        return f"could not be checked: {data.get('failure') or 'no usage record came back'}"
    return (f"works; weekly window resets at {seen['weekly_reset']}, "
            f"extra usage off: {seen['extra_usage_off']}")


def render_identity(answers: dict) -> str:
    """One line per cell: does the token work from there, and on which usage
    windows. Two labels that report the same windows are the same account."""
    lines = [f"{name:<14}{label:<12}{_said(answer)}" for (name, label), answer in answers.items()]
    return "\n".join(lines or ["No machine holds a token under those labels."])


# --- tokens lying in shell files ----------------------------------------------------------

STRAYS_NEXT = ("Removing a line does not switch a token off. Switch it off where it was made "
               "(claude.ai, Settings > Claude Code, for a login token; the Console for an API "
               "key), run this again with --live to see that it is refused, then remove the "
               "lines with --remove --machine NAME.")
AFTER_REMOVAL = ("A shell that was open there during this still holds its own history in memory "
                 "and can write it back when it exits: close those windows.")


def strays_all(fleet: Fleet, live: bool = False, start=_start) -> dict:
    """What is lying in each machine's shell files, by machine name: every
    token-shaped string as a digest, with its files and line numbers. With
    ``live``, each is also asked of the service, from the machine it lies on."""
    timeout = LIVE_SECONDS if live else PROBE_SECONDS
    return {machine.name: ask(machine, "strays", live, start=start, timeout=timeout)
            for machine in fleet.machines}


def remove_strays_from(fleet: Fleet, names: list[str], start=_start) -> dict:
    """Delete the lines that carry a token from the shell files of the machines
    named, and of no other. Each machine's answer, by name."""
    machines = {machine.name: machine for machine in fleet.machines}
    return {name: ask(machines[name], "remove_strays", start=start) for name in names}


def stored_as(state: dict) -> dict:
    """The labels each stored token goes by anywhere in the fleet, by digest.
    A stray with one of these digests is a token that is still in use."""
    pairs = ((entry["digest"], label) for name in state
             for label, entry in _stored(state, name).items() if entry["digest"])
    found: dict = {}
    for mark, label in pairs:
        found.setdefault(mark, set()).add(label)
    return {mark: ", ".join(map(repr, sorted(labels))) for mark, labels in found.items()}


def _verdict(live: dict | None) -> list[str]:
    """What the service said about one stray, as words for its row."""
    if live is None:
        return []
    if live["works"] is None:
        return [f"could not be tested: {live['why']}"]
    if not live["works"]:
        return ["refused by the service"]
    windows = live["windows"]
    detail = f"weekly window resets at {windows['weekly_reset']}" if windows else live["why"]
    return ["STILL WORKS" + (f" ({detail})" if detail else "")]


def _lines_word(numbers: list[int]) -> str:
    return f"line{'s' if len(numbers) > 1 else ''} {', '.join(map(str, numbers))}"


def _in_use(mark: str, known: dict) -> list[str]:
    """Words for a token that is also stored under a label: it is in use."""
    return [f"the token stored as {known[mark]}"] if mark in known else []


def _stray_rows(name: str, answer: dict, known: dict) -> list[str]:
    if not answer.get("ok"):
        return [f"{name}  {answer.get('why', 'no answer')}"]
    places = [(mark, entry, file, numbers) for mark, entry in answer["data"].items()
              for file, numbers in entry["where"].items()]
    rows = ["  ".join([name, file, _lines_word(numbers), entry["kind"], mark, *_verdict(entry["live"]),
                       *_in_use(mark, known)])
            for mark, entry, file, numbers in places]
    return rows or [f"{name}  nothing lying in its shell files"]


def render_strays(answers: dict, known: dict | None = None) -> str:
    """One line per token and file: where it lies, what kind it is, its digest
    and, after a live check, whether the service still accepts it. ``known``
    maps a digest to the labels it is stored under, so a stray that is still in
    use says so. Never the token itself."""
    rows = [_stray_rows(name, answer, known or {}) for name, answer in answers.items()]
    return "\n".join(itertools.chain.from_iterable(rows))


def any_strays(answers: dict) -> bool:
    """True unless every machine answered and none has anything lying about."""
    return not all(answer.get("ok") and not answer["data"] for answer in answers.values())


def _removed_row(name: str, answer: dict) -> str:
    if not answer.get("ok"):
        return f"{name}: {answer.get('why', 'no answer')}"
    data = answer["data"]
    done = [f"{count} line(s) from {file}" for file, count in data["removed"].items()]
    left = [f"{file} was left alone ({why})" for file, why in data["failed"].items()]
    return f"{name}: " + "; ".join(["removed " + ", ".join(done) if done else "nothing to remove", *left])


def cleaned(answer: dict) -> bool:
    """True when a machine answered and no file of its had to be left alone."""
    return bool(answer.get("ok") and not answer["data"]["failed"])


# --- tokens lying in what a harness keeps -------------------------------------------------

RECORDS_NEXT = ("A token in a session record cannot be cut out of it. Switch it off where it was "
                "made, and every copy stops working. Then run this again with --deep --live: "
                "each row should say it is refused.")


def records_all(fleet: Fleet, live: bool = False, start=_start) -> dict:
    """What lies in each machine's harness records, by machine name: every
    credible token as a digest, with the files that hold it, and what was
    looked in. A machine's own extra places are sent to that machine only.
    With ``live``, each token is asked of the service from where it lies."""
    return {machine.name: ask(machine, "records", live, list(machine.records), start=start,
                              timeout=LIVE_SECONDS)
            for machine in fleet.machines}


def _files_word(entry: dict) -> str:
    others = len(entry["files"]) - 1 + entry["more"]
    return entry["files"][0] + (f" and {others} more file(s)" if others else "")


def _scope(name: str, data: dict) -> str:
    """What was looked in on one machine, so that finding nothing has a scope."""
    seen = data["scanned"]
    if not seen["roots"]:
        return f"{name}  nothing to look in: none of the places a harness keeps records exists there"
    left = (f"; {data['left_out']} other string(s) of the shape are too short or plainly made up"
            if data["left_out"] else "")
    return (f"{name}  looked in {seen['files']:,} file(s) ({seen['bytes'] / 1e9:.1f} GB) under "
            f"{', '.join(seen['roots'])}{left}")


def _record_rows(name: str, answer: dict, known: dict) -> list[str]:
    if not answer.get("ok"):
        return [f"{name}  {answer.get('why', 'no answer')}"]
    data = answer["data"]
    rows = ["  ".join([name, _files_word(entry), entry["kind"], mark, *_verdict(entry["live"]),
                       *_in_use(mark, known)])
            for mark, entry in data["found"].items()]
    return [*rows, _scope(name, data)]


def render_records(answers: dict, known: dict | None = None) -> str:
    """One line per token found in a machine's harness records: the first file
    that holds it and how many more, its kind, its digest and, after a live
    check, whether the service still accepts it. Then one line per machine
    saying what was looked in. Never the token itself."""
    rows = [_record_rows(name, answer, known or {}) for name, answer in answers.items()]
    return "\n".join(itertools.chain.from_iterable(rows))


def records_settled(answers: dict) -> bool:
    """True when every machine answered and the service refuses every token
    found in its records. A copy in a record cannot be removed, so only a
    refusal settles it; one that was not asked about is not settled."""
    found = itertools.chain.from_iterable(
        ((answer.get("data") or {}).get("found") or {}).values() for answer in answers.values())
    return (all(answer.get("ok") for answer in answers.values())
            and all((entry["live"] or {}).get("works") is False for entry in found))


# --- the keychain items that hold a token --------------------------------------------------

def _keyring(state: dict, name: str) -> dict:
    """What one machine's survey says its keychain items hold. Empty for a
    machine that did not answer or has no keychain."""
    answer = state.get(name) or {}
    return ((answer.get("data") or {}).get("keychain") or {}) if answer.get("ok") else {}


def _own(fleet: Fleet, state: dict) -> set[str]:
    """The digests of the tokens the source stores under the fleet's labels:
    the ones a machine should be billing on."""
    stored = _stored(state, fleet.source.name)
    return {stored[label]["digest"] for label in fleet.labels if label in stored} - {None}


def _what_it_holds(mark: str, own: set[str], known: dict) -> str:
    """Words for the token a keychain item holds, by where else it is stored."""
    if mark in own:
        return f"the token stored as {known[mark]}"
    if mark in known:
        return f"the token stored as {known[mark]}, which is not one every machine should hold"
    return "a token that is stored under no label"


def _item_in_step(entry: dict, own: set[str]) -> bool:
    """True for an item that is not there, or that holds one of the fleet's
    own tokens. One that could not be read is not known to be right."""
    return entry["digest"] in own if entry["present"] else not entry["problem"]


def keychain_in_step(fleet: Fleet, state: dict, names: list[str] | None = None) -> bool:
    """True when every keychain item on the machines named (all of them, when
    none is named) is in step with the token store."""
    own = _own(fleet, state)
    chosen = [machine.name for machine in fleet.machines if not names or machine.name in names]
    entries = itertools.chain.from_iterable(_keyring(state,name).values() for name in chosen)
    return all(_item_in_step(entry, own) for entry in entries)


def _once_removed(item: str) -> str:
    """What becomes of one keychain item when the token it holds is removed on
    the vendor's side: who stops, or that nobody does."""
    readers = claude_keychain.READ_BY.get(item)
    if readers:
        return f"{readers} read it, and stop when that token is removed on the vendor's side"
    return ("nothing in GRIP reads it, so nothing stops when that token is removed on the vendor's "
            "side, and the item is left holding a dead token")


def keychain_waiting(fleet: Fleet, state: dict) -> list[str]:
    """Lines for the grid: each item that holds a token other than one of the
    fleet's own, while there is still time to put that right."""
    own, known = _own(fleet, state), stored_as(state)
    items = [(machine.name, item, entry) for machine in fleet.machines
             for item, entry in _keyring(state,machine.name).items()]
    return [f"{name}: keychain item {item} holds {_what_it_holds(entry['digest'], own, known)}; "
            f"{_once_removed(item)}. Put a stored one there: "
            f"grip-claude-fleet keychain --set {item}=LABEL"
            for name, item, entry in items if entry["digest"] and entry["digest"] not in own]


def keychain_lives_all(fleet: Fleet, state: dict, names: list[str] | None = None, start=_start) -> dict:
    """Ask the service about the token in each keychain item, on every machine
    that holds one. Each machine's answer, by name."""
    holding = [machine for machine in fleet.machines
               if (not names or machine.name in names)
               and any(entry["digest"] for entry in _keyring(state,machine.name).values())]
    return {machine.name: ask(machine, "keychain_lives", start=start, timeout=LIVE_SECONDS)
            for machine in holding}


def _live_words(answer: dict | None, item: str) -> list[str]:
    """What a live check said about one item, for its row. Nothing when no
    live check was asked of that machine; a machine that was asked and did not
    answer is said, so its row cannot pass for one that was tested."""
    if answer is None:
        return []
    if not answer.get("ok"):
        return [f"not tested: {answer.get('why', 'no answer')}"]
    return _verdict(answer["data"].get(item))


def _keychain_row(name: str, item: str, entry: dict, said: list[str]) -> list[str]:
    if not entry["present"] and not entry["problem"]:
        return []
    if not entry["digest"]:
        return [f"{name}  {item}  {entry['problem']}"]
    return ["  ".join([name, item, entry["digest"], *said])]


def _one_problem(held: dict) -> str | None:
    """The one reason nothing in a machine's keychain could be read, when
    every item gives the same one (a locked keychain does)."""
    problems = {entry["problem"] for entry in held.values()}
    whole = len(problems) == 1 and not any(entry["present"] for entry in held.values())
    return next(iter(problems)) if whole else None


def _keychain_rows(name: str, state: dict, own: set[str], known: dict, lives: dict) -> list[str]:
    answer = state.get(name) or {}
    if not answer.get("ok"):
        return [f"{name}  {answer.get('why', 'no answer')}"]
    held = _keyring(state, name)
    if not held:
        return [f"{name}  no keychain"]
    if _one_problem(held):
        return [f"{name}  {_one_problem(held)}"]
    rows = [_keychain_row(name, item, entry,
                          [_what_it_holds(entry["digest"], own, known), *_live_words(lives.get(name), item)])
            for item, entry in held.items()]
    return list(itertools.chain.from_iterable(rows)) or [f"{name}  no Claude token in its keychain"]


def render_keychain(fleet: Fleet, state: dict, lives: dict | None = None,
                    names: list[str] | None = None) -> str:
    """One line per keychain item that holds something: its digest, which
    stored label it is the token of, and after a live check whether the
    service still accepts it. Never the token itself."""
    own, known = _own(fleet, state), stored_as(state)
    chosen = [machine.name for machine in fleet.machines if not names or machine.name in names]
    rows = [_keychain_rows(name, state, own, known, lives or {}) for name in chosen]
    return "\n".join(itertools.chain.from_iterable(rows))


def keychain_works(lives: dict) -> bool:
    """True when every machine that was asked answered, and the service
    accepts the token in every item it was asked about."""
    verdicts = itertools.chain.from_iterable(
        ((answer.get("data") or {}).values()) for answer in lives.values())
    return all(answer.get("ok") for answer in lives.values()) and all(
        verdict["works"] is True for verdict in verdicts)


def set_keychain(fleet: Fleet, names: list[str], item: str, label: str, start=_start) -> dict:
    """Make one keychain item, on each machine named, hold the token that
    machine stores under ``label``. Each machine's answer, by name."""
    machines = {machine.name: machine for machine in fleet.machines}
    return {name: ask(machines[name], "keychain_put", item, label, start=start) for name in names}


# --- forgetting a kept copy ----------------------------------------------------------------
#
# Replacing a token keeps the old one under ``<label>-old``: it still works
# until it is removed on the vendor's side, and the kept copy is the only
# handle for showing that it has stopped. After that it is a dead token in a
# file on every machine, and this is its way out.

FORGOTTEN = "forgotten: the service refuses that token"
FORGET_NEXT = ("A copy whose token still works is kept. Remove that token on the vendor's side first "
               "(claude.ai, Settings > Claude Code, signed in as that account), then run this again.")


def forget(machine: Machine, label: str, start=_start) -> str | None:
    """Remove the token stored under ``label`` on one machine, through the
    store's own command there. Returns why it was not done, or None. The label
    becomes a word of a shell command, so anything that is not a label is
    refused here, whatever the caller checked."""
    problem = claude_token_store.label_problem(label)
    if problem:
        return f"{claude_token_store.scrub(repr(label))[0]} cannot be a label: {problem}"
    try:
        done = start(machine.argv(machine.forget(label)), "", COPY_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the store there could not be reached ({type(exc).__name__})"
    said = claude_token_store.scrubbed(done.stderr or done.stdout, 200)
    return None if done.returncode == 0 else f"the store there would not: {said}"


def why_kept(answer: dict) -> str | None:
    """Why a copy is not to be forgotten, from what its machine said when the
    service was asked about it. ``None`` only when the service refused the
    token: a call that could not be made is never a refusal."""
    if not answer.get("ok"):
        return str(answer.get("why", "no answer"))
    live = answer["data"]
    if live["works"] is False:
        return None
    if live["works"] is None:
        return f"could not be checked: {live['why']}"
    windows = live["windows"]
    return "the token still works" + (f" (weekly window resets at {windows['weekly_reset']})" if windows else "")


def _settle(machine: Machine, label: str, start) -> tuple[bool, str]:
    """Ask the service about the token one machine stores under ``label``,
    from that machine, and forget it there when the service refuses it."""
    asked = ask(machine, "stored_lives", label, start=start, timeout=LIVE_SECONDS)
    why = why_kept(asked) or forget(machine, label, start)
    return (False, f"kept: {why}") if why else (True, FORGOTTEN)


def _outcome(machine: Machine, state: dict, label: str, start) -> tuple[bool, str] | None:
    """What became of ``label`` on one machine. Nothing for a machine that
    does not store it. One that did not answer the survey is not known to be
    without it, so it counts as not forgotten."""
    answer = state.get(machine.name) or {}
    if not answer.get("ok"):
        return False, str(answer.get("why", "no answer"))
    return _settle(machine, label, start) if label in _stored(state, machine.name) else None


def forget_all(fleet: Fleet, state: dict, label: str, names: list[str], start=_start) -> dict:
    """On each machine named (all of them, when none is) that stores
    ``label``: one live call with that copy, and where the service refuses it,
    forget it there. Each machine's outcome, by name: whether it was
    forgotten, and the words for its row."""
    chosen = [machine for machine in fleet.machines if not names or machine.name in names]
    outcomes = ((machine.name, _outcome(machine, state, label, start)) for machine in chosen)
    return {name: outcome for name, outcome in outcomes if outcome}


def render_forgotten(label: str, answers: dict) -> str:
    """One line per machine that stores the label, or could not say."""
    if not answers:
        return f"no machine stores {label!r}"
    return "\n".join(f"{name}  {label}  {words}" for name, (_, words) in answers.items())


# --- the command line -------------------------------------------------------------------

def _check(fleet: Fleet, options, start) -> int:
    state = survey_all(fleet, start)
    cells = matrix(fleet, state)
    if options.json:
        print(json.dumps({"cells": {f"{name}/{label}": status for (name, label), status in cells.items()},
                          "machines": state}, sort_keys=True))
    else:
        print(render(fleet, state))
    return 0 if verified_share(cells) == 1.0 else 1


def _identify(fleet: Fleet, options, start) -> int:
    answers = identify_all(fleet, survey_all(fleet, start), options.labels, start)
    print(render_identity(answers))
    return 0 if answers and all(map(works, answers.values())) else 1


def _converge(fleet: Fleet, options, start) -> int:
    state, notes, reason = converge(fleet, apply=options.apply,
                                    allow_strays=options.allow_strays, start=start)
    print(render(fleet, state, notes))
    todo = [where for where in copies_needed(fleet, state) if where[0] not in notes.blocked]
    if not options.apply:
        print("Nothing was changed." + (
            " With --apply these would be copied, if the canary passes there: "
            + ", ".join(f"{label} to {name}" for name, label in todo) if todo else ""))
    print(f"Stopped: {REASONS[reason]}")
    return 0 if reason == "criterion" else 1


def _no_such_machine(fleet: Fleet, named: list[str]) -> str | None:
    """The words for the first machine named that is not in the list, or None."""
    names = [machine.name for machine in fleet.machines]
    unknown = [name for name in named if name not in names]
    return f"there is no machine called {unknown[0]!r}: the list has {', '.join(names)}" if unknown else None


def _strays_problem(fleet: Fleet, options) -> str | None:
    """Why the ``strays`` command cannot run as asked, or None."""
    unknown = _no_such_machine(fleet, options.machine)
    if unknown:
        return unknown
    if options.remove and options.deep:
        return ("a session record cannot be cleaned line by line: switch the token off where it "
                "was made instead. --remove without --deep cleans the shell files")
    if options.remove and not options.machine:
        return ("name the machine to clean with --machine NAME: lines are never removed from "
                "every machine at once")
    return None


def _remove(fleet: Fleet, options, start) -> int:
    answers = remove_strays_from(fleet, list(dict.fromkeys(options.machine)), start)
    print("\n".join(_removed_row(name, answer) for name, answer in answers.items()))
    print(AFTER_REMOVAL)
    return 0 if all(map(cleaned, answers.values())) else 1


def _strays(fleet: Fleet, options, start) -> int:
    problem = _strays_problem(fleet, options)
    if problem:
        print(f"[grip-claude-fleet] {problem}", file=sys.stderr)
        return 2
    if options.remove:
        return _remove(fleet, options, start)
    chosen = Fleet(fleet.labels, tuple(machine for machine in fleet.machines
                                       if not options.machine or machine.name in options.machine))
    known = stored_as(survey_all(fleet, start))
    answers = strays_all(chosen, options.live, start)
    print(render_strays(answers, known))
    deep = records_all(chosen, options.live, start) if options.deep else {}
    if options.deep:
        print(render_records(deep, known))
    hints = [(any_strays(answers), STRAYS_NEXT), (not records_settled(deep), RECORDS_NEXT)]
    print("".join(f"\n{text}\n" for due, text in hints if due), end="")
    return 1 if any(due for due, _ in hints) else 0


def _keychain_problem(fleet: Fleet, options) -> str | None:
    """Why the ``keychain`` command cannot run as asked, or None."""
    unknown = _no_such_machine(fleet, options.machine)
    if unknown:
        return unknown
    item, _, label = (options.set or "=").partition("=")
    if options.set is not None and not (item and label):
        return "--set takes ITEM=LABEL, such as grip-claude-oauth=laurie"
    return None


def _set_keychain(fleet: Fleet, options, start) -> int:
    item, _, label = options.set.partition("=")
    names = list(dict.fromkeys(options.machine)) or [fleet.source.name]
    answers = set_keychain(fleet, names, item, label, start)
    for name, answer in answers.items():
        print(f"{name}: {item} now holds the token stored as {label!r} ({answer['data']['digest']})"
              if answer.get("ok") else f"{name}: {answer.get('why', 'no answer')}")
    return 0 if all(answer.get("ok") for answer in answers.values()) else 1


def _keychain(fleet: Fleet, options, start) -> int:
    problem = _keychain_problem(fleet, options)
    if problem:
        print(f"[grip-claude-fleet] {problem}", file=sys.stderr)
        return 2
    if options.set is not None:
        return _set_keychain(fleet, options, start)
    state = survey_all(fleet, start)
    lives = keychain_lives_all(fleet, state, options.machine, start) if options.live else {}
    print(render_keychain(fleet, state, lives, options.machine))
    fine = keychain_in_step(fleet, state, options.machine) and keychain_works(lives)
    return 0 if fine else 1


def _forget_problem(fleet: Fleet, options) -> str | None:
    """Why the ``forget`` command cannot run as asked, or None."""
    problem = claude_token_store.label_problem(options.label)
    if problem:
        return f"{claude_token_store.scrub(repr(options.label))[0]} cannot be a label: {problem}"
    if options.label in fleet.labels:
        return (f"{options.label!r} is one of the fleet's own labels: a token under it is replaced by "
                "minting a new one, never forgotten")
    return _no_such_machine(fleet, options.machine)


def _forget(fleet: Fleet, options, start) -> int:
    problem = _forget_problem(fleet, options)
    if problem:
        print(f"[grip-claude-fleet] {problem}", file=sys.stderr)
        return 2
    answers = forget_all(fleet, survey_all(fleet, start), options.label, options.machine, start)
    print(render_forgotten(options.label, answers))
    kept = [words for done, words in answers.values() if not done]
    if any("still works" in words for words in kept):
        print(f"\n{FORGET_NEXT}")
    return 1 if kept else 0


REASONS = {
    "criterion": "every machine holds the right token for every label.",
    "waiting": "a round changed nothing, so what is left needs the operator.",
    "depth-budget": "the round limit was reached while things were still changing.",
}
COMMANDS = {"check": _check, "identify": _identify, "converge": _converge, "strays": _strays,
            "keychain": _keychain, "forget": _forget}


def _add_surveys(commands) -> None:
    """``check``, ``identify`` and ``converge``: the grid and what changes it."""
    check = commands.add_parser("check", help="survey every machine; changes nothing, calls nobody")
    check.add_argument("--json", action="store_true", help="print the survey as one JSON object")
    identify = commands.add_parser("identify", help="one tiny live call per machine and label")
    identify.add_argument("labels", nargs="*",
                          help="these stored labels, the fleet's own or not, such as an older "
                               "token kept as teammate-old (default: the fleet's labels)")
    change = commands.add_parser("converge", help="plan what would make every cell verified; "
                                                  "with --apply, do it")
    change.add_argument("--apply", action="store_true",
                        help="copy tokens where the gates are open (default: plan only)")
    change.add_argument("--allow-strays", action="store_true",
                        help="copy even to a machine with token-shaped text in its shell files "
                             "(the canary gate stays)")


def _add_strays(commands) -> None:
    strays = commands.add_parser("strays", help="tokens lying in shell start-up and history files: "
                                                "list them, test them, or remove their lines")
    strays.add_argument("--machine", action="append", default=[], metavar="NAME",
                        help="only this machine (may be given more than once)")
    strays.add_argument("--deep", action="store_true",
                        help="also look in what each harness keeps: prompt history, session "
                             "records, shell snapshots, its database. Slower. Nothing there can "
                             "be removed, only switched off")
    how = strays.add_mutually_exclusive_group()
    how.add_argument("--live", action="store_true",
                     help="ask the service whether each one still works, from the machine it "
                          "lies on (one tiny call per token)")
    how.add_argument("--remove", action="store_true",
                     help="delete the lines that carry a token, on the machines named with "
                          "--machine. Nothing is kept of them")


def _add_keychain(commands) -> None:
    keychain = commands.add_parser("keychain", help="the keychain items that hold a Claude token for "
                                                    "HAL and the harness launchers: what each holds, "
                                                    "and putting a stored token there")
    keychain.add_argument("--machine", action="append", default=[], metavar="NAME",
                          help="only this machine (default for --set: the one this runs on)")
    what = keychain.add_mutually_exclusive_group()
    what.add_argument("--live", action="store_true",
                      help="ask the service whether the token in each item still works")
    what.add_argument("--set", metavar="ITEM=LABEL",
                      help="make ITEM hold the token stored under LABEL on that machine. The token "
                           "is never shown and never an argument")


def _add_forget(commands) -> None:
    forget = commands.add_parser("forget", help="remove a kept copy, such as laurie-old, from every "
                                                "machine where the service refuses its token")
    forget.add_argument("label", help="the stored label to forget; never one of the fleet's own")
    forget.add_argument("--machine", action="append", default=[], metavar="NAME",
                        help="only this machine (may be given more than once)")


def _parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="grip-claude-fleet",
        description="Which machine holds which Claude token, and making every machine hold "
                    "the right ones. The machine list is read from "
                    "${XDG_CONFIG_HOME:-~/.config}/grip/claude-fleet.json.")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_surveys(commands)
    _add_strays(commands)
    _add_keychain(commands)
    _add_forget(commands)
    return parser


def main(argv: list[str] | None = None, env=None, start=_start) -> int:
    """The command. Exit code 0 when everything is as it should be, 1 when it
    is not, 2 when the machine list is missing or wrong, or when a token was
    given as an argument."""
    given = sys.argv[1:] if argv is None else argv
    if claude_token_store.token_among(given):
        print(f"[grip-claude-fleet] {claude_token_store.TOKEN_AS_ARGUMENT}", file=sys.stderr)
        return 2
    options = _parser().parse_args(given)
    try:
        fleet = load(config_path(env))
    except ConfigError as why:
        print(f"[grip-claude-fleet] {why}", file=sys.stderr)
        return 2
    return COMMANDS[options.command](fleet, options, start)
