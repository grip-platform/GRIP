# Embeddings Architecture

GRIP's embedding subsystem powers semantic search, KONO recall, skill
indexing, plan-registry similarity, and a dozen other capabilities.
This document explains the current architecture, the migration path
from `sqlite-lembed` to `ollama`, and the rollback procedure.

## Status (updated 2026-07-14)

- **Live database**: `~/.claude/embeddings.db` (768d nomic-embed-text vectors)
- **Default backend**: `ollama` (HTTP daemon, separate process, fault-isolated) — the only functional backend
- **Retired backend**: `lembed` (in-process SQLite extension) — the model layer was removed in W6/H218; `GRIP_EMBED_BACKEND=lembed` now selects a bare, model-less class that produces **no** embeddings. Do **not** set it (a stale `/grip-doctor` remediation string used to suggest it — that advice is wrong).
- **Model tag resolution**: a bare model name (`nomic-embed-text`) is resolved to an actually-pulled tag (e.g. `:v1.5`) before every call — untagged names 404 at `/api/embeddings`. Both the store/recall path (`lib/embeddings/ollama_backend.py::_ensure_model`) and the HAL shim (`lib/hal/embeddings.py::_pick_pulled_tag`) resolve tags.
- **Always-warm**: the HAL shim sends `keep_alive: -1`, pinning the (~274MB) embed model resident so semantic search never pays a cold reload after ollama's default 5-min idle unload. Trivial RAM cost (M-series / 32GB has ample headroom).
- **Health check**: `/grip-doctor` reports backend, model, dim, latency

## Why the migration

`sqlite-lembed` runs the embedding model **inside the SQLite process** via a
loaded extension (`lembed0.dylib`). Failures at the model layer surface as
**SIGSEGV in the parent Python process** — uncatchable, undebuggable.

Five prior fix commits to `sqlite-lembed` integration: `5ad69c39`,
`e926ca92`, `a523e367`, `68cee7a8`, `44444c5d`, `89105138`. Each patched
one input class; new failure modes kept emerging. On 2026-04-29 a fresh
SIGSEGV at `lembed0.dylib embed_single + 308` (NULL deref) made the
structural fragility unignorable.

`ollama` runs the model in a **separate daemon process**. Embedding
failures return HTTP 5xx, not segfaults. Process isolation is the only
durable fix for SIGSEGV-class bugs.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ Caller (15+ sites: kono, history_search, broly, …)  │
└────────────────────────┬────────────────────────────┘
                         │ get_engine()  ── lib/embeddings/__init__.py
                         ▼
        ┌────────────────────────────────────┐
        │ Dispatch by GRIP_EMBED_BACKEND     │
        └─────────┬──────────────────┬───────┘
                  │ "lembed" (default)│ "ollama" (opt-in)
                  ▼                   ▼
        ┌─────────────────┐  ┌─────────────────────────┐
        │ EmbeddingEngine │  │ OllamaEmbeddingBackend  │
        │ (sqlite-lembed) │  │ (subclasses Engine)     │
        │  in-process     │  │  delegates embed_text   │
        │  768d nomic     │  │  to lib/hal/embeddings  │
        └─────────────────┘  └────────────┬────────────┘
                                          │
                                          ▼
                                ┌────────────────────────┐
                                │ lib/hal/embeddings.py  │
                                │ HAL syscall shim       │
                                │  - embed(text)         │
                                │  - health()            │
                                │  - GRIP_EMBED_PROVIDER │
                                └────────────┬───────────┘
                                             │
                                             ▼
                                ┌────────────────────────┐
                                │ ollama daemon (HTTP)   │
                                │ POST /api/embeddings   │
                                │ model=nomic-embed-text │
                                └────────────────────────┘
```

`vec_embeddings` (sqlite-vec virtual table) is the **shared storage layer**
— same vector index regardless of backend. The dispatcher only swaps the
embedding *generation* path; vector search/storage stays identical.

## Files

| Path | Purpose |
|------|---------|
| `lib/embeddings/__init__.py` | `get_engine()` dispatcher reads `GRIP_EMBED_BACKEND` |
| `lib/embeddings/engine.py` | Legacy `EmbeddingEngine` (lembed) |
| `lib/embeddings/ollama_backend.py` | `OllamaEmbeddingBackend(EmbeddingEngine)` — overrides `embed_text` |
| `lib/embeddings/storage.py` | sqlite-vec storage (shared by both backends) |
| `lib/embeddings/search.py` | RRF-fused hybrid search (shared) |
| `lib/hal/embeddings.py` | HAL syscall shim — stable API, audit log, plain-language errors |
| `lib/grip_doctor.py:check_embeddings` | Health probe — pings backend, reports details |
| `tests/test_ollama_backend.py` | W2 coverage |
| `tests/test_embedding_dispatch.py` | W3 coverage |
| `tests/test_embedding_quality.py` | W4 recall@10 quality harness |
| `tests/test_hal_embeddings.py` | W5(b) HAL shim coverage |

## Activation

```bash
# Verify ollama is running with the embedding model
ollama list | grep nomic-embed-text   # should show 768d, ~274MB

# Activate ollama backend for the current shell
export GRIP_EMBED_BACKEND=ollama

# Smoke check
$HOME/.claude/venv/bin/python $HOME/.claude/lib/grip_doctor.py 2>&1 | grep -i embed
# Expected: GREEN, backend: ollama, model: nomic-embed-text, dim: 768, latency: <100ms
```

To make permanent, add to `~/.zshrc` or shell profile.

## Rollback

If the ollama backend misbehaves (latency regression, search quality drop,
daemon flapping), roll back to lembed:

```bash
# 1. Switch the dispatcher back
export GRIP_EMBED_BACKEND=lembed

# 2. (Optional) restore the pre-migration database snapshot if needed.
#    NOTE (2026-08-18): the snapshot is no longer tracked in git - a 94 MB
#    binary in every clone and fetch is not a reasonable way to distribute a
#    one-off rollback artefact. It lives in PARAMOUNT_BACKUPS on the machine
#    that made it (bin/grip-backup --where resolves the root per machine).
#    On a fresh clone this file will not exist, and that is expected.
trash ~/.claude/embeddings.db
cp "$(bin/grip-backup --where | awk '/root/{print $4}')"/*/embeddings-pre-migration-snapshot ~/.claude/embeddings.db

# 3. Verify
$HOME/.claude/venv/bin/python $HOME/.claude/lib/grip_doctor.py 2>&1 | grep -i embed
# Expected: backend: lembed (legacy SQLite extension)
```

The dispatcher's default is **lembed**, so removing the env var is also
sufficient for shell-level rollback. The pre-migration DB snapshot is
preserved at `~/.claude/embeddings.db.pre-migration-*` and is used only
if the ollama re-embedding produced incompatible vectors (W4's quality
harness confirmed recall@10 = 1.000, so this should not be needed).

## Hypothesis status

| ID | Wave | Claim | Status |
|----|------|-------|--------|
| H213 | W1 | ollama /api/embeddings returns 768d vectors p95 < 1.5s | CONFIRMED |
| H214 | W2 | OllamaEmbeddingBackend implements EmbeddingEngine interface | CONFIRMED |
| H215 | W3 | GRIP_EMBED_BACKEND dispatch toggles backend | CONFIRMED |
| H216 | W4 | recall@10 ≥ 0.85 vs lembed baseline | CONFIRMED (1.000) |
| H217 | W5 | ≥10 callers migrated + HAL shim + doctor health | PARTIAL — see CHANGELOG |
| H218 | W6 | Retire lembed, no regressions in 7 days | NOT YET RUN |

## Falsification

This architecture is wrong if any of the following hold:

- Search quality on golden set drops below recall@10 = 0.80 after 7 days
- Toggle fails to switch backends (env var has no effect)
- New ollama-backed SIGSEGV-class failure appears (improbable; would mean
  ollama itself is unstable)
- Total downtime during migration exceeds 30 minutes
- Re-embedding all 2,195 entries takes more than 30 min wall-clock

If any of these triggers fire, full rollback via env var + DB snapshot
restore.
