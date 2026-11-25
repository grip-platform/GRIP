#!/usr/bin/env python3
"""LOCAL seal for dogfood domain #4 (context_fold_cache) — the moat, composed.

Produces three LOCAL, replayable, tamper-evident artefacts under
artifacts/context-fold-memoisation-dogfood4/seal/ (NEVER the operator's live
global IDR ledger / context HEAD — every writer is pointed at a dedicated path):

  1. cite.verify (prove-it) — every measured number resolved to a VERBATIM quote
     in the benchmark JSON via lib.prove_it.verify_all (deterministic L1 floor).
     Numeric quotes are built FROM the loaded file so they match run-to-run.
  2. IDR leaf — the memoisation decision + measurement, HMAC-signed via
     lib.precog.idr.build_idr, anchored in an exogenous forest rooted at the
     registered hypothesis (hypo:H726); the Merkle root is recorded.
  3. memory-context node — the BELIEF the decision relied on (which rules +
     measurements fed it), appended to a dedicated local context chain and
     cross-referenced to the IDR leaf.

NO public publish, NO Bitcoin anchor, NO VPS/live change. Local only.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "lib"))

from lib.prove_it import Source, Citation, verify_all, provenance  # noqa: E402
from lib.precog.idr import build_idr, append_idr  # noqa: E402
from lib.idr_forest import empty_forest, add_idr, forest_merkle_root  # noqa: E402
from lib.context_chain import append_context  # noqa: E402

ART = _ROOT / "artifacts" / "context-fold-memoisation-dogfood4"
SEAL = ART / "seal"
HYPO_ANCHOR = "hypo:H726"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sources() -> tuple[list[Source], dict]:
    bench_text = (ART / "benchmark-results.json").read_text(encoding="utf-8")
    module_text = (_ROOT / "lib" / "context_fold_cache.py").read_text(encoding="utf-8")
    pytest_text = (SEAL / "pytest-output.txt").read_text(encoding="utf-8")
    sources = [
        Source("bench", "benchmark-results.json (this machine)", bench_text),
        Source("module", "lib/context_fold_cache.py (design + honesty boundary)", module_text),
        Source("pytest", "pytest output", pytest_text),
    ]
    return sources, json.loads(bench_text)


def _citations(bench: dict) -> list[Citation]:
    real = bench["real_path"]
    llm = bench["llm_token_mechanism"]
    e2e = bench["end_to_end"]

    def q(key: str, val) -> str:
        return f'"{key}": {json.dumps(val)}'

    return [
        Citation("c1", "raw-key memo of the real synthesis is net-POSITIVE compute/hit",
                 "bench", q("raw_memo_net_ms_per_hit", real["raw_memo_net_ms_per_hit"])),
        Citation("c2", "folded-key memo of the same cheap derivation is a NET LOSS",
                 "bench", q("folded_memo_net_ms_per_hit", real["folded_memo_net_ms_per_hit"])),
        Citation("c3", "the memoised derivation (holistic synthesis) is deterministic",
                 "bench", q("synthesise_deterministic_x3", True)),
        Citation("c4", "saving is honestly labelled compute/latency, not LLM tokens",
                 "bench", '"saving_type": "compute/latency (synthesise is pure Python, not an LLM call)"'),
        Citation("c5", "a cache hit skips the derivation (2 identical requests -> 1 call)",
                 "bench", q("derivation_skipped_on_hit", True)),
        Citation("c6", "LLM-token mechanism: a hit skips the WHOLE call (tokens and all)",
                 "bench", q("whole_llm_call_skipped_on_hit", True)),
        Citation("c7", "the cache key is a proven Banach fixed point (idempotent)",
                 "bench", q("key_is_idempotent_fixed_point", True)),
        Citation("c8", "honesty boundary carried verbatim from the prior art",
                 "module", "calls-skipped), never a live measurement"),
        Citation("c9", "the full new test suite passes",
                 "pytest", "passed"),
    ]


def _seal_prove_it() -> dict:
    sources, bench = _sources()
    cites = verify_all(_citations(bench), sources)
    prov = provenance(sources, cites)
    unresolved = [c.id for c in cites if c.status == "not_found"]
    spec = {
        "title": "Dogfood #4 context_fold_cache — measured claims, every one cited",
        "grounding": prov,
        "unresolved": unresolved,
        "citations": [
            {"id": c.id, "claim": c.claim, "source_id": c.source_id,
             "quote": c.quote, "status": c.status, "start": c.start, "end": c.end}
            for c in cites
        ],
        "sources": [{"id": s.id, "label": s.label, "sha256": _sha(s.text)} for s in sources],
    }
    (SEAL / "prove-it-spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    if unresolved:
        raise SystemExit(f"cite.verify FAILED — unresolved citations: {unresolved}")
    return spec


def _seal_idr(bench: dict, prove_sha: str) -> tuple[str, str]:
    module_sha = _sha((_ROOT / "lib" / "context_fold_cache.py").read_text(encoding="utf-8"))
    decision = {
        "action": "memoise GRIP's re-derived static context via Banach fold-cache",
        "domain": "dogfood-4-token-memoisation",
        "saving_type": bench["real_path"]["saving_type"],
        "raw_memo_net_ms_per_hit": bench["real_path"]["raw_memo_net_ms_per_hit"],
        "folded_memo_net_ms_per_hit": bench["real_path"]["folded_memo_net_ms_per_hit"],
        "finding": bench["real_path"]["finding"],
        "llm_token_saving": "mechanism-proven (calls-skipped), not live-measured",
        "hypothesis": "H726",
    }
    idr = build_idr(
        prompt="dogfood-4 seal",
        fingerprint=module_sha,
        decision=decision,
        predecessor_idr=HYPO_ANCHOR,
        depth=1,
        kind="dogfood-decision",
        inputs={"benchmark_sha256": _sha(json.dumps(bench, sort_keys=True)),
                "prove_it_sha256": prove_sha},
    )
    append_idr(idr, path=SEAL / "idr.jsonl")
    forest = add_idr(empty_forest((HYPO_ANCHOR,)), idr, anchor_id=HYPO_ANCHOR)
    root = forest_merkle_root(forest)
    (SEAL / "idr-forest-root.txt").write_text(root + "\n", encoding="utf-8")
    return idr.id, root


def _seal_memory(idr_id: str, idr_content_addr: str) -> str:
    node = append_context(
        decision={
            "belief": "raw-key content-hash memoisation of a cheap deterministic "
                      "derivation is net-positive; folded-key is net-negative — "
                      "fold-keying pays off only for expensive/LLM derivers",
            "rules_relied_on": [
                "cryptographic-causation.md", "exogenous-anchor-law.md",
                "loop-priority-external-over-theatre.md", "no-stubs.md",
                "scientific-method.md (shadow metrics; hypothesis H726)",
            ],
            "evidence": "artifacts/context-fold-memoisation-dogfood4/benchmark-results.json",
            "dogfood_domain": 4,
        },
        fingerprint=idr_content_addr,
        kind="dogfood-belief",
        records_idr=idr_content_addr,
        path=SEAL / "memory-context.jsonl",
        head_pointer=SEAL / "memory-context.head",
    )
    return node.id


def main() -> int:
    SEAL.mkdir(parents=True, exist_ok=True)
    spec = _seal_prove_it()
    prove_sha = _sha((SEAL / "prove-it-spec.json").read_text(encoding="utf-8"))
    bench = json.loads((ART / "benchmark-results.json").read_text(encoding="utf-8"))
    idr_id, root = _seal_idr(bench, prove_sha)
    idr_line = json.loads((SEAL / "idr.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    idr_addr = idr_line.get("audit", {}).get("content_addr", idr_id)
    node_id = _seal_memory(idr_id, idr_addr)
    print(json.dumps({
        "cite_verify_grounding": spec["grounding"].get("grounding_rate", spec["grounding"]),
        "idr_leaf_id": idr_id,
        "idr_merkle_root": root,
        "memory_context_node": node_id,
        "seal_dir": str(SEAL),
    }, indent=2))
    return 0


if __name__ == "__main__":
    # Dual-sign custody guard (GRIP#5628). This entrypoint has a
    # `#!/usr/bin/env python3` shebang and reaches a signed-record write, so under
    # an interpreter without `cryptography>=48` it would silently emit forgeable
    # `demo-shared-key` records. Re-exec under a capable interpreter FIRST, before
    # main() reaches any signing path.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "lib"))
    from pq_reexec import reexec_if_incapable as _reexec
    _reexec(_Path(__file__))

    raise SystemExit(main())
