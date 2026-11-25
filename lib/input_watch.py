"""input_watch — the pure logic behind bin/grip-input-watch.

Classify what lands in the INPUT drop-folder, absorb what is a deterministic
syscall (absorb-knowledge), queue a ticket for what needs an agent (absorb-graph,
grip-anywhere), and move every entry into processed/ so nothing is lost and
nothing is absorbed twice. No deletion anywhere: a move is the only transition,
so a failed run is re-runnable and a re-run is a no-op.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

TEXT_EXTS = {
    ".md", ".txt", ".json", ".jsonl", ".csv", ".yaml", ".yml",
    ".log", ".py", ".ts", ".js", ".sh", ".toml", ".rst",
}

# The bookkeeping entries that live inside INPUT but are never themselves inputs.
RESERVED = {"processed", "queue.jsonl", "processed.log"}


def classify(entry: Path) -> str:
    """Route an INPUT entry to 'knowledge' | 'graph' | 'anywhere' | 'backup-only'."""
    if entry.is_dir():
        if (entry / ".git").exists() or (entry / ".git").is_file():
            return "anywhere"  # a git repository -> grip-anywhere
        return "graph"  # a non-git directory -> absorb-graph (a corpus)
    if entry.suffix.lower() in TEXT_EXTS:
        return "knowledge"  # a text file -> absorb-knowledge
    return "backup-only"  # pdf / image / binary -> saved by being here, not absorbed


def _absorb_knowledge(path: Path, title: str | None = None) -> dict:
    """Absorb a text file through the canonical sink. Lazy import so the module
    stays testable without the hal dependency."""
    from lib.hal.knowledge_absorption import absorb_content

    text = path.read_text(encoding="utf-8", errors="replace")
    return absorb_content(text=text, title=title or path.name, source="content")


def _queue_ticket(queue: Path, kind: str, path: str) -> None:
    """Append a dispatch ticket for a skill-based absorb. A ticket is a record
    that the work is owed, never a claim that it was done."""
    ticket = {
        "type": kind,  # 'absorb-graph' | 'grip-anywhere'
        "path": path,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    with queue.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(ticket) + "\n")


_TICKETS = {"graph": "absorb-graph", "anywhere": "grip-anywhere"}
_SUMMARY_KEY = {"backup-only": "backup_only"}  # every other kind is its own key
_NAME_MAX = 255  # bytes in one path component on common file systems


def _free_name(folder: Path, name: str) -> Path:
    """The first of name, name-2, name-3 ... that nothing in folder holds.

    A second input with an earlier input's name replaced that input's kept
    copy, and a same-named folder was moved inside the first one and, the third
    time, stopped the run (GRIP#9338). A free name keeps every input. A name at
    the length limit is shortened to make room for "-2" (Codex on GRIP#9814).
    Two watch runs at the same moment could still pick one name; the watch
    runs from one scheduled job."""
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while True:
        tail = suffix if n == 1 else f"-{n}{suffix}"
        candidate = folder / _fit(stem, tail)
        if not (candidate.is_symlink() or candidate.exists()):
            return candidate
        n += 1


def _fit(stem: str, tail: str) -> str:
    """``stem + tail`` within _NAME_MAX bytes of UTF-8. The stem is shortened
    first, then the end of the tail, whose "-n" counter stays at its front.
    Every pass removes a character, so this always ends, even when the part
    after the last dot is itself too long (AGORA on GRIP#9814)."""
    while len((stem + tail).encode("utf-8")) > _NAME_MAX:
        if stem:
            stem = stem[:-1]
        else:
            tail = tail[:-1]
    return stem + tail


def _keep(entry: Path, kind: str, processed_dir: Path, queue: Path) -> Path:
    """Move the input to a free name in processed_dir first, then absorb it or
    queue its ticket from there. So a failed move absorbs nothing, and a ticket
    never names a path that does not exist yet (Codex and AGORA on GRIP#9814).
    If the absorb or the ticket fails, the input goes back to INPUT under a
    free name, so the next run tries it again, and the error is raised. If that
    move back fails too with an OS error, the input stays at its kept path in
    processed_dir, where later runs do not look, and the raised error names
    that path (GRIP#9826); any other error is raised as it came."""
    kept = _free_name(processed_dir, entry.name)
    shutil.move(str(entry), str(kept))  # move, never delete: the record stays
    try:
        if kind == "knowledge":
            _absorb_knowledge(kept, title=entry.name)
        elif kind in _TICKETS:
            _queue_ticket(queue, _TICKETS[kind], str(kept))
    except Exception as exc:
        try:
            shutil.move(str(kept), str(_free_name(entry.parent, entry.name)))
        except OSError as back:
            # Later runs look only in INPUT, so the FAILED log line must say
            # where the input now is (GRIP#9826).
            raise OSError(f"{exc}; moving it back to INPUT failed too ({back}), "
                          f"so it is still at {kept}") from exc
        raise
    return kept


def _log(log: Path, line: str) -> None:
    with log.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def process(input_dir: Path) -> dict:
    """Scan INPUT, route each entry, move it to processed/, log it. Idempotent.

    processed.log gets one tab-separated line per entry: "<name>, <kind>, <kept
    name>" when the entry was kept, or "<name>, FAILED, <error>" when moving,
    absorbing or ticketing it failed. Such an entry is in INPUT for the next
    run, unless moving it back failed too; then the error names where in
    processed/ it is (GRIP#9826).
    Nothing but this module reads the log."""
    processed_dir = input_dir / "processed"
    processed_dir.mkdir(exist_ok=True)
    queue = input_dir / "queue.jsonl"
    log = input_dir / "processed.log"
    summary = {"knowledge": 0, "graph": 0, "anywhere": 0, "backup_only": 0, "failed": 0}

    for entry in sorted(input_dir.iterdir()):
        if entry.name in RESERVED or entry.name.startswith("."):
            continue
        kind = classify(entry)
        try:
            kept = _keep(entry, kind, processed_dir, queue)
        except Exception as exc:  # noqa: BLE001 — a bad input must not stop the sweep
            summary["failed"] += 1
            _log(log, f"{entry.name}\tFAILED\t{exc}")
            continue
        key = _SUMMARY_KEY.get(kind, kind)
        summary[key] = summary.get(key, 0) + 1
        _log(log, f"{entry.name}\t{kind}\t{kept.name}")

    return summary
