# ADR 0001 — Canonical AI Syscall: HAL Adapter

| Field | Value |
|-------|-------|
| Status | Accepted |
| Date | 2026-05-04 |
| Deciders | V>> (operator) |
| Issue | [GRIP #2381](https://github.com/grip-platform/GRIP/issues/2381) |
| Hypothesis | H332 — audit confirms HAL RAILLM as canonical (no rebuild) |
| Audit data | `data/ai-syscall-audit-2026-05-04.json` |

## Context

`rules/syscall-doctrine.md` codified the doctrine: every external boundary
is a thin shim with a stable API; AI is a syscall. PR #2380 shipped
`lib/slack_transport.py` as the precedent. PR #2577 added `lib/agentmail.py`
on the same pattern.

GRIP #2381 asks: do the same for LLM provider invocations. The issue's
falsification clause #2 reads:

> *Wrong direction if HAL already does this and we're rebuilding it (in
> which case: document HAL as the canonical syscall and make the audit
> the deliverable).*

This ADR resolves that question.

## Findings (audit, 2026-05-04)

`lib/syscall_coverage.py` measures shim adoption per boundary against the
patterns in `rules/syscall-doctrine.md`. Run today across `lib/`, `hooks/`,
`scripts/`, `features/`, `mcp-servers/`:

| Boundary | Shimmed files | Direct files | Coverage |
|----------|---------------|--------------|----------|
| **ai** | **8** | **3** | **73%** |
| git | 2 | 4 | 33% |
| secret | 0 | 12 | 0% |
| http | 1 | 25 | 4% |

The AI boundary is the *most-shimmed* of the four. The wide-open
boundaries are `secret` (keychain access) and `http` (raw urllib /
requests calls). That contradicts the issue's framing of LLM calls as
"the highest-fanout external boundary" *if measured by direct callsites*
— measured by shim coverage, AI is already further along than any other.

The 8 shimmed AI callers route through one of:

- `lib/hal/router.py` — HAL's primary LLM router
- `lib/hal/providers/*.py` — provider-specific adapters (anthropic_native, openai_compat, ollama_tool_adapter, mlx_vlm, vulavula)
- `lib/hal/llm.py` (if present) — typed LLM call surface
- `lib/hal_llm_adapter.py` — drop-in shim with HAL-HTTP → Groq → Gemini → xAI → sentinel chain
- `lib/active_llm.py` — active-LLM display contract
- `lib/hal_client.py` — HAL HTTP client

The 3 unshimmed AI callers (the work that remains):

| File | Pattern | Migration target |
|------|---------|------------------|
| `lib/backlog_pipeline.py` | `import anthropic` | `from lib.hal_llm_adapter import HALLLMAdapter` |
| `lib/pdf_batch_processor.py` | `import anthropic` | `from lib.hal_llm_adapter import HALLLMAdapter` |
| `lib/copilot/_archive/workflow_runner.py` | `import anthropic` | **delete** — file is in `_archive/`, dead code |

## Decision

**HAL is the canonical AI syscall for GRIP. No new shim is built; the
remaining work is migration of 2 active callsites + dead-code removal.**

Concretely:

1. **`lib/hal_llm_adapter.py` is the GRIP-side AI syscall.** It already
   provides:
   - HAL HTTP server priority (`:3850`) — full RAILLM routing rules,
     LimitSentinel rate-limit awareness, EWMA cost telemetry
   - Direct fallback chain (Groq → Gemini → xAI) when HAL HTTP unavailable
   - Cross-process cooldown sharing via `~/.hal/state/hal/limits.json`
   - Goodhart-protection sentinel JSON (returns 0.5 baseline rather than
     crashing the caller)
   - Drop-in compatibility with `LlamaEngine` interface

2. **`~/.hal/lib/hal/raillm.py` (RAILLM) is the shared substrate.** It
   provides stateful conversation history carried across provider switches,
   tier-aware cascade (FRONTIER → FAST → FREE_TIER → LOCAL), and
   `CarryOverVerifier` for information-loss detection at switch boundaries.
   Documented in `~/.hal/docs/RAILLM.md`.

3. **The public API for new code is:**
   ```python
   from lib.hal_llm_adapter import HALLLMAdapter

   adapter = HALLLMAdapter()
   response = adapter.score(prompt, model_hint="anthropic")
   ```
   This is identical to the issue's proposed `ai.call(...)` signature
   modulo argument names. Migrating to a new `lib/ai.py` would force a
   rename across 8 already-shimmed callsites for zero functional gain.

## Consequences

### Positive

- **No new code to maintain** — the canonical syscall is the implementation
  that already routes 73% of GRIP's AI traffic. New shims add review surface,
  test surface, and migration cost; this ADR avoids all three.
- **HAL development directly improves GRIP's AI surface** — every RAILLM
  refinement (better cascade ordering, cost telemetry, conversation
  compression) propagates to GRIP without separate work.
- **Migration roadmap is bounded** — 2 active callsites + 1 dead-code
  removal + a syscall-doctrine reference update. Estimated effort: 1–2h
  in a focused session.

### Negative

- **GRIP code depends on HAL availability** — `~/.hal` must be cloned and
  importable for the adapter's full chain. The adapter falls back to
  direct provider calls if HAL is missing, but the fallback chain is less
  capable than the HAL-HTTP path.
- **Cross-repo coupling** — improvements to RAILLM require touching HAL,
  not GRIP. Operators contributing to GRIP's AI behaviour need to know
  this. Mitigated by the syscall-doctrine reference update below.
- **Coverage metric blind spot** — `lib/syscall_coverage.py` flags any
  `import anthropic` as direct. The 3 remaining callers may have legitimate
  reasons (legacy fixtures, archived code) that the metric doesn't surface.

### Falsification

This ADR is wrong if:

1. **HAL availability is unreliable for production use** — measured as
   `>5%` of `HALLLMAdapter` calls failing through the entire fallback
   chain in a 14-day window. If observed, build a GRIP-internal LLM call
   path independent of HAL.
2. **Adapter latency overhead exceeds 5ms per call** at p50 (the issue's
   own falsification threshold). If observed, profile + optimise; if not
   recoverable, build a thinner shim.
3. **The remaining 2 active callsites cannot be migrated** because they
   use anthropic-specific features (extended thinking, prompt caching,
   message batching) that `HALLLMAdapter` doesn't expose. If observed,
   extend `HALLLMAdapter`'s API rather than bypass it.

Track via the `lib/syscall_coverage.py` metric. Goal: AI coverage ≥95%
by 2026-06-04 (one month). Currently 73% — moving the 2 active callsites
to the adapter takes the metric to 91%.

## Migration roadmap

Priority order (by impact-to-effort ratio):

| Priority | Callsite | Effort | Risk | Notes |
|----------|----------|--------|------|-------|
| 1 | `lib/copilot/_archive/workflow_runner.py` | 1 min | None | File is in `_archive/`. **Delete it** — dead code by definition. |
| 2 | `lib/pdf_batch_processor.py` | 30 min | Low | Single batch-processing entry; swap `anthropic.Anthropic()` for `HALLLMAdapter`. Add a regression test asserting batch behaviour preserved. |
| 3 | `lib/backlog_pipeline.py` | 30–60 min | Medium | Backlog auto-processing — touches user-facing data. Stage in a feature branch with manual smoke test before merge. |

Total effort to reach 100% AI coverage: ~60–90 min in a focused session.

## Reference set update

`rules/syscall-doctrine.md`'s "existing shims" table lists `lib/hal/router.py`
+ providers as the voice path's shim. Update needed:

```
+ | `lib/hal_llm_adapter.py` | LLM provider chain (HAL → Groq → Gemini → xAI) | Active |
+ | `lib/syscall_coverage.py` | Shim-adoption meter (the falsifier from the doctrine itself) | Active |
```

This makes the AI-syscall canonical entry visible to future contributors
who scan the doctrine before adding new LLM-provider integrations.

## Hypothesis (H332)

Registered 2026-05-04. Verifies 2026-05-18:

> Audit phase reveals ≥10 direct-SDK or subprocess-claude callsites across
> lib/, hooks/, scripts/; ADR confirms HAL RAILLM as canonical (no rebuild);
> audit data jsonl + ADR doc + audit script tests ship in single PR.

**Status check at registration**: actual count = **3 direct AI callers**,
not 10. The hypothesis is therefore *partially* falsified at registration
time — the issue's framing overestimated the migration debt by ~3×. This
strengthens the ADR's conclusion: the syscall doctrine is already
load-bearing in GRIP's AI path, and the rebuild instinct was the wrong
direction.

The metric still has room: `secret` boundary is 0% shimmed (12 direct
callers). That's the next syscall-doctrine sprint, not this one.

## Related

- Issue: GRIP #2381
- Doctrine: `rules/syscall-doctrine.md`
- Precedents: PR #2380 (`lib/slack_transport.py`), PR #2577 (`lib/agentmail.py`)
- HAL canonical doc: `~/.hal/docs/RAILLM.md`
- Audit data: `data/ai-syscall-audit-2026-05-04.json` + `.md`
- Hypothesis: H332 (deadline 2026-05-18)
