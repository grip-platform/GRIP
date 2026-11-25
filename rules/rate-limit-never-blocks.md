# Rate-Limited Cannot Block Operations (PARAMOUNT)

Origin: 2026-06-21. V>> directive — *"this can't be a blocker for operations"* —
on a transient Anthropic server 429: **"Server is temporarily limiting requests"
/ "not your usage limit"**. That message is the provider shedding load, NOT the
account hitting its quota. It is retryable and failover-able by construction; a
session that reads it as "blocked" turns a 30-second hiccup into a stalled loop
and an upward poke to the operator — the exact load GRIP exists to remove.

## The two 429s are categorically different (distinguish by the harness's own wording)

A 429 is not one thing. The verdict — retry/failover vs wait/escalate — is read
off the message text, never off the bare status code:

| Class | The harness/provider says (≈) | Verdict |
|-------|-------------------------------|---------|
| **Transient server limit** | "Server is temporarily limiting requests", "not your usage limit", "overloaded", "try again", `Retry-After: N` | **RETRY then FAILOVER.** Never a halt, never a "blocked" status, never a turn-ending yield. The work proceeds — on a backoff, on another auth bucket, or on another provider. |
| **Usage cap** | "you have hit your usage limit", "quota exceeded", "plan limit reached", a reset timestamp | **WAIT or ESCALATE.** A genuine ceiling — back off to the reset, or surface to the operator. Still not a silent "blocked"; it is a *bounded* wait with a known end. |

When the wording is ambiguous, treat it as **transient** (the safer-to-retry
class): the cost of a needless retry/failover is a few seconds; the cost of a
false "blocked" is a stalled operation and operator load. This is the
load-shed-vs-ceiling discriminator — the same shape as
`gate-resolution-escape-hatch.md`'s "the remedy must lie outside the blocked
set": a transient 429's remedy (retry/failover) is always available, so it can
never be a terminal block.

## Response per surface (where the failover actually lives)

The signal is the same everywhere; the failover mechanism differs by surface.

- **GRIP's own LLM calls → HAL provider failover (ALREADY EXISTS).** Every GRIP
  AI syscall bottoms out in `lib/hal_llm_adapter.py`, whose Limit-Sentinel
  (`lib/hal/sentinel.py`) cascades the provider ladder (HAL → Groq → Gemini →
  xAI → neutral fallback) on a 429/overload and records a cooldown in
  `~/.hal/state/hal/limits.json` so a tripped provider is skipped until it
  recovers. A transient 429 on a GRIP LLM call is therefore *already* a non-event
  — it failovers and the call returns. Nothing to add here; this rule names the
  invariant the adapter already enforces, and forbids any new GRIP LLM callsite
  that bypasses it (per `syscall-doctrine.md`).
- **The main interactive `claude` session → harness retry + auth-bucket failover
  (L2).** This surface has no provider ladder — it is the Claude Code harness
  talking to Anthropic directly. A transient server 429 here is resolved by the
  harness's own retry/backoff and, where configured, an auth-bucket failover
  (subscription OAuth → `ANTHROPIC_API_KEY` via `grip-anthropic-external`, per
  `operational-context.md` "Claude Code subscription-auth fallback"). The session
  MUST treat the 429 as a transient retry condition, never report itself
  "blocked", and never end its turn on it. The mechanical half is L2's job; this
  L1 rule fixes the *behaviour* — wait the backoff, continue.
- **The relay `claude -p` spawn → same as the main session.** The VPS
  relay-dispatcher's short-lived `claude -p` (per `operational-context.md`
  "VPS relay CONSUMER") inherits the same harness retry + auth-bucket failover.
  A transient 429 mid-relay is retried/failovered, not surfaced to the channel
  recipient as a failure.
- **Autonomous loops (`/auto`, `/rsi`, `/sprint`, overnight) → backoff and
  CONTINUE, never a turn-ending yield.** A transient 429 inside a depth-0
  autonomous loop is handled exactly like harness compaction in
  `rsi-loop-no-yield.md`: a brief, in-flight relief, **not** a stop. The loop
  backs off (or failovers the LLM call through HAL) and resumes the same wave.
  Ending the turn to "wait out the rate limit" is the forbidden move — it
  re-creates the "why did you stop?" failure that `rsi-loop-no-yield.md` exists
  to kill. Only a *usage-cap* 429 with a reset timestamp may pause the loop, and
  then to the reset, never to the operator.

## L2 mechanical for the usage-cap case (2026-07-13)

The auth-bucket failover primitive (`bin/grip-auth-failover`) gained
`--note-cap [reset]` — the usage-cap complement of `--note-429`. By default
(cap policy `notify`, since GRIP#7274) a cap **notifies the operator with the
reset time, records it, and waits**: subscription first, no new paid spend.
Only under the opt-in policy `engage` does it fail over to the separate API-key
bucket **when one is still usable**; when nothing is left to fail over to (both
buckets exhausted, or the API key is credit-empty) it **notifies the operator +
records the reset**. Never a silent stop. This *surfaces* the cap
(informational — NOT an escalation-to-unblock, which the loop-pause clause
above still forbids: a FYI-notify creates no upward pressure; the failover or
the pause-to-reset handles continuation). Every
`note_cap` branch notifies (`tests/test_grip_auth_failover.py` asserts
never-silent). **HONEST LIMIT:** this catches the **relay `claude -p` spawn** and
any explicit `--note-cap` call; it CANNOT intercept the **interactive REPL's own
inference-cap** (harness-internal — GRIP hooks fire on tool calls, not on the
REPL's inference). For the interactive / AFK loop the mitigation is behavioural:
route heavy autonomous cognition through **Pool B** (HAL, off the weekly limit)
per `pool-a-offload-routing.md`, keep the API-key bucket credit-funded, and
engage `--note-cap` / `--engage` on a manual cap. **Verified incident:**
2026-07-13 an ~18h overnight AFK loop on a second account's weekly-limited REPL hit the
*shared* weekly cap with a credit-empty API fallback and **silently stopped** —
the exact failure this path converts into notify + pause-to-reset.

## The forbidden shape (recognise it and refuse it)

A session — interactive or autonomous — that reports `BLOCKED` / `halted` /
`cannot proceed` / ends its turn, citing a transient server 429, is in
violation. The message says "not your usage limit" precisely so the system knows
it may proceed by another path. Self-imposing a block off a load-shed signal is
the `operational-context.md` "No self-imposed blockers" anti-pattern applied to
rate limits: absence of *this* provider's capacity is not absence of capability.

## Falsification

Wrong if, over the next ≥10 transient-429 events it governs: (a) treating a
transient 429 as retry/failover provably loses work or corrupts state that
halting would have preserved (then a specific class needs a bounded pause — name
it, don't drop the rule); OR (b) the harness/provider wording proves
*indistinguishable* between load-shed and usage-cap often enough that the
two-class discriminator mis-routes more than it helps (then the discriminator is
mis-tuned — sharpen the wording table, keep the default-to-transient); OR (c) an
autonomous loop that backs-off-and-continues on a 429 measurably degrades output
versus a turn-ending wait (refuted by `rsi-loop-no-yield.md`'s same finding for
compaction). Track: false-"blocked" reports off a transient 429 per quarter
(target 0; was the motivating risk on 2026-06-21) + operator "why did you stop?"
corrections traced to a rate limit (target 0).

## Scope

Every surface that issues an Anthropic (or any provider) request: GRIP's own LLM
syscalls (`lib/hal_llm_adapter.py`), the main interactive `claude` session, the
relay `claude -p` spawn, and every autonomous loop. Sits beside
`rsi-loop-no-yield.md` (a transient 429 is a relief valve, never a yield),
`gate-resolution-escape-hatch.md` (the remedy lies outside the blocked set),
`exogenous-anchor-law.md` (HAL failover ≠ self-grant), `syscall-doctrine.md`
(all GRIP LLM calls route through the failover-bearing shim), and
`operational-context.md` "No self-imposed blockers" + "subscription-auth
fallback". The mechanical auth-bucket failover for the main/relay surfaces is L2;
this L1 rule is the behavioural law every surface obeys. Mirror into
`~/.hal/rules/` — HAL is the substrate that already implements the failover half.
