## Origin

V>>, 2026-09-10, pi-harness session in a GRIP worktree. The harness answered
fully but without the closer; the operator corrected: the GRIP canonical way of
output INCLUDES closing with RECOMMENDED BEST ACTION next.

Amended 2026-10-10 (V>> operator correction). Closers had become requests for
him to approve or run steps the agent could take itself -- "tell me to watch PR
#7836 and I'll take it through review and merge" (the merge sweep had already
merged it), "run this bash command", "approve X" for things that happen
automatically. This file framed every closer as a yes-or-no for him, which
invited exactly that. The closer stays; who owns its step is now decided by the
owner test in `rules/canonical-output-style.md`.

## The rule

Every substantive response from ANY harness — Claude Code, Codex, pi,
OpenCode, Cursor, agy, any other — ends with the **`RECOMMENDED NEXT STEP
(<owner>)`** line defined in `rules/canonical-output-style.md`, which is the
single authority for its marker, its owner, its command form and its null
case. This file first called it "RECOMMENDED BEST ACTION next", but
`lib/next_step_router.py` reads only the `RECOMMENDED NEXT STEP` marker, so a
closer written in the old words is parsed as no closer at all. The line names
exactly ONE concrete next step and its owner. When the owner is the agent, it
states what the agent is doing, usually already started, and asks for nothing.
It asks for his decision only when the owner test says the step is his. If the
answer itself WAS the action, the line states the next step that keeps
momentum. This is part of the canonical output style, not a replacement for it:
think out loud, answer short + plain + intuitive, then close with the next step.

Exempt: pure chitchat, one-line factual answers, and tool-only mid-task
turns. The Auto-Clarity rail (full prose for security / irreversible /
multi-step safety) is unaffected — prose first, then the closer.

## Why

An answer without a next step stalls. With one, he sees in a line what happens
next and whose move it is. For a step that is genuinely his, the closer makes it
his cheapest possible decision; for everything else it tells him what is already
happening and asks nothing. It is the session-keep-alive of GRIP's operator
loop.

## Falsification

DELETE this rule if, over ~20 substantive turns, operators never act on the
recommendation AND the block measurably adds only noise (the closer becomes
boilerplate rather than a decision). Mis-tune short of the kill: a closer is
never fabricated. When nothing is left on the agent it ends on the work -- what is
in motion and who owns it -- as `rules/canonical-output-style.md` says. The old
wording here, "no next action; awaiting operator direction", is withdrawn
because that rule bans a null line.

The 2026-10-10 amendment is wrong if, over the next ~20 substantive turns, the
operator corrects a step the agent took as `(mine)` that he says was his to
decide (over-correction), or bounces a `(yours)` step back as something the
agent could have done (under-correction). Either result narrows the owner test;
neither deletes the closer.

## Scope

All harnesses, all substantive turns, all GRIP surfaces. The line is one line,
concrete and falsifiable. A step he owns is one he can refuse; a step the agent
owns is one he can reverse.
