# GRIP guard mod (M2) — an organisation-tier guard, and why it is the second-best fix

Status: DESIGN. No spike in this change (the M1 witness spike is the built slice;
M2 is specified here against Claude Code's own `sec-default`, the canonical
organisation guard). Pairs with [`grip-witness-mod.md`](grip-witness-mod.md) (M1).

> The headline, before any of the mechanics: **a mod is the wrong place to put an
> organisation's hard security floor.** The real fix is a **managed PreToolUse
> hook**. M2 is a defence-in-depth layer that adds what a PreToolUse hook cannot
> do — it is not the enforcement of last resort, and it must never be sold as one.

## Threat model

An organisation deploys Claude Code to people who can install their own plugins.
Function hooks give *every* plugin a say on *every* event, in chain order, and a
person's plugins sit in the **user** tier. The organisation needs to keep certain
controls out of reach of the plugins a person installs:

- an **unknown plugin** must not load and join the chain;
- a person's plugin must not **lift a deny** the organisation set;
- what other mods **do** (the `$` calls they make, the tools they invoke) must be
  **logged**, not just permitted.

Claude Code already ships a guard for exactly this: **`sec-default`**, seated
first in the **prepend** tier wherever managed settings are present. M2 is GRIP's
guard in the same shape, extended to log other mods for the M1 witness. Reading
`sec-default` is a prerequisite for building M2 — it is the reference
implementation, and this design cites its moves rather than reinventing them.

## The tier system, and why it decides everything

The chain's tiers, outermost first: **prepend → user → append → builtin → core**.
`prepend` and `append` are the managed plugins an administrator lists; `user` is
what a person installs; `builtin` ships in the binary; `core` is the engine.

Two facts about tiers drive the whole design:

1. **The outermost seat sees every event first.** A guard in `prepend` runs before
   any user plugin, so it can refuse, log, or re-assert before a person's mod acts.
2. **`next.to(e, tier)` — skipping past the user tier — is refused outside a
   managed tier.** This is the load-bearing limit. A guard loaded with
   `--plugin-dir` (the `inline`/user path) **can only pass**: its one move that
   matters is refused. So M2 is a guard *only when the organisation deploys it
   through managed settings into the prepend tier.* Deployed any other way it is a
   no-op that looks like a guard — the most dangerous kind of theatre.

## What M2 does

### 1. Refuse unknown plugins at `plugin.register`

`plugin.register` fires once per hooks module the engine is about to load, and its
input is **pinned by the host**: `{ name, tier, root, version, provenance, uses }`,
where `uses` is the scanned `{ events, calls, env }` — the same lists
`claude plugin validate` prints. The guard returns `{ refuse: reason }` and the
module never joins: no hook, no noun, no tool, no command.

```typescript
on("plugin.register", { tier: "user" }, async ($, e, next) =>
  isKnown(e) ? next(e) : { refuse: refusal(e.name) })
```

- **Key the allowlist on what the engine pins, not on what a module says.** The
  types are explicit that `name`, `root`, `version` and `provenance` "are its own
  word… a rule keyed on them is one a rename walks past." So `isKnown` matches on
  the **engine-pinned `tier` and scanned `uses`**, and treats the self-reported
  name as a label only. A person's copy carrying an organisation mod's *name* is
  still in the `user` tier and is still refused. (This is the same census-hash
  trust argument the M1 witness makes for exempting GRIP's own mods, and it carries
  the same limit: it is a deployment argument, not a cryptographic identity.)
- **Read policy from managed settings only** — `$.settings.read({ source: "policy" })`
  — so the same key in a person's, a project's or a `--settings` file neither turns
  the guard on nor off.
- **Fail closed.** If the policy read is refused or the hook fails, its `.catch`
  refuses the module (see §3). A policy that cannot be read keeps every person's
  mod out.

### 2. Log other mods' API calls

Every call a mod makes on `$` (`fs.write`, `http.fetch`, `process.run`,
`model.complete`, `tool.call`, …) is itself an event the host serves, hookable by
name and by `on("*")`, and **`next.origin` names the calling plugin and its tier**
— set by the host alone, from the environment the call came from, so nothing a
plugin writes reaches it. M2 sits outermost and logs the API surface of every other
mod against that pinned origin:

```typescript
on("*", ($, e, next) => { log(next.origin, next.event, e); return next(e) })
```

This is the guard's *observability* arm and it is what PreToolUse cannot give: a
managed PreToolUse hook sees **tool calls**, but not the internal `$` calls another
mod makes between them. M2 logs those, and — because it is the same origin the M1
witness commits — the log joins the witness record: *which mod, in which tier, did
what, in what order, committed under the witness key.* Logging is watch-only: it
never blocks (that is §1 and §3), so a logging failure is swallowed, never turned
into a deny.

### 3. Re-assert deny decisions in `tool.check`, with a refusing `.catch`

`tool.check` resolves to `{ decision: "allow" | "ask" | "deny", reason?, rule? }`,
and **any hook may answer any verdict** — so a person's plugin installed to stop
permission prompts (`() => ({ decision: "allow" })`) would also lift an
organisation's deny rule. M2 holds the deny, in `sec-default`'s exact shape:

1. **Run the chain as it is.** If the answer is already a deny, or no link that may
   hold a person's plugin answered more permissively than the verdict handed to it
   (read off `next.trace`, whose tiers the engine pins), the answer passes — nothing
   loosened it.
2. **Otherwise re-run past the user tier** — `next.to(e, "append")`. That verdict
   never passed through a person's plugin, so neither its decision nor its rule can
   have been rewritten. The two runs differ by the user tier alone, so **a deny here
   that names its rule is a deny rule the user tier loosened**, and it is returned
   in place of the chain's answer. Only a deny that *names a rule* counts; a deny
   from a tool's own check or a settings hook (no rule) is not re-asserted.
3. **`tool.check` pins the question** (`tool`, `input`, `tool_use_id`), so no hook
   can get the rules evaluated on one command and run another; a rewrite belongs to
   `tool.call`, which runs before any of this.

**The refusing `.catch` — fail closed.** If the guard hook itself throws or overruns
its budget, its `.catch` answers from the one run it can read:

- a **deny stands** (never loosen on failure);
- a verdict **no person's plugin loosened stands**;
- one they **loosened**, or a run that **rejected**, is **refused** — because the
  deny rules were never consulted, the safe answer is to refuse the call rather than
  let an unconsulted-loosened allow through.

```typescript
on("tool.check", reassert).catch(async ($, e, next) => {
  const last = await next(e).catch(() => undefined)   // replay-safe
  return denyRulesHold(await policy()) ? heldAnswer(last, next.trace) : last
})
```

This is the opposite of the M1 witness's `.catch`, which **passes through** (a
recorder must never block). The guard's `.catch` **refuses** (a guard must never
fail open). Same mechanism, opposite polarity, because the two mods have opposite
jobs — and confusing them is the single worst mistake this design can make.

## Why the real fix is a managed PreToolUse hook

Everything above is true *only while M2 is loaded, in the managed tier, with a live
worker*. A **managed PreToolUse hook** — a classic settings hook the engine runs
before the permission decision — does not share those preconditions:

| | managed **PreToolUse hook** | **M2 mod** (function-hook guard) |
| --- | --- | --- |
| Enforcement layer | the engine's own permission path | inside the plugin chain |
| Survives a mod **worker crash** | **yes** (it is not a mod) | **no** — three worker crashes unload every non-built-in mod, guard included |
| Can be **remotely switched off** by disabling a plugin | no (it is managed settings) | yes |
| Runs on a **clean-lane** call that loads no mods | depends on the lane's hook policy, not on mods | **no** — no mods loaded, no guard |
| Needs **managed-tier deployment** to have any power | it *is* managed settings | yes — loaded with `--plugin-dir` it can only pass (`next.to` refused) |
| Can a user plugin **lift** it | not the deny itself | yes, unless M2 re-asserts (§3) |

So the organisation's **hard floor** — "this tool call is denied, whatever any
plugin says" — belongs in a **managed PreToolUse hook** (and managed `deny` rules),
which the engine enforces deterministically and which a person's plugin, a worker
crash, or a remote plugin-disable cannot reach. A mod guard that can be silently
unloaded by three crashes is not a floor; it is a curtain. M2 must be deployed
*behind* that floor, never *instead of* it.

**The strongest objection to this floor, and what it takes to answer it.** A
managed PreToolUse hook is a command the engine runs, and the vendor's own hooks
reference is blunt about a command hook that fails: "a policy hook with a wrong
path or a crashing script lets everything through". Until Claude Code 2.1.295
that was the end of the matter. That release added `onFailure: "block"` for
command and HTTP hooks, so that, in the release note's words, "a hook that can't
start, times out, or exits with an unexpected code blocks the action". The floor
is therefore a floor only under three conditions, and the managed-settings design
has to state all three: `onFailure: "block"` is set on every safety-class hook;
the hook is dependency-free, because a missing interpreter or virtual
environment would otherwise take the whole floor down at once (which is why
GRIP's plan for its own gates keeps Bash out of the `onFailure: "block"` set, so
the repair route stays open, per `rules/gate-resolution-escape-hatch.md`); and
the hook exits 0 or 2 only, since under `onFailure: "block"` any other exit code
reads as a failure, an allow JSON notwithstanding. Without these a managed hook
is a curtain of a different cut: it fails open on its own crash exactly as a mod
fails open on its worker's. The comparison table above assumes them.

## What M2 adds on top of the managed hook

If the managed PreToolUse hook is the floor, M2 is worth building only for what the
floor cannot do:

1. **Load-time admission (`plugin.register`).** A PreToolUse hook fires on *tool
   calls*; it has no say in *which plugins load*. M2 refuses an unknown mod before
   it ever joins the chain — a gate that simply does not exist at the PreToolUse
   layer. This is M2's most valuable, least-replaceable move.
2. **Cross-mod API-call logging.** PreToolUse sees tool calls; it does not see the
   `fs.write` / `http.fetch` / `process.run` / `model.complete` calls another mod
   makes on `$` between them. M2 logs those against the engine-pinned `next.origin`,
   giving the organisation an audit of *what its people's mods actually do*, not just
   which tools they touched.
3. **In-chain deny re-assertion.** A user-tier *function-hook* plugin can loosen a
   settings deny inside the `tool.check` chain. M2 catches that loosening and
   re-asserts — defence in depth beside the managed hook, covering the function-hook
   surface the classic hook does not itself arbitrate.
4. **Witness integration.** M2's log and refusals are events the M1 witness commits,
   so "the guard refused plugin X" and "mod Y called `http.fetch`" become anchored,
   joinable, tamper-evident records rather than debug lines. The guard and the
   witness share the per-prompt-id join and the same key custody.
5. **Policy-shaped refusals.** M2 reads the organisation's managed policy
   (allowlists, `allowManagedModsOnly`-style options) and refuses in its terms, with
   the one line that names the plugin and the reason — the same shape `sec-default`
   already ships.

## Coverage limits and attack list

M2 inherits every limit the M1 witness lists (clean-lane loads no mods; remote
switch-off; three worker crashes unload every non-built-in mod) **and these matter
more for a guard than for a witness**: a witness that is unloaded loses a record; a
guard that is unloaded loses a *control*. Guard-specific attacks, published beside
any "M2 protects the organisation" claim:

1. **Wrong-tier deployment** — loaded with `--plugin-dir`, `next.to` is refused and
   the guard can only pass: it looks like a guard and enforces nothing.
2. **Worker-crash disarm** — three crashes unload M2; the managed PreToolUse hook is
   what still stands. Anyone citing M2 as the floor is citing a curtain.
3. **Remote switch-off** — a managed/remote setting can disable the plugin.
4. **Rename walk-past** — an allowlist keyed on a plugin's self-reported
   `name`/`provenance` is bypassed by a rename; only engine-pinned `tier`/`uses` hold.
5. **In-tier spoof** — a mod in the *same* managed tier with the *same* uses-hash is
   indistinguishable from a known-good one (§1's trust limit).
6. **`tool.check` cost** — hooking `tool.check` makes the engine raise that event on
   *every* tool call (it otherwise raises it only when some plugin hooks it): a
   performance and blast-radius cost of seating the guard at all.
7. **Fail-open polarity error** — if M2's `.catch` ever passes through like the
   witness's, the guard fails open on its own failure. The refusing `.catch` (§3) is
   load-bearing; a refactor that "makes it consistent with the witness" breaks it.

## Relationship to `sec-default`

`sec-default` is Anthropic's built-in organisation guard and the reference M2 is
built against: same three moves (continue past the user tier with `next.to`, refuse
by name/tier, pass), same `plugin.register` + `allowManagedModsOnly` admission, same
`tool.check` deny-hold with a fail-closed `.catch`, same managed-settings-only
policy read. Its source ships beside the binary's types, under
`plugins/marketplaces/claude-code-plugins/mods/sec-default/` (the copy "as it is
built into the binary"; that folder is rewritten with each Claude Code release,
so these pointers are file and function names, never line numbers). The moves
live there as follows. **Continue past the user tier** is `next.to(e, 'append')`
throughout `hooks/register.ts`, on every `classic.*`, prompt, `settings.read`,
`session.compact` and `tool.list` seat. **Refuse by tier** is the
`plugin.register` seat in `hooks/register.ts` (matcher `{ tier: 'user' }`, which
records the module's provenance in `admittedAs` and asks
`Policy.isManagedModsOnly` from `hooks/policy/is-managed-mods-only.ts`, refusing
through `hooks/managed-mods-only-refusal/`), and the `tool.register` seat beside
it, which lets `prepend`/`append` callers through and asks `registerRefusalOf`
from `hooks/tool-register-refusal/` for a `user`-tier one. The **`tool.check`
deny-hold** is the `tool.check` seat in `hooks/register.ts`: it runs the chain,
reads which links a person's plugin rewrote off `next.trace`
(`HeldVerdict.rewrittenByUsers`), consults `Policy.denyRulesHold` from
`hooks/policy/deny-rules-hold.ts`, re-runs past the user tier with
`next.to(e, 'append')` and keeps the held verdict when `HeldVerdict.holdsOver`
says it is the tighter one; its fail-closed `.catch` answers through
`caughtAnswer` in `hooks/held-verdict/caught-answer.ts`. The managed-only policy
read is `Policy.SOURCE`, `{ source: 'policy' }` in `hooks/policy/source.ts`. M2
differs in two ways: it **logs other mods' `$`-call surface for the
M1 witness** (which `sec-default` does not do), and it is **GRIP-policy-shaped**
(GRIP's allowlists, GRIP's key custody, GRIP's witness join). Where `sec-default`
and M2 overlap, `sec-default`'s behaviour is the specification; M2 does not
reimplement it, it extends it. An organisation running `sec-default` in prepend
already has §1 and §3; M2's marginal value is §2 (cross-mod logging) and the witness
join — which is the honest case for building it.

## Open questions / next steps

- Decide whether M2 is a fork of `sec-default` or a companion mod seated beside it
  (companion avoids re-owning the deny-hold logic; fork centralises policy).
- Specify the managed PreToolUse hook that is the actual floor, and ship it *first*
  — M2 without it is not a security control.
- Define the cross-mod log's schema and its join to the M1 witness commitment, and
  the retention/disclosure rules for a log that names what every mod did.
- Reconcile with the crash-unload threshold: can the guard detect it was reloaded
  after a crash, and re-assert that its floor is the managed hook, not itself?
