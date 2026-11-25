"""What H798 needs from the classifier's live traffic: one content-free record per call.

H798 (registered 2026-10-10) asks whether the Jev-first classifier in
lib/infer_classify.py is no worse than its chat ladder alone. Its live
re-measure, due 2026-10-24, needs three numbers from real Stop-hook traffic:
how often the two rungs agree, the median latency of each, and the cost of a
call. Before this module those calls kept no record, so none of the three
could be measured.

**One row per inference call** goes to ``logs/infer-classify-decide.jsonl``:
the UTC time, a caller tag (a short hash of the question and the labels), the
text's SHA-256 (never the text), the rung that decided (``decide``, ``model``
or ``unanswered``), Jev's probabilities when it answered, and for each rung
that ran its latency in milliseconds, the cost it reported and its model id.
When the privacy floor kept the text away from Jev, the floor's reason code is
recorded too. A cache hit, an empty text or the offline switch makes no call
and leaves no row.

**The shadow reading gives the agreement.** After a Jev verdict, a separate
process asks the chat ladder the same question about the same text and appends
its label as a second row with the same call id and digest. The caller never
waits for it: the Stop hook has five seconds. The child gets the text on its
standard input, never in argv or a file, and sends it through the ladder's
normal path, so through the same redaction shield. At most
:data:`SHADOW_DAILY_CAP` shadows run in one UTC day, and
``GRIP_DECIDE_SHADOW_LADDER=0`` turns them off.

Every write fails open: telemetry never changes a verdict and never raises.
``python -m lib.infer_classify_report`` reads the log back.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from claude_dir import get_claude_dir  # noqa: E402

#: ``0`` turns the shadow reading off.
SHADOW_ENV = "GRIP_DECIDE_SHADOW_LADDER"
#: The most shadow readings started in one UTC day.
SHADOW_DAILY_CAP = 60
RUNGS = ("decide", "model")


def log_path() -> Path:
    return get_claude_dir() / "logs" / "infer-classify-decide.jsonl"


def _quota_path() -> Path:
    return get_claude_dir() / "state" / "infer-classify-shadow-quota.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clock() -> float:
    """The start time a rung passes back to :func:`note_rung`."""
    return time.monotonic()


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "surrogatepass")).hexdigest()


def caller_tag(question: str, labels: Sequence[str]) -> str:
    """A short hash that tells callers apart without storing their question."""
    blob = json.dumps([question, list(labels)], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8", "surrogatepass")).hexdigest()[:12]


def start(text: str, question: str, labels: Sequence[str]) -> dict:
    """A new call record. It holds no text, only the text's digest."""
    return {"time": _now().isoformat(), "kind": "call", "call_id": uuid.uuid4().hex,
            "caller": caller_tag(question, labels), "sha256": _digest(text),
            "rung": None, "label": None, "source": None, "probabilities": None,
            "floor": None, "rungs": {}}


def _cost(result: dict) -> Optional[float]:
    """A reported cost in US dollars. HAL decide reports it as a string, /api/infer as a number."""
    raw = result.get("cost_usd")
    if isinstance(raw, bool) or raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def note_rung(record: Optional[dict], rung: str, started: float, result) -> None:
    """Record one rung's latency, model id and cost. Fails open."""
    if record is None:
        return
    try:
        detail = {"latency_ms": round((time.monotonic() - started) * 1000, 1),
                  "model": None, "cost_usd": None}
        if isinstance(result, dict):
            model = result.get("model")
            detail["model"] = model if isinstance(model, str) and model else None
            detail["cost_usd"] = _cost(result)
        record["rungs"][rung] = detail
    except Exception:  # noqa: BLE001 -- telemetry never changes a verdict
        pass


def note_answer(record: Optional[dict], answer: dict, labels: Sequence[str]) -> None:
    """Record Jev's probabilities: a Noul's p for the first label, or a Choice's table."""
    if record is None or not isinstance(answer, dict):
        return
    p = answer.get("noul")
    if _probability(p):
        record["probabilities"] = {labels[0]: p, labels[1]: round(1 - p, 6)}
        return
    table = answer.get("probabilities")
    if isinstance(table, dict):
        record["probabilities"] = {k: v for k, v in table.items()
                                   if k in labels and _probability(v)} or None


def _probability(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= 1)


def note_floor(record: Optional[dict], reason: str) -> None:
    """Record the privacy floor's reason code when it kept the text away from Jev."""
    if record is not None:
        record["floor"] = reason


def append(row: dict) -> None:
    """Append one row in a single write, so concurrent hooks cannot interleave lines."""
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 -- a lost row is better than a failed hook
        pass


def finish(record: Optional[dict], text: str, question: str, labels: Sequence[str],
           label: Optional[str], source: str) -> None:
    """Close the call record, append it, and start the shadow after a Jev verdict."""
    if record is None:
        return
    record["rung"] = source if source in RUNGS else "unanswered"
    record["label"] = label
    record["source"] = source
    append(record)
    if source == "decide":
        start_shadow(text, question, labels, record)


def _quota_used(raw: str, day: str) -> int:
    """Today's count from the quota file; 0 for another day or a file that cannot be read.

    A crash mid-write can leave the file half-written. Read as unusable, it used to
    refuse every later reservation, and nothing ever rewrote it, so the shadow
    stayed off for good (AGORA's suggestion on GRIP#8491). Read as a fresh day, the
    next reservation rewrites it whole: at worst a day gets up to one more cap's
    worth of shadows.
    """
    try:
        state = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    used = state.get("count") if isinstance(state, dict) and state.get("day") == day else 0
    valid = isinstance(used, int) and not isinstance(used, bool) and used >= 0
    return used if valid else 0


def _reserve_shadow() -> bool:
    """Take one of today's shadow slots under a file lock. Any failure takes none.

    The lock comes from _compat: POSIX fcntl, or the LockFileEx lock on Windows. A direct
    ``import fcntl`` failed on a clean Windows install, so no slot was ever taken there
    (GRIP#8757)."""
    try:
        from _compat import fcntl
        path = _quota_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.seek(0)
            day = _now().date().isoformat()
            used = _quota_used(fh.read(), day)
            if used >= SHADOW_DAILY_CAP:
                return False
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps({"day": day, "count": used + 1}))
        return True
    except Exception:  # noqa: BLE001 -- no slot, no shadow
        return False


def start_shadow(text: str, question: str, labels: Sequence[str], record: dict) -> bool:
    """Start the detached ladder reading for a Jev verdict. True when a child started."""
    if os.environ.get(SHADOW_ENV, "").strip() == "0" or not _reserve_shadow():
        return False
    payload = json.dumps({"text": text, "question": question, "labels": list(labels),
                          "call_id": record["call_id"], "sha256": record["sha256"],
                          "caller": record["caller"]}).encode("utf-8", "surrogatepass")
    try:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--shadow"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
        child.stdin.write(payload)
        child.stdin.close()
    except Exception:  # noqa: BLE001 -- a shadow that cannot start is no reading
        return False
    return True


def run_shadow(payload: dict) -> Optional[dict]:
    """The child's work: ask the chat ladder alone, then append the shadow row."""
    import infer_classify
    labels = list(payload["labels"])
    row = {"time": _now().isoformat(), "kind": "shadow", "call_id": payload["call_id"],
           "caller": payload["caller"], "sha256": payload["sha256"], "rungs": {}}
    label = infer_classify._ask_chat_ladder(payload["text"], payload["question"],
                                            labels, None, row)
    row["label"] = label
    row["rung"] = "model" if label is not None else "unanswered"
    append(row)
    return row


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["--shadow"]:
        print("usage: infer_classify_telemetry.py --shadow  (the payload arrives on stdin)",
              file=sys.stderr)
        return 2
    try:
        run_shadow(json.loads(sys.stdin.buffer.read().decode("utf-8", "surrogatepass")))
    except Exception:  # noqa: BLE001 -- a detached child has nobody to report to
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
