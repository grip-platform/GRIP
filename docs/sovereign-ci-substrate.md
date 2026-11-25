# Sovereign-CI Substrate

**Status:** Production — all cited PRs merged to `main` 2026-07-07.
**Council seals:** `71a972fb` (AGORA-fix roster design) · `37fb447a` (CI-substrate design) · `22a9e910` (DO-NOT-BUILD verdict)
**Re-verify any seal:** `grip-sovereign-council --verify <sha>`
**Hypotheses:** H732 (pool routing, deadline 2026-07-20) · H733 (grip-council CI-readiness, deadline 2026-07-21)

---

## 1 — Two-Pool Doctrine

Source: `rules/pool-a-offload-routing.md` (PR #3870 — two-pool routing rule, merged).

Every GRIP session runs inside two spend pools. Only one hits the weekly usage limit.

| Pool | What runs here | Weekly-limited? |
|------|---------------|-----------------|
| **A — Anthropic harness** | The interactive Claude Code REPL, `claude -p` relay/batch spawns, and CC `Task`/`Agent`/Workflow subagents — including the Agent-tool broly at opus/max | **YES — this is the `/usage` %** |
| **B — HAL `grip_infer`** | GRIP's programmatic LLM calls → `lib/hal_llm_adapter.py` → RAILLM → groq / gemini / deepseek / qwen / xai / local | **NO — separate spend, ~$0** |

**The rule:** LLM-heavy cognition (councils, reviews, deliberation, autonomous/batch jobs) routes through Pool B by default. Pool A is spent only on the interactive REPL's own reasoning and any deliberately-Anthropic voice in a cross-provider council.

Reaching for the Agent-tool broly (opus/max) or `claude -p` when a HAL council does the same work is a weekly-limit leak. The Pool-B primitives already exist:

- `grip-council --roster open` — multi-provider, non-Anthropic roster, HAPPI seam, $0
- `grip-sovereign-council` — N non-Anthropic speakers + exogenous non-Anthropic judge + sha256 seal
- `lib/broly_council.py` — routes every voice through `_query_grip_infer` (Pool B)
- `grip-opencode run -m deepseek/deepseek-chat "…"` — non-interactive Pool-B inference

Verify Pool-B share stays non-Anthropic: `grip-llm-usage verify` (breaks on tamper).

---

## 2 — AGORA Made Sovereign

Source: `feedback_agora_ci_reviewer_provider_funding.md` (PRs #3872 — roster funded-first reorder; #3873 — workflow env fix; both merged).

AGORA is GRIP's CI PR-review council: `.github/workflows/agora-pr-review.yml` dispatches `lib/agora_pr_review.py` + `lib/hal_council_dispatch.py`.

### Root cause of the 0/3 outage

The speaker set is decided by which provider keys are injected into the workflow `env:` block — **not** by roster order. `_build_role_chains` filters `[e for e in roster if e[2]]` (auth non-empty). On GitHub Actions CI, `_apikey_auth_or_absent(ENV, keychain)` returns `{}` when `ENV` is absent (no macOS Keychain on Ubuntu runners). A roster rung whose key is missing from the workflow env is silently dropped. Re-ordering the roster alone does nothing until the funded providers' keys are in the env — PR #3872 (reorder) left CI 2/3 red; PR #3873's `env:` change is what actually fixed it.

Known CI-broken provider: **groq** is Cloudflare-firewall-blocked on GitHub runner IPs (HTTP 403, code 1010). This is not a billing issue; a top-up will not fix it. Keep groq out of the CI roster.

### The fix

PR #3873 workflow env: `DEEPSEEK_API_KEY` + `DASHSCOPE_API_KEY` (Qwen) + `XAI_API_KEY` + `GEMINI_API_KEY` (judge). Dead `ANTHROPIC_API_KEY` + `GROQ_API_KEY` dropped.

Result on CI: `available = [deepseek, qwen, xai]` → 3 distinct funded speakers + Gemini judge → 3/3 green.

**Why this is more exogenous than before:** every speaker is non-Anthropic, so no reviewer shares the Claude PR-author model family. The verifier's independence is structural, not claimed.

### Dogfood result

Ran AGORA on 5 previously-merged PRs after the fix — verdicts: MERGE · DEFER · DEFER · DEFER · REQUEST_CHANGES. Varied verdicts confirm genuine review, not a rubber stamp.

### Practical notes for operators

When AGORA is red: read the actual CI log speaker HTTP codes (`gh run view --job <id> --log-failed`), probe each provider's true credit/reachability (keychain key → `curl` the chat endpoint), and ensure ≥3 funded + CI-reachable non-Gemini providers' keys are in the workflow `env:`. Workflow edits land on `main` only (Gate N); a workflow-env change cannot self-test on its own PR (GitHub security). Verify post-merge: `gh workflow run agora-pr-review.yml -f pr_number=<open PR>`.

---

## 3 — grip-ci-substrate Composite Action

Source: `feedback_ci_substrate_hal_not_claude_code.md` (PR #3874 — composite action build; PR #3875 — robustness: `submodules:false` + isolated jobs; both merged). Hypothesis H733 (deadline 2026-07-21).

A reusable GitHub composite action at `.github/actions/grip-ci-substrate/action.yml` that runs any GRIP command in CI with:

- Zero Claude Code CLI
- Zero Anthropic harness
- One checkout of HAL (for the `hal` binary) + one checkout of GRIP

**Authored by a HAL model (DeepSeek via `grip_infer`) — itself a proof that non-Anthropic models can author the sovereign substrate.**

### One-line adoption

```yaml
- uses: grip-platform/GRIP/.github/actions/grip-ci-substrate@main
  with:
    command: grip-council --roster open "Is this PR safe to merge?"
    deepseek_api_key: ${{ secrets.DEEPSEEK_API_KEY }}
    dashscope_api_key: ${{ secrets.DASHSCOPE_API_KEY }}
    xai_api_key:       ${{ secrets.XAI_API_KEY }}
    gemini_api_key:    ${{ secrets.GEMINI_API_KEY }}
```

The `command` is passed via env (not shell arg) — injection-hardened.

### Dogfood result

`.github/workflows/grip-ci-substrate-dogfood.yml` ran **green** on PR #3874: printed `PROOF: GRIP substrate runs in CI … zero Claude Code CLI, zero Anthropic harness`. AGORA reviewed it PASS/DEFER 3/3 (council seal `37fb447a`).

### CI-usable GRIP tools (de-crypted set, no key needed)

These tools run cleanly in a CI checkout because their full dependency closure is in the git-crypt **de-crypted** set:

| Tool | What it does in CI |
|------|--------------------|
| `grip-council` / `grip-sovereign-council` | Non-Anthropic council + exogenous judge |
| `agora-demo` / `agora-falsify` / `agora-replay-discover` | AGORA deliberation + replay |
| `lib/guardrail_evasion_detector.py` | Deterministic evasion scan |
| `lib/redaction_shield.py` | PII redaction, no LLM |
| `lib/no_stubs_detector.py` | Completeness gate |
| `render-leak-sweep` | Visual-artifact denylist grep |
| `syscall-coverage` | Shim-adoption meter |
| `factcheck-guard` / `prove-it` | Provenance gates |

Tools that **cannot** run in CI (TTY / macOS-only / git-crypt-encrypted reads): `grip activate`, the channels wizard, `grip-headless-review` (spawns `claude -p`), `osa`, `grip-meet`, `voice-ai`, `brave-cdp`, `grip-cos`.

---

## 4 — Git-Crypt Guardrail (PARAMOUNT)

Source: council seal `37fb447a` — the CI-substrate design council's #1 risk, judge-crowned.

GRIP is git-crypt encrypted. **NEVER put the git-crypt key in CI** — committing the key as a CI secret leaks the entire encrypted client-data corpus via the GitHub Secrets store and any secret-scanning surface that sees the workflow.

The composite action works **only** because AGORA's code and its dep closure (`agora_pr_review.py`, `hal_council_dispatch.py`, all `lib/*` they import) are in the git-crypt **de-crypted** set — `.gitattributes` decrypts non-sensitive operational code for readability.

**How to check before adding a tool to a CI workflow:**

```bash
git check-attr filter -- <path>
# "filter: git-crypt" → encrypted → CI-forbidden without the key (forbidden)
# "filter: unspecified" → de-crypted → safe in CI
```

If a tool reads an encrypted file (`rules/operational-context.md`, genomes, `data/genomes/grip.json`, sensitive configs) it **cannot** run in CI without the key — and the key must never be there. Move the tool to a local pre-merge gate instead.

---

## 5 — Sovereign ULTRACODE Already Exists (DO-NOT-BUILD)

Council seal `22a9e910` (state/sovereign-council/22a9e910d830c976.json) — 3/3 non-Anthropic speakers + Gemini judge — **unanimous DO-NOT-BUILD** for a `/ultracode` wrapper.

The capability already exists in the current tool stack. A wrapper would add a shim with no new capability — theatre, not engineering (`rules/loop-priority-external-over-theatre.md`).

### Sovereign tool map

| Tool | Capability |
|------|------------|
| `grip-sovereign-council` | N non-Anthropic speakers + exogenous non-Anthropic judge, parallel, council-sealed, sha256 prove-it |
| `grip-council --roster open` | Multi-provider council, non-Anthropic roster, $0, HAPPI seam — same path AGORA uses |
| `bin/grip-harness` | Wires GRIP's Pool-B substrate into any harness (codex / aider / opencode / cursor / CI) locally |
| `grip-ci-substrate` (action) | Same substrate in any GitHub Actions CI job, no Claude Code, no Anthropic |
| `/dispatch` + `/converge` | Autonomous task dispatch + convergence loop to any exogenous criterion |

The verdict: ship with this stack. Building a new `/ultracode` command would violate YAGNI (no new capability delivered), the no-stubs rule (naming a capability it does not provide beyond the existing tools), and the loop-priority doctrine (internal theatre displacing external value).

### Council provenance

| Seal SHA | Council | Verdict |
|----------|---------|---------|
| `71a972fb` | AGORA-fix roster design | AGORA sovereign roster architecture ratified |
| `37fb447a` | CI-substrate design | grip-ci-substrate action ratified; git-crypt guardrail crowned #1 risk |
| `22a9e910` | DO-NOT-BUILD deliberation | `/ultracode` wrapper unanimously rejected; tool-map above is the capability |

Re-verify: `grip-sovereign-council --verify 71a972fb` (and so on for the other two).

---

## Quick Reference

```bash
# Run a GRIP non-Anthropic council locally
grip-sovereign-council "Is this design sound?"

# Run the same in CI (GitHub Actions)
# → Use grip-ci-substrate action with provider secrets

# Check Pool-B spend (target: anthropic-cch ≤ 6%)
grip-llm-usage verify

# Check which paths are git-crypt encrypted (before CI-enabling a tool)
git check-attr filter -- lib/agora_pr_review.py

# Verify AGORA green on an open PR
gh workflow run agora-pr-review.yml -f pr_number=<N>

# Verify a council seal
grip-sovereign-council --verify 22a9e910
```
