"""Recall subject index — a REBUILDABLE materialised view over the moat.

Never a source of truth: the signed IDR chain, context chain, receipts and
file-history blobs are canonical; ``build()`` derives this store from them
and can always be re-run. Layout under ``store_dir`` (default
``get_claude_dir()/state/recall/``, overridable via ``$GRIP_RECALL_STORE``
so hooks/CLI/tests share one injection seam):

  events.jsonl  append-only IndexEvent log (source bytes for verification)
  mmr.json      Merkle Mountain Range state + root (F4 audit commitment)
  index.db      SQLite materialised view (+ FTS5 when available)
  meta.json     {fts, sources, built_at}

Verify-on-read (F4): every query recomputes each event's content hash
against its event_id AND the MMR root against the stored root; the verdict
travels as ``Freshness`` on every result.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

try:
    from lib.claude_dir import get_claude_dir
except ImportError:  # dual-root lib/ resolution (learned-insights f47b0f96)
    from claude_dir import get_claude_dir  # type: ignore

from lib.recall import mmr
from lib.recall.aliases import DEFAULT_ALIASES, expand, normalise
from lib.recall.schema import (
    Artefact,
    EVENT_ALIAS_ADD,
    EVENT_ARTEFACT_SEEN,
    EVENT_INVALIDATE,
    EVENT_SUPERSEDE,
    Freshness,
    IndexEvent,
    QueryResult,
    STATUS_ACTIVE,
    STATUS_INVALIDATED,
    STATUS_SUPERSEDED,
    canonical_json,
    content_hash,
)

STORE_ENV = "GRIP_RECALL_STORE"
_HASH_CAP_BYTES = 20 * 1024 * 1024
_EVENT_LEAF_FIELDS = ("kind", "ts", "subject", "aliases", "artefact",
                      "target_event_id", "note")

_ART_COLS = (
    "subject", "type", "path", "blob_hash", "created_at", "status", "idr_id",
    "receipt_path", "receipt_hash", "supersedes", "invalidated_by", "source",
)


def default_store() -> Path:
    env = os.environ.get(STORE_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    return get_claude_dir() / "state" / "recall"


def resolve_store(store) -> Path:
    return Path(store).expanduser() if store else default_store()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mtime_iso(p: Path) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(p.stat().st_mtime))


# ------------------------------------------------------------------ sqlite
_DDL = (
    "CREATE TABLE IF NOT EXISTS meta_kv(k TEXT PRIMARY KEY, v TEXT)",
    "CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " event_id TEXT UNIQUE, kind TEXT, ts TEXT, subject TEXT, norm_subject TEXT,"
    " payload TEXT)",
    "CREATE TABLE IF NOT EXISTS artefacts(event_id TEXT PRIMARY KEY, seq INTEGER,"
    " subject TEXT, norm_subject TEXT, type TEXT, path TEXT, blob_hash TEXT,"
    " created_at TEXT, status TEXT, idr_id TEXT, receipt_path TEXT,"
    " receipt_hash TEXT, supersedes TEXT, invalidated_by TEXT, source TEXT,"
    " aliases TEXT)",
    "CREATE TABLE IF NOT EXISTS subject_aliases(norm_subject TEXT, alias TEXT)",
)


def _connect(store: Path) -> sqlite3.Connection:
    store.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(store / "index.db"))
    for ddl in _DDL:
        conn.execute(ddl)
    _ensure_fts(conn)
    conn.commit()
    return conn


def _ensure_fts(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT v FROM meta_kv WHERE k='fts'").fetchone():
        return
    enabled = "0"
    if os.environ.get("GRIP_RECALL_NO_FTS", "") != "1":
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS artefacts_fts USING "
                "fts5(event_id UNINDEXED, subject, aliases, path)"
            )
            enabled = "1"
        except sqlite3.OperationalError:
            enabled = "0"
    conn.execute("INSERT OR REPLACE INTO meta_kv(k, v) VALUES('fts', ?)", (enabled,))


def _fts_on(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT v FROM meta_kv WHERE k='fts'").fetchone()
    return bool(row and row[0] == "1")


# ------------------------------------------------------- event application
def _apply_seen(conn: sqlite3.Connection, ev: dict, seq: int) -> None:
    art = dict(ev.get("artefact") or {})
    norm = normalise(art.get("subject", ""))
    conn.execute(
        "UPDATE artefacts SET status=? WHERE norm_subject=? AND path=? AND status=?",
        (STATUS_SUPERSEDED, norm, art.get("path", ""), STATUS_ACTIVE),
    )
    alias_text = " ".join(ev.get("aliases") or [])
    cols = ", ".join(_ART_COLS)
    marks = ",".join("?" * (len(_ART_COLS) + 4))
    conn.execute(
        f"INSERT OR REPLACE INTO artefacts(event_id, seq, norm_subject, aliases, {cols})"
        f" VALUES ({marks})",
        (ev["event_id"], seq, norm, alias_text, *[art.get(c) for c in _ART_COLS]),
    )
    if _fts_on(conn):
        conn.execute(
            "INSERT INTO artefacts_fts(event_id, subject, aliases, path) VALUES (?,?,?,?)",
            (ev["event_id"], norm, alias_text, art.get("path", "")),
        )


def _apply_supersede(conn: sqlite3.Connection, ev: dict, seq: int) -> None:
    conn.execute(
        "UPDATE artefacts SET status=?, supersedes=? WHERE event_id=?",
        (STATUS_SUPERSEDED, ev["event_id"], ev.get("target_event_id")),
    )


def _apply_invalidate(conn: sqlite3.Connection, ev: dict, seq: int) -> None:
    conn.execute(
        "UPDATE artefacts SET status=?, invalidated_by=? WHERE event_id=?",
        (STATUS_INVALIDATED, ev["event_id"], ev.get("target_event_id")),
    )


def _apply_alias(conn: sqlite3.Connection, ev: dict, seq: int) -> None:
    norm = normalise(ev.get("subject", ""))
    rows = [(norm, normalise(a)) for a in (ev.get("aliases") or [])]
    conn.executemany("INSERT INTO subject_aliases(norm_subject, alias) VALUES (?,?)", rows)


_APPLY = {
    EVENT_ARTEFACT_SEEN: _apply_seen,
    EVENT_SUPERSEDE: _apply_supersede,
    EVENT_INVALIDATE: _apply_invalidate,
    EVENT_ALIAS_ADD: _apply_alias,
}


def _event_dict(event) -> dict:
    if hasattr(event, "to_dict"):
        return event.to_dict()
    ev = dict(event)
    if "event_id" not in ev:
        ev["event_id"] = content_hash({k: ev.get(k) for k in _EVENT_LEAF_FIELDS})
    return ev


def append_event(event, store=None) -> str:
    """Append one IndexEvent: jsonl + MMR leaf + SQLite view. Returns event_id."""
    st = resolve_store(store)
    st.mkdir(parents=True, exist_ok=True)
    ev = _event_dict(event)
    with (st / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(canonical_json(ev) + "\n")
    _save_mmr(st, mmr.append_leaf(ev["event_id"], _load_mmr(st)))
    conn = _connect(st)
    try:
        cur = conn.execute(
            "INSERT INTO events(event_id, kind, ts, subject, norm_subject, payload)"
            " VALUES (?,?,?,?,?,?)",
            (ev["event_id"], ev.get("kind"), ev.get("ts"), ev.get("subject"),
             normalise(ev.get("subject", "")), canonical_json(ev)),
        )
        _APPLY.get(ev.get("kind"), lambda *_: None)(conn, ev, cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    return ev["event_id"]


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _parse_mmr(data: bytes | None) -> dict:
    if data is None:
        return mmr.empty_state()
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:  # JSONDecodeError and UnicodeDecodeError are both ValueErrors
        return mmr.empty_state()


def _load_mmr(st: Path) -> dict:
    return _parse_mmr(_read_bytes(st / "mmr.json"))


def _save_mmr(st: Path, state: dict) -> None:
    state = dict(state)
    state["root"] = mmr.root(state)
    (st / "mmr.json").write_text(json.dumps(state, indent=0), encoding="utf-8")


# --------------------------------------------------------- verify-on-read
def _leaf_of_line(line: str) -> tuple[str | None, str]:
    """(recomputed_leaf, stored_event_id) for one events.jsonl line."""
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return None, ""
    recomputed = content_hash({k: ev.get(k) for k in _EVENT_LEAF_FIELDS})
    return recomputed, str(ev.get("event_id") or "")


_VERIFY_MEMO = "verify-memo.json"
# Bump when _EVENT_LEAF_FIELDS, content_hash or the MMR change: a memo is valid
# only for the verification code that wrote it.
_VERIFY_VERSION = "verify-events/1"


def _verify_events(st: Path) -> tuple[bool, str, list, str]:
    """Recompute every event's leaf + the MMR root against stored bytes.

    The result is a pure function of the bytes of events.jsonl and mmr.json, so
    a verified result is kept under their SHA-256 digests and reused while both
    files are byte-identical; any changed byte verifies in full again. Anyone
    able to forge that memo could already rewrite both files consistently, so it
    defends against the same faults as the full check (GRIP#8866).

    Also returns the MMR root of the leaves read, so no caller rebuilds it."""
    try:
        events = (st / "events.jsonl").read_bytes()
    except FileNotFoundError:
        return False, "index not built (no events.jsonl)", [], mmr.root(mmr.state_from([]))
    mmr_bytes = _read_bytes(st / "mmr.json")
    key = _memo_key(events, mmr_bytes)
    cached = _cached_verification(st, key)
    if cached is not None:
        return cached
    result = _verify_bytes(events, mmr_bytes)
    if result[0]:
        _remember_verification(st, key, result[2], result[3])
    return result


def _memo_key(events: bytes, mmr_bytes: bytes | None) -> dict:
    return {"version": _VERIFY_VERSION, "events": hashlib.sha256(events).hexdigest(),
            "mmr": None if mmr_bytes is None else hashlib.sha256(mmr_bytes).hexdigest()}


def _cached_verification(st: Path, key: dict) -> tuple[bool, str, list, str] | None:
    """The verified result kept for exactly these bytes, or None."""
    try:
        memo = json.loads((st / _VERIFY_MEMO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(memo, dict) or memo.get("key") != key:
        return None
    leaves = memo.get("leaves")
    if not isinstance(leaves, list) or len(leaves) != memo.get("count"):
        return None
    return True, "", leaves, memo.get("head")


def _remember_verification(st: Path, key: dict, leaves: list, head: str) -> None:
    """Keep a verified result for these bytes; a failed write costs only speed."""
    memo = json.dumps({"key": key, "count": len(leaves), "head": head, "leaves": leaves})
    tmp = st / f"{_VERIFY_MEMO}.{os.getpid()}.tmp"
    try:
        tmp.write_text(memo, encoding="utf-8")
        os.replace(tmp, st / _VERIFY_MEMO)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _verify_bytes(events: bytes, mmr_bytes: bytes | None) -> tuple[bool, str, list, str]:
    """The full check: every event's leaf, then the MMR root and count."""
    lines = [ln for ln in events.decode("utf-8").splitlines() if ln.strip()]
    leaves, detail = _leaves_of(lines)
    head = mmr.root(mmr.state_from(leaves))
    if detail:
        return False, detail, leaves, head
    stored_state = _parse_mmr(mmr_bytes)
    if stored_state.get("root") != head or int(stored_state.get("count", -1)) != len(leaves):
        return False, "MMR root/count mismatch vs stored mmr.json", leaves, head
    return True, "", leaves, head


def _leaves_of(lines: list[str]) -> tuple[list[str], str]:
    """(leaves, failure detail) for the event lines, stopping at the first bad line."""
    leaves: list[str] = []
    for i, line in enumerate(lines):
        recomputed, stored = _leaf_of_line(line)
        if recomputed is None:
            return leaves, f"event line {i} unparseable"
        if recomputed != stored:
            return leaves, f"event line {i} content hash mismatch"
        leaves.append(recomputed)
    return leaves, ""


def store_freshness(store=None) -> Freshness:
    st = resolve_store(store)
    ok, detail, leaves, head = _verify_events(st)
    return Freshness(
        verified=ok,
        index_head=head,
        event_count=len(leaves),
        checked_at=_now(),
        detail=detail,
    )


# ------------------------------------------------------------------ query
def _row_to_artefact(row: sqlite3.Row) -> dict:
    return Artefact.from_dict({c: row[c] for c in _ART_COLS}).to_dict()


def _registered_keys(conn: sqlite3.Connection, keys: set) -> set:
    keys = {k for k in keys if k}
    if not keys:
        return set()
    out = set(keys)
    marks = ",".join("?" * len(keys))
    args = tuple(keys)
    rows = conn.execute(
        f"SELECT norm_subject FROM subject_aliases WHERE alias IN ({marks})", args)
    out.update(r[0] for r in rows)
    rows = conn.execute(
        f"SELECT alias FROM subject_aliases WHERE norm_subject IN ({marks})", args)
    out.update(r[0] for r in rows)
    return out


def _rows_by_norm(conn, norms: set, type_filter) -> list:
    if not norms:
        return []
    marks = ",".join("?" * len(norms))
    sql = f"SELECT * FROM artefacts WHERE norm_subject IN ({marks})"
    args = list(norms)
    if type_filter:
        sql += " AND type=?"
        args.append(type_filter)
    return conn.execute(sql + " ORDER BY seq DESC", args).fetchall()


def _like_clause(keys: set) -> tuple[str, list]:
    clauses = ["(norm_subject LIKE ? OR aliases LIKE ? OR path LIKE ?)"] * len(keys)
    args = [arg for k in keys for arg in (f"%{k}%",) * 3]
    return " OR ".join(clauses), args


def _rows_fts(conn, keys: set, type_filter) -> list:
    if _fts_on(conn):
        match = " OR ".join('"' + k.replace('"', " ") + '"' for k in keys)
        ids = [r[0] for r in conn.execute(
            "SELECT event_id FROM artefacts_fts WHERE artefacts_fts MATCH ?", (match,))]
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        sql = f"SELECT * FROM artefacts WHERE event_id IN ({marks})"
        args: list = list(ids)
    else:  # graceful LIKE fallback — still the fts tier, recorded in detail
        clause, args = _like_clause(keys)
        sql = f"SELECT * FROM artefacts WHERE ({clause})"
    if type_filter:
        sql += " AND type=?"
        args.append(type_filter)
    return conn.execute(sql + " ORDER BY seq DESC", args).fetchall()


def _lookup(conn, subject: str, type_filter) -> tuple[list, str]:
    """Tiered lookup: exact + alias unioned (exact rows first), fts when both
    empty. Basis is the strongest contributing tier — never "semantic"."""
    norm = normalise(subject)
    exact_rows = _rows_by_norm(conn, {norm} if norm else set(), type_filter)
    keys = _registered_keys(conn, expand(subject) or {norm})
    alias_rows = _rows_by_norm(conn, keys - {norm}, type_filter)
    if exact_rows or alias_rows:
        return exact_rows + alias_rows, "exact" if exact_rows else "alias"
    return _rows_fts(conn, keys or {norm}, type_filter), "fts"


def query_subject_basis(subject: str, type=None, store=None) -> tuple[QueryResult, str]:
    """query_subject + the lookup basis used ("exact"|"alias"|"fts")."""
    st = resolve_store(store)
    ok, detail, leaves, head = _verify_events(st)
    rows, basis = [], "exact"
    if (st / "index.db").exists():
        conn = _connect(st)
        conn.row_factory = sqlite3.Row
        try:
            rows, basis = _lookup(conn, subject, type)
            if basis == "fts" and not _fts_on(conn):
                detail = (detail + "; " if detail else "") + "fts5 unavailable; LIKE fallback"
        finally:
            conn.close()
    freshness = Freshness(
        verified=ok, index_head=head,
        event_count=len(leaves), checked_at=_now(), detail=detail,
    )
    result = QueryResult(
        subject=subject,
        artefacts=tuple(_row_to_artefact(r) for r in rows),
        freshness=freshness.to_dict(),
        inclusion_proof=_inclusion_proof(rows, leaves, head),
    )
    return result, basis


def query_subject(subject: str, type=None, store=None) -> QueryResult:
    return query_subject_basis(subject, type=type, store=store)[0]


def _inclusion_proof(rows: list, leaves: list, head: str) -> dict | None:
    """Proof for the newest row's event; `head` is root(state_from(leaves))."""
    if not rows or not leaves:
        return None
    newest = rows[0]["event_id"]
    if newest not in leaves:
        return None
    position = leaves.index(newest)
    return {
        "leaf": newest,
        "position": position,
        "proof": mmr.prove(leaves, position),
        "root": head,
    }


# --------------------------------------------------------- verify_artefact
def _hash_file(path: Path) -> str:
    try:
        if path.stat().st_size > _HASH_CAP_BYTES:
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _idr_leaf_found(idr_id: str, store: Path) -> bool | None:
    try:
        meta = json.loads((store / "meta.json").read_text(encoding="utf-8"))
        idr_path = Path(meta.get("sources", {}).get("idr") or "")
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not idr_path.is_file():
        return None
    needles = (f'"id":"{idr_id}"', f'"id": "{idr_id}"')
    with idr_path.open(encoding="utf-8") as fh:
        return any(needles[0] in line or needles[1] in line for line in fh)


def _blob_check(art: dict, file_present: bool, p: Path) -> bool | None:
    blob = art.get("blob_hash") or ""
    if not blob:
        return None
    return bool(file_present and _hash_file(p) == blob)


def _receipt_check(art: dict) -> bool | None:
    rp, rh = art.get("receipt_path"), art.get("receipt_hash")
    if not (rp and rh):
        return None
    rpath = Path(str(rp)).expanduser()
    return bool(rpath.is_file() and _hash_file(rpath) == rh)


def verify_artefact(artefact, store=None) -> dict:
    """Re-verify one artefact against disk + moat: the freshness half of F5."""
    art = artefact.to_dict() if hasattr(artefact, "to_dict") else dict(artefact)
    st = resolve_store(store)
    p = Path(str(art.get("path", ""))).expanduser()
    file_present = p.is_file()
    checks = {
        "file_present": file_present,
        "blob_hash_match": _blob_check(art, file_present, p),
        "receipt_hash_match": _receipt_check(art),
        "idr_leaf_found": _idr_leaf_found(art["idr_id"], st) if art.get("idr_id") else None,
    }
    ok = file_present and all(v is not False for v in checks.values())
    return {"ok": bool(ok), "checks": checks}


# ------------------------------------------------------------- extraction
# A path named in a record's text, in one of three forms. The first is a Windows
# path from a drive letter, with either separator: a folder name may hold single
# spaces (C:\Users\Jane Example\...), the file name may not. The other two are
# POSIX: absolute or home-relative, and relative with an extension.
_PATH_RE = re.compile(
    r"\b[A-Za-z]:[\\/](?:[\w.@-]+(?: [\w.@-]+)*[\\/])*[\w.@-]+"
    r"|(?:~/|/)[\w.@-]+(?:/[\w.@-]+)+|\b[\w-]+(?:/[\w.@-]+)+\.[A-Za-z0-9]{1,12}\b")
_RECEIPT_NAME_RE = re.compile(r"\b[\w.-]+\.receipt\.json\b")
_HASH_RE = re.compile(r"\b[0-9a-f]{40,64}\b")
_FH_TOKEN_RE = re.compile(r"\b[0-9a-f]{8,64}@v\d+\b")
_EXCLUDE_PARTS = ("/node_modules/", "/venv/", "/.venv/", "/__pycache__/", "/.git/",
                  "/usr/", "/opt/homebrew/", "/System/", "/dist/", "/build/")
_DRIVE_RE = re.compile(r"[A-Za-z]:[\\/]")
_TYPE_BY_EXT = {
    "mp4": "render", "mov": "render", "webm": "render", "gif": "render",
    "png": "render", "jpg": "render", "jpeg": "render", "svg": "render", "pdf": "render",
    "md": "doc", "txt": "doc", "rst": "doc",
    "py": "code", "ts": "code", "js": "code", "sh": "code", "go": "code", "rs": "code",
}
_ALIAS_TOKENS = tuple(sorted(
    {normalise(k) for k in DEFAULT_ALIASES}
    | {normalise(a) for vals in DEFAULT_ALIASES.values() for a in vals}
))


def _iter_strings(obj, depth: int = 0):
    if depth > 12:
        return
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_strings(v, depth + 1)


def _subject_from_name(name: str) -> str:
    # The file's own name, after the last separator of either kind. A Windows path
    # uses backslashes, and split on "/" alone it stayed whole, so a receipt found
    # on disk was filed under its full path and no claim ever matched it.
    base = re.split(r"[\\/]", name)[-1]
    if base.endswith(".receipt.json"):
        return base[: -len(".receipt.json")]
    base = re.sub(r"@v\d+$", "", base)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return stem or base


def _type_for(path: str) -> str:
    if path.endswith(".receipt.json"):
        return "receipt"
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _TYPE_BY_EXT.get(ext, "record")


def _file_artefact(path_str: str, ts: str, origin: str, idr_id: str | None) -> Artefact:
    p = Path(path_str).expanduser()
    blob = _hash_file(p) if p.is_file() else ""
    kind = _type_for(path_str)
    is_receipt = kind == "receipt"
    return Artefact(
        subject=_subject_from_name(path_str), type=kind, path=str(p),
        blob_hash=blob, created_at=ts, status=STATUS_ACTIVE,
        idr_id=idr_id, receipt_path=str(p) if is_receipt else None,
        receipt_hash=(blob or None) if is_receipt else None, source=origin,
    )


def _pointer_artefact(subject: str, ts: str, origin: str, rec_id: str,
                      idr_id: str | None) -> Artefact:
    return Artefact(
        subject=subject, type="record", path=f"{origin}:{rec_id}",
        blob_hash="", created_at=ts, status=STATUS_ACTIVE,
        idr_id=idr_id, source=origin,
    )


def _excluded(token: str) -> bool:
    """A path inside a dependency, build or system folder. The exclusions are written
    with "/", so a Windows path is compared in that form, and without case, because
    Windows folder names ignore case. A POSIX path is compared as written."""
    if _DRIVE_RE.match(token):
        folded = token.replace("\\", "/").lower()
        return any(x.lower() in folded for x in _EXCLUDE_PARTS)
    return any(x in token for x in _EXCLUDE_PARTS)


def _record_path_tokens(text: str) -> set:
    tokens = set(_PATH_RE.findall(text)) | set(_RECEIPT_NAME_RE.findall(text))
    tokens = {t for t in tokens if not _excluded(t)}
    # a bare filename is redundant when a longer captured token already ends
    # with it after either separator (the full path carries the information;
    # the name would only create a duplicate pointer artefact)
    return {t for t in tokens
            if not any(u != t and u.endswith(("/" + t, "\\" + t)) for u in tokens)}


def _path_token_artefact(token: str, ts: str, origin: str, rec_id: str,
                         idr_id: str | None) -> Artefact:
    p = Path(token).expanduser()
    if p.is_file():
        return _file_artefact(token, ts, origin, idr_id)
    return _pointer_artefact(_subject_from_name(token), ts, origin, rec_id, idr_id)


def _artefacts_from_record(rec: dict, origin: str,
                           fh_root=None) -> list[tuple[Artefact, tuple]]:
    """Rule-based extractor: path tokens, receipt names, file-history/hash
    tokens as aliases, alias-token mentions as moat-record pointers, and
    record-mentioned file-history blobs as subject-linked file artefacts."""
    rec_id = str(rec.get("id") or "")
    ts = str(rec.get("ts") or "")
    idr_id = rec_id if origin == "idr" else None
    text = " ".join(_iter_strings(rec))
    norm_text = f" {normalise(text)} "
    fh_tokens = set(_FH_TOKEN_RE.findall(text))
    extra = tuple(sorted(set(_HASH_RE.findall(text)) | fh_tokens))
    arts = [_path_token_artefact(t, ts, origin, rec_id, idr_id)
            for t in sorted(_record_path_tokens(text))]
    seen = {normalise(a.subject) for a in arts}
    mentioned = [t for t in _ALIAS_TOKENS if f" {t} " in norm_text and t not in seen]
    arts.extend(_pointer_artefact(t, ts, origin, rec_id, idr_id) for t in mentioned)
    arts.extend(_fh_linked_artefacts(fh_tokens, ts, origin, idr_id, fh_root, mentioned))
    return [(a, extra) for a in arts]


def _fh_linked_blobs(fh_tokens: set, fh_root) -> list:
    """Resolve record-mentioned file-history tokens to blobs on disk."""
    if not fh_root or not fh_tokens:
        return []
    root = Path(fh_root).expanduser()
    if not root.is_dir():
        return []
    candidates: list = []
    for token in sorted(fh_tokens):
        candidates.extend(root.glob(f"*/{token}"))
    return [b for b in sorted(candidates) if b.is_file()]


def _fh_linked_artefacts(fh_tokens: set, ts: str, origin: str,
                         idr_id: str | None, fh_root, subjects: list) -> list:
    """A moat record that mentions both a subject and a file-history blob
    token IS the subject-to-blob linkage: index the blob under that subject.
    Only blobs that exist on disk are linked — a link is never invented."""
    if not subjects:
        return []
    return [
        Artefact(
            subject=subjects[0], type="blob", path=str(blob),
            blob_hash=_hash_file(blob),
            created_at=ts or _mtime_iso(blob), status=STATUS_ACTIVE,
            idr_id=idr_id, source=origin,
        )
        for blob in _fh_linked_blobs(fh_tokens, fh_root)
    ]


def _scan_records(path, origin: str, fh_root=None):
    if not path or not Path(path).is_file():
        return
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            rec = _parse_json_line(line)
            if rec is not None:
                yield from _artefacts_from_record(rec, origin, fh_root)


def _parse_json_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    return rec if isinstance(rec, dict) else None


def _fh_blob_artefacts(entry: Path) -> list[tuple[Artefact, tuple]]:
    out = []
    for blob in sorted(p for p in entry.iterdir() if p.is_file()):
        art = Artefact(
            subject=entry.name, type="record", path=str(blob),
            blob_hash=_hash_file(blob), created_at=_mtime_iso(blob),
            status=STATUS_ACTIVE, source="file-history",
        )
        out.append((art, (blob.name.split("@", 1)[0],)))
    return out


def _walk_file_history(root):
    if not root or not Path(root).is_dir():
        return
    for entry in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        yield from _fh_blob_artefacts(entry)


_WALK_EXCLUDE = {"node_modules", "venv", ".venv", "__pycache__", ".git", "dist",
                 "build", "target", "vendor", "Pods", ".next", "file-history",
                 "embeddings"}


def _receipt_paths_under(root: Path) -> list[Path]:
    paths: list[Path] = []
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        if len(Path(dirpath).parts) - base_depth >= 6:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in _WALK_EXCLUDE]
        paths.extend(Path(dirpath) / f for f in filenames if f.endswith(".receipt.json"))
    return paths


def _glob_receipts(spec):
    """spec: iterable of explicit paths, or a root dir to walk (depth-capped)."""
    if spec is None:
        return
    if isinstance(spec, (list, tuple, set)):
        paths = [Path(p).expanduser() for p in spec]
    else:
        paths = _receipt_paths_under(Path(spec).expanduser())
    for p in paths:
        if p.is_file():
            yield _file_artefact(str(p), _mtime_iso(p), "receipt", None), ()


def _resolve_sources(sources: dict | None) -> dict:
    if sources is not None:
        return {k: sources.get(k) for k in ("idr", "context", "file_history", "receipts")}
    cd = get_claude_dir()
    return {
        "idr": cd / "state" / "precog" / "idr.jsonl",
        "context": cd / "state" / "precog" / "context.jsonl",
        "file_history": cd / "file-history",
        "receipts": cd,
    }


def _discovered(src: dict):
    fh_root = src["file_history"]
    yield from _scan_records(src["idr"], "idr", fh_root)
    yield from _scan_records(src["context"], "context", fh_root)
    yield from _walk_file_history(fh_root)
    yield from _glob_receipts(src["receipts"])


def build(store_dir=None, sources=None) -> dict:
    """Full rebuild of the materialised view from the moat sources."""
    st = resolve_store(store_dir)
    st.mkdir(parents=True, exist_ok=True)
    for name in ("events.jsonl", "mmr.json", "index.db", "meta.json", _VERIFY_MEMO):
        (st / name).unlink(missing_ok=True)
    src = _resolve_sources(sources)
    conn = _connect(st)
    fts = _fts_on(conn)
    conn.close()
    (st / "meta.json").write_text(json.dumps({
        "fts": fts, "built_at": _now(),
        "sources": {k: str(v) if v else None for k, v in src.items()},
    }, indent=2), encoding="utf-8")
    seen: set = set()
    count = 0
    for art, extra in _discovered(src):
        key = (normalise(art.subject), art.path)
        if key in seen:
            continue
        seen.add(key)
        append_event(IndexEvent(
            kind=EVENT_ARTEFACT_SEEN, ts=art.created_at or _now(),
            subject=art.subject,
            aliases=tuple(sorted(expand(art.subject) | set(extra))),
            artefact=art.to_dict(),
        ), store=st)
        count += 1
    return {"artefacts": count, "store": str(st),
            "root": mmr.root(_load_mmr(st)), "fts": fts}
