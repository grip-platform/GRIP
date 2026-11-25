---
name: hal-provider-refresh
description: Refresh the HAL provider catalog — research current models across providers, adversarially verify in two passes so no hallucinated model enters the catalog, and diff against config/providers.yaml and config/model_catalog.yaml. Human-gated merge.
---

# /hal-provider-refresh

Run the HAL provider catalog refresh workflow: research current models across all
providers, adversarially verify (two-pass — no hallucinated models enter the catalog),
diff against the current `config/providers.yaml` + `config/model_catalog.yaml`, and
open a PR proposing the adds/sunsets for human review.

## Usage

```
/hal-provider-refresh
```

No arguments. The workflow runs automatically and opens a PR on branch
`feat/hal-catalog-refresh-<YYYY-MM-DD>`.

## What it does

1. **Research** — fans out one web-search agent per provider (Anthropic, OpenAI,
   Google Gemini, Moonshot Kimi, Alibaba Qwen, DeepSeek, xAI, Mistral, Groq,
   OpenRouter). Each agent fetches the provider's canonical models + deprecations page
   and returns only facts tied to a real source URL.

2. **Adversarial verify gate** — a second independent agent re-checks every model ID
   from step 1. Any model not confirmable from an official source is marked
   `verified=false` and dropped. This is the anti-hallucination core: a model with no
   second-source confirmation never enters the PR. (This gate caught claude-fable-5,
   claude-mythos-5, gemini-3.5-pro, qwen3.6-27b, and two grok-4.20 variants in the
   June 2026 run.)

3. **Diff** — compares verified findings against the current catalog. Produces:
   - **ADDS**: new model IDs with full `model_catalog.yaml` one-liner metadata
   - **SUNSETS**: catalog IDs that appear on a provider deprecations page (with source URL)
   - **ROUTING REPOINTS**: any sunset ID used in `routing.fallback_chain`, `tier_chains`,
     `council`, or `default_model` — flagged for human review, not auto-repointed
   - **SUSPENSION ALERTS**: models taken down (export-control, ToS) — never added

4. **PR** — opens a `feat/hal-catalog-refresh-<date>` branch with the two-file diff
   and a plain-English PR body including a verified-models table (with source URLs),
   a sunsets table, and a "⚠ routing repoint required" section.

## Human-gated merge (non-negotiable)

The workflow opens the PR; it does **not** merge. Rationale: the catalog drives which
models HAL actually routes to, and routing repoints touch live substrate behaviour.
AGORA review + human merge is the exogenous anchor (`rules/exogenous-anchor-law.md`).
Under the closer's owner test (`rules/canonical-output-style.md`) this merge is the
Irreversible class -- a production change to live routing -- so it stays `(yours)`
even when AGORA is green; everything before it (research, verify, open the PR) is
`(mine)`.

## Verify gate — why it matters

A naive "scrape and add" updater will add hallucinated, preview, or suspended models.
The two-source adversarial verify is what makes the workflow safe to run unattended-to-PR.
Without it the workflow is net-negative (pollutes the catalog faster than humans clean it).

## Invocation (entry point)

`/hal-provider-refresh` runs `.claude/workflows/hal-provider-refresh.js` through
the Workflow tool, in a session. There is no bin any more: `bin/hal-provider-refresh`
(2026-06-24 to 2026-10-18) called a `lib/workflow_runner.py` that no commit ever
added, so it never ran; its only recorded run died on that missing file. It was
removed rather than left standing (rules/no-stubs.md).

## Schedule

The nightly job is `bin/grip-model-watch` (`lib/model_watch.py`,
`config/launchd/com.grip.model-watch.plist.template`, 04:17 local). It reads every
provider's own `GET /models`, diffs the served ids against both catalogues, and keeps
one GitHub issue current with a dogfood-domain checklist per new id. It calls no
model and opens no PR; this workflow is the deeper in-session pass that fills the
rows from the vendor's pages. Manual trigger: `/hal-provider-refresh` in a session,
or `bin/grip-model-watch` for the list diff (`DRY_RUN=1` to skip the issue).

## Workflow source

`.claude/workflows/hal-provider-refresh.js`

## Landing a single model

This skill answers "what does each provider serve now" across the whole
catalogue. `/dogfood-domain` takes one verified model from that answer and lands
it: proving access separately from documentation, classifying the billing path,
placing the catalogue and cost rows in both repositories, and deciding
reachability without letting a newly announced model become a default. Reach for
it whenever a refresh surfaces a model that a launch flag should be able to
name.
