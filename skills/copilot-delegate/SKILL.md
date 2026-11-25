---
name: copilot-delegate
description: Delegates cheap factual queries, explanations, command suggestions, and routine diff reviews to GitHub Copilot CLI (free GPT-4.1) instead of burning Premium Claude tokens. Use when prompt is "ask copilot", "delegate to gpt-4.1", "free tier review", "save Premium tokens", or when auto-mode router routes to copilot.
status: Active
version: 1.0.0
effort: low
triggers:
  - /copilot:ask
  - /copilot:explain
  - /copilot:suggest
  - /copilot:review
  - "ask copilot"
  - "delegate to gpt-4.1"
  - "free tier review"
  - "save Premium tokens"
---

# copilot-delegate

Cost-aware AI orchestration. Routes cheap, single-shot queries to GitHub Copilot CLI (free GPT-4.1) and reserves Claude's Premium context window for what it does best: multi-file reasoning, non-trivial refactors, and test writing.

## Token-Economy Rationale

Claude's 1M-token context is a scarce resource. Every prompt that asks for a one-line shell command, a quick explanation, or a routine diff review consumes tokens that could otherwise be spent on multi-file reasoning. Copilot CLI exposes GPT-4.1 for free under a Copilot subscription — the right place for the cheap stuff.

| Workload | Best home | Why |
|----------|-----------|-----|
| "What does `xargs -I{}` do?" | Copilot | Single-shot factual; no project context needed |
| "Suggest a command to find all PNGs over 1MB" | Copilot | Pure command synthesis; verifiable in one glance |
| "Review this diff for obvious issues" | Copilot | First-pass triage; Claude only if it flags something deep |
| "Explain how OAuth PKCE works" | Copilot | General knowledge; no GRIP context |
| Refactor `auto_mode_selector.py` to extract caching | Claude | Multi-file reasoning, codebase awareness |
| Write tests covering 5 edge cases in this module | Claude | Test design requires understanding the implementation |
| Trace a bug across 4 files | Claude | Mental model spans many files |

## When to Delegate (use Copilot)

- Single factual query with a short answer
- Concise explanation of a general concept (no GRIP-internal knowledge needed)
- Shell command suggestion (one-liner; user verifies before running)
- First-pass diff review (security smells, obvious bugs, style issues)

## When NOT to Delegate (stay on Claude)

- Multi-file reasoning (touches more than one file in the codebase)
- Non-trivial refactors (extract class, rewrite module)
- Test writing (requires understanding implementation under test)
- Anything requiring GRIP knowledge (skill catalogue, mode definitions, KONO state)
- Decisions with multiple valid options (AskUserQuestion territory, not LLM territory)

## Flag Set (verify before depending on these)

The library calls Copilot CLI as:

```bash
copilot -p "<prompt>" --model gpt-4.1 --allow-all-tools --no-color --output-format text -s
```

Sanity check the current CLI surface BEFORE depending on these flags:

```bash
copilot --help | grep -E 'model|format|color|tools'
```

If the flag surface has drifted, update `lib/copilot_delegate.py:_BASE_FLAGS` and document the change in this skill's changelog.

## Slash Commands

| Command | Purpose |
|---------|---------|
| `/copilot:ask <question>` | Surface answer verbatim; no Claude re-reasoning |
| `/copilot:explain <topic>` | Concise explanation pass |
| `/copilot:suggest <task>` | Shell command only, in `bash` block, asks before exec |
| `/copilot:review [path]` | Empty arg pipes `git diff --no-color`; otherwise reviews path |

Each command shells out via `lib/copilot_delegate.py` and surfaces the result.

## Auto-Mode Routing

`lib/auto_mode_selector.py:should_delegate_to_copilot(prompt)` returns `'ask'`, `'explain'`, `'suggest'`, `'review'`, or `None`. The selector inspects the prompt for delegation-eligible patterns BEFORE invoking the more expensive mode-selection LLM call. This is a free heuristic gate.

## Falsifier

Token-economy routing has regressed if either of:

1. Copilot delegation rate drops below the baseline (measured by counting `model_used == 'copilot'` entries in `learning/mode-selection-metrics.jsonl` over a 7-day window) — implies the router has stopped catching delegation-eligible prompts
2. Copilot delegations route work that needed Claude (e.g. multi-file refactors land on Copilot and produce wrong answers) — measured by user override rate

Track both via `/evo`.

## Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-04-25 | Initial creation. Skill + 4 slash commands + library + auto-mode routing. |
