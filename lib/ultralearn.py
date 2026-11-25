"""ultralearn — the lean RSI-witness kernel (sovereign-council-endorsed BUILD-MINIMAL).

One idempotent cycle over GRIP's existing primitives:

    introspect  ->  deliberate (exogenous sovereign council)  ->  prove  ->  publish

- **introspect**  : a cheap, deterministic snapshot of GRIP's own RSI state
                    (genome generation + fitness, insight count, memory generation).
- **deliberate**  : `grip-sovereign-council` — N non-Anthropic speakers + a non-Anthropic
                    judge (Pool-B, $0, exogenous by construction). No model grades its own
                    homework (exogenous-anchor-law).
- **prove**       : the council ALREADY emits a sha256 provenance seal + a signed IDR leaf.
                    We HARVEST that seal — we do not re-mint provenance (no-stubs: don't fake
                    an artifact a real subsystem already produces).
- **publish**     : append the sealed run-record to the witness ledger that the internal
                    strat-ops page (W3) renders.

## The Banach guarantee (f∘f = f) — the whole point

The cycle is content-addressed on the introspection snapshot through
`lib.ultracode.kernel.run`. Re-running on an **unchanged** snapshot makes **zero** new
council calls — the seal is served from a persistent content-addressed cache. This is the
idempotency the sovereign council named as the single external-value anchor: a
third-party-auditable, cryptographically-sealed proof that GRIP ran an exogenous
deliberation over its own RSI state, re-verifiable via
``grip-sovereign-council --verify <seal>``.

## Honesty rails

- The council is DIP-injected (`adapter=`), so tests exercise idempotence without spending
  Pool-B or depending on non-deterministic LLM output.
- A council failure (network / provider / timeout) fails **soft and honest**: the cycle
  returns a record with ``verdict="UNAVAILABLE"`` and NO seal, and that record is NOT
  written to the witness ledger. Only genuinely-sealed runs are ever witnessed
  (rate-limit-never-blocks: a transient failure is not a reason to fabricate a seal).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from _compat import fcntl as _fcntl  # noqa: E402  -- POSIX fcntl, or the LockFileEx lock on Windows
from cc_insights_refresh import describe as _describe_insights  # noqa: E402
from cc_insights_refresh import digest as _insights_digest  # noqa: E402
from cc_insights_refresh import refresh as _refresh_cc_insights  # noqa: E402
from claude_dir import get_claude_dir  # noqa: E402
from ultracode.kernel import State, content_hash, run  # noqa: E402

CLAUDE_DIR = get_claude_dir()
STATE_DIR = CLAUDE_DIR / "state" / "ultralearn"
CACHE_PATH = STATE_DIR / "kernel-cache.json"
LEDGER_PATH = STATE_DIR / "witness-ledger.jsonl"
_COUNCIL_BIN = CLAUDE_DIR / "bin" / "grip-sovereign-council"

# Council stdout markers (see a live run: PROVENANCE sha256 / IDR leaf id / consensus).
_SEAL_RE = re.compile(r"PROVENANCE\s+sha256\s*:\s*([0-9a-f]{64})")
_IDR_RE = re.compile(r"IDR\s+leaf\s+id\s*:\s*(\S+)")
_CONSENSUS_RE = re.compile(r"consensus:\s*(.+)")


# --------------------------------------------------------------------------- introspect
def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _count_lines(path: Path) -> Optional[int]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return None


def _memory_generation(path: Path) -> Optional[int]:
    """Top generation number from the MEMORY.md lineage table ('| 851 | ... |')."""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\|\s*(\d+)\s*\|", line)
            if m:
                return int(m.group(1))
    except OSError:
        return None
    return None


def introspect() -> dict:
    """A cheap snapshot of GRIP's own RSI state, coarsened to MEANINGFUL MILESTONES.

    The snapshot deliberately measures milestones, not raw churn: fitness is rounded and the
    insight count is bucketed (~every 50). This is load-bearing for the Banach guarantee —
    it lets f∘f=f hold *in practice*, so a re-run seconds later on unchanged milestones is a
    pure cache hit and the council convenes on genuine RSI advancement, not on the constant
    insight churn of a busy multi-session tree. Every field degrades to ``None`` on a missing
    source — an honest "unavailable", never a fabricated value (feature-complete domain).
    """
    genome = _read_json(CLAUDE_DIR / "data" / "genome-shared.json") or {}
    if not isinstance(genome, dict):
        genome = {}
    insights = _count_lines(CLAUDE_DIR / "learning" / "insights.jsonl")
    return {
        # genome-shared.json schema: {version, exported_at, exported_from_gen, gene_count, genes}
        "genome_generation": genome.get("exported_from_gen"),
        "genome_genes": genome.get("gene_count"),
        "insights_milestone": (insights // 50) if insights is not None else None,
        "memory_generation": _memory_generation(
            CLAUDE_DIR / "projects" / "-Users-void--grip" / "memory" / "MEMORY.md"
        ),
        # Claude Code's own /insights analysis of how sessions actually went (facet count in
        # tens + the most frequent friction types), coarse enough that the cache still hits
        # on unchanged state. Refreshed by main() before the cycle; None when no facets exist.
        "cc_insights": _insights_digest(CLAUDE_DIR / "usage-data" / "facets"),
    }


def _build_question(snapshot: dict, subject: Optional[str] = None) -> str:
    """A grounded deliberation prompt (Rule 21-bis: never send a council an ungrounded ask).

    With no `subject` the prompt is byte-identical to every prompt this function has ever
    produced, so existing seals and the content-addressed cache stay valid (Banach f.f=f).
    A `subject` names the specific thing to deliberate on; because run() content-hashes the
    question, a NEW subject earns a fresh council and the SAME subject re-serves from cache.

    Accepting a caller's subject and silently dropping it is the defect this parameter
    exists to prevent - rules/ultraprompt-moat-council-rereview.md records the identical
    bug in the /ultrareason escalation, which asked the council the literal word "council".
    """
    facts = json.dumps(snapshot, sort_keys=True, indent=2)
    focus = (
        "\n\nDELIBERATE SPECIFICALLY ON THIS, grounded in the state above:\n"
        f"{subject.strip()}"
        if subject and subject.strip() else ""
    )
    return (
        "SOVEREIGN COUNCIL — exogenous RSI introspection. GRIP's current self-measured "
        "recursive-self-improvement state (verify-canonical, read from disk this run):\n\n"
        f"{facts}{focus}\n\n"
        "Apply the discriminator 'what changes for someone OUTSIDE the building?'. Name the "
        "SINGLE highest-leverage next improvement to GRIP that a skeptic could externally "
        "verify, and one falsifiable check for it. Be adversarial about internal theatre. "
        "Return one verdict token PROCEED | HOLD plus the one improvement and its falsifier."
    )


# ----------------------------------------------------------------------------- deliberate
class CouncilAdapter:
    """Real deliberation seam: shell to `grip-sovereign-council`, harvest its seal.

    Fails soft: on any error the returned dict carries ``verdict='UNAVAILABLE'`` and no
    seal, so the caller can refuse to witness an unsealed run rather than fake one.
    """

    def __init__(self, timeout_s: int = 300):
        self.timeout_s = timeout_s

    def dispatch(self, task: Any) -> dict:
        question = task if isinstance(task, str) else json.dumps(task, sort_keys=True)
        try:
            proc = subprocess.run(
                [str(_COUNCIL_BIN), question],
                capture_output=True, text=True, timeout=self.timeout_s,
                cwd=str(CLAUDE_DIR),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"verdict": "UNAVAILABLE", "seal": None, "idr_leaf": None,
                    "error": f"{type(exc).__name__}: {exc}"}
        out = proc.stdout or ""
        seal = _SEAL_RE.search(out)
        idr = _IDR_RE.search(out)
        consensus = _CONSENSUS_RE.search(out)
        if not seal:
            return {"verdict": "UNAVAILABLE", "seal": None, "idr_leaf": None,
                    "error": f"council emitted no seal (rc={proc.returncode})"}
        return {
            "verdict": (consensus.group(1).strip() if consensus else "SEALED"),
            "seal": seal.group(1),
            "idr_leaf": idr.group(1) if idr else None,
        }


# ------------------------------------------------------------------------------- the cycle
def _load_cache(path: Path) -> dict:
    return _read_json(path) or {}


def _save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, sort_keys=True, indent=0), encoding="utf-8")
    tmp.replace(path)


@contextlib.contextmanager
def _exclusive_lock(fh):
    """Hold an exclusive advisory lock over `fh` for the duration of the block.

    `flock` from _compat: POSIX fcntl, or the LockFileEx lock on Windows, so the guard
    holds across processes on both (GRIP#8757). Released on exit even if the block raises."""
    _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
    try:
        yield
    finally:
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)


def _append_ledger_dedup(path: Path, record: dict) -> bool:
    """Append a sealed record iff its (snapshot_hash, subject) is not already witnessed.

    Returns True if a new line was written. Idempotent by construction: re-running the
    cycle on an unchanged snapshot hits the cache, yields the same seal, and never grows
    the ledger (the Banach property, at the publish layer too).

    Keyed on snapshot_hash AND subject because those stopped being one fact once
    `subject` existed. One snapshot used to admit exactly one question, so "same
    snapshot" meant "same deliberation"; a snapshot can now carry many genuinely
    distinct subjects, and keying on the snapshot alone would witness the first and
    silently discard every later one - a real, externally-sealed council vanishing from
    the very ledger that is this module's only external-value anchor.

    Deliberately keyed on the REQUEST, not on the returned seal. Keying on the seal
    looks equivalent and is not: it makes witnessing depend on the adapter minting a
    distinct seal per question, so a degenerate or constant-seal adapter would silently
    drop real records. The request is knowable without trusting the council.

    Atomic across concurrent cycles: the read-dedup-then-append runs under one exclusive
    advisory lock held for the whole critical section. Without it two cycles racing on the same
    fresh snapshot could both observe "absent" and both append, breaking publish-idempotence.
    The dedup read is a FRESH `read_text()` (a separate fd) taken AFTER the lock is held, so no
    stale buffered-reader content can mask a concurrent write; the append is O_APPEND ("a" mode,
    always at EOF); flush+fsync run BEFORE the lock releases so the next lock-acquirer reads
    durable data (else it would see our still-buffered write as "absent" and double-append)."""
    key = (record["snapshot_hash"], record.get("subject"))
    payload = json.dumps(record, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh, _exclusive_lock(fh):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                prev = json.loads(line)
                if (prev.get("snapshot_hash"), prev.get("subject")) == key:
                    return False
            except ValueError:
                continue
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    return True


def ultralearn_cycle(
    snapshot: Optional[dict] = None,
    adapter: Any = None,
    cache_path: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
    now: Optional[float] = None,
    subject: Optional[str] = None,
) -> dict:
    """Run one introspect -> deliberate -> prove -> publish cycle. Idempotent on `snapshot`.

    `adapter` (DIP) defaults to the real `CouncilAdapter`; tests inject a fake to exercise
    idempotence without Pool-B spend. `now` is injectable so the timestamp never pollutes
    the content-addressing (the snapshot alone keys the cache — f∘f=f)."""
    snapshot = introspect() if snapshot is None else snapshot
    adapter = CouncilAdapter() if adapter is None else adapter
    cache_path = CACHE_PATH if cache_path is None else cache_path
    ledger_path = LEDGER_PATH if ledger_path is None else ledger_path

    question = _build_question(snapshot, subject)
    snapshot_hash = content_hash(snapshot)

    cache = _load_cache(cache_path)
    _pre_keys = set(cache)
    # Kernel run(): content-hashes [question, state.root] and short-circuits to `cache`.
    # An unchanged snapshot => unchanged question => cache hit => ZERO adapter calls.
    result = run(question, State(), adapter, cache)
    council = result.output
    sealed = bool(council.get("seal"))
    if not sealed:
        # Never persist a transient failure: drop the key run() just added so the NEXT
        # run retries the council instead of serving a cached UNAVAILABLE (fail-open,
        # rate-limit-never-blocks). A seal, once minted, stays cached (Banach f∘f=f).
        for _k in set(cache) - _pre_keys:
            cache.pop(_k, None)
    _save_cache(cache_path, cache)
    record = {
        "snapshot": snapshot,
        "snapshot_hash": snapshot_hash,
        "subject": subject,
        "verdict": council.get("verdict"),
        "seal": council.get("seal"),
        "idr_leaf": council.get("idr_leaf"),
        "council_calls": result.calls,        # 0 on an idempotent re-run
        "cache_hit": bool(result.cache_hits),
        "ts": (time.time() if now is None else now),
    }
    if "error" in council:
        record["error"] = council["error"]

    # Only genuinely-sealed runs are witnessed. An UNAVAILABLE council never fakes a seal.
    record["witnessed"] = _append_ledger_dedup(ledger_path, record) if sealed else False
    return record


def refresh_insights() -> Optional[dict]:
    """Refresh Claude Code's /insights so introspect() reads current facets.

    Runs `claude -p "/insights"` on Sonnet 5 through lib/cc_insights_refresh.py, at most once
    per GRIP_INSIGHTS_MAX_AGE_HOURS (default 24) because /insights spends the Claude weekly
    limit. GRIP_INSIGHTS_REFRESH=0 switches it off. Fail-soft: a failed refresh is reported
    and the cycle still runs on whatever facets exist (rate-limit-never-blocks)."""
    if os.environ.get("GRIP_INSIGHTS_REFRESH", "1") == "0":
        return None
    result = _refresh_cc_insights()
    print(f"[ultralearn] insights: {_describe_insights(result)}")
    return result


def main(argv: Optional[list[str]] = None) -> None:
    # Parse before any work. `--help` used to become the council's subject, and the refresh
    # below then started a real `claude -p /insights` run (GRIP#8805).
    parser = argparse.ArgumentParser(
        prog="ultralearn",
        description="Run one sealed introspection cycle: refresh Claude Code's /insights, "
                    "ask the sovereign council about GRIP's own state, and record the seal.")
    parser.add_argument(
        "subject", nargs="*",
        help="what the council deliberates on (optional; the words are joined with spaces; "
             "put a subject that starts with '-' after '--')")
    args = parser.parse_args(argv)
    # A subject passed on the command line REACHES the council. Without this the
    # caller's argument is silently discarded and the sealed record answers a
    # question nobody asked (see _build_question for the sibling defect).
    subject = " ".join(args.subject).strip() or None
    refresh_insights()
    rec = ultralearn_cycle(subject=subject)
    status = "SEALED" if rec.get("seal") else "UNAVAILABLE"
    print(f"[ultralearn] {status} · calls={rec['council_calls']} "
          f"cache_hit={rec['cache_hit']} witnessed={rec['witnessed']}")
    if rec.get("seal"):
        print(f"[ultralearn] seal   : {rec['seal']}")
        print(f"[ultralearn] idr    : {rec['idr_leaf']}")
        print(f"[ultralearn] verify : grip-sovereign-council --verify {rec['seal'][:16]}")
    elif rec.get("error"):
        print(f"[ultralearn] error  : {rec['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
