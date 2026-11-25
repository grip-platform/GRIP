"""Read a WhatsApp chat export into the same row shape as the mirror.

The per-JID mirror (``state/whatsapp-history/``) records what the live daemon
saw, so it starts when the daemon started and holds ``[voice note 21s]`` where
a voice note was. A WhatsApp **export** is a different order of evidence: the
whole thread from the phone, back to its first message, with the actual media
files beside it.

Measured on one thread, 2026-09-10 — the export holds **102,435 messages from
2014-05-06 to 2026-09-09 with 10,294 attachments**, where the mirror holds
20,394 records from 2026-04-18 across *all 143 threads combined*. Twelve years
the mirror never had, and 3,857 voice notes it holds only as placeholders.

    from lib.wa_export import parse_export, senders

    senders(path)                      # who appears, so the caller picks
    rows = parse_export(path, owner="V>>")

Rows come back in the mirror's own shape — ``ts``, ``direction``, ``chat``,
``sender``, ``type``, ``text``, ``messageId``, ``pushName`` — so every reader
already written against the mirror works on an export unchanged. Two fields
are added: ``media_file`` (the attachment's name inside the archive) and
``source`` (``"export"``), so a merged corpus can always say which record came
from where.

Format notes, learned by measurement rather than assumed
--------------------------------------------------------
- Lines are ``[YYYY/MM/DD, HH:MM:SS] Sender: text``. A line without that
  prefix continues the previous message — 27,316 of them in the measured file,
  so a parser that treats every line as a message loses a fifth of the text.
- WhatsApp inserts U+200E before attachments and U+202A/U+202C around some
  runs — 26,655 invisible characters in the measured file. They break naive
  matching and are stripped here.
- An attachment appears as ``<attached: 00009736-AUDIO-2016-12-01.opus>`` and
  the named file really exists in the archive, which is what turns an
  uncaptured placeholder into something a person or a transcriber can open.

Falsifier
---------
Wrong if the parsed message count diverges from the export's own timestamped
line count by more than the known bracket-in-text cases, or if a round trip
through ``parse_export`` loses an attachment reference. Verifier:
``tests/test_wa_chronology.py`` export cases.
"""
from __future__ import annotations

import datetime as _dt
import os
import hashlib
import re
from collections import Counter
from pathlib import Path

__all__ = [
    "EXPORT_LINE",
    "media_type_for",
    "parse_export",
    "senders",
    "call_records",
    "merge_with_mirror",
]

# [2026/09/09, 21:31:44] Sender Name: message text
EXPORT_LINE = re.compile(
    r"^\[(?P<y>\d{4})/(?P<mo>\d{2})/(?P<d>\d{2}), "
    r"(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})\]\s(?P<rest>.*)$"
)
_ATTACH = re.compile(r"<attached:\s*(?P<name>[^>]+)>")
# Directional and byte-order marks WhatsApp sprinkles through the text.
_INVISIBLE = str.maketrans("", "", "‎‏‪‫‬﻿")

_IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".bmp"}
_VIDEO = {".mp4", ".mov", ".3gp", ".avi", ".mkv", ".webm"}
_VOICE = {".opus"}  # WhatsApp voice notes specifically
_OTHER_AUDIO = {".mp3", ".m4a", ".wav", ".ogg", ".aac"}


# Who a row is FROM, in one place. Six modules decided this independently and
# they did not all agree: most asked whether the direction starts with "out",
# while the verifier and the chronology tested equality with "out-phone"
# exactly. The corpus holds only "in" and "out-phone" today — 371,978 rows, no
# exceptions — so the disagreement produces no wrong answer yet.
#
# It is a live hazard rather than a dead one. lib/wa_watcher declares "out-bot"
# a real direction, and bin/grip-wa-corpus-update now appends rows from the
# live mirror, which is exactly the path that would introduce one. On the day
# it does, the statistics would count that message as his and the chronology
# would not, and the two artefacts would disagree about who said something —
# the error class that once put his own words in his wife's mouth.
#
# "out-bot" is a message GRIP sent from his account. It is HIS, and it is
# marked so it can be flagged: the operator's instruction for agent-authored
# messages is flag but keep, never drop and never silently pass off as typed.
# The zone this matter is read in. Every artefact renders SAST, and a corpus
# timestamp rendered without one uses whatever the MACHINE is set to — which is
# right on this Mac by luck and wrong on a UTC host. That is not hypothetical:
# the handover names Codex, OpenCode and DSH as possible next harnesses and GRIP
# runs a VPS. Bucketing lane1 by year in UTC instead of SAST moves 58 messages
# out of 2015 and into 2016, and every pack count in wa_forensics is bucketed by
# that year.
MATTER_TZ = _dt.timezone(_dt.timedelta(hours=2))


def matter_dt(ts) -> "_dt.datetime":
    """A corpus timestamp as a datetime in the zone this matter is read in."""
    return _dt.datetime.fromtimestamp(float(ts or 0), MATTER_TZ)


OPERATOR_DIRECTIONS = frozenset({"out-phone", "out-bot"})
AGENT_AUTHORED = frozenset({"out-bot"})


def discover_lanes(matter) -> list[str]:
    """Every lane in this matter, by stem, naturally ordered.

    Lane names are the matter's own — they carry the names of real people —
    so they belong on disk beside the data and never as a default inside
    committed code (no-sensitive-info-in-grip.md; this repo mirrors to a
    client org). Three tools used to default to one specific matter's two
    lane names, which put a real first name into a CLI --help string.
    """
    import glob as _glob
    stems = [os.path.basename(p)[:-len(".jsonl")]
             for p in _glob.glob(f"{matter}/lanes/*.jsonl")]

    def key(stem: str):
        # "lane2" must sort before "lane10"; a plain sort puts 10 first.
        m = re.match(r"lane(\d+)", stem)
        return (int(m.group(1)) if m else 1 << 30, stem)

    return sorted(stems, key=key)


def principal_lanes(matter) -> list[str]:
    """The lanes a matter treats as its subject, or all of them.

    A matter may name its principal lanes in ``matter.json``::

        {"principal_lanes": ["lane1-...", "lane2-..."]}

    That file lives in the matter directory, never in this repository, which
    is the point: the selection is matter-specific and the names in it are
    real people. With no such file every lane is used — a WIDER default, so
    the failure mode is including too much rather than silently dropping a
    thread from a legal record.
    """
    import json as _json
    config = os.path.join(str(matter), "matter.json")
    if os.path.exists(config):
        try:
            with open(config, encoding="utf-8") as fh:
                named = _json.load(fh).get("principal_lanes")
        except (ValueError, OSError):
            named = None
        if isinstance(named, list) and named:
            return [str(x) for x in named]
    return discover_lanes(matter)


def require_matter(path: str) -> str:
    """The matter directory, or a one-line instruction instead of a traceback.

    The default is deliberately a neutral, non-existent path: naming the real
    one in committed code would put a personal legal matter into a repository
    that mirrors to a client org. So the common failure is simply that nobody
    set GRIP_WA_MATTER, and the tool should say that rather than raise
    FileNotFoundError from somewhere three frames down.
    """
    if os.path.isdir(path):
        return path
    raise SystemExit(
        f"no matter directory at {path}\n"
        "  set GRIP_WA_MATTER to the forensic corpus directory, or pass --matter")


MEDIA_PLACEHOLDERS = ("[voice note", "[image", "[video", "[document", "[sticker",
                      "[gif", "[audio", "[contact", "[missing", "<attached")


def carries_words(row: dict) -> bool:
    """Whether a row holds words a person could read.

    Not "is it media" — a photo with a caption carries words and an unread
    voice note does not, so the test is what is in the text field rather than
    what type the row is. After the transcripts were merged a voice row does
    carry words, and this count falls, which is the corpus improving rather
    than the measure drifting.

    A length cutoff was the wrong test and is why this now lives in one place.
    "[document 00083304-Fur Flies Mobile Dog And Cat Grooming ...]" is sixty-odd
    characters of FILENAME carrying no message; three rows in this corpus were
    counted as readable for that reason alone, and they were the only rows on
    which three independent implementations of this rule disagreed across
    190,992. What makes a row a placeholder is that the whole row IS the
    marker, not how long the marker is.
    """
    t = str(row.get("text") or "").strip()
    if not t:
        return False
    low = t.lower()
    if any(low.startswith(pre) for pre in MEDIA_PLACEHOLDERS):
        return not (t.endswith("]") or t.startswith("<attached"))
    return True


def party_of(row: dict) -> str:
    """"him" or "other", read from the recorded direction."""
    return "him" if str(row.get("direction") or "") in OPERATOR_DIRECTIONS else "other"


def agent_authored(row: dict) -> bool:
    """Whether GRIP sent this from his account rather than him typing it."""
    return str(row.get("direction") or "") in AGENT_AUTHORED


def media_type_for(filename: str) -> str:
    """Row ``type`` for an attachment, in the crawler's own vocabulary.

    ``.opus`` is a voice note; other audio is a file someone sent, which is a
    document rather than a spoken message. The distinction matters because a
    voice note is speech that a chronology must either transcribe or mark as
    unread, while an attached song is not.
    """
    ext = Path(filename).suffix.lower()
    if ext in _VOICE:
        return "voice"
    if ext in _IMAGE:
        return "image"
    if ext in _VIDEO:
        return "video"
    if ext in _OTHER_AUDIO:
        return "document"
    return "document" if ext else "other"


def _placeholder_for(kind: str, name: str) -> str:
    """The crawler's own placeholder vocabulary (history-store.ts classifyMessage).

    One vocabulary across mirror and export, so a reader written against either
    treats an uncaptured body the same way. A caption-less voice note is
    ``[voice note]``, an image ``[image]``, a document ``[document <name>]``.
    """
    if kind == "voice":
        return "[voice note]"
    if kind == "image":
        return "[image]"
    if kind == "video":
        return "[video]"
    return f"[document {name}]".strip()


def _clean(text: str) -> str:
    return text.translate(_INVISIBLE)


def _split_sender(rest: str) -> tuple[str, str]:
    """Sender and body. Returns ``("", rest)`` for a line carrying no sender."""
    head, sep, tail = rest.partition(": ")
    # A sender name never contains these; a colon inside message text does.
    if not sep or len(head) > 64 or "\n" in head:
        return "", rest
    return head.strip(), tail


def senders(path: Path | str) -> dict[str, int]:
    """Who appears in the export and how often — so the owner is CHOSEN.

    The export carries no marker for which party exported it, so the operator
    is named by the caller rather than guessed. Guessing it inverts every
    speaker attribution in the resulting chronology, which has happened twice
    in this codebase from a rendering shortcut and is why direction is never
    inferred here.
    """
    counts: Counter[str] = Counter()
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = EXPORT_LINE.match(_clean(line))
        if not m:
            continue
        who, _ = _split_sender(m.group("rest"))
        if who:
            counts[who] += 1
    return dict(counts.most_common())


def parse_export(path: Path | str, *, owner: str, chat: str = "",
                 media_root: Path | str | None = None) -> list[dict]:
    """Parse an export ``_chat.txt`` into mirror-shaped rows, oldest first.

    ``owner`` is the sender name belonging to the person whose phone produced
    the export; their messages become ``direction="out-phone"``, everyone
    else's ``"in"``. ``media_root``, when given, is where the archive was
    extracted, so ``media_path`` can point at a file that actually opens.
    """
    path = Path(path)
    rows: list[dict] = []
    pending: dict | None = None

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        pending["text"] = pending["text"].strip()
        attach = _ATTACH.search(pending["text"])
        if attach:
            name = attach.group("name").strip()
            pending["media_file"] = name
            pending["type"] = media_type_for(name)
            # The body must read as the CRAWLER's placeholder, never as the raw
            # <attached:> tag. Downstream, wa_chronology decides "gap or caption"
            # by looking for that placeholder; a tag that survives here renders
            # as a caption, and a hole in the record prints as content.
            caption = _ATTACH.sub("", pending["text"]).strip()
            pending["text"] = caption if caption else _placeholder_for(pending["type"], name)
            if media_root:
                candidate = Path(media_root) / name
                pending["media_path"] = str(candidate)
                pending["media_present"] = candidate.exists()
        pending["messageId"] = hashlib.sha256(
            f"{pending['ts']}|{pending['sender']}|{pending['text']}".encode()
        ).hexdigest()[:22]
        rows.append(pending)
        pending = None

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = _clean(raw)
        m = EXPORT_LINE.match(line)
        if not m:
            if pending is not None:
                pending["text"] += "\n" + line
            continue
        flush()
        who, body = _split_sender(m.group("rest"))
        ts = _dt.datetime(
            int(m.group("y")), int(m.group("mo")), int(m.group("d")),
            int(m.group("h")), int(m.group("mi")), int(m.group("s")),
        ).timestamp()
        pending = {
            "ts": ts,
            "direction": "out-phone" if who == owner else "in",
            "chat": chat,
            "sender": "" if who == owner else who,
            "type": "text",
            "text": body,
            "messageId": "",
            "pushName": who,
            "source": "export",
        }
    flush()
    return rows


def merge_with_mirror(export_rows: list[dict], mirror_rows: list[dict]) -> list[dict]:
    """One corpus from both sources, oldest first, without double-counting.

    The two stores overlap wherever both were running, and they disagree in a
    way that matters: the mirror keeps a message the sender later DELETED,
    while the export keeps the sender's EDIT and loses the original. Neither
    is complete, so a row present in only one source is kept and labelled
    rather than reconciled away.

    Deduplication is on (timestamp to the second, direction, text), which is
    exactly the tuple both sources agree on for a message neither party
    touched afterwards.
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for row in sorted(list(export_rows) + list(mirror_rows), key=lambda r: r.get("ts", 0)):
        key = (int(row.get("ts", 0)), row.get("direction", ""), str(row.get("text", "")))
        if key in seen:
            continue
        seen.add(key)
        row.setdefault("source", "mirror")
        out.append(row)
    return out


# Every shape WhatsApp writes into a thread for a call. The separator before
# the outcome is U+00A0 (a non-breaking space), not a regular space, which is
# why a pattern anchored on "  " misses the answered rows entirely.
#
# Measured across the 16-thread corpus, 2026-09-10 (2,791 rows):
#   Voice call\xa0 8 min                        834   answered, WITH duration
#   Voice call\xa0 No answer                    451   placed, not answered
#   Voice call\xa0 51 sec                       424   answered, WITH duration
#   Missed voice call\xa0 Tap to call back      268   received, missed
#   Voice call\xa0 Answered on other device     257   answered elsewhere
#   Missed voice call                           144   received, missed
#   Video call\xa0 2 min                        101   answered, WITH duration
#   Missed voice call\xa0 Click to call back     91   wording varies by version
#   ...and the video equivalents of each, plus "Voice call\xa0 Ended" (7)
_CALL = re.compile(
    r"(?P<missed>missed\s+)?(?P<medium>voice|video)\s+call"
    r"(?:[\s\u00a0]+(?P<outcome>"
    r"(?P<num>\d+)\s*(?P<unit>min|sec)"
    r"|no answer|answered on other device|ended"
    r"|tap to call back|click to call back"
    r"))?",
    re.I,
)
_SILENCED = re.compile(r"silenced\s+call", re.I)


def _call_outcome(m: re.Match) -> tuple[str, int | None]:
    """(outcome, duration_seconds) for one matched call line."""
    if m.group("num"):
        n = int(m.group("num"))
        return "answered", n * 60 if m.group("unit").lower() == "min" else n
    text = (m.group("outcome") or "").strip().lower()
    if m.group("missed") or text in ("tap to call back", "click to call back"):
        return "missed", None
    if text == "no answer":
        return "no_answer", None
    if text == "answered on other device":
        return "answered_elsewhere", None
    if text == "ended":
        return "ended", None
    return "unspecified", None


def call_records(rows: list[dict]) -> list[dict]:
    """Call events recoverable from an export, with who placed them.

    An earlier version of this function matched only MISSED calls and its
    docstring stated that an answered call leaves no record and that duration
    is absent in every case. Both claims are false, and measurement refuted
    them: of 2,791 call rows in this corpus, **1,376 carry a duration**. The
    wrong version caught 553 of the 2,791 — a fifth — and, worse, told a
    reader they could not conclude something the data supports. A limits
    header built on it would have put a false limitation into a legal file.

    What an export actually records, and what may be concluded from it:

    - **Answered calls ARE logged, with duration** ("Voice call\xa0 8 min").
      So these counts DO bear on how often and how long the parties spoke.
    - **Direction encodes who PLACED the call.** Measured on the main thread:
      "No answer" is 193 of 193 outbound, "Missed" is 306 of 307 inbound, and
      of 657 duration rows no timestamp carries both directions — each call is
      logged once, on one side. So an outbound row is a call the export's
      owner made; an inbound row is one made to them. The single outbound
      "Missed" in 307 is an anomaly, not a counter-example to be smoothed over.
    - **Content is absent in every case, without exception.** A cluster of
      calls around a date evidences that something happened; it can never
      evidence what was said. Every negative claim about what was discussed
      must still be scoped to the text record.
    - "Answered on other device" rows carry no duration, so a call that
      connected on a laptop or a second phone is present as an event and
      absent as a length.

    Returns one dict per call: ``ts``, ``medium`` (voice/video), ``outcome``
    (answered / no_answer / missed / answered_elsewhere / ended / unspecified),
    ``duration_s`` (None unless the line carried one), ``placed_by``
    ("owner" for an outbound row, "other" for an inbound one), ``direction``
    verbatim, ``party``, and ``idx``.
    """
    out = []
    for row in rows:
        # A call event is a system row WhatsApp writes into the transcript. It
        # is never itself a voice note, an image or a document, so a media row
        # cannot be one however its body reads.
        #
        # This guard was latent until the voice transcripts were merged into
        # the corpus. Before that a voice row's body was the string
        # "[voice note]", which matched nothing; afterwards it is what the
        # person actually said, and nine of them in the main thread say
        # "voice call" or "video call" out loud — "give me 30 minutes to
        # complete my video call". Each became a phantom call event, inflating
        # the thread's call count from 1,377 to 1,386 and landing in the
        # "unspecified outcome" bucket, which is exactly where a reader would
        # least question it.
        if str(row.get("type", "text")) not in ("text", ""):
            continue
        text = str(row.get("text", ""))
        match = _CALL.search(text)
        if not match:
            if not _SILENCED.search(text):
                continue
            outcome, duration, medium = "silenced", None, "voice"
        else:
            outcome, duration = _call_outcome(match)
            medium = match.group("medium").lower()
        direction = row.get("direction", "")
        out.append({
            "ts": row.get("ts"),
            "medium": medium,
            "outcome": outcome,
            "duration_s": duration,
            "placed_by": "owner" if party_of(row) == "him" else "other",
            "direction": direction,
            "party": row.get("pushName") or row.get("sender") or "",
            "idx": row.get("idx"),
        })
    return out


def dedupe_across_exports(rows: list[dict], *,
                          cross_source_only: bool = True) -> list[dict]:
    """One corpus from two exports of the SAME thread taken on different days.

    Separate from ``merge_with_mirror`` because the key that works for
    export-versus-mirror does not work here, and using it silently keeps every
    duplicate. That function dedupes on the timestamp **to the second**, which
    is right when both stores recorded the same event from the same clock.
    Two exports do not: measured on this corpus, the same message carries
    timestamps one second apart between two exports of one thread, so an
    exact-second key matches almost nothing.

    Nor can messageId be used. Across the two exports of the main thread,
    10,097 of 11,600 rows are the same message and **only 550 ids coincide** —
    WhatsApp mints a fresh id per export. An id-keyed dedupe here looks like it
    ran and removes 5% of what it should.

    The key that holds is (UTC **date**, direction, whitespace-normalised
    text). It tolerates the one-second drift, and the date rather than the
    timestamp is what makes it tolerant. The cost is stated plainly: two
    genuinely distinct but identical messages sent by the same party on the
    same day collapse into one. In this corpus that is overwhelmingly "ok" and
    "👍", where the loss does not change a finding; where an exact repetition
    is itself the evidence, count from a single export instead.

    ``cross_source_only`` (default True) is what keeps that cost bounded, and
    it was added after measuring: run without it over the two exports of the
    marriage thread and 18,126 rows disappear, against the ~10,097 that are
    genuinely cross-export duplicates. The extra ~8,000 are one person sending
    "ok" twice in a day inside a SINGLE export — real messages, collapsed. So
    a repeat is treated as a duplicate only when it arrives from a different
    source; a repeat within one export is kept, because there the export is
    already the authority on what was sent.

    Rows are returned oldest first, and each survivor carries ``also_in``
    listing the other sources that held a copy, so a merge is auditable rather
    than merely smaller — and a row corroborated by two independent exports is
    stronger evidence than one appearing in a single file.
    """
    # The source of a row is read from `lane`, and rows loaded straight from a
    # lane file do not carry one. Without it every duplicate looks same-export,
    # nothing is removed, and the function returns its input while reporting
    # success — measured: 125,622 rows in, 125,622 out, and every figure
    # computed downstream inflated by about 40%. A silent no-op in a
    # deduplication is worse than a crash, because the caller cannot tell the
    # difference between "no duplicates" and "never looked".
    if cross_source_only and len(rows) > 1 and not any(r.get("lane") for r in rows):
        raise ValueError(
            "dedupe_across_exports(cross_source_only=True) reads each row's source "
            "from row['lane'], and no row carries one. Set it to the export or lane "
            "the row came from before calling, or pass cross_source_only=False if "
            "the rows really are from a single source.")
    seen: dict[tuple, dict] = {}
    out: list[dict] = []
    for row in sorted(rows, key=lambda r: (r.get("ts", 0), str(r.get("lane", "")))):
        stamp = int(row.get("ts", 0))
        day = (_dt.datetime.fromtimestamp(stamp, _dt.timezone.utc).strftime("%Y-%m-%d")
               if stamp else "")
        key = (day, row.get("direction", ""),
               " ".join(str(row.get("text", "")).split()))
        first = seen.get(key)
        if first is None:
            seen[key] = row
            out.append(row)
            continue
        source = row.get("lane", "")
        if cross_source_only and source == first.get("lane", ""):
            # Same export, same day, same words: a person really did send "ok"
            # twice. Keep it. Only a repeat arriving from a DIFFERENT export is
            # the duplicate this function exists to remove.
            out.append(row)
            continue
        also = first.setdefault("also_in", [])
        if source not in also:
            also.append(source)
    return out
