---
name: verbind
description: Coin a slash verb GRIP does not know and bind it, through one Koppel round with the operator, to an existing read-only operation. Use when the operator types "/verbind <new-verb> <what they want>", or runs "/verbind refine <verb>", "/verbind list" or "/verbind forget <verb>". A bound verb is applied with no further question; a deferred verb is never asked about again.
---

# /verbind — a coined verb becomes a binding

The operator types a verb that does not exist yet, with words saying what they
want: `/verbind shipwreck the stale branches`. Verbind asks them once, in a
**Koppel** round (Afrikaans *koppel*: to couple), what `/shipwreck` should mean,
binds it to one existing read-only operation, and applies it. The next
`/verbind shipwreck ...` applies the binding with no question. Contract:
`docs/VERBIND.md`. Formal model: `formal/VERBIND.lean`.

Why `/verbind shipwreck` and not `/shipwreck`: measured on Claude Code 2.1.288,
an interactive session rejects an unknown leading slash command ("Unknown
command: /shipwreck") before any hook or model sees it. `/verbind` is this
skill, so its form always arrives.

## What to do when this skill runs

1. **If a `[VERBIND]` block is already in context for this prompt** (the hook
   ran the gate), follow it exactly and stop here.
2. **Otherwise run the gate yourself**, passing the operator's prompt verbatim:

   ```bash
   "${CLAUDE_DIR:-$HOME/.claude}/bin/grip-verbind" gate --prompt '<the full prompt, starting /verbind>'
   ```

3. Read the JSON. Its `instruction` field, when present, is the next step:
   - `action: ask` — run the Koppel round: `AskUserQuestion` with exactly the
     given questions, then `grip-verbind refine <verb> --kind K --target SLUG
     --args '<words>' --note '<their reason>'`, or `refine <verb> --defer`.
   - `action: apply` — run the bound operation as the instruction says.
   - `deny`, `defer` or `none` — answer the prompt as plain text. Say the
     `surface` line once.

Never bind a meaning the operator did not choose.

## The other subcommands

```bash
grip-verbind list              # bindings, their quality gates, deferred verbs, receipt chain
grip-verbind list --readonly   # the operations a v1 binding may target
grip-verbind refine VERB --kind skill --target SLUG --note "why"   # correct a binding
grip-verbind forget VERB       # drop a binding or a deferral; the verb is asked again
grip-verbind ultralearned VERB --seal ID   # record the /ultralearn seal (a promotion gate)
```

## What v1 will and will not do

- **Bindable:** only operations on `config/verbind-readonly.json`, checked
  against the catalogue at run time; agents only if they declare no tool beyond
  Read, Grep and Glob.
- **Trusted:** only the operator's own interactive session. A relayed WhatsApp
  or Slack prompt, and any headless session, is never asked, never binds and
  never applies an existing binding: authority is re-checked on every use, so a
  binding fixes what a verb means, never who may use it. If that check itself
  fails, the answer is no.
- **No autonomous binding.** A Koppel answer yields RATIFIED; CREATE is refused.
- **Recommendations are off.** The meaning floor ranks the catalogue but
  labels nothing "(Recommended)": on its blind bank 5 of the 6 labels it would
  have given were wrong (`docs/measurements/2026-10-03-verbind-floor-blind-bank.md`).
- **Promotion** at two independent uses, and only once tested, monitored and
  ultralearned, opens a GitHub issue; it never writes a skill by itself.
- **Every decision is sealed** with the outcome it actually took (bound,
  ratified, deferred, denied or exhausted): a chained receipt in
  `state/verbind/` and a hash-only leaf on the IDR chain.
