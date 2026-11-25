---
name: decide
description: Ask Jev (TypeSafe's decision model, via HAL decide) a quick typed yes/no, pick-one or how-much question about a file, diff or log already in hand. Use when a session needs a fast, cheap probability reading on internal text rather than a full reasoning pass. Never for personal, client or secret data; never as the only gate on an action; never for arithmetic or date comparison, which Jev does badly.
command: decide
status: active
effort: low
version: 1.0.0
created: 2026-10-12
user_invocable: true
triggers:
  - /decide
  - "quick decision on this"
  - "ask Jev about this"
  - "yes or no on this file"
---

# /decide — a quick typed question about text you already have

`/decide` wraps `lib/decide_quick.py`, a small CLI over HAL's `decide`
capability. It asks Jev — TypeSafe AI's **decision model**, not a chat model
— one or more typed questions about a piece of text, and prints each
answer's probability, the model id that answered, and the fixed disclaimer
**"machine reading, precision unmeasured"**.

## What Jev is, in three lines

Jev answers typed questions about a `state` text: `noul` (probability a
yes/no question is yes), `choice` (one of N options with a probability
each), `score` (a value over 2-10 ordered levels). It writes no text and
holds no conversation. GRIP reaches it only through HAL:
`lib.hal_llm_adapter._query_grip_decide`.

## When to use it

A quick, cheap probability reading on text the session already has in
hand — a file, a diff, a log excerpt, a short passage — where the question
has a clean yes/no, pick-one, or how-much shape. For example: "does this
diff touch anything that looks like a secret?", "which of these three
error messages is most likely the root cause?", "how urgent does this log
line read?".

## When NOT to use it

- **Anything personal, client or secret.** The data limit (binding, from
  the Jev integration plan): *"Internal + public only: GRIP/HAL code,
  docs, logs, public legal sources. Never personal matters, WhatsApp
  content, children's data, secrets or client documents."* Every call goes
  through the decision-model privacy floor first
  (`lib.decision_privacy.may_send`); when it refuses, nothing is sent and
  the CLI says so on one line instead of guessing.
- **Anything that needs real reasoning, arithmetic, or date comparison.**
  TypeSafe's own documented weaknesses: literal reading, unreliable
  arithmetic and date comparison, multi-hop reasoning, being distracted by
  large irrelevant state, being steerable by injected text in `state`, and
  favouring whichever Choice option is listed first. `/decide` corrects for
  the last one (see below) but not the others — for real reasoning, use the
  session's own judgement, not a decision model.
- **Never as the only gate on an action.** A Jev reading is advisory and
  uncalibrated (TypeSafe publishes no ECE, Brier score or reliability plot).
  It informs a decision; it does not make one. An answer inside the
  0.30-0.70 band is reported as **"uncertain"**, never as a verdict.

## Usage

```bash
# A single yes/no question, state given inline:
bin/grip-py lib/decide_quick.py --state "$(cat somefile.md)" \
  --noul q1 "Does this text describe a security fix?"

# State from a file, a pick-one question:
bin/grip-py lib/decide_quick.py --state-file diff.patch \
  --choice which "Which change is riskiest?" --options "auth,logging,tests"

# A how-much question, state piped on stdin:
cat error.log | bin/grip-py lib/decide_quick.py \
  --score urgency "How urgent does this look?" --levels "low|medium|high|critical"

# Several questions in one call (one Jev request, not three):
bin/grip-py lib/decide_quick.py --state-file notes.md \
  --noul done "Is this task complete?" \
  --choice next "What should happen next?" --options "merge,revise,discard" \
  --json

# Seal the call as a signed decision record:
bin/grip-py lib/decide_quick.py --state-file notes.md \
  --noul done "Is this task complete?" --seal
```

Always run it under `bin/grip-py` (the GRIP venv shim), never bare
`python3` — a system interpreter cannot dual-sign, and `--seal` would
write a forgeable demo seal instead of a real one
(`rules/never-system-python-for-grip.md`).

## Behaviour

- **State** comes from `--state TEXT`, `--state-file PATH`, or stdin —
  exactly one source; the content is never read twice.
- **The privacy floor runs first.** When it refuses, the CLI prints why on
  one line and exits non-zero **without calling Jev at all**.
- **A Choice is asked in both option orders and averaged**, because Jev
  favours the first-listed option. `/decide` sends the options list
  forward, then reversed, in the SAME Jev call, and averages each option's
  probability across both — cancelling the position bias rather than
  reporting it.
- **Every typed answer prints its probabilities, the model id that
  answered, and the fixed label "machine reading, precision unmeasured".**
  An answer in the 0.30-0.70 band prints as **"uncertain"** instead of a
  verdict. `--json` prints the same facts as JSON.
- **`--seal` records a signed decision record** — the question text, a
  SHA-256 digest of `state` (never the state itself), the answers and the
  model id — reusing `lib.precog.idr`, the same signer every other GRIP
  tool seals through. It writes no new signer. When the running
  interpreter can only produce a demo (HMAC, forgeable-by-design) seal,
  the CLI says so plainly instead of printing success.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | At least one question was answered (an "uncertain" reading still counts) |
| 1 | Every question came back with no answer (Jev unreachable, or a malformed reply) |
| 2 | A usage error — bad flags, a mismatched `--options`/`--levels` count, no questions given |
| 3 | The privacy floor refused the state; nothing was sent |

## Related

- `lib/hal_llm_adapter.py::_query_grip_decide` — the one send point every
  Jev caller in GRIP shares. `/decide` adds no new network send point.
- `lib/decision_privacy.py` — the privacy floor every call goes through.
- `lib/infer_classify.py`, `lib/prompt_routing_score.py`,
  `lib/ultraprompt_relevance.py` — the
  other Jev callers in GRIP, each wired for one specific decision rather
  than a general-purpose question. The routing shadow is off by default;
  it requires `GRIP_PROMPT_ROUTING_SCORE=1`. The verdict shadow similarly
  requires `GRIP_JEV_VERDICT_PREDICTOR=1`; its offline calibration report
  remains available. See `docs/jev-shadows.md` for the stop decision.
- `docs/decide.md` (HAL repository) — what Jev is and is not, its known
  weaknesses, and the terms that bind us.

## Falsification

Wrong if, over the next ~20 invocations: the privacy floor refuses a call
that was genuinely safe to send (tighten the floor's false-positive rate,
never bypass it); or a Choice's averaged reading is no better calibrated
than a single forward-order call (the averaging buys nothing — drop it,
keep the single call); or an "uncertain" reading is routinely treated by a
caller as a verdict anyway (the 0.30-0.70 band is being ignored — make the
label louder, or gate on it mechanically).

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-10-12 | Initial — noul/choice/score, option-order averaging, privacy floor, --seal |
