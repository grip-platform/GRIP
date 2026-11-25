---
name: tldr
description: Ultra-compressed answer mode. Returns the load-bearing fact only, no setup, no narrative, no caveats unless safety-critical. Use when operator wants the answer NOW with zero ceremony.
status: Active
effort: low
version: 1.0.0
created: 2026-05-21
user_invocable: true
command: tldr
triggers:
  - /tldr
  - tldr mode
  - just the answer
  - one line
  - short answer
---

# /tldr — Ultra-Compressed Answer

This is the `line` preset of `/clarify` (`skills/clarify/SKILL.md`). Its shared rules apply here too: nothing added, words to type unchanged, 80% ASD-STE100.

Returns the load-bearing fact only. No setup. No narrative. No caveats unless safety-critical. Manual-invocation only.

## Length cap

**≤3 lines OR ≤25 words, whichever hits first.**

Rationale: ≤140 chars too tight (many load-bearing answers span a fact + an anchor: "PR at W7. Council ratified L28 strip."). Single-line denies a falsification clause room when safety demands it. ≤25 words / ≤3 lines preserves Precision-Preservation (load-bearing fact + minimal anchor + optional safety qualifier) while staying recognisably "tldr".

## Behavioural contract

1. Answer in ≤3 lines / ≤25 words.
2. Lead with the load-bearing fact (verb + noun, not preamble).
3. Drop the reasoning trace (canonical-output-style.md Layer 1). EXCEPT safety-gated topics (Auto-Clarity Override per `canonical-output-style.md`) where /tldr is suppressed and full prose returns.
4. Keep evidence tokens load-bearing per Precision-Preservation Invariant (path, line, exact count). Strip decorative refs.
5. No follow-up questions same turn. If clarification needed, ASK next turn.

## Activation

```
/tldr             # this turn only
just the answer   # synonym
one line          # synonym
```

Auto-fires: NONE. Manual-invocation only — avoids `misunderstood_request` 18% baseline failure mode (per `output-tier-default.md`).

## Auto-Clarity Override (non-negotiable)

Pause /tldr and use normal full prose for:

- Security warnings
- Irreversible action confirmations (delete, force-push, drop table, deploy to prod)
- Multi-step sequences where fragment ambiguity risks misread
- When user is confused or repeating the same question

Resume /tldr only on next explicit invocation.

## Examples

**Normal answer** (62 tokens):
> "I checked the build logs and the failure is in the test suite at line 42 of test_auth.py. The assertion is comparing a stale token to a fresh one. You should update the mock to use freezegun."

**/tldr answer** (16 tokens):
> "test_auth.py:42 — stale token vs fresh. Fix: freezegun mock."

**Safety-gated** (auto-suppressed):
> User: "/tldr — should I `git push --force-with-lease origin main`?"
> Reply (full prose, Auto-Clarity Override):
> "Force-push to main is irreversible if a teammate has pulled the prior tip — they cannot fast-forward your rewrite, only reset. Before pressing it: (1) verify no concurrent session has pushed since your `git fetch`, (2) confirm the branch protection allows force-push for your identity, (3) tell the team. The `--force-with-lease` flag only protects against remote-tip changes you have not seen, not against teammates' un-pushed work."

## Token budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~600 |
| Per /tldr invocation | ~30-100 (vs ~300-800 standard) |
| Tests | ~200 |

ROI: saves ~200-700 tokens per invocation when operator genuinely wants tldr.

## Tests

5 Goodhart-resistant tests in `tests/test_tldr.py` per Rule 14:

| Test | Asserts | Mutation it catches |
|------|---------|---------------------|
| `test_tldr_respects_word_cap` | Output ≤ 25 words on a battery of 20 prompts | Bug disabling cap |
| `test_tldr_preserves_evidence_tokens` | Paths/SHAs/exact counts present when prompt asks "where" / "what SHA" | Bug stripping precision under /tldr |
| `test_tldr_suppressed_on_safety` | Auto-Clarity triggers (delete/force-push) produce FULL prose | Bug where /tldr overrides safety rails |
| `test_tldr_no_preamble` | First 3 words contain no "Let me", "I'll", "First", "The answer is..." | Bug re-introducing preamble |
| `test_tldr_lead_is_load_bearing` | First sentence contains a noun OR verb actionable to user | Bug where lead is meta-commentary |

R0 falsifier: if 5 tests pass yet V>> still describes outputs as "too verbose / wrong info / missing the point" >10% of the time, tests are Goodhart-gamed — re-design to assert SEMANTIC correctness via Whisper-round-trip or council judgement, not mechanical word count.

## Related skills

- `caveman` — different compression strategy (token-level prose)
- `plain-language` — register strip without compression
- `eli5` — explanatory expansion (opposite direction)
- canonical-output-style — Layer 2 governs all output; /tldr is the manual override

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-05-21 | Initial — manual-invocation, ≤3 lines / ≤25 words, Auto-Clarity suppression |
