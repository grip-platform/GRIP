"""lib/tick_experiment.py: the tick's third granted act, an approved experiment for an overdue hypothesis.

The hypothesis engine (``lib/hypothesis_engine.py``) holds predictions with deadlines, and the always-on tick
reports a pending one past its deadline (``grip_tick.hypothesis_findings``). This act runs, once, a measurement
the operator approved in advance for such a hypothesis, and puts what it printed beside the hypothesis for the
operator's verdict. It never settles a verdict: it never writes a hypothesis or its status, and the engine row it
adds (``hypothesis_engine.record_experiment``) says only that a run happened and what it printed.

What the operator approves is a manifest, ``state/grip-tick/experiments/<id>.json``: the hypothesis id, the exact
argv, a timeout of at most MAX_TIMEOUT_S seconds, the SHA-256 of every input the approval covers, and the files
declared live. ``grip-tick-experiment draft`` writes one from the files as they stand: every file the argv names is
pinned, a script handed to an interpreter included, unless it is declared ``--live`` (data such as a log, read as it
is when the run happens), and the approval line shows which files are live. The executable is always pinned and never
live: a bare name such as ``rg`` or ``python3`` is looked up on SAFE_PATH, the PATH the run has, and the manifest
names the file found, so an update that replaces it authorises nothing (Codex on GRIP#8856).
``grip-tick-grant issue experiment --hypothesis <id>`` checks those hashes against the files, prints the argv,
and signs a grant whose ``experiments`` limit binds each id to its manifest's SHA-256. A manifest edited after
the approval, or an input whose bytes changed, authorises nothing: the tick reports the drift and runs nothing
until the operator approves again.

The gate, every clause of which must hold: a verified ``experiment`` grant naming the ``hyp:overdue`` trigger
and the hypothesis id; the hypothesis overdue on this tick; an id the hypothesis store carries in one record only
(``grip_tick`` checks it when it chooses the run and again just before the spawn); the manifest's SHA-256 equal to
the grant's and every input unchanged; ``sandbox-exec`` on the machine; no run reserved for this hypothesis under
this manifest and this grant (``run_key``). The inputs are checked once more just before the spawn. The run: the
argv under ``sandbox-exec`` with the network denied, Apple Events denied and every file write denied except inside
the run folder's ``out/``, with the GRIP tree as its working folder, in an environment holding only PATH, HOME,
the locale, TMPDIR (inside ``out/``), CLAUDE_DIR and PYTHONDONTWRITEBYTECODE, in its own process group, which is
killed whole when its leader ends or its timeout passes, so no background child outlives it. No file it writes
may grow past MAX_OUTPUT_BYTES, and ``out/`` is measured at every poll while the run lasts: past MAX_RUN_BYTES,
every file together, or MAX_RUN_ENTRIES files and folders, the group is killed and the result says so
(``over_quota``). The tick reads both streams back through its own descriptors, writes its own copies, the
manifest and the signed grant into the run folder above ``out/``, which the command cannot write, so it cannot
plant a link the tick would follow, and then removes ``out/``. Every file the tick writes goes through a temporary
file and an atomic rename. Before any seal, ``result.json`` is written marked ``reserved``: from then on the run
counts as done under its approval, whatever happens to its seal (Codex on GRIP#8856). The result is then sealed
on the tick's act ledgers (``lib/tick_seal.py``: a ``tick-act-experiment`` leaf and a paired memory node),
``result.json`` is rewritten marked ``sealed``, and the engine row follows. A run that fails before its
reservation, or whose seal fails cleanly (``SealFailed``: the ledger holds no act), is removed and runs again on a
later tick, which is harmless because a run can change nothing outside its folder. A run whose leaf is on the
ledger without its node or a roll-back leaf (``SealUncertain``) is kept marked ``uncertain``. A run stopped
between its reservation and its record for any other reason, a kill or a full disk, stays ``reserved``: it never
runs again, gets no engine row, and is reported once (``unsettled``), because whether its seal reached the ledger
is not known. A run whose engine row could not be written says so in ``result.json``, and a later tick writes the
row; the engine skips a run it already holds, so a retry never adds a second row.

Stated honestly: the sandbox confines the run's network and its writes, not its reads, and not its calls to
system services other than Apple Events, and a descendant that leaves the run's process group with setsid is not
killed. The write cap is a measurement every POLL_S, so a fast writer can pass it by what it writes between two
measurements before its group is killed. The approval covers an exact command over exact script bytes; a module
the script imports and the manifest does not list is not covered, which is why ``draft`` takes further
``--input`` paths, and an executable that is itself a launcher, such as ``/usr/bin/python3``, which starts the
Command Line Tools' Python, is pinned as the launcher, not as what it starts. A file declared
live, a log for example, is read as it is, because measuring live data is the point of the run. The reversal is to
remove the run folder (``grip-tick-experiment remove <name>``): the run could write nowhere else, and the
hypothesis then becomes eligible for one more run under the same grant.

Falsifiable prediction (GPT-6's, ``docs/measurements/2026-10-28-tick-gpt6-review.md``): over fourteen days of
grants, no granted run needs an input its manifest did not state; one such run refutes the manifest design.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

_LIB = Path(__file__).resolve().parent
for _p in (_LIB, _LIB.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from claude_dir import get_claude_dir  # noqa: E402
from jcs import dumps as canonical_dumps  # noqa: E402
import tick_seal  # noqa: E402

SealFailed = tick_seal.SealFailed

VERSION = 1
TRIGGER = "hyp:overdue"  # the trigger an experiment grant must name: the tick's own overdue-hypothesis finding
MAX_TIMEOUT_S = 8  # one run fits inside the tick's act budget (grip_tick.ACT_BUDGET_SHARE of grip_tick.BUDGET_S)
KILL_WAIT_S = 2
POLL_S = 0.02  # how often the end of a run's leader is looked for
OUT = "out"  # the one folder inside a run that the sandboxed command may write; the tick's own files sit above it
MAX_OUTPUT_BYTES = 1 << 20  # the largest file a run may write, its two streams included (RLIMIT_FSIZE)
MAX_RUN_BYTES = 8 * MAX_OUTPUT_BYTES  # the most a run may write into out/, every file together (measured, not a limit)
MAX_RUN_ENTRIES = 1024  # the most files and folders a run may make in out/
INCOMPLETE_S = 600  # a run folder with no result.json after this long is the tick's own debris
SETTLED = ("sealed", "uncertain")  # a run's record once its seal has answered; "reserved" before that
FORCE_PASSES = 4  # unlock-and-remove passes before a folder the command locked counts as undeletable
SANDBOX = Path("/usr/bin/sandbox-exec")
SAFE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
HID_RE = re.compile(r"H\d{3,6}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
MANIFEST_KEYS = frozenset({"v", "hypothesis", "argv", "timeout_s", "inputs", "live"})
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
Runner = Callable[[List[str], Path, Path, int], dict]
Record = Callable[[str, dict], bool]


class ManifestError(ValueError):
    """A manifest that cannot be approved or run; the message is for the operator."""


class SandboxMissing(RuntimeError):
    """sandbox-exec is not on this machine, so no experiment runs here."""


def experiments_dir() -> Path:
    return get_claude_dir() / "state" / "grip-tick" / "experiments"


def manifest_path(folder: Path, hid: str) -> Path:
    return folder / f"{hid}.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_sha256(manifest: dict) -> str:
    """The SHA-256 of the manifest's canonical JSON (RFC 8785): the value an experiment grant binds."""
    return hashlib.sha256(canonical_dumps(manifest)).hexdigest()


# ----------------------------------------------------------------------------- manifests

def _check_argv(argv: object) -> None:
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ManifestError("argv must be a non-empty list of non-empty strings")


def _check_inputs(inputs: object) -> None:
    pairs = inputs.items() if isinstance(inputs, dict) else [(None, None)]
    if not all(isinstance(k, str) and k and isinstance(v, str) and SHA_RE.fullmatch(v) for k, v in pairs):
        raise ManifestError("inputs must map each path to the 64-hex SHA-256 of its bytes")


def _check_live(live: object, argv: List[str]) -> None:
    """``live`` names the argv elements the operator declared as data read live, each one of the argv's own."""
    if not isinstance(live, list) or not all(isinstance(p, str) and p in argv for p in live):
        raise ManifestError("live must list argv elements, each declared with --live when the manifest is drafted")


def _check_executable(manifest: dict) -> None:
    """The executable is named by its path, pinned and never live, so the bytes that start are the bytes approved: a
    bare name would be looked up on PATH when the run starts, past every hash (Codex on GRIP#8856)."""
    exe = manifest["argv"][0]
    if "/" not in exe or exe not in manifest["inputs"] or exe in manifest["live"]:
        raise ManifestError("argv[0] must name the executable by its path, pinned in inputs and never live; draft "
                            "resolves a bare name such as rg on the run's PATH")


def validate(manifest: object) -> dict:
    """The manifest when its shape is right; ManifestError naming the first thing that is wrong."""
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS or manifest.get("v") != VERSION:
        raise ManifestError(f"a manifest has exactly the keys {sorted(MANIFEST_KEYS)}, with v={VERSION}")
    if not isinstance(manifest["hypothesis"], str) or not HID_RE.fullmatch(manifest["hypothesis"]):
        raise ManifestError("hypothesis must be an engine id such as H857")
    _check_argv(manifest["argv"])
    timeout = manifest["timeout_s"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT_S:
        raise ManifestError(f"timeout_s must be a whole number of seconds from 1 to {MAX_TIMEOUT_S}")
    _check_inputs(manifest["inputs"])
    _check_live(manifest["live"], manifest["argv"])
    _check_executable(manifest)
    return manifest


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"the manifest at {path} is missing or unreadable ({type(exc).__name__})") from exc
    return validate(data)


def resolve(entry: str, root: Path) -> Path:
    """A path in a manifest: absolute as written, otherwise under the GRIP tree."""
    path = Path(entry)
    return path if path.is_absolute() else root / path


def input_drift(manifest: dict, root: Path) -> List[str]:
    """Each input whose bytes no longer match the approval, with why; empty when every one matches."""
    out = []
    for entry, want in sorted(manifest["inputs"].items()):
        try:
            have = file_sha256(resolve(entry, root))
        except OSError:
            out.append(f"{entry} is missing or unreadable")
            continue
        if have != want:
            out.append(f"{entry} changed since the approval")
    return out


def _pin_executable(argv: List[str], live: Iterable[str]) -> List[str]:
    """argv with its executable named by a path. A bare name is looked up on SAFE_PATH, the PATH the run has, which
    finds the file sandbox-exec would start; ManifestError when it is not there, or when it is declared live."""
    if argv and argv[0] in live:
        raise ManifestError(f"{argv[0]} is the executable the run starts, so it is always pinned and never --live")
    if not argv or "/" in argv[0]:
        return list(argv)
    found = shutil.which(argv[0], path=SAFE_PATH)
    if not found:
        raise ManifestError(f"{argv[0]} is not on the run's PATH ({SAFE_PATH})")
    return [found, *argv[1:]]


def draft(hid: str, argv: List[str], timeout_s: int, extra: List[str], root: Path, live: List[str] = ()) -> dict:
    """A manifest for argv. Every argv element that names a file (under the GRIP tree, or an absolute path) is an
    input, hashed as it stands now, unless it is declared ``live``: data such as a log, read as it is when the run
    happens. Pinning by default means a script handed to an interpreter (``python3 probe``) is pinned too, whatever
    its name (Codex on GRIP#8856). The executable is pinned as well, a bare name resolved on the run's PATH first
    (``_pin_executable``), and so is every ``extra`` path. The operator reads the manifest, its pinned inputs and its
    live ones, before approving it."""
    argv = _pin_executable(list(argv), live)
    inputs = {}
    for entry in [a for a in argv if a not in live and resolve(a, root).is_file()] + list(extra):
        path = resolve(entry, root)
        if not path.is_file():
            raise ManifestError(f"--input {entry} is not a file")
        inputs[entry] = file_sha256(path)
    return validate({"v": VERSION, "hypothesis": hid, "argv": list(argv), "timeout_s": int(timeout_s),
                     "inputs": inputs, "live": sorted(set(live))})


def describe(manifest: dict) -> str:
    """One line for the operator: the exact command, its timeout, how many inputs the approval pins, and which files
    are read live, so a live declaration is never approved unseen."""
    live = f", live: {', '.join(manifest['live'])}" if manifest["live"] else ""
    return (f"{manifest['hypothesis']}: {shlex.join(manifest['argv'])} (timeout {manifest['timeout_s']} s, "
            f"{len(manifest['inputs'])} input(s) pinned{live})")


# ----------------------------------------------------------------------------- the gate

def run_key(hid: str, sha: str, grant_hash: str) -> str:
    """One run per hypothesis, per approved manifest, per grant."""
    return f"{hid}:{sha[:16]}:{grant_hash}"


def runs_dir(folder: Path) -> Path:
    return folder / "runs"


def list_runs(runs: Path) -> List[dict]:
    out: List[dict] = []
    if not runs.is_dir():
        return out
    for path in sorted(runs.glob("*/result.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            out.append({"folder": path.parent.name, "error": "result unreadable"})
    return out


def run_keys(runs: Path) -> Set[str]:
    """The key of every completed run: a folder whose result.json was written after its seal."""
    return {str(r["run_key"]) for r in list_runs(runs) if r.get("run_key")}


def _matching(folder: Path, hid: str, want: str, root: Path) -> Tuple[Optional[dict], str]:
    """The approved manifest for one hypothesis, or None and why it may not run."""
    try:
        manifest = load_manifest(manifest_path(folder, hid))
    except ManifestError as exc:
        return None, str(exc)
    if manifest["hypothesis"] != hid:
        return None, f"its manifest names {manifest['hypothesis']}"
    if manifest_sha256(manifest) != want:
        return None, "its manifest changed since the approval"
    drift = input_drift(manifest, root)
    return (None, "; ".join(drift)) if drift else (manifest, "")


def select(folder: Path, approved: Dict[str, str], overdue: Iterable[str], grant_hash: str,
           root: Path) -> Tuple[Optional[dict], List[Tuple[str, str]]]:
    """The first overdue hypothesis, by id, that the grant approves, that has no completed run under this
    approval, and whose manifest and inputs still match it; and (id, why) for each one skipped on the way."""
    done = run_keys(runs_dir(folder))
    skipped: List[Tuple[str, str]] = []
    for hid in sorted(set(overdue) & set(approved)):
        if run_key(hid, approved[hid], grant_hash) in done:
            continue
        manifest, why = _matching(folder, hid, approved[hid], root)
        if manifest is not None:
            return {"hypothesis": hid, "manifest": manifest, "sha256": approved[hid]}, skipped
        skipped.append((hid, why))
    return None, skipped


def drop_incomplete(runs: Path, now: float) -> List[str]:
    """Remove the tick's own debris, a run folder with no result.json older than INCOMPLETE_S (a kill between the
    folder and its seal); the names removed."""
    gone: List[str] = []
    if not runs.is_dir():
        return gone
    for folder in sorted(p for p in runs.iterdir() if p.is_dir() and not (p / "result.json").exists()):
        if now - folder.stat().st_mtime >= INCOMPLETE_S and force_remove(folder):
            gone.append(folder.name)
    return gone


def _started(result: dict) -> float:
    """When a run started, by its result; a time that cannot be read counts as long ago."""
    import calendar
    try:
        return float(calendar.timegm(time.strptime(str(result.get("created")), TIME_FORMAT)))
    except ValueError:
        return 0.0


def unsettled(runs: Path, now: float) -> List[dict]:
    """Every run still marked reserved INCOMPLETE_S after it started: the tick stopped between the reservation and the
    record, a kill or a full disk, so whether its seal reached the ledger is not known. It never runs again under its
    approval and is owed no engine row; ``remove`` frees its hypothesis for one more run (Codex on GRIP#8856)."""
    return [r for r in list_runs(runs) if r.get("record") == "reserved" and now - _started(r) >= INCOMPLETE_S]


def _unlock(path: Path) -> None:
    """Restore the owner's access, then clear user flags, top down, following no link (``chmod -R`` and
    ``chflags -R``). Access goes first so the flag pass can descend into a folder the command left unreadable."""
    for cmd in (["/bin/chmod", "-R", "u+rwx", str(path)], ["/usr/bin/chflags", "-R", "0", str(path)]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):  # no chflags off macOS; the removal still decides
            pass


def force_remove(path: Path) -> bool:
    """Remove a folder the sandboxed command could write, however it left it. Each pass unlocks and removes; one lock
    nested inside another (an immutable folder made unreadable) yields one level a pass. True when it is gone, so a
    caller never treats a folder that still holds the command's files as removed (Codex on GRIP#8856)."""
    for _ in range(FORCE_PASSES):
        if not os.path.lexists(path):
            return True
        if path.is_dir() and not path.is_symlink():
            _unlock(path)
        shutil.rmtree(path, ignore_errors=True)
    return not os.path.lexists(path)


# ----------------------------------------------------------------------------- the run

def _sb_string(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def sandbox_profile(out: Path) -> str:
    """No network, no Apple Events, and no file write anywhere but ``out`` and /dev/null. ``out`` must be resolved,
    because the sandbox matches real paths and never a symlink such as ~/.claude. The run's own files sit in the
    folder above ``out``, which the command cannot write, so it cannot plant a link there for the tick to follow
    (Codex on GRIP#8856)."""
    return ("(version 1)(allow default)(deny network*)(deny appleevent-send)(deny file-write*)"
            f'(allow file-write* (subpath {_sb_string(out)}) (literal "/dev/null"))')


def scrubbed_env(out: Path, root: Path) -> Dict[str, str]:
    """The run's whole environment: no token, key or session variable of the tick's reaches it."""
    return {"PATH": SAFE_PATH, "HOME": str(Path.home()), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
            "TMPDIR": str(out.resolve() / "tmp"), "PYTHONDONTWRITEBYTECODE": "1", "CLAUDE_DIR": str(root)}


def _limit_files() -> None:
    """In the child, before exec: no file it writes may grow past MAX_OUTPUT_BYTES (SIGXFSZ ends it)."""
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))


def _lsize(path: str) -> int:
    """The bytes a file or a link itself holds, never what a link names; 0 for one removed since it was listed."""
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


def over_quota(out: Path) -> bool:
    """True once a run has made more than MAX_RUN_ENTRIES files and folders in ``out`` or written more than
    MAX_RUN_BYTES there, every file together: RLIMIT_FSIZE caps each file alone, so many files could otherwise fill
    the disk (Codex on GRIP#8856). The walk follows no link, passes over a folder it cannot read, and stops at the
    first folder that crosses either cap."""
    count = total = 0
    for folder, dirs, files in os.walk(out):
        count += len(dirs) + len(files)
        total += sum(_lsize(os.path.join(folder, name)) for name in files)
        if count > MAX_RUN_ENTRIES or total > MAX_RUN_BYTES:
            return True
    return False


def _leader_done(proc: "subprocess.Popen[bytes]") -> bool:
    """True once the run's leader has ended. Where os.waitid exists the leader is left unreaped (WNOWAIT), so its
    process group id stays reserved for the kill that follows; elsewhere it is reaped, which leaves microseconds in
    which a recycled id could name another group."""
    if hasattr(os, "waitid"):
        return os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    return proc.poll() is not None


def _watch(proc: "subprocess.Popen[bytes]", timeout_s: float, out: Path) -> str:
    """Wait for the run's leader to end, measuring ``out`` at every poll: "" when the leader ended, "quota" when the
    run crossed its aggregate cap first, "timeout" when it outlived ``timeout_s``."""
    deadline = time.monotonic() + timeout_s
    while not _leader_done(proc):
        if over_quota(out):
            return "quota"
        if time.monotonic() >= deadline:
            return "timeout"
        time.sleep(POLL_S)
    return ""


def _end_run(proc: "subprocess.Popen[bytes]", timeout_s: int, out: Path) -> str:
    """End the run: wait for its leader (``_watch``), then kill the whole process group it leads, every time, so a
    background child the leader left behind stops with it (Codex on GRIP#8856), and only then reap the leader. Why
    it ended: "", "timeout" or "quota". A descendant that left the group with setsid is not killed."""
    stopped = _watch(proc, timeout_s, out)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:  # nothing left in the group: macOS answers EPERM for a group holding only its exited leader
        pass
    proc.wait(timeout=KILL_WAIT_S)
    return stopped


def _read_back(fh) -> bytes:
    """A stream as the command wrote it, read through the tick's own descriptor, never through a path the command
    could have replaced with a link."""
    fh.seek(0)
    return fh.read(MAX_OUTPUT_BYTES + 1)


def run_sandboxed(argv: List[str], folder: Path, root: Path, timeout_s: int) -> dict:
    """Run argv under sandbox-exec in its own process group, the GRIP tree as its working folder, its streams in
    ``folder/out``, the only place it may write. ``{"exit", "milliseconds", "timed_out", "over_quota", "stdout",
    "stderr"}``, the streams as bytes; SandboxMissing when there is no sandbox."""
    if not SANDBOX.exists():
        raise SandboxMissing(f"{SANDBOX} is not on this machine, and an experiment runs only inside it")
    out = folder / OUT
    (out / "tmp").mkdir(parents=True, exist_ok=True)
    cmd = [str(SANDBOX), "-p", sandbox_profile(out.resolve()), *argv]
    started = time.monotonic()
    with (out / "stdout.txt").open("w+b") as so, (out / "stderr.txt").open("w+b") as se:
        proc = subprocess.Popen(cmd, cwd=str(root), env=scrubbed_env(out, root), stdin=subprocess.DEVNULL,
                                stdout=so, stderr=se, start_new_session=True, preexec_fn=_limit_files)
        stopped = _end_run(proc, timeout_s, out)
        stdout, stderr = _read_back(so), _read_back(se)
    return {"exit": proc.returncode, "milliseconds": int((time.monotonic() - started) * 1000),
            "timed_out": stopped == "timeout", "over_quota": stopped == "quota", "stdout": stdout, "stderr": stderr}


def _stream(data: bytes) -> dict:
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "capped": len(data) >= MAX_OUTPUT_BYTES}


def ending(result: dict) -> str:
    """How a run ended beyond its exit status, for a report: at its write cap, after its timeout, or nothing."""
    if result.get("over_quota"):
        return " after it crossed its write cap"
    return " after its timeout" if result.get("timed_out") else ""


def _write_bytes(path: Path, data: bytes) -> None:
    """Write through a temporary file and an atomic rename: a crash leaves the old file or the new one, never half of
    either (Codex on GRIP#8856), and the rename replaces a link at ``path`` rather than writing through it."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _write_json(path: Path, value: dict) -> None:
    _write_bytes(path, (json.dumps(value, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def run_name(hid: str, now: float) -> str:
    return f"{hid}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}"


def _seal(result: dict, idr_path: Path, context_path: Path, context_head: Path) -> Tuple[str, str]:
    decision = {"act": "experiment", "hypothesis": result["hypothesis"], "folder": result["folder"],
                "exit": result["exit"], "timed_out": result["timed_out"], "over_quota": result["over_quota"],
                "stdout_sha256": result["stdout"]["sha256"], "stderr_sha256": result["stderr"]["sha256"]}
    inputs = {"grant_hash": result["grant_hash"], "manifest_sha256": result["manifest_sha256"],
              "argv": result["argv"], "milliseconds": result["milliseconds"],
              "stdout_bytes": result["stdout"]["bytes"], "stderr_bytes": result["stderr"]["bytes"]}
    summary = (f"tick experiment {result['folder']} for {result['hypothesis']} under grant {result['grant_hash']}: "
               f"exit {result['exit']}{ending(result)}, stdout "
               f"{result['stdout']['bytes']} bytes sha256 {result['stdout']['sha256'][:12]}; the verdict stays the "
               f"operator's")
    return tick_seal.seal_act("experiment", decision, inputs, summary, idr_path, context_path, context_head)


def _keep(folder: Path, manifest: dict, grant: dict, ran: dict, sha: str, grant_hash: str, now: float) -> dict:
    """The tick's own copy of a run, written atomically where the command cannot write: the manifest, the signed
    grant and the two streams it read back. The command's scratch folder then goes. The result, marked reserved:
    not yet sealed, and no leaf or memory node named."""
    _write_json(folder / "manifest.json", manifest)
    _write_json(folder / "grant.json", grant)
    _write_bytes(folder / "stdout.txt", ran["stdout"])
    _write_bytes(folder / "stderr.txt", ran["stderr"])
    if not force_remove(folder / OUT):
        raise RuntimeError(f"the command left {folder / OUT} undeletable, so the run cannot complete")
    hid = manifest["hypothesis"]
    return {"v": VERSION, "folder": folder.name, "hypothesis": hid, "run_key": run_key(hid, sha, grant_hash),
            "argv": list(manifest["argv"]), "manifest_sha256": sha, "grant_hash": grant_hash,
            "exit": int(ran["exit"]), "timed_out": bool(ran["timed_out"]), "over_quota": bool(ran.get("over_quota")),
            "milliseconds": int(ran["milliseconds"]), "stdout": _stream(ran["stdout"]), "stderr": _stream(ran["stderr"]),
            "created": time.strftime(TIME_FORMAT, time.gmtime(now)), "engine_recorded": False, "idr_leaf": "",
            "memory_node": "", "record": "reserved"}


def run_experiment(runs: Path, chosen: dict, grant: dict, grant_hash: str, root: Path, now: float, idr_path: Path,
                   context_path: Path, context_head: Path, runner: Optional[Runner] = None) -> dict:
    """Run one approved experiment in a new run folder, reserve it, then seal it; the result. The inputs are checked
    again just before the spawn, so a script changed after the gate chose the run is not run under the approval
    (Codex on GRIP#8856): ManifestError, and nothing runs. ``result.json`` is written marked reserved before the
    seal (``_reserve``), so from then on the run never repeats under its approval, whatever happens to the seal
    (``_settle``)."""
    hid, manifest = chosen["hypothesis"], chosen["manifest"]
    drift = input_drift(manifest, root)
    if drift:
        raise ManifestError(f"{hid} did not run: {'; '.join(drift)} just before the run")
    folder = runs / run_name(hid, now)
    folder.mkdir(parents=True, exist_ok=False)
    result = _reserve(folder, chosen, grant, grant_hash, root, now, runner)
    return _settle(folder, result, idr_path, context_path, context_head)


def _reserve(folder: Path, chosen: dict, grant: dict, grant_hash: str, root: Path, now: float,
             runner: Optional[Runner]) -> dict:
    """Run the command, keep the tick's copy of it, and write ``result.json`` marked reserved: the once-only marker,
    on disk before any seal (Codex on GRIP#8856). A failure before it removes the folder and raises (SandboxMissing,
    or the OSError itself), and the run happens again on a later tick, which is harmless because nothing was sealed
    and the run could change nothing outside its folder."""
    manifest = chosen["manifest"]
    try:
        ran = (runner or run_sandboxed)(list(manifest["argv"]), folder, root, int(manifest["timeout_s"]))
        result = _keep(folder, manifest, grant, ran, chosen["sha256"], grant_hash, now)
        _write_json(folder / "result.json", result)
    except BaseException:
        force_remove(folder)  # a folder it cannot remove has no result.json, so drop_incomplete tries again later
        raise
    return result


def _settle(folder: Path, result: dict, idr_path: Path, context_path: Path, context_head: Path) -> dict:
    """Seal a reserved run and rewrite its ``result.json``. A seal that fails cleanly (SealFailed: the ledger holds no
    act) removes the folder and raises, and the run happens again on a later tick. SealUncertain keeps it, marked
    uncertain. Anything else, the record after a good seal included, leaves the reservation in place and raises, so
    the run never happens a second time; ``unsettled`` reports it."""
    try:
        leaf, node = _seal(result, idr_path, context_path, context_head)
    except tick_seal.SealUncertain as exc:
        result.update(idr_leaf=exc.leaf, record="uncertain")
    except tick_seal.SealFailed:
        force_remove(folder)
        raise
    else:
        result.update(idr_leaf=leaf, memory_node=node, record="sealed")
    _write_json(folder / "result.json", result)
    return result


# ----------------------------------------------------------------------------- the engine row

def _engine_row(result: dict) -> dict:
    return {"run": result["folder"], "at": result["created"], "exit": result["exit"],
            "timed_out": result["timed_out"], "milliseconds": result["milliseconds"], "stdout": result["stdout"],
            "stderr": result["stderr"], "manifest_sha256": result["manifest_sha256"],
            "grant_hash": result["grant_hash"], "idr_leaf": result["idr_leaf"], "by": "grip-tick"}


def _engine_record(hid: str, row: dict) -> bool:
    import hypothesis_engine
    return hypothesis_engine.record_experiment(hid, row)


def _owed(result: dict) -> bool:
    """A settled run, sealed or uncertain with its leaf named, whose engine row is not written yet. A run still
    reserved is owed none: it names no leaf."""
    return result.get("engine_recorded") is False and bool(result.get("run_key")) and result.get("record") in SETTLED


def _record_one(runs: Path, result: dict, record: Optional[Record]) -> str:
    """One run's engine row, after which result.json says so; the error's type name when either write failed, so a
    full disk between the two is one retryable error and never takes the tick down (Codex on GRIP#8856). The
    engine skips a run it already holds, so the retry adds no second row."""
    try:
        (record or _engine_record)(str(result["hypothesis"]), _engine_row(result))
        _write_json(runs / str(result["folder"]) / "result.json", dict(result, engine_recorded=True))
    except Exception as exc:  # noqa: BLE001  the run stays recorded in its folder; a later tick writes the row
        return type(exc).__name__
    return ""


def record_pending(runs: Path, record: Optional[Record] = None) -> List[Tuple[str, str]]:
    """Write the engine row of every settled run that is still owed one; (folder, error) for each that failed."""
    failed: List[Tuple[str, str]] = []
    for result in list_runs(runs):
        error = _record_one(runs, result, record) if _owed(result) else ""
        if error:
            failed.append((str(result["folder"]), error))
    return failed


# ----------------------------------------------------------------------------- the command line

def remove_run(runs: Path, name: str) -> bool:
    """The reversal: delete one run folder, and only a folder directly under the runs folder."""
    if not name or name != Path(name).name:
        return False
    folder = (runs / name).resolve()
    if folder.parent != runs.resolve() or not folder.is_dir():
        return False
    return force_remove(folder)


def split_command(argv: List[str]) -> Tuple[List[str], List[str]]:
    """The options before the first ``--`` and the exact command after it. Split by hand, because argparse's
    REMAINDER takes everything after the first positional, options such as ``--timeout`` included."""
    if "--" not in argv:
        return list(argv), []
    cut = argv.index("--")
    return list(argv[:cut]), list(argv[cut + 1:])


def _draft(folder: Path, args: argparse.Namespace, command: List[str]) -> int:
    if not command:
        raise ManifestError("draft needs the exact command after --, for example: draft H857 -- rg -c BLOCKED logs/x.log")
    manifest = draft(args.hypothesis, command, args.timeout, args.input, get_claude_dir(), args.live)
    path = manifest_path(folder, args.hypothesis)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, manifest)
    print(f"grip-tick-experiment: wrote {path}\n  {describe(manifest)}\n  approve it at your terminal with: "
          f"bin/grip-tick-grant issue experiment --hypothesis {args.hypothesis}")
    return 0


def _list(runs: Path) -> int:
    rows = list_runs(runs)
    print(f"experiment runs under {runs}: {len(rows)}")
    for r in rows:
        print(f"  {r.get('folder')}  {r.get('hypothesis', '?')}  exit {r.get('exit', '?')}  "
              f"stdout {r.get('stdout', {}).get('bytes', 0)} bytes sha256 {str(r.get('stdout', {}).get('sha256', ''))[:12]}"
              f"  leaf {r.get('idr_leaf') or 'none'}  engine {'yes' if r.get('engine_recorded') else 'owed'}"
              f"  record {r.get('record', '?')}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Draft, list or remove the always-on tick's approved experiments.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft", help="write state/grip-tick/experiments/<id>.json for the operator to approve",
                       epilog="Put the exact command after --: draft H857 --live logs/x.log -- rg -c BLOCKED logs/x.log")
    d.add_argument("hypothesis")
    d.add_argument("--timeout", type=int, default=5, help=f"seconds, 1 to {MAX_TIMEOUT_S}")
    d.add_argument("--input", action="append", default=[], help="a further file whose bytes the approval binds")
    d.add_argument("--live", action="append", default=[],
                   help="a file the command names that is data read live, such as a log; every other file it names "
                        "is pinned")
    sub.add_parser("list", help="every run with its hypothesis, exit, output hash, seal and engine row")
    sub.add_parser("remove", help="delete one run folder (the act's reversal)").add_argument("name")
    options, command = split_command(sys.argv[1:] if argv is None else argv)
    args = ap.parse_args(options)
    if command and args.cmd != "draft":
        print("grip-tick-experiment: only draft takes a command after --", file=sys.stderr)
        return 2
    folder = experiments_dir()
    try:
        if args.cmd == "draft":
            return _draft(folder, args, command)
    except ManifestError as exc:
        print(f"grip-tick-experiment: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "remove":
        ok = remove_run(runs_dir(folder), args.name)
        print(f"grip-tick-experiment: {'removed' if ok else 'no such run'} {args.name}")
        return 0 if ok else 2
    return _list(runs_dir(folder))


if __name__ == "__main__":
    raise SystemExit(main())
