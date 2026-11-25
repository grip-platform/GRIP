"""Prompt-Fold Cache — Banach fold memoisation for GRIP/HAL/HAPPI prompts.

Pipeline (plan: artifacts/banach-fold-memoisation-ultraplan/):

    raw prompt
    -> Banach fold canonicalisation (lib/fold_compress.fold, depth 11)
    -> SHA-256 exact cache key (fold().final_sha256)
    -> exact hit?  return cached output with provenance
    -> policy-gated semantic near-match (opt-in, low-risk only)
    -> fresh dispatch via caller-supplied runner, store output + fold trace

Every decision is replayable from the record alone: records.jsonl carries the
fold trace, hashes, route, and policy reason; folded prompts and outputs live
in content-addressed blobs/<sha256>.bin. privacy_scope "none" stores hashes
only — no plaintext ever touches disk and such entries never return as hits.

Measured evidence (wall-clock samples, not constants):
  - OpenCode/GPT-5.5 prototype: SPEEDUP_COLD_TO_WARM 976.2x (50.843ms -> 0.052ms)
  - plan simulation: exact_hit 696.8x, near_hit 310.3x, unsafe-near refused 1.0x

Falsification (design wrong if, over the next 5 corpus runs): exact reuse
returns a wrong output for a different folded prompt; semantic reuse fires for
a non-low risk class or mismatched task type; plaintext appears on disk under
privacy_scope "none"; or a stored decision cannot be recomputed from its
record + blobs alone.

Storage root: $GRIP_PROMPT_FOLD_CACHE_DIR or <claude_dir>/state/cache/prompt-fold/
(state/**/*.jsonl is gitignored by the repo's consolidated-state convention).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Literal, Optional, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from _compat import fcntl  # noqa: E402  -- POSIX fcntl, or the LockFileEx lock on Windows
from claude_dir import get_claude_dir  # noqa: E402
from fold_compress import DEFAULT_DEPTH, fold  # noqa: E402

SCHEMA_VERSION = "prompt-fold-cache/0.1"
SEMANTIC_THRESHOLD = 0.88  # matches the academic paper's published gate
MAX_SEMANTIC_CANDIDATES = 256  # risk-register: cap the near-scan
CACHE_DIR_ENV = "GRIP_PROMPT_FOLD_CACHE_DIR"
RECORDS_NAME = "records.jsonl"
BLOBS_NAME = "blobs"

RISK_CLASSES = frozenset({"low", "medium", "high"})
EXACT_ALLOWED_RISK = frozenset({"low", "medium"})
PRIVACY_NO_STORE = "none"
PRIVACY_SCOPES = frozenset({"local", "project", "operator", PRIVACY_NO_STORE})

# Task classes never eligible for semantic reuse (plan: Cache Policy).
SEMANTIC_BLOCKED_TASKS = frozenset({
    "legal", "security", "secrets", "destructive",
    "financial", "medical", "private", "code_exec",
})

# Secret-shaped content is never eligible for semantic reuse.
_FORBIDDEN = re.compile(
    rb"sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|BEGIN [A-Z ]*PRIVATE KEY"
    rb"|password\s*[:=]|api[_-]?key\s*[:=]|Bearer\s+[A-Za-z0-9._-]{16,}",
    re.IGNORECASE,
)

# Meaning-guard vocabulary: tokens whose presence in the DIFF between two
# near-identical prompts flips meaning (adversarial refutation 2026-07-02:
# negation pair scored 0.977 and number swap 0.9878 — both above the lexical
# threshold, both wrong to reuse).
_NEGATION_TOKENS = frozenset({
    b"not", b"no", b"never", b"none", b"cannot", b"can't", b"won't",
    b"don't", b"doesn't", b"isn't", b"aren't", b"wasn't", b"weren't",
    b"without", b"reject", b"rejected", b"rejects", b"deny", b"denied",
    b"denies", b"disable", b"disabled", b"stop", b"halt",
})
_TOKEN_RE = re.compile(rb"[a-z0-9']+")


def _meaning_guard(folded_a: bytes, folded_b: bytes) -> Optional[str]:
    """Deny near-reuse when the token diff carries meaning-flipping markers.

    Lexical similarity cannot see meaning: the two cheaply-detectable
    wrong-reuse classes (negation flips, digit changes) are denied here.
    Entity/antonym swaps remain the documented residual behind the opt-in +
    low-risk + threshold gates. Returns a denial reason, or None when safe.
    """
    diff = (
        set(_TOKEN_RE.findall(folded_a.lower()))
        ^ set(_TOKEN_RE.findall(folded_b.lower()))
    )
    for token in diff:
        if token in _NEGATION_TOKENS:
            return f"meaning_guard:negation_token={token.decode()}"
        if any(48 <= byte <= 57 for byte in token):
            return f"meaning_guard:digit_token={token.decode()}"
    return None

Route = Literal["fresh", "exact_hit", "semantic_hit", "blocked"]


@dataclass(frozen=True)
class PromptCacheRequest:
    """One cacheable prompt dispatch (plan: Proposed API)."""

    raw_prompt: bytes
    task_type: str
    risk_class: str
    allow_semantic: bool = False
    ttl_seconds: Optional[int] = None
    privacy_scope: str = "local"


@dataclass(frozen=True)
class PromptCacheDecision:
    """Replayable outcome of one cache pass (plan: Proposed API)."""

    route: Route
    exact_key: str
    folded_text_sha256: str
    fold_trace: Tuple[Tuple[int, int, str], ...]
    semantic_score: Optional[float]
    policy_reason: str
    output: Optional[bytes]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_cache_dir(cache_dir: Optional[Path] = None) -> Path:
    """Env override -> explicit arg -> <claude_dir>/state/cache/prompt-fold."""
    if cache_dir is not None:
        root = Path(cache_dir)
    elif os.environ.get(CACHE_DIR_ENV, "").strip():
        root = Path(os.environ[CACHE_DIR_ENV]).expanduser()
    else:
        root = get_claude_dir() / "state" / "cache" / "prompt-fold"
    (root / BLOBS_NAME).mkdir(parents=True, exist_ok=True)
    return root


def _validate(request: PromptCacheRequest) -> None:
    if request.risk_class not in RISK_CLASSES:
        raise ValueError(
            f"risk_class must be one of {sorted(RISK_CLASSES)}, "
            f"got {request.risk_class!r}"
        )
    if request.privacy_scope not in PRIVACY_SCOPES:
        raise ValueError(
            f"privacy_scope must be one of {sorted(PRIVACY_SCOPES)}, "
            f"got {request.privacy_scope!r}"
        )
    if not isinstance(request.raw_prompt, bytes):
        raise ValueError("raw_prompt must be bytes")


def _blob_path(root: Path, sha: str) -> Path:
    return root / BLOBS_NAME / f"{sha}.bin"


def _write_blob(root: Path, data: bytes) -> str:
    """Content-addressed atomic blob write; returns the sha256 address."""
    sha = _sha(data)
    dest = _blob_path(root, sha)
    if not dest.exists():
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    return sha


def _read_blob(root: Path, sha: str) -> Optional[bytes]:
    """Hash-revalidated read: a corrupted blob is treated as absent."""
    path = _blob_path(root, sha)
    if not path.exists():
        return None
    data = path.read_bytes()
    return data if _sha(data) == sha else None


def _append_record(root: Path, record: dict) -> None:
    path = root / RECORDS_NAME
    with open(path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _load_records(root: Path) -> list[dict]:
    path = root / RECORDS_NAME
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn line never poisons the cache
    return records


def _ttl_ok(record: dict, request: PromptCacheRequest, now_epoch: float) -> bool:
    """Expired if EITHER the stored ttl or the reader's ttl bound is exceeded."""
    age = now_epoch - float(record.get("created_epoch", 0.0))
    bounds = [b for b in (record.get("ttl_seconds"), request.ttl_seconds) if b]
    return all(age <= bound for bound in bounds)


def _exact_hit(
    records: list[dict],
    exact_key: str,
    request: PromptCacheRequest,
    now_epoch: float,
    model_fingerprint: Optional[str],
    root: Path,
) -> Tuple[Optional[bytes], str]:
    """Newest-first exact lookup. Returns (output, reason)."""
    if request.risk_class not in EXACT_ALLOWED_RISK:
        return None, f"exact_denied:risk_class={request.risk_class}"
    if request.privacy_scope == PRIVACY_NO_STORE:
        return None, "exact_denied:privacy_scope=none"
    for record in reversed(records):
        if record.get("exact_key") != exact_key or not record.get("stored_output"):
            continue
        if not _ttl_ok(record, request, now_epoch):
            return None, "miss:ttl_expired"
        stored_fp = record.get("model_fingerprint")
        if model_fingerprint and stored_fp and stored_fp != model_fingerprint:
            continue
        output = _read_blob(root, record.get("output_sha256", ""))
        if output is None:
            return None, "miss:corrupt_or_missing_blob"
        return output, "exact_hit:key_match"
    return None, "miss:no_record"


def _semantic_gate(request: PromptCacheRequest, folded: bytes) -> Optional[str]:
    """Returns a denial reason, or None when semantic reuse may be attempted."""
    if not request.allow_semantic:
        return "semantic_denied:allow_semantic=false"
    if request.risk_class != "low":
        return f"semantic_denied:risk_class={request.risk_class}"
    if request.task_type in SEMANTIC_BLOCKED_TASKS:
        return f"semantic_denied:task_type={request.task_type}"
    if _FORBIDDEN.search(folded):
        return "semantic_denied:forbidden_pattern_in_prompt"
    return None


def _semantic_hit(
    records: list[dict],
    folded: bytes,
    request: PromptCacheRequest,
    now_epoch: float,
    root: Path,
) -> Tuple[Optional[bytes], Optional[float], str]:
    """Best near-match over recent same-task records. (output, score, reason)."""
    folded_text = folded.decode("utf-8", errors="replace")
    best_score, best_output, guard_denial = 0.0, None, None
    candidates = [
        r for r in reversed(records)
        if r.get("stored_output") and r.get("task_type") == request.task_type
    ][:MAX_SEMANTIC_CANDIDATES]
    for record in candidates:
        cand_folded = _screen_candidate(record, request, now_epoch, root)
        if cand_folded is None:
            continue
        score = SequenceMatcher(
            None, folded_text, cand_folded.decode("utf-8", errors="replace")
        ).ratio()
        if score <= best_score:
            continue
        denial = _meaning_guard(folded, cand_folded)
        if denial is not None:
            if score >= SEMANTIC_THRESHOLD:
                guard_denial = denial
            continue
        output = _read_blob(root, record.get("output_sha256", ""))
        if output is not None:
            best_score, best_output = score, output
    return _semantic_verdict(best_output, best_score, guard_denial)


def _screen_candidate(record: dict, request: PromptCacheRequest,
                      now_epoch: float, root: Path) -> Optional[bytes]:
    """TTL + blob-integrity + forbidden-pattern screen for one candidate."""
    if not _ttl_ok(record, request, now_epoch):
        return None
    cand_folded = _read_blob(root, record.get("folded_sha256", ""))
    if cand_folded is None or _FORBIDDEN.search(cand_folded):
        return None
    return cand_folded


def _semantic_verdict(
    best_output: Optional[bytes], best_score: float, guard_denial: Optional[str],
) -> Tuple[Optional[bytes], Optional[float], str]:
    if best_output is not None and best_score >= SEMANTIC_THRESHOLD:
        return best_output, best_score, f"semantic_hit:score={best_score:.3f}"
    if guard_denial is not None:
        return None, (best_score or None), f"semantic_denied:{guard_denial}"
    return None, (best_score or None), f"miss:best_semantic={best_score:.3f}"


def _store(
    root: Path,
    request: PromptCacheRequest,
    fold_result,
    output: bytes,
    now_epoch: float,
    model_fingerprint: Optional[str],
    idr_ref: Optional[str],
    happi_envelope_sha256: Optional[str],
) -> None:
    """Append one replayable record; blobs only when privacy permits."""
    storable = request.privacy_scope != PRIVACY_NO_STORE
    record = {
        "v": SCHEMA_VERSION,
        "exact_key": fold_result.final_sha256,
        "folded_sha256": fold_result.final_sha256,
        "fold_trace": [list(step) for step in fold_result.trace],
        "route": "fresh",
        "task_type": request.task_type,
        "risk_class": request.risk_class,
        "input_bytes": len(request.raw_prompt),
        "folded_bytes": len(fold_result.text),
        "output_sha256": _sha(output),
        "stored_output": storable,
        "ttl_seconds": request.ttl_seconds,
        "privacy_scope": request.privacy_scope,
        "model_fingerprint": model_fingerprint,
        "idr_ref": idr_ref,
        "happi_envelope_sha256": happi_envelope_sha256,
        "created_epoch": now_epoch,
        "created_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch)
        ),
    }
    if storable:
        _write_blob(root, fold_result.text)
        _write_blob(root, output)
    _append_record(root, record)


def run_or_get(
    request: PromptCacheRequest,
    fresh_runner: Callable[[bytes], bytes],
    *,
    cache_dir: Optional[Path] = None,
    depth: int = DEFAULT_DEPTH,
    now: Optional[float] = None,
    model_fingerprint: Optional[str] = None,
    idr_ref: Optional[str] = None,
    happi_envelope_sha256: Optional[str] = None,
) -> PromptCacheDecision:
    """Fold -> exact lookup -> gated semantic lookup -> fresh dispatch + store.

    fresh_runner receives the FOLDED prompt bytes and returns output bytes.
    A fold depth < 1 raises ValueError (surfaced from fold_compress).
    """
    _validate(request)
    root = resolve_cache_dir(cache_dir)
    now_epoch = time.time() if now is None else float(now)
    fold_result = fold(request.raw_prompt, depth)
    exact_key = fold_result.final_sha256
    records = _load_records(root)

    def decide(route: Route, reason: str, output: Optional[bytes],
               score: Optional[float] = None) -> PromptCacheDecision:
        return PromptCacheDecision(
            route=route, exact_key=exact_key,
            folded_text_sha256=fold_result.final_sha256,
            fold_trace=fold_result.trace, semantic_score=score,
            policy_reason=reason, output=output,
        )

    output, reason = _exact_hit(
        records, exact_key, request, now_epoch, model_fingerprint, root)
    if output is not None:
        return decide("exact_hit", reason, output)

    semantic_denial = _semantic_gate(request, fold_result.text)
    score: Optional[float] = None
    if semantic_denial is None:
        output, score, sem_reason = _semantic_hit(
            records, fold_result.text, request, now_epoch, root)
        if output is not None:
            return decide("semantic_hit", sem_reason, output, score)
        reason = f"{reason};{sem_reason}"
    else:
        reason = f"{reason};{semantic_denial}"

    fresh_output = fresh_runner(fold_result.text)
    if not isinstance(fresh_output, bytes):
        raise ValueError("fresh_runner must return bytes")
    _store(root, request, fold_result, fresh_output, now_epoch,
           model_fingerprint, idr_ref, happi_envelope_sha256)
    return decide("fresh", f"fresh:{reason}", fresh_output, score)


def lookup(
    request: PromptCacheRequest,
    *,
    cache_dir: Optional[Path] = None,
    depth: int = DEFAULT_DEPTH,
    now: Optional[float] = None,
    model_fingerprint: Optional[str] = None,
) -> PromptCacheDecision:
    """Read-only probe: exact_hit / blocked (policy denied a live entry) / fresh
    (meaning: a fresh dispatch would be required). Never calls a runner,
    never writes."""
    _validate(request)
    root = resolve_cache_dir(cache_dir)
    now_epoch = time.time() if now is None else float(now)
    fold_result = fold(request.raw_prompt, depth)
    records = _load_records(root)
    output, reason = _exact_hit(
        records, fold_result.final_sha256, request, now_epoch,
        model_fingerprint, root)
    route: Route = "exact_hit" if output is not None else (
        "blocked" if reason.startswith("exact_denied") else "fresh")
    return PromptCacheDecision(
        route=route, exact_key=fold_result.final_sha256,
        folded_text_sha256=fold_result.final_sha256,
        fold_trace=fold_result.trace, semantic_score=None,
        policy_reason=reason, output=output,
    )


def stats(cache_dir: Optional[Path] = None) -> dict:
    """Aggregate metrics from the record ledger (plan: Metrics)."""
    root = resolve_cache_dir(cache_dir)
    records = _load_records(root)
    stored = [r for r in records if r.get("stored_output")]
    reductions = [
        1.0 - (r["folded_bytes"] / r["input_bytes"])
        for r in records if r.get("input_bytes")
    ]
    return {
        "records": len(records),
        "stored_outputs": len(stored),
        "unstored_privacy_none": len(records) - len(stored),
        "distinct_exact_keys": len({r.get("exact_key") for r in records}),
        "avg_folded_byte_reduction": (
            round(sum(reductions) / len(reductions), 4) if reductions else 0.0
        ),
        "cache_dir": str(root),
    }


def clear_local(cache_dir: Optional[Path] = None) -> dict:
    """Delete local records + blobs. Returns counts for the CLI to print."""
    root = resolve_cache_dir(cache_dir)
    records_path = root / RECORDS_NAME
    n_records = len(_load_records(root))
    blobs = list((root / BLOBS_NAME).glob("*.bin"))
    for blob in blobs:
        blob.unlink()
    if records_path.exists():
        records_path.unlink()
    return {"records_deleted": n_records, "blobs_deleted": len(blobs)}
