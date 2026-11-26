"""Incremental, public-document-only SQLite corpus and lexical retrieval."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from lib.claude_dir import get_claude_dir
from lib.path_encoding import project_dir
from lib.recall.normalise import fts_query

STORES = {"memory": "memory_dir", "rule": "rules_dir", "skill": "skills_dir",
          "doc": "docs_dir", "hal": "hal_memory_dir"}
PREFIXES = {"memory": "memory", "rule": "rules", "skill": "skills", "doc": "docs", "hal": "hal"}


@dataclass(frozen=True)
class Doc:
    id: str
    kind: str
    title: str
    body: str
    mtime_ns: int
    size: int
    sha256: str
    private: bool
    superseded_by: str | None
    links: tuple[str, ...]


def default_memory_dir(base: Path) -> Path:
    """The memory folder Claude Code keeps for the GRIP install at ``base``.

    Its name comes from path_encoding, which names it as Claude Code does. A
    name built by hand kept the drive and the backslashes on Windows, so the
    join dropped ``projects`` and recall read a stray folder (found 2026-11-26).
    """
    return project_dir(base.resolve(), base) / "memory"


@dataclass(frozen=True)
class CorpusRoots:
    memory_dir: Path | None = None
    rules_dir: Path | None = None
    skills_dir: Path | None = None
    docs_dir: Path | None = None
    hal_memory_dir: Path | None = None

    @classmethod
    def default(cls) -> CorpusRoots:
        base = get_claude_dir()
        defaults = (default_memory_dir(base), base / "rules", base / "skills",
                    base / "docs", base.parent / ".hal" / "memory")
        names = ("MEMORY", "RULES", "SKILLS", "DOCS", "HAL")
        paths = [Path(os.environ.get(f"GRIP_RECALL_{name}_DIR") or path).expanduser()
                 for name, path in zip(names, defaults)]
        return cls(*paths)


@dataclass(frozen=True)
class RefreshReport:
    added: int
    updated: int
    removed: int
    total: int
    epoch: int


@dataclass(frozen=True)
class Hit:
    id: str
    kind: str
    title: str
    score: float
    superseded_by: str | None


def _entries(path):
    try:
        with os.scandir(path) as entries:
            return sorted(entries, key=lambda e: e.name)
    except FileNotFoundError:
        return []


def _directories(root, kind):
    if kind == "doc":
        return [root / "doctrine", root / "measurements"]
    if kind == "skill":
        return [Path(e.path) for e in _entries(root) if e.is_dir(follow_symlinks=False)]
    return [root]


def _eligible(name, kind):
    if kind == "skill":
        return name == "SKILL.md"
    if kind == "memory" and (name == "MEMORY.md" or name.startswith("MEMORY-archive")):
        return False
    return name.endswith(".md")


def scan_store(root, kind):
    """Return relative path, path and stat; never open source contents."""
    if root is None:
        return []
    root = Path(root)
    rows = []
    for directory in _directories(root, kind):
        rows.extend(_rows_in(directory, root, kind))
    return rows


def _rows_in(directory, root, kind):
    """Stat one directory's eligible files; each file is visited once across the store.

    Every entry lies inside `root`, so its relative name is a slice of the entry's
    own path string. Building a Path per file and calling relative_to cost more
    than the stat itself, on every question (GRIP#8866)."""
    start = len(str(root)) + 1
    return [(entry.path[start:].replace(os.sep, "/"), entry.path, entry.stat())
            for entry in _entries(directory)
            if _eligible(entry.name, kind) and entry.is_file(follow_symlinks=False)]


def scan(roots):
    return {kind: scan_store(getattr(roots, field), kind) for kind, field in STORES.items()}


def store_vectors(roots, scanned):
    result = {}
    for kind, field in STORES.items():
        root = getattr(roots, field)
        rows = scanned[kind]
        manifest = [(name, st.st_mtime_ns, st.st_size) for name, _, st in rows]
        result[kind] = {
            "path": str(root) if root is not None else None,
            "available": root is not None and Path(root).is_dir(),
            "count": len(rows), "epoch": max((st.st_mtime_ns for _, _, st in rows), default=0),
            "size": sum(st.st_size for _, _, st in rows),
            "manifest": digest(manifest),
        }
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _scalar(value):
    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    return value.strip("\"'")


def _fields(lines):
    fields, section = {}, ""
    for line in lines:
        match = re.match(r"^(\s*)([\w_]+):\s*(.*?)\s*$", line)
        if not match:
            continue
        indent, name, raw = match.groups()
        if not indent:
            section = name if not raw else ""
        if not indent or (section == "metadata" and name == "private"):
            value = _scalar(raw)
            if name == "private":
                fields[name] = fields.get(name, False) or value.casefold() == "true"
            else:
                fields[name] = value
    return fields


def frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    return _fields(lines[1:end]), "\n".join(lines[end + 1:])


def heading(body):
    match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    return match.group(1).strip() if match else None


def read_doc(path, doc_id, kind, stat):
    data = path.read_bytes()
    fields, body = frontmatter(data.decode("utf-8", errors="replace"))
    title = fields.get("name") or heading(body) or path.stem
    body = fields.get("description", "") + "\n" + body
    targets = tuple(dict.fromkeys(_target(t, kind) for t in re.findall(r"\[\[([^\]]+)\]\]", body)))
    return Doc(doc_id, kind, title, body, stat.st_mtime_ns, stat.st_size,
               hashlib.sha256(data).hexdigest(), bool(fields.get("private")),
               fields.get("superseded_by") or None, targets)


def _target(reference, kind):
    target = reference.split("|", 1)[0].split("#", 1)[0].strip()
    if not target.endswith(".md"):
        target += ".md"
    if target.split("/", 1)[0] not in PREFIXES.values():
        target = PREFIXES[kind] + "/" + target
    return target


_DDL = (
    "CREATE TABLE IF NOT EXISTS docs(id TEXT PRIMARY KEY, kind, title, mtime_ns INTEGER, "
    "size INTEGER, sha256, superseded_by)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(id UNINDEXED, title, body, tokenize='porter unicode61')",
    "CREATE TABLE IF NOT EXISTS edges(src, dst, PRIMARY KEY(src,dst))",
    "CREATE TABLE IF NOT EXISTS link_refs(src, dst, PRIMARY KEY(src,dst))",
    "CREATE TABLE IF NOT EXISTS source_state(id PRIMARY KEY, mtime_ns INTEGER, size INTEGER, root)",
    "CREATE TABLE IF NOT EXISTS corpus_meta(key PRIMARY KEY, value)",
)


def connect(db_path):
    return sqlite3.connect(str(db_path))


def read_connect(db_path):
    return sqlite3.connect(_read_only_uri(Path(db_path).resolve()), uri=True)


def _read_only_uri(path):
    """The read-only SQLite URI of an absolute path.

    Path.as_uri() imports urllib.request, and http.client with it, which cost
    about 12 ms on every question (GRIP#8866). On POSIX the quoted path is the
    same URI. Windows keeps as_uri() for its drive-letter rules."""
    if os.name == "nt":
        return path.as_uri() + "?mode=ro"
    return "file://" + quote(str(path)) + "?mode=ro"


def _delete(conn, doc_id):
    for table, column in (("docs", "id"), ("docs_fts", "id"), ("link_refs", "src")):
        conn.execute(f"DELETE FROM {table} WHERE {column}=?", (doc_id,))


def _insert(conn, doc):
    conn.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?)",
                 (doc.id, doc.kind, doc.title, doc.mtime_ns, doc.size, doc.sha256, doc.superseded_by))
    conn.execute("INSERT INTO docs_fts VALUES (?,?,?)", (doc.id, doc.title, doc.body))
    conn.executemany("INSERT OR IGNORE INTO link_refs VALUES (?,?)", [(doc.id, t) for t in doc.links])


def _update_store(conn, kind, root, rows, old, seen):
    """Re-read only the files of one store whose (mtime, size, root) stamp changed."""
    for name, path, stat in rows:
        doc_id = PREFIXES[kind] + "/" + name
        seen.add(doc_id)
        stamp = (stat.st_mtime_ns, stat.st_size, root)
        if old.get(doc_id) == stamp:
            continue
        doc = read_doc(Path(path), doc_id, kind, stat)
        _delete(conn, doc_id)
        if not doc.private:
            _insert(conn, doc)
        conn.execute("INSERT OR REPLACE INTO source_state VALUES (?,?,?,?)", (doc_id, *stamp))


def _update_sources(conn, roots, scanned):
    old = {r[0]: tuple(r[1:]) for r in conn.execute("SELECT * FROM source_state")}
    seen = set()
    for kind, rows in scanned.items():
        _update_store(conn, kind, str(getattr(roots, STORES[kind])), rows, old, seen)
    for doc_id in old.keys() - seen:
        _delete(conn, doc_id)
        conn.execute("DELETE FROM source_state WHERE id=?", (doc_id,))


def _alias_names(doc_id):
    path = Path(doc_id)
    names = {doc_id, doc_id.split("/", 1)[1], path.name, path.stem}
    if path.name == "SKILL.md":
        names.add(path.parent.name)
    return names


def _add_aliases(aliases, doc_id):
    for name in _alias_names(doc_id):
        aliases.setdefault(name, set()).add(doc_id)


def _link_aliases(ids):
    aliases = {}
    for doc_id in sorted(ids):
        _add_aliases(aliases, doc_id)
    return aliases


def _resolve_target(target, ids, aliases):
    if target in ids:
        return target
    path = Path(target)
    candidates = aliases.get(path.name, set()) | aliases.get(path.stem, set())
    local = {i for i in candidates if i.split("/", 1)[0] == target.split("/", 1)[0]}
    candidates = local or candidates
    return next(iter(candidates)) if len(candidates) == 1 else None


def _resolve_edges(conn):
    ids = {row[0] for row in conn.execute("SELECT id FROM docs")}
    aliases = _link_aliases(ids)
    edges = []
    for source, target in conn.execute("SELECT src,dst FROM link_refs ORDER BY src,dst"):
        resolved = _resolve_target(target, ids, aliases)
        if resolved is not None:
            edges.append((source, resolved))
    conn.execute("DELETE FROM edges")
    conn.executemany("INSERT OR IGNORE INTO edges VALUES (?,?)", edges)


def refresh(db_path, roots) -> RefreshReport:
    scanned = scan(roots)
    vectors = store_vectors(roots, scanned)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as conn, conn:
        for ddl in _DDL:
            conn.execute(ddl)
        before = _document_stamps(conn)
        _update_sources(conn, roots, scanned)
        _resolve_edges(conn)
        after = _document_stamps(conn)
        conn.execute("INSERT OR REPLACE INTO corpus_meta VALUES ('sources',?)", (digest(vectors),))
        epoch = conn.execute("SELECT coalesce(max(mtime_ns),0) FROM docs").fetchone()[0]
    return RefreshReport(len(after.keys() - before.keys()),
                         sum(before[i] != after[i] for i in before.keys() & after.keys()),
                         len(before.keys() - after.keys()), len(after), epoch)


def _document_stamps(conn):
    return {row[0]: tuple(row[1:]) for row in conn.execute("SELECT id,mtime_ns,size,sha256 FROM docs")}


def indexed_state(db_path):
    with closing(read_connect(db_path)) as conn:
        row = conn.execute("SELECT value FROM corpus_meta WHERE key='sources'").fetchone()
        coverage = {kind: {"count": count, "epoch": epoch} for kind, count, epoch in
                    conn.execute("SELECT kind,count(*),max(mtime_ns) FROM docs GROUP BY kind")}
    return row[0] if row else None, coverage


def search(db_path, query, k=5) -> list[Hit]:
    match = fts_query(query)
    if match is None or k <= 0:
        return []
    with closing(read_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT d.id,d.kind,d.title,-bm25(docs_fts,0,10.0,1.0),d.superseded_by "
            "FROM docs_fts JOIN docs d ON d.id=docs_fts.id WHERE docs_fts MATCH ? "
            "ORDER BY bm25(docs_fts,0,10.0,1.0),d.id LIMIT ?", (match, k)).fetchall()
    return [Hit(*row) for row in rows]


def has_key(db_path, key) -> bool:
    with closing(read_connect(db_path)) as conn:
        if conn.execute("SELECT 1 FROM docs WHERE id=? OR substr(id,instr(id,'/')+1)=? LIMIT 1",
                        (key, key)).fetchone() is not None:
            return True
        if "/" in key or not key:
            return False
        return conn.execute("SELECT 1 FROM docs WHERE substr(id,-?)=? LIMIT 1",
                            (len(key) + 1, "/" + key)).fetchone() is not None


def graph(db_path):
    with closing(read_connect(db_path)) as conn:
        edges = conn.execute("SELECT src,dst FROM edges ORDER BY src,dst").fetchall()
        docs = {row[0]: Hit(*row) for row in conn.execute(
            "SELECT id,kind,title,0.0,superseded_by FROM docs ORDER BY id")}
    return edges, docs
