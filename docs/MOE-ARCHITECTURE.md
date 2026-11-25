# Multi-Provider MoE Architecture

Design document for Issue #194: transform GRIP from single-provider (Claude-only) to an intelligent multi-provider system.

## Status

**Phase**: Design (skeleton)
**Priority**: P0
**Dependencies**: Embedding engine (exists), BAR prompt router (exists), Broly council (exists)

## Architecture Overview

```
User Prompt
    |
    v
[UserPromptSubmit Hook]
    |
    v
[Semantic Router] ── classifies intent, complexity, domain
    |
    ├── Simple (cache hit, sim > 0.95) ──> Vector Cache Response
    |
    ├── Simple (no cache) ──> Local Tier (Ollama)
    |       |
    |       v
    |   [Quality Gate] ── self-consistency + complexity check
    |       |
    |       ├── PASS ──> Return local response + cache it
    |       └── FAIL ──> Escalate to Cloud Tier
    |
    ├── Medium ──> Cloud Tier (Sonnet/Haiku)
    |
    ├── Complex ──> Cloud Tier (Opus)
    |
    └── Critical ──> Council (Multi-Provider MESH)
            |
            v
        [Broly Council] ── cross-provider deliberation
            |
            v
        Consensus Response
```

## Existing Infrastructure (What We Have)

| Component | File | Status |
|-----------|------|--------|
| Embedding engine | `lib/embeddings/engine.py` | Complete, Nomic 768D |
| Intent classifier | `lib/embeddings/classify.py` | Complete, **store empty** |
| Hybrid search (RRF) | `lib/embeddings/search.py` | Complete |
| Prompt router (BAR) | `lib/prompt_router.py` | Complete, Claude-only tiers |
| Threshold manager | `lib/threshold_manager.py` | Complete |
| Broly council | `lib/broly/consensus.py` | Complete, Claude-only agents |
| Epic decomposer | `lib/epic_decomposer.py` | Complete |

## Phase 1: Seed Concept Store + Activate Semantic Routing

**Goal**: Make `classify_intent()` actually return results by seeding the vector store.

### 1.1 Seed Intents

Populate `embeddings` table with `type = 'concept_intent'` rows for:
- Common GRIP operations (commit, PR, test, refactor, explain, debug)
- Domain-specific intents (converge, broly, mode-switch, skill-create)
- Complexity tiers (simple-lookup, moderate-analysis, complex-architecture)

```python
# lib/embeddings/seed.py
SEED_INTENTS = [
    ("simple_lookup", "Look up a function definition or file path"),
    ("simple_edit", "Make a small edit to a single file"),
    ("moderate_analysis", "Analyze code patterns across multiple files"),
    ("complex_refactor", "Refactor architecture across the codebase"),
    ("critical_decision", "Make an architectural decision with trade-offs"),
    # ... 30-50 intents covering the GRIP operation space
]
```

### 1.2 Activate Tier 2 in BAR

BAR's semantic fallback (`get_full_classification()`) is wired but never fires because the store is empty. After seeding:
- Heuristic (Tier 1) handles 80% of prompts at ~1ms
- Semantic (Tier 2) catches the 20% where keywords are ambiguous

### 1.3 Validation

- Run BAR on 100 historical prompts from session transcripts
- Measure: heuristic accuracy vs. semantic accuracy
- Threshold: semantic should flip the correct decision in >= 15% of ambiguous cases

**Effort**: Small (1-2 sessions)
**Files**: `lib/embeddings/seed.py` (new), `lib/prompt_router.py` (minor wire-up)

## Phase 2: Local Inference Tier (Ollama)

**Goal**: Route simple tasks to local models, saving API costs.

### 2.1 Provider Abstraction

Replace `subagent_model: str` with a structured model spec:

```python
@dataclass
class ModelSpec:
    provider: str       # "claude", "ollama", "gemini", "litellm"
    model_id: str       # "opus-4-6", "llama3.2:3b", "gemini-2.0-flash"
    tier: str           # "local", "cloud-fast", "cloud-smart", "cloud-best"
    cost_per_1k: float  # estimated cost in USD
```

Extend `RoutingDecision` to carry `ModelSpec` instead of string.

### 2.2 Ollama Integration

```python
# lib/providers/ollama.py
class OllamaProvider:
    def __init__(self, base_url="http://localhost:11434"):
        self.base_url = base_url

    async def generate(self, model: str, prompt: str) -> str:
        """Call Ollama API. Returns response text."""

    async def is_available(self) -> bool:
        """Check if Ollama is running and model is loaded."""
```

### 2.3 Quality Gate (Key Design Decision)

The hardest problem: how to know if local output is good enough.

**Proposed approach: Dual-signal gate**

1. **Self-consistency check** (free, ~50ms): Embed the query and the response. If cosine similarity < 0.3, the response is likely off-topic. Escalate.
2. **Complexity pre-filter** (free, ~1ms): If BAR classified the task as `moderate+`, skip local entirely. Only route `simple` tasks to Ollama.

This avoids the expensive "judge model" approach (calling Haiku to rate Ollama output) while still catching obvious failures.

**Rejected alternatives**:
- Haiku-as-judge: adds 200ms + cost per local call, defeats the purpose
- Confidence score from Ollama: not reliably available across models
- Fixed heuristic only: too brittle, misses semantic failures

### 2.4 Vector Similarity Cache

Upgrade from SHA-256 exact match to vector similarity:

```python
# lib/embeddings/cache.py
def lookup_cache(engine, query: str, threshold: float = 0.95) -> Optional[str]:
    """Find cached response for semantically similar query."""
    query_vec = engine.embed_text(query)
    # ANN search in vec_embeddings where type = 'response_cache'
    results = engine.search_similar(query_vec, type="response_cache", top_k=1)
    if results and results[0].similarity >= threshold:
        return results[0].metadata["response"]
    return None

def cache_response(engine, query: str, response: str) -> None:
    """Store query-response pair in vector cache."""
    engine.store_embedding(query, type="response_cache", metadata={"response": response})
```

**Effort**: Medium (2-3 sessions)
**Files**: `lib/providers/ollama.py` (new), `lib/providers/__init__.py` (new), `lib/embeddings/cache.py` (new), `lib/prompt_router.py` (extend)

## Phase 3: Multi-Provider Council

**Goal**: Broly MESH with cross-provider deliberation.

### 3.1 Constraint: Claude Code Task Tool is Claude-Only

The `Task` tool spawns Claude agents. It cannot spawn Ollama or Gemini agents. Two approaches:

**Option A: HTTP API orchestration (recommended)**
- The lead Claude agent calls external provider APIs directly via `httpx`
- Responses are collected and fed into existing `Council.deliberate()` machinery
- Pros: Works today, no infrastructure changes
- Cons: External API calls from within Claude Code session

**Option B: LiteLLM unified gateway**
- LiteLLM proxy running locally, all providers behind one endpoint
- Broly calls LiteLLM instead of spawning Task agents for non-Claude members
- Pros: Provider-agnostic, clean abstraction
- Cons: Extra dependency, setup burden

### 3.2 Council Composition

For a 6-member council (current default):
- 2x Claude Opus (high-stakes reasoning)
- 1x Claude Sonnet (cost-efficient analysis)
- 1x Gemini Flash (speed, alternative perspective)
- 1x Local Ollama (cost-free baseline)
- 1x Devil's Advocate (any provider, contrarian prompt)

Consensus rules unchanged: FACTUAL (unanimous), STRATEGIC (67% supermajority).

### 3.3 Adaptive Stability Detection

KS test for early stopping: if the distribution of council votes is statistically stable after 4/6 members, skip remaining evaluations.

**Effort**: Large (3-5 sessions)
**Files**: `lib/providers/gemini.py` (new), `lib/broly/multi_provider.py` (new), `lib/broly/consensus.py` (extend)

## Phase 4: RSI Integration

**Goal**: Wire MoE into the RSI orchestration loop.

### 4.1 Epic Decomposition Wire-Up

`rsi_orchestrator.py` calls `epic_decomposer` for `overhaul`-labeled issues like #194 itself. Each subtask gets a `ModelSpec` recommendation based on complexity.

### 4.2 Cost Tracking per Provider

Extend `cost-tracker.py` with per-provider breakdowns:
- Claude API: actual token costs
- Ollama: estimated compute time (energy cost proxy)
- Gemini: API costs
- Cache hits: zero cost

### 4.3 Routing Feedback Loop

After each routed task completes:
1. Record outcome quality signal (test pass, Ralph score, user acceptance)
2. Feed back to `ThresholdManager` for adaptive threshold calibration
3. Feed back to BAR's `calibrate_weights()` for keyword weight updates

**Effort**: Medium (2-3 sessions)
**Files**: `lib/rsi_orchestrator.py` (extend), `lib/cost_tracker.py` (extend)

## Implementation Priority

| Phase | Effort | Impact | Dependencies |
|-------|--------|--------|-------------|
| P1: Seed + Activate | Small | High (unlocks semantic routing) | None |
| P2: Ollama Local | Medium | High (cost savings) | P1 |
| P3: Multi-Provider | Large | Medium (diversity) | P2 |
| P4: RSI Integration | Medium | Medium (automation) | P1 |

**Recommended order**: P1 first (quick win), then P4 (independent of P2/P3), then P2, then P3.

## Open Questions

1. Should the MoE router intercept at `UserPromptSubmit` hook level (pre-Claude) or at BAR advisory level (within the session)?
2. What is the minimum viable Ollama model for GRIP tasks? (llama3.2:3b vs codellama:7b vs qwen2.5-coder:7b)
3. Should cached responses be invalidated when the codebase changes? (Git diff hash as cache key component?)
4. How to handle provider failures gracefully without blocking the session?

## References

- Issue #194: https://github.com/grip-platform/GRIP/issues/194
- BAR implementation: `lib/prompt_router.py`
- Embedding engine: `lib/embeddings/engine.py`
- Broly council: `lib/broly/consensus.py`
- Cost tracker: `lib/cost_tracker.py`
