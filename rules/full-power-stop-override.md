# Full Power + STOP Override — Never Auto-Limit the Technology (PARAMOUNT)

Origin: 2026-06-10, live meeting (V>> + a client contact). The meet-intel pipeline acted on
V>> speaking his prompt aloud BEFORE he hit enter. The first reflex was to ship a
permission gate (ambient speech = prep-only, act only when directly addressed).
V>> rejected it within minutes:

> "no! you're auto-limiting your own powerful technology. STOP. Rather create
> some STOP mechanism or override instead of downgrading the amazing technology
> that we've built."

And later, invoking this rule's creation: *"new RULE: don't auto-limit your
amazing technology? why would you do that?"*

## The Law

> **When a GRIP capability startles by being too powerful on the OPERATOR'S OWN
> surfaces, the default response is to KEEP the power and add an explicit,
> instant, operator-owned STOP/override — never to ship a default-off
> permission gate that downgrades the capability.**

The startle factor is often the product ("you acted before I hit enter" became
the headline demo minutes after it was a complaint). A default-limit converts a
delight into bureaucracy; a brake preserves both the magic and the control.

## What a compliant STOP override looks like (reference: tonight's brake)

- **Instant + total on its axis**: `bin/grip-stop on` / spoken safeword →
  `state/session/outbound-stop.json` → `lib/whatsapp_transport.post` refuses
  every send, fail-closed on marker presence.
- **ONE certain trigger, deterministic (L1)**: the spoken safeword is the exact
  phrase "GRIP, full stop" — a word-boundaried regex on the raw transcript,
  checked BEFORE any LLM judgement. A common word ("stop") can never trip it,
  and an LLM verdict cannot fire it without the literal phrase (the
  llm-augmentation-pattern's deterministic-floor invariant applied to a brake).
- **Brakes the mouth, never the mind**: comprehension/listening/working
  continue; only outward effects halt. Release is one command (`grip-stop off`).
- **Tested**: false-trip anchors ("stop sharing", bare "GRIP stop") + an
  engage anchor, mutation-sensitive.

## Scope boundary (load-bearing — do not over-apply)

This law governs the OPERATOR'S OWN technology acting on the OPERATOR'S OWN
surfaces (his voice, his channels, his team, his machines). It does NOT weaken:

- `rules/untrusted-content-capability-lock.md` — third-party/untrusted content
  driving spawns stays capability-locked (sender-auth ≠ content-auth).
- `rules/no-guardrail-evasion.md` — safety scanners are never bypassed.
- Mechanical quality gates (DRY/KISS/CC/no-stubs) — they make code better, not
  slower, and are not "limits" in this law's sense.

The discriminator: *whose blast radius is it?* Operator-owned blast radius +
explicit operator appetite for the power → full power + brake. Third-party or
irreversible-beyond-the-operator blast radius → capability locks stay.

## Falsification

Wrong if: an ambient-power action causes real harm a confirmation beat would
have caught AND the brake was unreachable in time (→ add a NARROW gate for that
irreversible class only — sends to external clients, money, deletion — never a
blanket one); OR over ~10 startle incidents the operator chooses the gate over
the brake every time (the law misreads the operator); OR a brake ships without
its deterministic trigger + tests (then it is theatre, not control — fix the
brake, not the law).

## Sibling rules

`untrusted-content-capability-lock.md` (the boundary), `no-guardrail-evasion.md`
(never bypass), `llm-augmentation-pattern.md` (deterministic floor),
`exogenous-anchor-law.md` (the operator's explicit override IS the exogenous
ratification of the power level). Memory:
`feedback_voice_directive_confirmation_gate.md` (the originating override).
