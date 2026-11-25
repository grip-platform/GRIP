# ADR 0007 — Embedding Model Pluggability via Registry Shim

| Field | Value |
|-------|-------|
| Status | Accepted |
| Date | 2026-05-21 |
| Deciders | V>> (operator) |
| Carve-out from | PR #3120 (V>>-directed "out of scope" reclamation) |
| Hypothesis | H-EMBED-SHIM-1 (deadline 2026-06-04) |
| Sibling ADR | [0001 — AI Syscall Canonical Shim](./0001-ai-syscall-canonical-shim.md) |

## Context

`lib/embeddings/` already had an abstract `EmbeddingEngine` ABC + `get_engine()`
factory (post-W6 H218). What was missing was a **registry layer** mapping
model ids to engine classes — the seam future model swaps need to touch
*instead of* call sites. Without it, switching to (say) `bge-small-en-v1.5`
would require editing `ollama_backend.py` constants, the schema constant
`VECTOR_DIM = 768`, and every call site that imports `OllamaEmbeddingBackend`
directly.

Current canonical state (verified against `lib/embeddings/storage.py:45,63`
and `ollama_backend.py:29`):

| Property | Value |
|---|---|
| Active model | `nomic-embed-text` (Ollama, local daemon) |
| Vector dim | 768 |
| DB schema | `vec0(embedding float[768])` — hard-coded |
| Cache prefix | `ol:` |

H678 has an open warm-start window (deadline 2026-05-28). **This ADR adds
the seam but does NOT swap the model** — the swap is a separate workstream
gated on H678 closure and a DB migration plan.

## Decision

Ship `lib/embeddings/registry.py` as a stdlib-only registry:

1. `get_engine_for(model_id, *, expected_dim=None) -> EmbeddingEngine` —
   refuse-on-load on unknown id or dim mismatch.
2. `default_model_id()` — resolves `config/embeddings.yaml::default_model`
   if present, else compiled-in `"nomic-embed-text"`.
3. `registered_models()` — shallow copy of the registry (inspection/test
   surface).
4. Two error types: `UnknownEmbeddingModelError` (KeyError subclass) and
   `EmbeddingDimensionMismatchError` (RuntimeError subclass).
5. `engine.py::get_engine()` body now delegates to
   `registry.get_engine_for()`. Default behaviour: byte-identical.

PyYAML import is `try/except` — no new hard dependency. Config file is
optional; absent yaml or absent file → compiled-in defaults.

## Candidate models compared

| Model | Dim | Backend | Drop-in vs current 768-dim DB? | Notes |
|---|---|---|---|---|
| **`nomic-embed-text`** *(current)* | 768 | Ollama local | n/a (canonical) | W6 H218 chose this; process-isolated, no SIGSEGV risk |
| `all-MiniLM-L6-v2` | 384 | sentence-transformers | **No** — dim mismatch refused at load | Mentioned in CLAUDE.md as stale; would need DB migration |
| `bge-small-en-v1.5` | 384 | sentence-transformers | **No** — dim mismatch | Stronger English retrieval at small dim, but requires migration |
| `all-mpnet-base-v2` | 768 | sentence-transformers | Compatible dim, **factory not wired** | Could be wired without DB migration; deferred until needed |
| `nomic-embed-text-v1.5` (Q8_0 GGUF) | 768 | llama.cpp / lembed | Compatible dim, **retired** (W6 H218 sunset for SIGSEGV reasons) | Listed for completeness |

## Dim-mismatch refuse-on-load rule (load-bearing)

The DB schema declares `vec0(embedding float[768])`. Inserting a 384-dim
vector would either fail at apsw insert time (loud) OR — if apsw is
permissive on a vec0 dim mismatch — silently poison the index. Either
failure mode is too late.

**The shim refuses at `get_engine_for(model_id)` call time**, before any
embedding is generated, by comparing the registry's declared `dimension`
against the canonical `VECTOR_DIM` (or an explicit `expected_dim` arg).
Migration to a different-dim model requires:

1. A dedicated migration PR that bumps `VECTOR_DIM` + the schema + drops
   the existing `embeddings`/`vec_embeddings`/`prompt_cache` rows OR
   transforms them.
2. Wiring the new backend's factory in the registry.
3. Setting `config/embeddings.yaml::default_model` to the new id.

None of which this PR does.

## Hypothesis H-EMBED-SHIM-1

- **Claim**: Over 2026-05-21 → 2026-06-04 (14 days), 0 production call
  sites regress. `get_engine()` returns an `OllamaEmbeddingBackend`
  instance with byte-identical behaviour. 1 dry-run model-swap via config
  change either returns the alternate engine OR raises a clean
  `UnknownEmbeddingModelError` / `EmbeddingDimensionMismatchError`.
- **Falsified if**: any existing embeddings test fails on main; or a
  callsite reports stale `model_id`; or the swap raises any other error
  class.

## Falsification (the ADR itself)

This ADR is wrong if:

1. A future model swap STILL requires editing non-registry files outside
   `config/embeddings.yaml` + `registry.py::_REGISTRY`. Then the seam
   missed something — extend it.
2. Dim mismatch is ever detected at insert time (`storage.py` apsw raise)
   instead of load time. Then the refuse-on-load is being bypassed — fix
   the bypass.
3. The byte-identical-default invariant breaks: a fresh checkout with no
   config file behaves differently from pre-shim. Then revert.

## Out of scope (explicit)

- Actually swap the embedding model (gated on H678 + migration plan).
- Migrate the 768-dim DB.
- Implement `SentenceTransformersBackend` or `BGEBackend`.
- Touch `lib/embeddings/search.py` (T2 owns it).
- Update CLAUDE.md's stale `all-MiniLM-L6-v2` mention (separate doc PR).

## References

- `drafts/embedding-shim-mesh-2026-05-21.md` — internal mesh deliberation
  (KONO-engine-expert / Compat-Guard / Pragmatist / R0-Falsifier /
  Integrator).
- `lib/embeddings/registry.py` — the shim.
- `tests/test_embeddings_registry.py` — Goodhart-resistant tests.
- `rules/syscall-doctrine.md` — the syscall doctrine this shim instantiates.
- `rules/knowledge-maturation-functor.md` — N=1 instance; rule-now, shim-at-N=3
  for any future cross-boundary registry pattern.
