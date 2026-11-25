# GRIP Plan-Tier & the 1M-Context Credit Gate — Source Trace

**Dated note (2026-10-15):** the entitlement trace below is from the Opus 4.6 / Sonnet 4.6
era (its eligibility set enumerates `opus-4-6`/`opus-4-7`) and predates the current model
line-up. It is not wrong about the server-side gate mechanism, but Claude Code now gives
Pro the full 1M window by default on every current model (Opus 5/5.5, Sonnet 5/5.5, Haiku
5.5, Fable 5/5.1) — only the two previous-generation models still gate 1M behind usage
credits on Pro. See `docs/measurements/2026-10-15-pro-plan-vendor-facts.md` for the
current, vendor-sourced picture.

Origin: 2026-05-28. A Pro-plan user (a former client contact) found GRIP unusable: Claude
Code's 1M context window is credit-gated, leaving Pro at 200K. This
document is the **R0 evidence trail** for the `grip-tier` feature — every
claim is traced to a line in Claude Code's own source so the design rests
on verified facts, not assumption.

## The question

Is the 1M-context window enforced **client-side** (something a GRIP user
could flip with an env var / config) or **server-side** (an entitlement
on the auth principal that no client trick bypasses)?

## Verification method

Read Claude Code's own bundle. The last version shipping readable JS is
**2.1.112** (`@anthropic-ai/claude-code@2.1.112`); 2.1.113+ ships a
native Bun binary (JSC bytecode). The readable bundle is checked out at
`~/CodeTonight/TF>>>>/cli-formatted.js` (575,521 lines, `js-beautify`
output). The current installed binary is **2.1.154**
(`~/.local/share/claude/versions/2.1.154`, Mach-O arm64). We trace the
logic in 2.1.112 and confirm the string surface in 2.1.154.

## Finding 1 — the context-window resolver is `ff()` (client-side belief)

`cli-formatted.js` L155772:

```js
function ff(model, betas) {
  if (DISABLE_COMPACT && CLAUDE_CODE_MAX_CONTEXT_TOKENS) return that_number; // env lever
  if (DP(model)) return 1e6;            // model name matches /[1m]/i
  if (betas.includes("context-1m-2025-08-07") && vo(model)) return 1e6;
  if (XV8(model)) return 1e6;           // sonnet-4-6 + internal coral_reef flag
  return 200000;                        // DR1 default
}
```

- `DP(q)` (L155760): `/[1m]/i.test(model)` — a model alias containing
  `[1m]` makes the client *believe* the window is 1M.
- `vo(q)` (L155766): model ∈ {sonnet-4, opus-4-6, opus-4-7} — eligibility.
- `zq6()` (L155757): `CLAUDE_CODE_DISABLE_1M_CONTEXT` set → kill all 1M
  paths.

This only sets the window the client **requests**. It is not the gate.

## Finding 2 — the server returns the real limit; the client adapts DOWN

`JbK()` L430763 parses the server's 400 error:

```text
"input length and `max_tokens` exceed context limit: {input} + {max} > {LIMIT}"
```

The client reads the server's `{LIMIT}` and shrinks `max_tokens`
(`tengu_max_tokens_context_overflow_adjustment`, L430676). The client can
ask for 1M; if the principal isn't entitled, the server caps it and the
client adapts down. **The ceiling is the server's, not the client's.**

## Finding 3 — subscription users cannot inject betas

`Qgq()` L155920:

```js
if (i7()) { console.warn("Custom betas are only available for API key users. Ignoring provided betas."); return }
```

`i7()` (L158217) = subscription/OAuth auth. So on Pro/Max,
`ANTHROPIC_BETAS=context-1m...` is **silently dropped**. The 1M beta is
still auto-pushed by the `[1m]` suffix (`DP`), but that only sets the
client's belief — see Finding 2.

## Finding 4 — the gate's own error names the only two exits

`cli-formatted.js` L214528:

```js
let msg = I7()
  ? "enable extra usage at claude.ai/settings/usage, or use --model to switch to standard context"
  : "run /extra-usage to enable, or /model to switch to standard context";
```

When a 1M request exceeds entitlement, Claude Code itself tells you to
**buy usage credits** OR **switch to the 200K window**. There is no third
(client-trick) door.

## Finding 5 — `CLAUDE_CODE_AUTO_COMPACT_WINDOW` is min'd with the real window

`Jn()` L411414 (the auto-compact resolver):

```js
function Jn(model, settingValue) {
  let real = ff(model, eM());          // the server-entitled window
  if (CLAUDE_CODE_AUTO_COMPACT_WINDOW) {
    let A = clamp(value, 100000, 1000000);
    return { window: Math.min(real, A), ... };   // ← cannot exceed `real`
  }
  ...
}
```

So on a 200K principal, `CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000` resolves
to `min(200000, 1000000) = 200000`. The compaction lever cannot raise the
ceiling — it only moves *when* you compact, downward.

## Finding 6 — the new 2.1.154 env vars are CLI-written, not user overrides

Three env vars appear NEW in 2.1.154 (absent in 2.1.112). They are **not**
client-side entitlement overrides:

| Env var | Role (verified) | Overrides gate? |
|---|---|---|
| `CLAUDE_CODE_SUBSCRIPTION_TYPE` | CLI **writes** `env.X = profile.subscriptionType` for subprocess inheritance + telemetry. Read-back is `env.X \|\| null`. The real gate (`PF()` L294066, L431018) reads the **verified OAuth profile** (`o7().subscriptionType`), never env. | NO |
| `CLAUDE_CODE_RATE_LIMIT_TIER` | Same write+inherit plumbing for `rateLimitTier`. | NO |
| `CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE` (present in 2.1.112 too) | Overrides the **client-side pre-send token guard** `isAtBlockingLimit` (`UM6()` L411494, consumed L397766). Default = `effectiveWindow − 3000`. Bypassed entirely when auto-compact is on. The request still hits the server where the real limit + credits apply. | NO |
| `context-hint-2026-04-09` beta | First-party context-management beta (`d6A()` L504884), subscription-gated by the same `includeFirstPartyBetas`/`Qgq` path. Clears thinking blocks on 422/424 to save tokens. | NO |

Hand-setting `SUBSCRIPTION_TYPE`/`RATE_LIMIT_TIER` only fills a
display/telemetry slot — server-validated, no gate moved.

## Conclusion (verdict)

**The 1M-context window is a server-side entitlement on the auth
principal (plan tier + credit balance). No client env var, beta header,
model-alias trick, or config bypasses it.** This is the correct security
design; finding the lever absent IS the result.

| Plan | Opus 1M | Sonnet 1M |
|---|---|---|
| Max / Team / Enterprise | included | requires usage credits |
| Pro | requires usage credits | requires usage credits |
| API / pay-as-you-go | full access | full access |

## What this means for GRIP (the `grip-tier` feature)

GRIP does not need 1M to be useful — its memory architecture (HOLISTIC
kernel, KONO recall, `/save` warm-start) is built to survive compaction.
The `grip-tier` feature (`lib/grip_tier.py`, `bin/grip-tier`) resolves a
plan tier and applies an advisory envelope:

- **pro** → `CLAUDE_CODE_AUTO_COMPACT_WINDOW ~180K` (compact *before* the
  200K wall — and since `BLOCKING_LIMIT_OVERRIDE` is bypassed when
  auto-compact is on, early auto-compact is precisely the right lever) +
  route heavy-context work to HAL non-Anthropic providers.
- **max** → assume Opus 1M, let the model default stand.
- **api** → full 1M, no routing.
- **auto** (default) → API-key present → `api`; else `pro` as the
  subscription safe-floor (never asserts an entitlement).

The honest user-facing message: *"On Pro? GRIP runs great at 200K. Want
1M? Add usage credits at claude.ai/settings/usage."*

## Falsification

This trace is wrong if a future Claude Code version makes any of the env
vars in Finding 6 an **authoritative input** to the entitlement check
(i.e. the gate reads env instead of the OAuth profile), OR introduces a
client flag that `ff()`/`Jn()` honour above the server limit. Re-verify
against the then-current bundle/binary. Tracked: re-run this trace on each
major CC release that touches context/entitlement.

## Adversarial counter-argument (steelmanned, then adjudicated)

The strongest attack on this trace's *method* (`/falsify-self`, 2026-05-29):

> A native Bun binary compiles its JS to JSC bytecode in the `__BUN/__bun`
> Mach-O section, but Anthropic could equally have lifted the entitlement
> decision *out* of that bytecode into a hand-written native routine in the
> `__text` segment — a few hundred bytes of compiled ARM64 that reads the
> OAuth profile's `subscriptionType`, compares it to a `coral_reef`-style
> flag table, and decides the 1M grant *client-side* before any request is
> sent, while leaving the user-visible error strings (`context-1m`,
> `extra usage`, `exceed context limit`) untouched in the readable string
> table — so a strings-based or JS-only analysis (exactly the TF>>>>
> method) sees only the messaging and concludes "server-gated", never the
> gate itself.

**Adjudication: the premise is true, the conclusion does not follow.** Two
independent falsifiers retire it:

1. **The window is a client *request*, not a client *grant* — the server
   is the unconditional authority.** The client sends `max_tokens` derived
   from `ff()`'s window in the request body (`query_api_request_sent`,
   cli-formatted.js L505625; `max_tokens` computed at L505485). The server
   then returns an HTTP 400 `"input length and max_tokens exceed context
   limit: {in} + {max} > {LIMIT}"` if the principal isn't entitled, and the
   client's `JbK()` handler (L430746) parses it **unconditionally** — no
   `if (auth)` guard, fires on *any* 400 matching the pattern, every auth
   scheme. So even a `__text`-inlined client routine that *grants* 1M
   cannot manufacture server *acceptance*: the inlined check would change
   only *where the client forms its request*, never *who decides the real
   ceiling*. The conclusion ("Pro cannot obtain a usable 1M without
   credits") is invariant to the gate's location.

2. **The claim is settled behaviourally without decompiling `__text` at
   all.** Per `verify-canonical-not-claimed.md`, the authority is an e2e
   probe, not a source read: send >200K tokens on a Pro principal and
   observe truncation/400. "Hidden in `__text`" is therefore simultaneously
   *possible* and *irrelevant* — the observable outcome is the criterion,
   and the JS read was only ever corroborating evidence for it.
   (R0-honest open: this e2e is **un-run from the authoring machine**, which
   is api-tier — full 1M, so it cannot trigger the gate. The probe needs a
   Pro principal with no credits; until run, the verdict rests on falsifier
   #1, the unconditional server-400, which IS observable here. Falsifier #2
   is the *stronger* check and remains the recommended confirmation.)

This is a textbook over-mapping (Gentner): it maps the *surface*
("native code can hide logic" ⇒ "so the gate could be hidden") but not the
*relation that matters* ("final authority over the grant"), which is
unchanged. **It is a sound critique of the method (JS-only reading is not
proof of absence) and a hollow critique of the conclusion (server
authority holds regardless).** The honest correction it earns: this doc's
verdict rests on the *unconditional server-400 mechanism* + the
*behavioural e2e*, NOT on "we read the JS and saw no client gate". The
absence-of-evidence in the JS surface is corroborating, not load-bearing;
the load-bearing fact is that the server is the sole grantor by
construction.

## Context-management levers — what's free, what they actually do (2026-05-29)

Reverse-engineering CC's context-management subsystem (not to bypass the
1M gate — closed — but to find legitimate ways to get more from the 200K a
Pro user *has*). A first pass claimed `BZ8` = "free server-side
tool-result clearing that stretches 200K". A **broly mesh council
verified-canonically refuted that** (H-CTX-STRETCH-1 / H779 FALSIFIED on
its central premise). The corrected, code-grounded picture:

| Mechanism | What it ACTUALLY does | Gated? | Capacity-extender? |
|---|---|---|---|
| **1M window** (`context-1m-2025-08-07`, `Zo`) | A bigger window | **Credit-gated** on every subscription plan (Pro + Max-Sonnet) | Yes — but paid |
| **CC local auto-compact** (`CLAUDE_CODE_AUTO_COMPACT_WINDOW`, default `R85`=180000 L504735) | CC summarises the conversation *locally* via an LLM call, then continues | **Free**, any plan | **Yes** — `grip tier pro` drives it at 180K |
| **Server-side `BZ8`** (`context-management-2025-06-27`) | Clears **prior-turn THINKING blocks** only (`keep:"all"`), and **only when extended thinking is on**. NOT tool results, NOT summarisation. | **Free**, orthogonal to subscription | **No** — thinking-block janitor, marginal input-token trim |
| **`compact-2026-01-12`** (`compact_20260112` edit) | True server-side **summarisation** at ~200K | Free (SDK docs L559344-559364; Opus 4.7/4.6 + Sonnet 4.6) | **Yes — but CC's runtime never sends it** → HAL-direct only |

**Gate proof — `BZ8` IS free + orthogonal to tier** (cli-formatted.js
L156001-156003): `if (firstParty && !DISABLE_EXPERIMENTAL_BETAS && (A||O))
K.push(BZ8)` where `A = USE_API_CONTEXT_MANAGEMENT && false` (dead) and
`O = iO_(q)` (model ∈ Claude-4). No `i7()`/credit/`coral_reef` term;
`i7()` and `BZ8` never co-occur in the bundle. So subscription tier
neither enables nor disables it — **Pro, Max, and API all get `BZ8`
identically.** (This part of the first pass was correct.)

**Payload proof — `BZ8` does FAR less than claimed** (`C85`,
L504723-504734): the entire `context_management` body is

```js
function C85({hasThinking = false}) {
  let edits = [];
  if (hasThinking) edits.push({ type: "clear_thinking_20251015", keep: "all" });
  return edits.length ? { edits } : undefined;   // undefined when thinking off
}
```

So `BZ8` clears only earlier **thinking** blocks (keeping all answers + tool
I/O), and emits nothing at all when thinking is off. It does **not** touch
the tool-result mass that dominates a long agentic transcript. The "stretch
200K" claim conflated *clearing* with *summarisation* — refuted.

**Corrected bottom line for `grip tier pro` / Max:**
- `BZ8` is free and active on every tier, but it's a **thinking-block
  janitor** — a small input-token (cost/latency) trim when thinking is on,
  **not** a window extension. Honest framing unchanged: Pro is 200K; the
  real 200K-stretch is **CC local auto-compact (180K) + KONO memory**.
- **`i7()` × `BZ8` = orthogonal.** Max gets `BZ8` identically to Pro. Max's
  capacity advantage is **Opus 1M** (separate credit-*included* path),
  unrelated to `BZ8`. There is no `BZ8`-based Max advantage to harvest.
- **The genuine unclaimed free lever is `compact-2026-01-12`** (real
  server-side summarisation) — CC's own runtime never sends it (only
  `clear_thinking` via `C85`), so **HAL** sending
  `betas:["compact-2026-01-12"] + context_management:{edits:[{type:"compact_20260112"}]}`
  directly is real headroom, not a duplicate of `BZ8`. That's the actionable
  follow-up, on the HAL path, not the CC-session path.

**H-CTX-STRETCH-1 / H779 verdict: FALSIFIED** for all tiers as a
*capacity* claim (Pro / Max-Opus / Max-Sonnet / API) — `keep:"all"` +
thinking-only scope cannot enlarge a usable window. A *cost-trim* sub-claim
(fewer re-sent input tokens when thinking on, on metered API/overage paths)
survives but is small and bounded by accumulated thinking text, not the
larger tool-result mass. Sharpest remaining measurement: a thinking-on
tool-heavy session comparing cumulative `usage.input_tokens` with `BZ8`
present vs `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` control — but
"extends capacity" stays falsified regardless.

**Process note (R0 / verify-canonical):** the first pass read the `BZ8`
*gate* (L156003) but not its *payload* (`C85` L504723); the mesh council
read the payload and refuted the capability claim. Lesson: a beta being
*sent* says nothing about what it *does* — verify the payload builder, not
just the push condition.
