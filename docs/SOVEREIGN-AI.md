# GRIP Sovereign AI

**TL;DR:** Three tools that run zero-Anthropic-LLM workloads from the GRIP repo:
parallel inference across non-Anthropic providers, a multi-LLM council with
cryptographic verdicts, and an open-source REPL wired to the same providers.

*Council-drafted via `grip-sovereign-council`. IDR leaf: `precog-1782592766-6eca13b4`.
Verify: `grip-sovereign-council --verify 88bd63025677c573`.*

---

## Why This Exists

- **LLM independence** — GRIP's substrate (HAL) routes to DeepSeek, OpenAI, Gemini, and xAI;
  no Anthropic egress required for inference or agentic work. CI review (AGORA) uses only non-Anthropic speakers (DeepSeek, Qwen, xAI) with Gemini judge.
- **Resilience against government access restrictions** — if a jurisdiction restricts one
  provider, the council pattern and OpenCode REPL switch to any other without code changes.
- **Budget efficiency** — DeepSeek and Groq cost a fraction of frontier Anthropic pricing
  for most workloads; the council distributes cost across the cheapest capable model per task.

---

## Quick Start

| Tool | Purpose | Command |
|------|---------|---------|
| `bin/grip-sovereign-demo` | Parallel inference across N providers (concurrency proven) | `grip-sovereign-demo "your prompt"` |
| `bin/grip-sovereign-council` | Multi-LLM council + exogenous judge + tamper-evident seal | `grip-sovereign-council "your prompt"` |
| `bin/grip-opencode` | OpenCode REPL wired to non-Anthropic providers via HAL keychain | `grip-opencode` |

---

## Tools

### 1. grip-sovereign-demo

Fires N non-Anthropic providers **in parallel** and returns all answers with timing.
Concurrency is proven, not asserted: total wall-clock ≈ max(individual latency),
not their sum. A sequential run would take ≈ sum.

```bash
# All default providers (DeepSeek, OpenAI, Gemini, Groq)
grip-sovereign-demo "What is a durable competitive moat?"

# Custom set
grip-sovereign-demo --providers deepseek,gemini "Explain Merkle trees in one paragraph"
```

Default providers: `deepseek`, `openai`, `gemini`, `groq`.
Anthropic calls: **0**.

---

### 2. grip-sovereign-council

A multi-LLM council with a cryptographically sealed verdict:

1. **Fan-out** — N speakers answer in parallel (non-Anthropic).
2. **Judge** — a *different* non-Anthropic model synthesises and picks the best answer.
3. **Seal** — a SHA256 is computed over `{prompt, all answers, judge verdict}`.
4. **IDR leaf** — an HMAC-SHA256-signed Intent Decision Record is appended to the chain.
5. **Merkle** — all council verdicts are committed to a single RFC6962 Merkle root.

```bash
# Basic — default speakers (DeepSeek, OpenAI, Gemini, Groq) + Gemini Pro judge
grip-sovereign-council "What is the strongest argument for AI model independence?"

# Custom speakers and judge
grip-sovereign-council --speakers deepseek,openai,gemini --judge gemini-pro "your question"

# Verify a past verdict by its SHA256
grip-sovereign-council --verify a3b44fe779a1d9c5

# Print Merkle root committing all sealed verdicts
grip-sovereign-council --merkle
```

Anthropic calls: **0**. Every speaker and the judge is non-Anthropic.

---

### 3. grip-opencode

A Bash launcher that configures **OpenCode** (open-source, Claude-Code-compatible REPL)
to use non-Anthropic providers from the HAL keychain. The GRIP safety gates
(`integrations/opencode/grip-safety.js`) are wired in as OpenCode hooks, so the same
DRY/KISS/no-stubs quality gates that run in the Claude Code REPL apply here too.

```bash
# Interactive — pick a model from the menu
grip-opencode

# Non-interactive — run a single prompt
grip-opencode run -m deepseek/deepseek-chat "Refactor this function to be idempotent"
grip-opencode run -m oai/gpt-4o "Review this PR diff for correctness bugs"
```

Available sovereign models:

| Provider | Model ID |
|----------|----------|
| DeepSeek | `deepseek/deepseek-chat` |
| DeepSeek | `deepseek/deepseek-reasoner` |
| DeepSeek | `deepseek/deepseek-v4-pro` |
| OpenAI   | `oai/gpt-4o-mini` |
| OpenAI   | `oai/gpt-4o` |

Config: `config/opencode-sovereign.json` (auto-written by `grip-opencode` on first run).

---

## Architecture

```
                    grip-sovereign-council "Q"
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         DeepSeek          OpenAI           Gemini     (parallel, non-Anthropic)
              │               │               │
              └───────────────┴───────────────┘
                              │
                        Fan-out results
                              │
                              ▼
                    JUDGE: Gemini Pro       (exogenous — different from all speakers)
                    picks best + synthesises
                              │
                              ▼
              ┌───────────────────────────────┐
              │  SHA256 seal                  │
              │  hash({prompt, answers,       │
              │         judge verdict})       │
              └───────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │  IDR forest leaf              │
              │  HMAC-SHA256 signed           │
              │  appended to idr-chain.jsonl  │
              └───────────────────────────────┘
                              │
                      (accumulates)
                              │
                              ▼
              ┌───────────────────────────────┐
              │  RFC6962 Merkle root          │
              │  commits ALL sealed verdicts  │
              │  → externally anchorable      │
              │    (Bitcoin timestamp, etc.)  │
              └───────────────────────────────┘
```

---

## Verification

Every council verdict is sealed and independently verifiable.

```bash
# Verify a verdict by its short SHA256 (first 16 hex chars printed after each run)
grip-sovereign-council --verify a3b44fe779a1d9c5

# Expected output (PROVEN):
#   PROVENANCE sha256 : a3b44fe779a1d9c5…  PROVEN ✓ (sha256 + IDR HMAC)

# Print the single Merkle root committing every sealed verdict to date
grip-sovereign-council --merkle

# Expected output:
#   council verdicts committed : 5
#   Merkle root (RFC6962)      : <64-char hex>
#   externally anchorable      : submit this root on-chain …
```

Tampering with any byte of any saved record causes `--verify` to report `TAMPERED`.

---

## Tests

`tests/test_sovereign_council.py` — 9 Goodhart-proof tests. Key coverage:

| Test | What it catches |
|------|----------------|
| `test_tamper_in_an_answer_is_detected` | Forging any speaker answer breaks the seal |
| `test_tamper_in_the_judge_verdict_is_detected` | Rewriting the winner breaks the seal |
| `test_idr_leaf_id_in_sealed_record` | IDR library is importable; leaf is written |
| `test_idr_leaf_verifies` | A freshly sealed leaf passes `--verify` |
| `test_nonexistent_idr_id_fails_verify` | Fabricated IDs are reported NOT FOUND |
| `test_merkle_root_commits_all_verdicts_and_changes` | Adding a verdict changes the root |
| `test_bin_subprocess_help_exits_cleanly` | **P0 regression anchor**: the bin imports cleanly as a subprocess (the original `ModuleNotFoundError: No module named 'file_lock'` bug) |

```bash
# Run all 9 tests
venv/bin/pytest tests/test_sovereign_council.py -v
```

---

## Related files

| File | Purpose |
|------|---------|
| `bin/grip-sovereign-demo` | Parallel non-Anthropic inference proof |
| `bin/grip-sovereign-council` | Multi-LLM council + IDR sealed verdicts |
| `bin/grip-opencode` | OpenCode REPL with sovereign providers |
| `config/opencode-sovereign.json` | Portable OpenCode provider config |
| `integrations/opencode/grip-safety.js` | GRIP quality gates as OpenCode hooks |
| `tests/test_sovereign_council.py` | 9 tamper-evidence + regression tests |
| `state/sovereign-council/` | Saved council verdict records (JSON) |
