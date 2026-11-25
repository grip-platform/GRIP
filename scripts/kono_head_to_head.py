#!/usr/bin/env python3
"""KONO head-to-head: does KONO's recall find the right memory record better than keyword search?

Everything runs on this machine. A local model (Ollama) writes the test
questions, KONO runs against SCRATCH copies of its database, and the live
``embeddings.db`` is only ever read, once, through SQLite's backup API.

Stages, each run as its own process:

    corpus     list the eligible memory records and assign train or held-out
    protocol   freeze the pre-registered protocol and print its hash
    snapshot   copy the live KONO database into the scratch directory
    index      add every eligible record to that copy through KONO's own store path
    memonly    derive a second copy that holds only the memory records
    questions  ask the local model for two questions per record (resumable)
    vectors    embed records and questions for the variant retrievers
    score      run one retriever on one split
    report     compute the metrics and the pre-registered verdict

KONO resolves its database from CLAUDE_DIR when it is imported, so a stage
that loads KONO points CLAUDE_DIR at a scratch directory first and then checks
that every path KONO resolved is the scratch copy (``load_kono``). Whatever
KONO writes while it works (prompt cache, counters) lands in that copy.

Usage (run with the GRIP venv):
    grip-py scripts/kono_head_to_head.py --state <dir> <stage> [options]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from itertools import chain, product
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
from claude_dir import get_claude_dir  # noqa: E402

SPLIT_SALT = "kono-h2h-2026-10-17"
HELDOUT_BYTE_CUTOFF = 77  # first sha256 byte below this -> held-out (77/256, about 30%)
ORDER_SALT = "kono-h2h-order"
RECORD_TYPE = "factual"  # no row in the live store carries this type, so ids map cleanly
MIN_RECORD_CHARS = 120
INDEX_FILE = re.compile(r"^(MEMORY|D0)\b", re.IGNORECASE)
PRIVATE_LINE = re.compile(r"^\s*private:\s*true\s*$", re.IGNORECASE | re.MULTILINE)
MODIFIED_LINE = re.compile(r"^\s*modified:\s*['\"]?(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", re.MULTILINE)
TOKEN = re.compile(r"[a-z0-9][a-z0-9_.\-/]*[a-z0-9]")

OLLAMA = os.environ.get("KONO_H2H_OLLAMA", "http://127.0.0.1:11434")
QUESTION_MODEL = "qwen3:8b"
NOTE_CHARS = 6000
TOP_K = 3
POOL = 30  # candidates each arm hands to the hybrid fusion
RRF_K = 60
DOC_PREFIX, QUERY_PREFIX = "search_document: ", "search_query: "
CHUNK_CHARS, CHUNK_OVERLAP, MAX_CHUNKS = 1200, 200, 16
MAX_TERMS = 12
STYLES = ("question", "task")

BAR = {"hit_at_3_min": 0.70, "margin_over_rg_min": 0.20, "top5_share_max_exclusive": 0.10}
VECTOR_VARIANTS = ("vec_plain", "vec_prefix", "vec_chunks", "hybrid")

STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers him his how i if in into is it its itself just
me more most my no nor not now of off on once only or other our out over own same she should
so some such than that the their them then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your
yours get gets make makes need needs want way ways thing things something
""".split())

PROTOCOL = {
    "question": "Does KONO's recall find the memory record that answers a question better than "
                "keyword search over the memory folder?",
    "corpus": "every *.md in the memory folder except index and archive files (MEMORY*, D0), any file "
              "containing a 'private: true' line, and records under 120 characters of description plus body",
    "split": f"sha256('{SPLIT_SALT}:' + file name); first byte below {HELDOUT_BYTE_CUTOFF} is held-out "
             "(about 30%), the rest train",
    "questions": f"{QUESTION_MODEL} on the local Ollama, one call per record, temperature 0.4, seed from the "
                 "file name; one direct question and one task-style request per record; a pair failing "
                 "validation is retried once with seed+1, then dropped",
    "retrievers": {
        "kono_prod_full": "MemorySubstrate.recall(limit=3, min_fitness=0.1, expand_spatial=False), the recall "
                          "hook's own call, on a copy of the live store with every record added through "
                          "store_session_memory (type 'factual', created_at set to the record's modified time); "
                          "attestation is disabled so results cannot depend on question order",
        "kono_prod_memonly": "the same call on a copy holding only the memory records",
        "rg_keyword": "content words of the query (length 3+, stopwords removed, first 12); one "
                      "'rg -i -F -c' per term over the memory folder; files ranked by distinct terms "
                      "matched, then total matching lines, then name",
        "fts_bm25": "the same terms OR-ed into SQLite FTS5 (porter unicode61) over each record's full text, "
                    "ranked by bm25; a second lexical baseline, never a FIX candidate",
        "vec_plain": "cosine over nomic-embed-text vectors of each record's first 2,000 characters, KONO's "
                     "own truncation and endpoint, records only",
        "vec_prefix": "as vec_plain with nomic's task prefixes ('search_document: ', 'search_query: ')",
        "vec_chunks": "prefixed vectors of 1,200-character chunks (200 overlap, title on each, at most 16); "
                      "a record scores its best chunk",
        "hybrid": f"reciprocal rank fusion (k={RRF_K}, equal weights) of the top {POOL} from vec_chunks "
                  f"and the top {POOL} from fts_bm25",
    },
    "metrics": {
        "hit_at_3": "share of questions whose source record is among the top 3",
        "top5_share": "share of all returned result slots taken by the five most often returned documents",
        "mrr_at_3": "mean reciprocal rank within the top 3 (secondary)",
    },
    "bar": BAR,
    "verdict": {
        "KEEP": "kono_prod_full meets every bar on held-out: today's recall code works once the records "
                "are indexed",
        "FIX": "kono_prod_full fails, but kono_prod_memonly or the vector variant with the best TRAIN "
               "hit@3 (vec_plain, vec_prefix, vec_chunks, hybrid) meets every bar on held-out",
        "RETIRE": "no vector-based retriever meets the bar on held-out: retire the vector path and use "
                  "keyword search over the memory folder",
    },
    "held_out_rule": "held-out results are computed after the train-based choice is fixed, and the "
                     "harness refuses to overwrite them",
}


# --- small file helpers -------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def jsonl_lines(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def append_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(jsonl_lines(rows))


def write_jsonl(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(jsonl_lines(rows), encoding="utf-8")
    tmp.replace(path)


def stable_hash(salt: str, text: str) -> bytes:
    return hashlib.sha256(f"{salt}:{text}".encode("utf-8")).digest()


# --- memory records -----------------------------------------------------------

def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (fields, body). Reads only top-level name and description."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fields: dict = {}
    for line in text[3:end].splitlines():
        match = re.match(r"^(name|description)\s*:\s*(.*)$", line)
        if match and match.group(1) not in fields:
            fields[match.group(1)] = match.group(2).strip().strip("\"'")
    return fields, text[end + 4:].lstrip("\n")


def record_parts(path: Path) -> tuple[str, str, str]:
    fields, body = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    return fields.get("name") or path.stem, fields.get("description", ""), body.strip()


def record_text(path: Path) -> str:
    title, description, body = record_parts(path)
    return f"{title}\n{description}\n\n{body}".strip()


def exclusion_reason(path: Path) -> str | None:
    if INDEX_FILE.match(path.name):
        return "index"
    if PRIVATE_LINE.search(path.read_text(encoding="utf-8", errors="replace")):
        return "private"
    _, description, body = record_parts(path)
    if len(description) + len(body) < MIN_RECORD_CHARS:
        return "too-short"
    return None


def split_of(slug: str) -> str:
    return "heldout" if stable_hash(SPLIT_SALT, slug)[0] < HELDOUT_BYTE_CUTOFF else "train"


def record_date(path: Path) -> str:
    """The record's modified time as KONO stores created_at: 'YYYY-MM-DD HH:MM:SS' UTC."""
    match = MODIFIED_LINE.search(path.read_text(encoding="utf-8", errors="replace")[:3000])
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def chunks_of(title: str, body: str) -> list[str]:
    if len(body) <= CHUNK_CHARS:
        return [f"{title}\n{body}"]
    starts = range(0, len(body), CHUNK_CHARS - CHUNK_OVERLAP)
    return [f"{title}\n{body[s:s + CHUNK_CHARS]}" for s in starts][:MAX_CHUNKS]


def indexed_chunks(item: tuple[int, Path]) -> list[tuple[int, str]]:
    index, path = item
    title, _, body = record_parts(path)
    return [(index, chunk) for chunk in chunks_of(title, body)]


def query_terms(text: str) -> list[str]:
    kept = [t for t in TOKEN.findall(text.lower()) if len(t) >= 3 and t not in STOPWORDS]
    return list(dict.fromkeys(kept))[:MAX_TERMS]


# --- KONO, loaded against a scratch copy only --------------------------------

def assert_scratch_paths(expected: Path, resolved: dict) -> None:
    """Exit unless every path KONO resolved is ``expected``."""
    for label, path in resolved.items():
        if Path(path).resolve() != expected.resolve():
            raise SystemExit(f"refusing: {label} resolves {path}, not the scratch copy {expected}")


def load_kono(root: Path):
    """Import KONO with CLAUDE_DIR pointed at ``root``, and prove the redirect took.

    KONO reads CLAUDE_DIR once, at import, so this must run before any KONO
    module is imported in this process. Fails closed: if any database path KONO
    resolved is not ``root/embeddings.db``, the process exits before a query or
    a write can reach another database.
    """
    target = root / "embeddings.db"
    if not target.exists():
        raise SystemExit(f"refusing: {target} does not exist; run the stage that creates it first")
    if any(name in sys.modules for name in ("embeddings", "kono_substrate")):
        raise SystemExit("refusing: KONO was imported before CLAUDE_DIR was redirected")
    os.environ["CLAUDE_DIR"] = str(root)
    os.environ.pop("KONO_SEARCH_TELEMETRY", None)
    engine_module = importlib.import_module("embeddings.engine")
    substrate = importlib.import_module("kono_substrate")
    engine = importlib.import_module("embeddings").get_engine()
    assert_scratch_paths(target, {
        "embeddings.engine.DB_PATH": engine_module.DB_PATH,
        "kono_substrate.DB_PATH": substrate.DB_PATH,
        "engine.db_path": engine.db_path,
    })
    return engine, substrate


def backup_copy(source: Path, dest: Path) -> None:
    if dest.exists():
        raise SystemExit(f"refusing: {dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    writer = sqlite3.connect(dest)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()


def count_rows(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {
            "documents": conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0],
            "records": conn.execute("SELECT COUNT(*) FROM embeddings WHERE type = ?", (RECORD_TYPE,)).fetchone()[0],
            "vectors": conn.execute("SELECT COUNT(*) FROM vec_embeddings_rowids").fetchone()[0],
        }
    finally:
        conn.close()


# --- stages: corpus, protocol, snapshot, index, memonly ------------------------

def cmd_corpus(args) -> None:
    rows, skipped = [], Counter()
    for path in sorted(args.memory_dir.glob("*.md")):
        reason = exclusion_reason(path)
        if reason:
            skipped[reason] += 1
            continue
        rows.append({"slug": path.name, "split": split_of(path.name), "date": record_date(path),
                     "chars": len(record_text(path))})
    write_jsonl(args.state / "corpus.jsonl", rows)
    splits = Counter(r["split"] for r in rows)
    print(json.dumps({"records": len(rows), **splits, "skipped": dict(skipped)}))


def cmd_protocol(args) -> None:
    path = args.state / "protocol.json"
    if path.exists():
        raise SystemExit(f"refusing: {path} is frozen already")
    path.write_text(json.dumps(PROTOCOL, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "protocol_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }))


def cmd_snapshot(args) -> None:
    dest = args.state / "full" / "embeddings.db"
    backup_copy(args.live_db, dest)
    print(json.dumps({"snapshot": str(dest), **count_rows(dest)}))


def store_outcome(emb_id, before: int, owners: dict) -> str:
    if emb_id is None:
        return "refused"
    if emb_id <= before:
        return "merged-into-existing"
    return "merged" if emb_id in owners else "stored"


def cmd_index(args) -> None:
    root = args.state / "full"
    _, substrate = load_kono(root)
    memory = substrate.MemorySubstrate(db_path=root / "embeddings.db")
    conn = memory.connect()
    before = list(conn.execute("SELECT COALESCE(MAX(id), 0) FROM embeddings"))[0][0]
    owners: dict = {}
    mapping = []
    for row in read_jsonl(args.state / "corpus.jsonl"):
        emb_id = memory.store_session_memory(record_text(args.memory_dir / row["slug"]), memory_type=RECORD_TYPE)
        outcome = store_outcome(emb_id, before, owners)
        if outcome == "stored":
            owners[emb_id] = row["slug"]
            conn.execute("UPDATE embeddings SET created_at = ? WHERE id = ?", (row["date"], emb_id))
        mapping.append({"slug": row["slug"], "id": emb_id, "status": outcome})
    write_jsonl(args.state / "mapping.jsonl", mapping)
    print(json.dumps({**Counter(m["status"] for m in mapping), **count_rows(root / "embeddings.db")}))


def cmd_memonly(args) -> None:
    dest = args.state / "memonly" / "embeddings.db"
    backup_copy(args.state / "full" / "embeddings.db", dest)
    _, substrate = load_kono(dest.parent)
    conn = substrate.MemorySubstrate(db_path=dest).connect()
    # The live delete trigger reads the removed row back from the content table, which no longer
    # holds it; drop it for the bulk delete, rebuild the keyword index from what is left, restore it.
    trigger = list(conn.execute("SELECT sql FROM sqlite_master WHERE name = 'embeddings_fts_delete'"))
    conn.execute("DROP TRIGGER IF EXISTS embeddings_fts_delete")
    conn.execute("DELETE FROM embeddings WHERE type != ?", (RECORD_TYPE,))
    conn.execute("DELETE FROM vec_embeddings WHERE rowid NOT IN (SELECT id FROM embeddings)")
    conn.execute("INSERT INTO embeddings_fts(embeddings_fts) VALUES('rebuild')")
    if trigger:
        conn.execute(trigger[0][0])
    print(json.dumps(count_rows(dest)))


# --- stage: questions -------------------------------------------------------------

QUESTION_SYSTEM = ("You write test items for a retrieval experiment. You read one engineering note from a "
                   "software project called GRIP and reply with JSON only.")

QUESTION_PROMPT = """Note:
<<<
{note}
>>>

Write two things a GRIP engineer might type, each of which this note would help with.

"question": a direct question whose answer is in the note. 8 to 30 words.
"task": a request or a situation, typed by an engineer who does not know this note exists, where the note's lesson would change what they do. 8 to 30 words. Do not state the lesson or the answer.

Do not mention a note, record or memory. Do not copy the note's title or any of its sentences. Use your own words, but keep a technical name such as a command, file or tool when an engineer would naturally type it."""

QUESTION_SCHEMA = {
    "type": "object",
    "properties": {"question": {"type": "string"}, "task": {"type": "string"}},
    "required": ["question", "task"],
}

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ollama_post(route: str, body: dict, timeout: int = 300) -> dict:
    request = urllib.request.Request(f"{OLLAMA}{route}", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with _NO_PROXY.open(request, timeout=timeout) as response:
        return json.load(response)


def ask_model(note: str, seed: int) -> dict:
    reply = ollama_post("/api/chat", {
        "model": QUESTION_MODEL, "stream": False, "think": False, "format": QUESTION_SCHEMA,
        "options": {"temperature": 0.4, "seed": seed, "num_ctx": 8192, "num_predict": 300},
        "messages": [{"role": "system", "content": QUESTION_SYSTEM},
                     {"role": "user", "content": QUESTION_PROMPT.format(note=note[:NOTE_CHARS])}],
    })
    return json.loads(reply["message"]["content"])


def valid_text(text, stem: str) -> bool:
    return isinstance(text, str) and 5 <= len(text.split()) <= 45 and stem not in text.lower()


def valid_pair(pair: dict, slug: str) -> bool:
    stem = Path(slug).stem.lower()
    question, task = pair.get("question"), pair.get("task")
    return valid_text(question, stem) and valid_text(task, stem) and question.strip() != task.strip()


def items_from(pair: dict, row: dict) -> list[dict]:
    return [{"qid": f"{row['slug']}#{style}", "slug": row["slug"], "split": row["split"],
             "style": style, "text": pair[style].strip()} for style in STYLES]


def ask_valid(note: str, seed: int, slug: str) -> dict | None:
    try:
        pair = ask_model(note, seed)
    except (OSError, ValueError, KeyError):
        return None
    return pair if valid_pair(pair, slug) else None


def question_items(memory_dir: Path, row: dict) -> list[dict]:
    note = record_text(memory_dir / row["slug"])
    seed = int.from_bytes(stable_hash("seed", row["slug"])[:4], "big")
    pair = ask_valid(note, seed, row["slug"]) or ask_valid(note, seed + 1, row["slug"])
    return items_from(pair, row) if pair else []


def cmd_questions(args) -> None:
    out = args.state / "questions.jsonl"
    done = {q["slug"] for q in read_jsonl(out)}
    todo = [r for r in read_jsonl(args.state / "corpus.jsonl") if r["slug"] not in done]
    todo.sort(key=lambda r: stable_hash(ORDER_SALT, r["slug"]))  # a partial run is a random sample
    if args.limit:
        todo = todo[:args.limit]
    lock, started = threading.Lock(), time.time()

    def work(row: dict) -> bool:
        items = question_items(args.memory_dir, row)
        with lock:
            append_jsonl(out if items else args.state / "questions-failed.jsonl", items or [{"slug": row["slug"]}])
        return bool(items)

    ok = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for count, success in enumerate(pool.map(work, todo), 1):
            ok += success
            if count % 25 == 0 or count == len(todo):
                rate = count / max(time.time() - started, 1e-9)
                print(json.dumps({"done": count, "of": len(todo), "ok": ok, "per_minute": round(rate * 60, 1)}),
                      flush=True)


# --- stage: vectors -----------------------------------------------------------------

def cmd_vectors(args) -> None:
    import numpy as np
    engine, _ = load_kono(args.state / "memonly")

    def embed(text: str):
        return np.frombuffer(engine.embed_text(text, use_cache=False), dtype=np.float32)

    corpus = read_jsonl(args.state / "corpus.jsonl")
    questions = read_jsonl(args.state / "questions.jsonl")
    paths = [args.memory_dir / r["slug"] for r in corpus]
    pairs = list(chain.from_iterable(map(indexed_chunks, enumerate(paths))))
    np.savez(
        args.state / "vectors.npz",
        rec_plain=np.stack([embed(record_text(p)) for p in paths]),
        rec_prefix=np.stack([embed(DOC_PREFIX + record_text(p)) for p in paths]),
        chunks=np.stack([embed(DOC_PREFIX + chunk) for _, chunk in pairs]),
        chunk_owner=np.array([index for index, _ in pairs]),
        q_plain=np.stack([embed(q["text"]) for q in questions]),
        q_prefix=np.stack([embed(QUERY_PREFIX + q["text"]) for q in questions]),
    )
    write_jsonl(args.state / "vectors-order.jsonl", [{"slugs": [r["slug"] for r in corpus],
                                                       "qids": [q["qid"] for q in questions]}])
    print(json.dumps({"records": len(paths), "chunks": len(pairs), "questions": len(questions)}))


# --- retrievers ---------------------------------------------------------------------

def result_row(question: dict, ranked: list[dict], **extra) -> dict:
    docs = [r["doc"] for r in ranked]
    rank = docs.index(question["slug"]) + 1 if question["slug"] in docs else None
    return {"qid": question["qid"], "slug": question["slug"], "split": question["split"],
            "style": question["style"], "ranked": ranked, "rank": rank, **extra}


def other_label(conn, emb_id) -> str:
    found = list(conn.execute("SELECT type FROM embeddings WHERE id = ?", (emb_id,)))
    return f"other:{found[0][0] if found else '?'}:{emb_id}"


def kono_ranked(hits: list[dict], owner: dict, conn) -> list[dict]:
    return [{"doc": owner.get(h.get("id")) or other_label(conn, h.get("id")), "score": h.get("score")}
            for h in hits[:TOP_K]]


KONO_KNOBS = {"nodecay": frozenset({"nodecay"}), "nofac": frozenset({"nofac"}),
              "bare": frozenset({"nodecay", "nofac"})}


def retrieve_kono(args, root_name: str, questions: list[dict], knobs: frozenset = frozenset()) -> list[dict]:
    """KONO's production recall; ``knobs`` switch off the recency decay and/or the structural re-rank."""
    root = args.state / root_name
    if "nofac" in knobs:
        os.environ["GRIP_SMT_FAC"] = "0"  # KONO's own kill switch for the structural re-rank
    engine, substrate = load_kono(root)
    if "nodecay" in knobs:  # no switch exists; recall imports this name at call time, so patch the module
        importlib.import_module("embeddings.search").apply_recency_decay = lambda results, *a, **k: results
    substrate.MemorySubstrate.attest_recalled = lambda self, *a, **k: None  # order-independent scoring
    memory = substrate.MemorySubstrate(db_path=root / "embeddings.db")
    conn = memory.connect()
    owner = {m["id"]: m["slug"] for m in read_jsonl(args.state / "mapping.jsonl") if m["status"] == "stored"}
    keyword_arm = importlib.import_module("embeddings.search")._keyword_search_fts5
    rows = []
    for question in questions:
        hits = memory.recall(query=question["text"], limit=TOP_K, min_fitness=0.1, expand_spatial=False)
        fts_hits = len(keyword_arm(engine, question["text"], limit=TOP_K * 3))
        rows.append(result_row(question, kono_ranked(hits, owner, conn), fts_hits=fts_hits))
    return rows


def rg_counts(term: str, memory_dir: Path, allowed: set) -> dict:
    """Matching-line counts per allowed record for one literal, case-insensitive term."""
    done = subprocess.run(["rg", "--no-config", "--no-ignore", "-i", "-F", "-c", "-g", "*.md", "-e", term,
                           str(memory_dir)], capture_output=True, text=True, timeout=120)
    counts = {}
    for line in done.stdout.splitlines():
        path, _, count = line.rpartition(":")
        if Path(path).name in allowed:
            counts[Path(path).name] = int(count)
    return counts


def rg_ranked(text: str, memory_dir: Path, allowed: set) -> list[dict]:
    per_term = [rg_counts(term, memory_dir, allowed) for term in query_terms(text)]
    distinct = sum((Counter(dict.fromkeys(counts, 1)) for counts in per_term), Counter())
    total = sum((Counter(counts) for counts in per_term), Counter())
    order = sorted(distinct, key=lambda s: (-distinct[s], -total[s], s))[:TOP_K]
    return [{"doc": s, "score": distinct[s] + total[s] / 1e6} for s in order]


def retrieve_rg(args, questions: list[dict]) -> list[dict]:
    allowed = {r["slug"] for r in read_jsonl(args.state / "corpus.jsonl")}
    with ThreadPoolExecutor(max_workers=4) as pool:
        ranked = list(pool.map(lambda q: rg_ranked(q["text"], args.memory_dir, allowed), questions))
    return [result_row(q, r) for q, r in zip(questions, ranked)]


def bm25_index(args) -> tuple[sqlite3.Connection, list[str]]:
    slugs = [r["slug"] for r in read_jsonl(args.state / "corpus.jsonl")]
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE docs USING fts5(body, tokenize='porter unicode61')")
    db.executemany("INSERT INTO docs(rowid, body) VALUES (?, ?)",
                   [(i, record_text(args.memory_dir / s)) for i, s in enumerate(slugs)])
    return db, slugs


def bm25_ranked(db: sqlite3.Connection, slugs: list[str], text: str, limit: int) -> list[dict]:
    terms = query_terms(text)
    if not terms:
        return []
    match = " OR ".join('"' + term.replace('"', " ") + '"' for term in terms)
    rows = db.execute("SELECT rowid, bm25(docs) FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT ?",
                      (match, limit)).fetchall()
    return [{"doc": slugs[rowid], "score": -score} for rowid, score in rows]


def unit(matrix):
    import numpy as np
    return matrix / np.clip(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12, None)


def vector_scores(args, name: str):
    """Question-by-record cosine matrix for one vector variant, with the record and question order."""
    import numpy as np
    data = np.load(args.state / "vectors.npz")
    order = read_jsonl(args.state / "vectors-order.jsonl")[0]
    if name == "vec_plain":
        scores = unit(data["q_plain"]) @ unit(data["rec_plain"]).T
    elif name == "diag_l2":
        # KONO's own ordering: raw L2 distance between unnormalised vectors (negated, so higher is closer).
        q, d = data["q_plain"], data["rec_plain"]
        scores = 2 * q @ d.T - (q * q).sum(axis=1)[:, None] - (d * d).sum(axis=1)[None, :]
    elif name == "vec_prefix":
        scores = unit(data["q_prefix"]) @ unit(data["rec_prefix"]).T
    else:
        per_chunk = unit(data["q_prefix"]) @ unit(data["chunks"]).T
        starts = np.flatnonzero(np.r_[True, np.diff(data["chunk_owner"]) != 0])
        scores = np.maximum.reduceat(per_chunk, starts, axis=1)
    return scores, order["slugs"], {qid: i for i, qid in enumerate(order["qids"])}


def top_of(row, slugs: list[str], limit: int) -> list[dict]:
    import numpy as np
    best = np.argsort(-row, kind="stable")[:limit]
    return [{"doc": slugs[i], "score": float(row[i])} for i in best]


def rrf_scores(ranked: list[dict]) -> Counter:
    return Counter({item["doc"]: 1.0 / (RRF_K + position) for position, item in enumerate(ranked, 1)})


def fuse(lists: list[list[dict]], limit: int) -> list[dict]:
    scores = sum((rrf_scores(ranked) for ranked in lists), Counter())
    order = sorted(scores, key=lambda d: (-scores[d], d))[:limit]
    return [{"doc": d, "score": scores[d]} for d in order]


def retrieve_local(args, name: str, questions: list[dict]) -> list[dict]:
    db, bm25_slugs = bm25_index(args) if name in ("fts_bm25", "hybrid") else (None, None)
    vector_name = "vec_chunks" if name == "hybrid" else name
    scores, slugs, qindex = vector_scores(args, vector_name) if name != "fts_bm25" else (None, None, None)
    rows = []
    for question in questions:
        lexical = bm25_ranked(db, bm25_slugs, question["text"], POOL) if db else []
        vector = top_of(scores[qindex[question["qid"]]], slugs, POOL) if scores is not None else []
        ranked = fuse([vector, lexical], TOP_K) if name == "hybrid" else (lexical or vector)[:TOP_K]
        rows.append(result_row(question, ranked))
    return rows


def full_store_vectors(args):
    """Every vector in the production-shaped copy, labelled as the KONO retrievers label results."""
    import numpy as np
    conn = sqlite3.connect(f"file:{args.state / 'full' / 'embeddings.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, type, vector FROM embeddings ORDER BY id").fetchall()
    finally:
        conn.close()
    owner = {m["id"]: m["slug"] for m in read_jsonl(args.state / "mapping.jsonl") if m["status"] == "stored"}
    labels = [owner.get(emb_id) or f"other:{kind}:{emb_id}" for emb_id, kind, _ in rows]
    return np.stack([np.frombuffer(vector, dtype=np.float32) for _, _, vector in rows]), labels


def retrieve_full(args, name: str, questions: list[dict]) -> list[dict]:
    """KONO's raw vector arm over the whole copy, ordered by raw L2 (its own ordering) or by cosine."""
    import numpy as np
    matrix, labels = full_store_vectors(args)
    qindex = {qid: i for i, qid in enumerate(read_jsonl(args.state / "vectors-order.jsonl")[0]["qids"])}
    picked = np.load(args.state / "vectors.npz")["q_plain"][[qindex[q["qid"]] for q in questions]]
    if name == "diag_full_l2":  # the per-question |q|^2 term is constant across documents, so it is dropped
        scores = 2 * picked @ matrix.T - (matrix * matrix).sum(axis=1)[None, :]
    else:
        scores = unit(picked) @ unit(matrix).T
    return [result_row(q, top_of(row, labels, TOP_K)) for q, row in zip(questions, scores)]


RETRIEVERS = ("kono_prod_full", "kono_prod_memonly", "rg_keyword", "fts_bm25") + VECTOR_VARIANTS
# Added after H866 was registered, as diagnostics: reported on their own, never in the verdict.
EXPLORATORY = ("diag_l2", "diag_full_l2", "diag_full_cos",
               "diag_kono_full_nodecay", "diag_kono_full_nofac", "diag_kono_full_bare")


def cmd_score(args) -> None:
    out_dir = args.state / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{args.retriever}.{args.split}.jsonl"
    if args.split == "heldout" and out.exists():
        raise SystemExit(f"refusing: held-out results {out} already exist and are scored once")
    questions = [q for q in read_jsonl(args.state / "questions.jsonl") if q["split"] == args.split]
    if args.retriever.startswith("kono_prod_"):
        rows = retrieve_kono(args, args.retriever.removeprefix("kono_prod_"), questions)
    elif args.retriever == "rg_keyword":
        rows = retrieve_rg(args, questions)
    elif args.retriever.startswith("diag_full_"):
        rows = retrieve_full(args, args.retriever, questions)
    elif args.retriever.startswith("diag_kono_full_"):
        rows = retrieve_kono(args, "full", questions, KONO_KNOBS[args.retriever.removeprefix("diag_kono_full_")])
    else:
        rows = retrieve_local(args, args.retriever, questions)
    write_jsonl(out, rows)
    print(json.dumps({"retriever": args.retriever, "split": args.split, **summarise(rows)}))


# --- stage: report ------------------------------------------------------------------

def wilson(hits: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    p = hits / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return round(centre - spread, 4), round(centre + spread, 4)


def docs_of(row: dict) -> list[str]:
    return [item["doc"] for item in row["ranked"]]


def style_rates(rows: list[dict]) -> dict:
    asked, found = Counter(), Counter()
    for row in rows:
        asked[row["style"]] += 1
        found[row["style"]] += bool(row["rank"])
    return {style: round(found[style] / asked[style], 4) for style in sorted(asked)}


def summarise(rows: list[dict]) -> dict:
    total = len(rows)
    hits = sum(1 for r in rows if r["rank"])
    slots = Counter(chain.from_iterable(map(docs_of, rows)))
    filled = sum(slots.values())
    return {
        "n": total,
        "hit_at_3": round(hits / total, 4) if total else 0.0,
        "hit_at_3_ci95": wilson(hits, total),
        "mrr_at_3": round(sum(1 / r["rank"] for r in rows if r["rank"]) / total, 4) if total else 0.0,
        "top5_share": round(sum(c for _, c in slots.most_common(5)) / filled, 4) if filled else 0.0,
        "by_style": style_rates(rows),
    }


def passes(candidate: dict, keyword: dict) -> bool:
    # Rates are rounded to 4 places; round the margin too, or 0.70 - 0.50 = 0.19999999999999996
    # would fail a bar it meets exactly.
    margin = round(candidate["hit_at_3"] - keyword["hit_at_3"], 4)
    return (candidate["hit_at_3"] >= BAR["hit_at_3_min"]
            and margin >= BAR["margin_over_rg_min"]
            and candidate["top5_share"] < BAR["top5_share_max_exclusive"])


def verdict(train: dict, heldout: dict) -> dict:
    keyword = heldout["rg_keyword"]
    if passes(heldout["kono_prod_full"], keyword):
        return {"verdict": "KEEP", "by": "kono_prod_full"}
    best = max(VECTOR_VARIANTS, key=lambda n: (train[n]["hit_at_3"], -train[n]["top5_share"]))
    for candidate in ("kono_prod_memonly", best):
        if passes(heldout[candidate], keyword):
            return {"verdict": "FIX", "by": candidate, "best_train_variant": best}
    return {"verdict": "RETIRE", "best_train_variant": best}


def summaries_for(args, names: tuple) -> dict:
    found: dict = {"train": {}, "heldout": {}}
    for split, name in product(found, names):
        path = args.state / "results" / f"{name}.{split}.jsonl"
        if path.exists():
            found[split][name] = summarise(read_jsonl(path))
    return found


def cmd_report(args) -> None:
    summaries = summaries_for(args, RETRIEVERS)
    complete = all(len(found) == len(RETRIEVERS) for found in summaries.values())
    report = {"summaries": summaries, "exploratory": summaries_for(args, EXPLORATORY), "bar": BAR,
              "verdict": verdict(summaries["train"], summaries["heldout"]) if complete else "INCOMPLETE"}
    (args.state / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["verdict"]))


# --- entry point --------------------------------------------------------------------

def default_paths() -> tuple[Path, Path, Path]:
    home = get_claude_dir().resolve()
    slug = str(home).replace("/", "-").replace(".", "-")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return home / "state" / f"kono-trial-{day}", home / "projects" / slug / "memory", home / "embeddings.db"


def parse_args(argv: list[str]) -> argparse.Namespace:
    state, memory_dir, live_db = default_paths()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, default=state, help="scratch directory for this trial")
    parser.add_argument("--memory-dir", type=Path, default=memory_dir, help="the memory folder")
    parser.add_argument("--live-db", type=Path, default=live_db, help="live KONO database (read only)")
    stages = parser.add_subparsers(dest="stage", required=True)
    for name in ("corpus", "protocol", "snapshot", "index", "memonly", "vectors", "report"):
        stages.add_parser(name)
    questions = stages.add_parser("questions")
    questions.add_argument("--workers", type=int, default=2)
    questions.add_argument("--limit", type=int, default=0, help="stop after this many records (0 = all)")
    score = stages.add_parser("score")
    score.add_argument("--retriever", choices=RETRIEVERS + EXPLORATORY, required=True)
    score.add_argument("--split", choices=("train", "heldout"), required=True)
    return parser.parse_args(argv)


STAGES = {"corpus": cmd_corpus, "protocol": cmd_protocol, "snapshot": cmd_snapshot, "index": cmd_index,
          "memonly": cmd_memonly, "questions": cmd_questions, "vectors": cmd_vectors, "score": cmd_score,
          "report": cmd_report}


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.state.mkdir(parents=True, exist_ok=True)
    STAGES[args.stage](args)


if __name__ == "__main__":
    main()
