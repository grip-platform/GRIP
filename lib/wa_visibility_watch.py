"""WhatsApp visibility watch: notice when a contact's profile goes dark, or comes back.

WhatsApp never tells an account that a contact has blocked it. What the account
can see is whether the contact's profile photo and "about" text are visible to
it. A block hides both, and so do the contact's own privacy settings, so this
watch reports "visible" and "hidden", never "blocked". A contact who shows
neither to anyone reads "hidden" from the first check, and from then on only a
change carries information.

Each check asks the grip-channel bridge's ``whatsapp_contact_visibility`` MCP
tool (``mcp-servers/grip-channel/src/contact-visibility.ts``). That tool is
read-only: it sends nothing to the contact, returns neither the photo nor the
about text, and records nothing about when the contact is online.

Files, none tracked by git:

- ``config/wa-visibility.local.json`` holds the contacts, each a label and a
  number, and optionally the alert channels (``"notify"``, default macOS only).
- ``state/wa-visibility/<key>.json`` holds the last reading of each contact.
  The key is a digest of the number, so no file name carries it.
- ``state/wa-visibility/changes.jsonl`` holds one line per change, with its time.

Output and alerts name a contact by its label only, never by its number.

Behaviour worth knowing:

- One check runs at a time: an overlapping manual check waits for the hourly
  one, so a change is never alerted twice.
- A change is logged and alerted before the new reading is saved. A failure
  part-way repeats the change at the next check rather than losing it.
- A broken contacts file stops ``check``, ``status`` and ``arm`` with a clear
  error, but never ``disarm`` or ``log``.
- An armed job whose contacts file is later emptied keeps running with
  nothing to check, quietly; ``disarm`` stops it.
- ``arm`` renders the GRIP folder it resolved (``$CLAUDE_DIR``, else
  ``~/.claude``) into the job, so the job reads the same installation.
- ``arm`` and ``disarm`` work only on macOS, which has launchd. Elsewhere they
  stop with a message and exit 2 before anything is written.

Usage:
    grip-wa-visibility check [--quiet] [--json]   ask now, record, alert on a change
    grip-wa-visibility status [--json]            the last readings, without asking
    grip-wa-visibility log [-n N]                 the most recent changes
    grip-wa-visibility arm | disarm               start or stop the hourly launchd job (macOS)
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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional
from xml.sax.saxutils import escape

_LIB = Path(__file__).resolve().parent
if str(_LIB.parent) not in sys.path:
    sys.path.insert(0, str(_LIB.parent))
from lib.claude_dir import get_claude_dir  # noqa: E402

TOOL_NAME = "whatsapp_contact_visibility"
JOB_LABEL = "com.grip.wa-visibility"
READINGS = ("visible", "hidden", "not-on-whatsapp")
CONFIG_EXAMPLE = '{"contacts": [{"label": "Contact A", "number": "+27 00 000 0000"}], "notify": ["macos"]}'

Caller = Callable[[str, dict], str]
Notifier = Callable[[str], None]
Runner = Callable[..., subprocess.CompletedProcess]


class ConfigError(ValueError):
    """The contacts file cannot be used as written."""


@dataclass(frozen=True)
class Contact:
    label: str
    number: str

    @property
    def key(self) -> str:
        """A digest of the number as the bridge reads it; it names the state file without carrying the number."""
        digits = re.sub(r"\D", "", self.number)
        if digits.startswith("00"):
            digits = digits[2:]  # the bridge reads 0027... as 27..., so both spellings share one history
        return hashlib.sha256(digits.encode("ascii")).hexdigest()[:16]


def config_path() -> Path:
    return get_claude_dir() / "config" / "wa-visibility.local.json"


def state_dir() -> Path:
    return get_claude_dir() / "state" / "wa-visibility"


def load_config(path: Path) -> dict:
    """The config file as a dict. A missing file is an empty config, not an error."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON ({exc.msg}, line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must hold a JSON object")
    return data


def _contact_from(entry: object, index: int) -> Contact:
    entry = entry if isinstance(entry, dict) else {}
    label = str(entry.get("label", "")).strip()
    number = str(entry.get("number", "")).strip()
    if not label or not number:
        raise ConfigError(f"contact {index} needs both a label and a number")
    return Contact(label, number)


def load_contacts(data: dict) -> list[Contact]:
    """The configured contacts, each label and each number once. An error names entries, never a number."""
    contacts: list[Contact] = []
    for index, entry in enumerate(data.get("contacts") or [], start=1):
        contact = _contact_from(entry, index)
        if any(known.label == contact.label for known in contacts):
            raise ConfigError(f"the label {contact.label!r} is used twice")
        twin = next((known for known in contacts if known.key == contact.key), None)
        if twin:
            raise ConfigError(f"contact {index} ({contact.label!r}) has the same number as {twin.label!r}")
        contacts.append(contact)
    return contacts


# Anything shaped like a phone number: seven or more digits, bare or split by spaces, dots, dashes or brackets.
_NUMBER_SHAPE = re.compile(r"\+?\d(?:[\s().-]*\d){6,}")


def _scrub(text: object) -> str:
    """Text with anything shaped like a phone number replaced, so no number can ride out in it."""
    return _NUMBER_SHAPE.sub("<number>", str(text))[:160]


def default_caller(name: str, arguments: dict) -> str:
    from lib.whatsapp_transport import call_tool  # late: tests pass their own caller

    return call_tool(name, arguments)


def _scrub_details(report: dict) -> dict:
    """The reading with the number removed everywhere it could sit: the JID, and any error detail."""
    report.pop("jid", None)
    for name in ("photo", "about"):
        signal = report.get(name)
        if isinstance(signal, dict) and signal.get("detail") is not None:
            signal["detail"] = _scrub(signal["detail"])
    return report


def fetch_report(contact: Contact, caller: Caller) -> dict:
    """One reading from the bridge. Any failure reads as "unknown", which is never a change."""
    try:
        reply = caller(TOOL_NAME, {"contact": contact.number})
    except Exception as exc:  # noqa: BLE001 -- every transport failure is an unknown reading
        return {"summary": "unknown", "error": type(exc).__name__}
    try:
        report = json.loads(reply)
    except (TypeError, ValueError):
        return {"summary": "unknown", "error": _scrub(reply)}
    if not isinstance(report, dict) or report.get("summary") not in READINGS + ("unknown",):
        return {"summary": "unknown", "error": "unexpected reply from the bridge"}
    return _scrub_details(report)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def transition(
    previous: Optional[dict], report: dict, label: str, moment: datetime
) -> tuple[dict, Optional[dict]]:
    """The next stored reading for one contact, and the change it records, or None."""
    stamp = _stamp(moment)
    summary = report.get("summary", "unknown")
    if summary == "unknown":
        kept = dict(previous or {"summary": "unknown", "since": None})
        kept.update(label=label, last_checked=stamp, last_error=report.get("error", "no reading"))
        return kept, None
    known = previous if previous and previous.get("summary") in READINGS else None
    unchanged = bool(known) and known["summary"] == summary
    state = {
        "label": label,
        "summary": summary,
        "since": (known.get("since") or stamp) if unchanged else stamp,
        "last_checked": stamp,
        "registered": report.get("registered"),
        "photo": report.get("photo"),
        "about": report.get("about"),
    }
    if unchanged:
        return state, None
    change = {
        "at": stamp,
        "label": label,
        "from": known["summary"] if known else None,
        "to": summary,
        "previous_since": known.get("since") if known else None,
        "photo": report.get("photo"),
        "about": report.get("about"),
    }
    return state, change


_FIRST = {
    "visible": "first reading: their profile photo or about text is visible to you.",
    "hidden": (
        "first reading: neither their profile photo nor their about text is visible to you. "
        "Someone who shows neither to anyone reads the same, so only a change from here says something."
    ),
    "not-on-whatsapp": "first reading: the number does not show as registered on WhatsApp.",
}

_CHANGES = {
    ("visible", "hidden"): (
        "their profile photo and about text are no longer visible to you (visible since {since}). "
        "A block looks like this, and so does a privacy change."
    ),
    ("hidden", "visible"): (
        "their profile photo or about text is visible to you again (hidden since {since}). "
        "An unblock looks like this, and so does a privacy change."
    ),
    ("visible", "not-on-whatsapp"): "the number no longer shows as registered on WhatsApp (visible since {since}).",
    ("hidden", "not-on-whatsapp"): "the number no longer shows as registered on WhatsApp (hidden since {since}).",
    ("not-on-whatsapp", "visible"): "the number is on WhatsApp again, and its profile is visible to you.",
    ("not-on-whatsapp", "hidden"): "the number is on WhatsApp again, but its photo and about text are hidden from you.",
}


def _when(stamp: Optional[str]) -> str:
    if not stamp:
        return "an unknown time"
    try:
        return datetime.fromisoformat(stamp).astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    except ValueError:
        return stamp


def describe_change(change: dict) -> str:
    """One plain sentence about a change, naming the contact by its label."""
    old, new = change.get("from"), change["to"]
    text = _FIRST[new] if old is None else _CHANGES.get((old, new), f"changed from {old} to {new}.")
    return f"{change['label']}: " + text.format(since=_when(change.get("previous_since")))


def read_state(directory: Path, contact: Contact) -> Optional[dict]:
    try:
        data = json.loads((directory / f"{contact.key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_state(directory: Path, contact: Contact, state: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    scratch = directory / f".{contact.key}.{os.getpid()}.tmp.json"  # its own name per process
    scratch.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(scratch, directory / f"{contact.key}.json")


def append_change(directory: Path, change: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "changes.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(change, sort_keys=True) + "\n")


def read_changes(directory: Path, limit: int) -> list[dict]:
    path = directory / "changes.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-limit:] if limit > 0 else rows


@contextmanager
def state_lock(directory: Path) -> Iterator[None]:
    """One check at a time per state folder, so an overlapping manual check and hourly job cannot race."""
    directory.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # Windows: no launchd job runs there, so there is no second check to race
        yield
        return
    with (directory / ".lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _check_one(contact: Contact, caller: Caller, notifier: Notifier, directory: Path, moment: datetime) -> dict:
    """Log and alert a change BEFORE saving the reading, so a failure part-way repeats it rather than losing it."""
    previous = read_state(directory, contact)
    state, change = transition(previous, fetch_report(contact, caller), contact.label, moment)
    if change:
        append_change(directory, change)
        if change["from"] is not None:
            notifier(describe_change(change))
    write_state(directory, contact, state)
    return {"label": contact.label, "state": state, "change": change}


def check_all(
    contacts: Iterable[Contact],
    caller: Caller,
    notifier: Notifier,
    directory: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> list[dict]:
    """Read every contact once, under the state lock; alert on each change after the first."""
    with state_lock(directory):
        return [_check_one(contact, caller, notifier, directory, clock()) for contact in contacts]


def default_notifier(channels: Iterable[str]) -> Notifier:
    """Alerts through grip_notify. A failed alert is reported on stderr and never stops a check."""
    names = list(channels) or ["macos"]

    def send(text: str) -> None:
        try:
            from lib.grip_notify import notify  # late: tests pass their own notifier

            delivered = notify(text, title="WhatsApp visibility", channels=names, mode="fan-out")
        except Exception as exc:  # noqa: BLE001 -- an alert failure must not lose the reading
            delivered = False
            print(f"alert failed ({type(exc).__name__})", file=sys.stderr)
        if not delivered:
            print(f"alert not delivered: {text}", file=sys.stderr)

    return send


def _row(label: str, state: Optional[dict]) -> str:
    if not state:
        return f"{label}: no reading yet"
    checked = _when(state.get("last_checked"))
    if state.get("summary") in READINGS:
        line = f"{label}: {state['summary']} since {_when(state.get('since'))}, last checked {checked}"
    else:
        line = f"{label}: no good reading yet, last checked {checked}"
    if state.get("last_error"):
        line += f" (that check failed: {state['last_error']})"
    return line


def _no_contacts() -> int:
    print(f"No contacts yet. Add one to {config_path()}, for example:\n  {CONFIG_EXAMPLE}", file=sys.stderr)
    return 2


def _run_check(contacts: list[Contact], config: dict, args, caller: Caller, notifier: Optional[Notifier]) -> int:
    if not contacts:
        return 0 if args.quiet else _no_contacts()  # the hourly job with nothing to watch is not a failure
    send = notifier or default_notifier(config.get("notify") or ["macos"])
    results = check_all(contacts, caller, send, state_dir())
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0
    for result in results:
        if result["change"]:
            print(f"{_when(result['change']['at'])}  {describe_change(result['change'])}")
        elif not args.quiet or result["state"].get("last_error"):
            print(_row(result["label"], result["state"]))
    return 0


def _print_status(contacts: list[Contact], as_json: bool) -> int:
    rows = {contact.label: read_state(state_dir(), contact) for contact in contacts}
    if as_json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    for label, state in rows.items():
        print(_row(label, state))
    return 0 if rows else _no_contacts()


def _print_log(limit: int) -> int:
    changes = read_changes(state_dir(), limit)
    for change in changes:
        print(f"{_when(change.get('at'))}  {describe_change(change)}")
    if not changes:
        print("No changes recorded yet.")
    return 0


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{JOB_LABEL}.plist"


def _domain() -> Optional[str]:
    """This user's launchd domain, or None off macOS, which has no launchd (Windows has no os.getuid either)."""
    if sys.platform != "darwin":
        return None
    return f"gui/{os.getuid()}"


def _no_launchd(verb: str) -> int:
    print(f"Nothing to {verb} here: the hourly job runs under launchd, and only macOS has launchd.", file=sys.stderr)
    return 2


def render_plist(home: str, grip_dir: str) -> str:
    """The job, pointed at the GRIP folder this run resolved, so the job reads the same installation."""
    template = Path(grip_dir) / "launchd" / f"{JOB_LABEL}.plist.template"
    text = template.read_text(encoding="utf-8")
    return text.replace("__GRIP_DIR__", escape(grip_dir)).replace("__HOME__", escape(home))


def _wait_until_unloaded(runner: Runner, domain: str, attempts: int = 20) -> bool:
    """True once launchd no longer knows the job. bootout returns before the job is gone, so this polls."""
    for _ in range(attempts):
        if runner(["launchctl", "print", f"{domain}/{JOB_LABEL}"], capture_output=True).returncode != 0:
            return True
        time.sleep(0.25)
    return False


def arm(contacts: list[Contact], runner: Runner = subprocess.run) -> int:
    """Install and load the hourly job; refuses off macOS, and while no contact is configured."""
    domain = _domain()
    if domain is None:
        return _no_launchd("arm")  # first: off macOS a contact would not help
    if not contacts:
        return _no_contacts()
    grip_dir = get_claude_dir()
    (grip_dir / "logs").mkdir(parents=True, exist_ok=True)
    target = _launch_agent_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_plist(str(Path.home()), str(grip_dir)), encoding="utf-8")
    runner(["launchctl", "bootout", f"{domain}/{JOB_LABEL}"], capture_output=True)
    _wait_until_unloaded(runner, domain)
    loaded = runner(["launchctl", "bootstrap", domain, str(target)], capture_output=True, text=True)
    if loaded.returncode != 0:
        print(f"launchctl bootstrap failed: {(loaded.stderr or '').strip()}", file=sys.stderr)
        return 1
    print(f"Armed {JOB_LABEL}: one check now, then one an hour. Log: {grip_dir / 'logs' / 'wa-visibility.log'}")
    return 0


def disarm(runner: Runner = subprocess.run) -> int:
    """Unload and remove the hourly job; the readings and the change log stay. Never claims success while it runs."""
    domain = _domain()
    if domain is None:
        return _no_launchd("disarm")
    runner(["launchctl", "bootout", f"{domain}/{JOB_LABEL}"], capture_output=True)
    if not _wait_until_unloaded(runner, domain):
        print(
            f"{JOB_LABEL} is still loaded after launchctl bootout, so its file was kept. "
            f"Check it with: launchctl print {domain}/{JOB_LABEL}",
            file=sys.stderr,
        )
        return 1
    _launch_agent_path().unlink(missing_ok=True)
    print(f"Disarmed {JOB_LABEL}. The readings and the change log stay in {state_dir()}.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grip-wa-visibility",
        description=(
            "Watch whether WhatsApp contacts' profiles are visible to your number. WhatsApp never "
            "reports blocks, so 'hidden' can mean a block or a privacy setting."
        ),
        epilog=f"Contacts live in {config_path()}, for example:\n  {CONFIG_EXAMPLE}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(command="check", quiet=False, json=False, n=20)
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check", help="ask now, record the readings, alert on a change")
    check.add_argument("--quiet", action="store_true", help="print only changes and failed checks")
    check.add_argument("--json", action="store_true", help="print the readings as JSON")
    status = sub.add_parser("status", help="the last readings, without asking WhatsApp")
    status.add_argument("--json", action="store_true", help="print the readings as JSON")
    log = sub.add_parser("log", help="the most recent changes")
    log.add_argument("-n", type=int, default=20, help="how many changes to show (default 20)")
    sub.add_parser("arm", help="start the hourly launchd job (macOS only)")
    sub.add_parser("disarm", help="stop the hourly launchd job (macOS only)")
    return parser


def main(
    argv: Optional[list[str]] = None,
    *,
    caller: Caller = default_caller,
    notifier: Optional[Notifier] = None,
    runner: Runner = subprocess.run,
) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "check"
    if command == "disarm":
        return disarm(runner)  # needs no contacts, so a broken contacts file can never block it
    if command == "log":
        return _print_log(args.n)
    try:
        config = load_config(config_path())
        contacts = load_contacts(config)
    except ConfigError as exc:
        print(f"grip-wa-visibility: {exc}", file=sys.stderr)
        return 2
    commands = {
        "check": lambda: _run_check(contacts, config, args, caller, notifier),
        "status": lambda: _print_status(contacts, args.json),
        "arm": lambda: arm(contacts, runner),
    }
    return commands[command]()


if __name__ == "__main__":
    sys.exit(main())
