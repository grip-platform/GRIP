"""One rolling 24-hour issue budget for every insights pipeline producer.

The lock covers lookup, reservation and creation. A reservation is persisted
before calling GitHub: an ambiguous timeout or interrupted process consumes a
slot for 24 hours rather than risking an extra issue. Explicit failures release
it. An unreadable ledger or failed lookup prevents creation.
"""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

REPO = "grip-platform/GRIP"
LIMIT = 3
WINDOW = 24 * 60 * 60


def _save(path, records):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        try:
            json.dump(records, f)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _title_query(title):
    """Search for the title as one quoted phrase, so search syntax inside it stays text.

    Unquoted, a generated title holding `is:closed` or `label:x` becomes a filter
    and can hide the open issue the exact-title check is looking for. A double
    quote inside the title would end the phrase early, so it becomes a space.
    """
    return '"' + title.replace('"', " ") + '" in:title'


def _valid_record(record):
    return (isinstance(record, dict) and isinstance(record.get("title"), str)
            and isinstance(record.get("timestamp"), (int, float))
            and 0 <= record["timestamp"] < float("inf"))


def _recent_records(path, timestamp):
    """The ledger's entries from the last 24 hours; raises on a malformed ledger."""
    records = json.loads(path.read_text()) if path.exists() else []
    if not isinstance(records, list) or not all(_valid_record(r) for r in records):
        raise ValueError("invalid issue ledger")
    return [r for r in records if timestamp - r["timestamp"] < WINDOW]


def _budget_skip(records, title):
    if any(r["title"] == title for r in records):
        return {"skipped": "title already opened or reserved in the last 24 hours"}
    if len(records) >= LIMIT:
        return {"skipped": "shared issue budget exhausted (3 per 24 hours)"}
    return None


def _open_issue_skip(title, runner):
    """A skip or an error when an open issue already holds this exact title; None when new."""
    result = runner(
        ["gh", "issue", "list", "-R", REPO, "--state", "open",
         "--search", _title_query(title), "--json", "title", "--limit", "1000"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        return {"error": f"Issue lookup failed: {result.stderr.strip()}"}
    matches = json.loads(result.stdout)
    if not isinstance(matches, list) or not all(
        isinstance(m, dict) and isinstance(m.get("title"), str) for m in matches
    ):
        raise ValueError("invalid issue lookup response")
    if any(m["title"] == title for m in matches):
        return {"skipped": "exact title already has an open issue"}
    if len(matches) >= 1000:
        return {"error": "Issue lookup truncated; cannot establish title uniqueness"}
    return None


def _reserve_and_create(title, body, runner, path, records, timestamp, now):
    """Persist a reservation, then create the issue; an explicit failure releases the slot."""
    reservation = {"title": title, "timestamp": timestamp, "status": "reserved"}
    records.append(reservation)
    _save(path, records)
    result = runner(
        ["gh", "issue", "create", "-R", REPO, "--title", title,
         "--body", body, "--label", "enhancement"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        records.remove(reservation)
        _save(path, records)
        return {"error": result.stderr.strip() or "Issue creation failed"}
    reservation.update(status="created", issue_url=result.stdout.strip(),
                       timestamp=time.time() if now is None else now)
    _save(path, records)
    return {"issue_url": reservation["issue_url"]}


def _platform_fcntl():
    """fcntl for flock(): the POSIX module, or _compat's LockFileEx shim on Windows.

    Windows has no fcntl module, so a bare import failed there (GRIP#8757). The
    import runs when called, so a failed import stays inside create_issue's error
    contract.
    """
    try:
        from _compat import fcntl
    except ImportError:  # imported as lib.insights_issue_budget, without lib/ on sys.path
        from lib._compat import fcntl
    return fcntl


def create_issue(title, body, *, runner=None, ledger_path=None, now=None):
    """Return issue_url, skipped reason, or error; only issue_url means created.

    Runner and clock are injectable so tests never need the real GitHub CLI.
    All callers use the same default ledger, including separate CLI processes.
    """
    runner = runner or subprocess.run
    root = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
    path = Path(ledger_path) if ledger_path is not None else root / "state/insights-issues/ledger.json"
    try:
        fcntl = _platform_fcntl()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            timestamp = time.time() if now is None else now
            records = _recent_records(path, timestamp)
            skip = _budget_skip(records, title) or _open_issue_skip(title, runner)
            if skip:
                return skip
            return _reserve_and_create(title, body, runner, path, records, timestamp, now)
    except Exception as exc:
        return {"error": str(exc)}
