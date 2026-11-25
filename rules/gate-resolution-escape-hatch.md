# Gate Resolution Escape-Hatch + Gate-as-RSI-Target (PARAMOUNT)

V>>'s insight: gate-self-edit is not merely a danger — the gate primitive is
itself a legitimate RSI target. This rule captures both halves (the missing
retroactive `/learn`), and is the first dogfood of RSI on `domain[GATES]`.

## Invariant 1 — Resolution Escape-Hatch (the deadlock-prevention law)

> **A blocking gate MUST leave open the operations needed to RESOLVE its own
> blocked condition. A gate that can block its own resolution is a deadlock bug.**

The failure shape: a gate denies a class of tool calls so broadly that the
*remedy* for the blocked condition falls inside the denied set → the operator/loop
is trapped, and only an out-of-band (external, ungated) actor can recover.

Audit (2026-06-08): the **context gate** was the SOLE total-block gate (denies
ALL tools at the ceiling) and so the only one with this bug. Its escape hatches:
(a) the MEMORY.md-Edit exemption (partial), (b) the fix shipped today —
`_handle_critical` now OBSERVEs-and-continues for both autonomous + interactive
(never HALT), (c) the proper structural relief ② (ultraclear→/clear marker-watcher
— clear the window, never edit the gate). Every NARROW-scope gate
(dependency-guardian, channel-scope, guardrail-evasion, cc, gh-default-repo,
cross-org, grip-hal-repo-protection) is safe BY CONSTRUCTION: their resolution
(read elsewhere / use the right scope / write non-evading, simpler code) lies
OUTSIDE the denied set, so they cannot deadlock. The lesson is total-block gates
specifically — they need an explicit resolution escape hatch.

## Invariant 2 — Gate-self-edit: forbidden at RUNTIME, valid as RSI ENGINEERING

V>>'s synthesis (the half easy to miss):

| Mode | Verdict |
|------|---------|
| **Runtime bypass** — edit a gate to let your CURRENTLY-blocked action through | **FORBIDDEN.** Ultimate guardrail-evasion (`no-guardrail-evasion.md`) + bootstrap-authority self-grant (`exogenous-anchor-law.md`). The deadlock that blocks GRIP (but not an external human) from doing this is a SAFETY FEATURE, not a bug. |
| **RSI engineering** — refactor / optimise / enhance / fix-deadlock-bug in the gate CODE, via CBC → tests → CI → AGORA | **VALID + VALUABLE.** The gate primitive is an RSI target. Improving it (with past learnings, like this deadlock) is how GRIP gets better at not-deadlocking. Ratified exogenously by AGORA+CI; the authority-touching part keeps V>> in the loop (bootstrap carve-out). |

The line is NOT "never touch gates." It is: **improve gates as ratified dev work;
never bypass them at runtime.** Resolve a runtime deadlock STRUCTURALLY (②: clear
the window) — never by self-editing the gate to wave your blocked action through.

## RSI-on-GATES mandate

- Every NEW blocking gate is audited against Invariant 1 before shipping: name the
  blocked condition, name its resolution, prove the resolution is NOT in the
  denied set (or add an explicit exemption / structural relief).
- Gate improvements ride the normal RSI loop (CBC, Goodhart tests, PR → AGORA).
  The meta-gate-test (`tests/test_gate_escape_hatch.py`) enforces Invariant 1
  mechanically: every gate that can return a total/broad deny must declare its
  escape hatch. It is no longer future or deferred — it exists, it runs, and on
  2026-08-30 it caught a real one.

## Falsification

Wrong if, over the next ~10 gate changes: (a) a total-block gate with NO escape
hatch never once deadlocks (Invariant 1 over-cautious → relax to advisory); OR
(b) encoding the escape-hatch audit adds friction without preventing a real
deadlock (the deadlock incident rate was already 0); OR (c) a bounded,
AGORA-ratified runtime self-gate-edit is ever demonstrably safer than the
structural alternative (Invariant 2's runtime ban is then too strict — narrow it,
never drop it). Track: gate-deadlock incidents/quarter (target 0; was ≥1 on
2026-06-08) + manual out-of-band gate interventions (H839, target 0 post-②).

## Scope

Every PreToolUse/hook gate in `hooks/*.py` + `lib/gates/*.py`. Sits beside
`exogenous-anchor-law.md` (bootstrap authority), `no-guardrail-evasion.md`
(runtime bypass forbidden), `efficiency-rules.md` Rule 8 (the context gate it
fixes). Mirror into `~/.hal/rules/` if a HAL gate gains a total-block mode.

Narrative (origin, incidents, measurements): `docs/doctrine/gate-resolution-escape-hatch.md`.
