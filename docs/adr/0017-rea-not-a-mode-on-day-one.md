# Decision: reverse engineering is NOT a GRIP mode on day one (2026-10-10)

Authors: V>> (operator), Claude Fable 5.1 (main session), with the seats named
below. Context and the full plan: `plans/lucky-zooming-graham.md` (gitignored
plans directory; the facts it rests on are restated here).

## The question put to two independent deliberations

> GRIP is adding the `morluto/rea` reverse-engineering tool server as a
> launchable harness entry plus an always-on MCP server plus a skill whose
> first section is an authorisation gate. Should reverse engineering ALSO
> become a GRIP mode on day one -- a mode's only live effects being
> auto-loaded skills, a token budget, a prompt injection and a default council
> level -- or should the mode be the N=2 promotion target, created only once
> two independent reverse-engineering sessions show the gate was needed and
> not auto-loaded? Name the evidence that would change your answer.

## Verdict

**No mode on day one. Harness entry, always-on MCP server, policy gate and
wrapper skill ship now; the mode is the N=2 promotion target, decided by
hypothesis H-RE-MODE.**

## The two records

1. **Ultrathink trio** (`bin/grip-ultrathink --rounds 2 --session-fable`):
   seats Fable 5.1 (this session, position file), grok-4.6 standing in for
   GPT-6 Astra (the Codex seat failed with exit 1 at 7 s and the paid HAL
   rung is off by policy; the fallback kept the vendors disjoint: anthropic,
   xai, deepseek) and DeepSeek v4 Pro. All three positions: N=2 target, not
   day one. Sealed sha256 `264b9095a2e97c67…`, IDR leaf
   `precog-1791635244-1ccb275d`, record
   `state/ultrathink/264b9095a2e97c673f1843ce7c2117ba00845cf24784770a62e0e69a4cfdadf8.json`.
2. **Sovereign council** (`bin/grip-sovereign-council`, zero Anthropic calls):
   speakers deepseek-v4-pro (cut off at its token budget, leaning N=2),
   grok-4.5 (judged best), gpt-oss-120b via groq (N=2); the GPT-6 Astra seat
   failed (Codex exit 1) and is counted as absent, so 3 of 4 spoke. Judge
   gemini-3.1-pro-preview: *"defer creating the reverse-engineering mode until
   the N=2 promotion criteria are met, unless pre-launch evidence of token
   exhaustion, compliance mandates, or necessary auto-loading justifies a
   day-one implementation."* Seal `8f8ea2bc87d38014`, IDR leaf
   `precog-1791635044-f28adacf`; re-verify with
   `grip-sovereign-council --verify 8f8ea2bc87d38014`.

## The evidence the seats said would change the answer

- Two or more real sessions where REA ran, authorisation framing mattered and
  the `reverse-engineering` skill was not in context (grok-4.5, deepseek, the
  trio's Astra stand-in) -- this is H-RE-MODE verbatim.
- Repeated context overflow tied to REA observation volume under the default
  budget (grok-4.5, groq) -- a mode's `token_budget` lever.
- A compliance requirement for a named mode in audit logs (grok-4.5, groq).
- Operator demand: more than 10% of sessions invoking the skill (groq).

## One concern R0-tested, not waved through

grok-4.5 and DeepSeek both said target authorisation is prompt-level only and
asked for a hard link -- a target allowlist or a skill-load prerequisite before
REA tools are callable. Tested against the code and the rules: REA's inspection
tools read bytes the agent already reaches with `cat`, `nm` and `otool`
through the Bash tool, so a path allowlist on reads locks nothing the session
does not already have, while costing every legitimate inspection a switch
(`rules/full-power-stop-override.md`). The hard link belongs where the
capability actually changes -- running the target, opening it in a browser,
observing a live UI, bulk-extracting an archive -- and that is exactly where
`lib/rea_tool_policy.py` puts it (`RUNTIME_TOOLS`, denied unless
`GRIP_REA_ALLOW_RUNTIME=1`). DeepSeek's own falsifier stands as the forward
test: a red-team session in which the model performs an unauthorised read-only
REA call without the skill loaded would argue for a global policy restriction,
not for a mode.

## What this decides, and what it does not

Decided: no `modes/definitions/reverse-engineering-mode.yaml` in this batch of
work. Not decided: whether one is ever built -- that is H-RE-MODE
(`lib/hypothesis_engine.py`), deadline 2026-11-09, prediction "≥2 sessions
with `mcp__rea__` calls and no skill activation → a mode is warranted".
