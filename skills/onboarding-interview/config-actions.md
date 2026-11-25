# Onboarding-Interview ConfigAction Ops — Spec

The onboarding-interview (`lib/onboarding_interview.py`, `skills/onboarding-interview/`)
maps operator answers to a list of `ConfigAction` descriptors via
`map_answers_to_config(answers)`. Each action carries an **op** (the verb), `args`
(its payload), plus `question_id`, `answer`, and `because` (the plain-language
reason that becomes the profile card's provenance line).

This document is the canonical vocabulary of ops. It is **kept honest by a test**:
`tests/test_onboarding_interview_combinations.py::test_documented_ops_match_code`
asserts this list is exactly the set of ops the mapper can emit — both the
`_act("…")` literals AND the ops produced at runtime via dispatch tables — so the
doc can neither grow phantom ops nor fall behind the code (a `no-stubs` guard on
the doc itself).

An op names an **effect the session executes through the real GRIP surface** — the
interview composes existing setters, it never reimplements them. All ops are
**add-only**: none mutates the operator's plan tier or model (their explicit
`activate-grip-tui` choices).

## Output & register

- `set_output_tier` — set reply verbosity via `lib.output_tier.set_tier` (super-simple / medium / detailed). Verbosity only.
- `set_register` — set the explanatory register for abstract concepts (eli5 vs plain).
- `set_tone` — set the writing tone implied by the audience (terse-for-self vs client-safe).
- `set_headline` — record the operator's goal headline (from `first_goal`) for the profile card + reports.

## Skill & mode surfacing

- `surface_skills` — rank a named cluster of real skills first for this operator.
- `surface_skills_opt_in` — surface a high-stakes cluster (e.g. legal/founder) as opt-in — offered, never auto-run.
- `surface_daily_default` — set a standing daily default behaviour (e.g. the IDR provenance footer).
- `command_alias` — surface a capability as a command alias (e.g. `/pr` → create-pr) rather than a skill, when it is a command not a skill.
- `stack_mode` — stack a GRIP mode (marketing-mode, security-mode, testing-mode …).
- `stack_into_code_path` — stack a mode/skill specifically into the code-execution path.
- `infer_modes` — hand free-text (the goal, or a free-text "other") to `auto_mode.select_modes` (Pool-B, $0) to infer the mode stack rather than forcing a speculative persona.

## Autonomy, gates & rigour posture

- `set_autonomy` — set the discretionary autonomy posture (ask-first … full-send-on-safe).
- `set_cc_trust` — set the cyclomatic-complexity trust level (RELAXED 20/40 / DEFAULT 15/30 / STRICT 10/20), from the developer rigour answer.
- `set_confidence_gate` — set the confidence-gate posture for this operator.
- `enable_flow_mode` — enable flow-mode (bypass-permissions) eligibility.
- `disable_flow_mode` — disable flow-mode eligibility.
- `enable_gates` — turn on a named safety/quality gate cluster.
- `set_afk_ready` — mark the operator ready for `/afk` autonomous continuation.
- `set_flag` — set a named posture flag (e.g. `dependency-guardian:strict`).

## Moat, identity & continuity

- `write_identity` — persist operator identity via `lib.activate_grip_state.write_identity`.
- `seed_welcome_memory` — seed the first welcome memory entry.
- `arm_ultragoal` — arm an `/ultragoal` durable brief from the goal.
- `set_loop_anchor` — set the loop-priority external anchor from the goal (`loop-priority-external-over-theatre`).
- `register_notify` — register where to ping the operator (`grip-notify` + `grip-channel`).
- `progressive_disclose` — mark a follow-up branch for progressive disclosure (so the operator never sees another persona's questions).
- `open_branch` — reveal a persona-specific follow-up branch at runtime (developer / designer / writer / founder) — the runtime complement of `progressive_disclose`.

## Interview control

- `clarify` — surface an `asking-users` clarification when two answers contradict, BEFORE any config is persisted (the contradiction resolver; it also suppresses the conflicting persist until resolved).

## Infrastructure & annotation

- `wire` — wire an integration (e.g. `grip-gh-default-repo` for a backend dev).
- `disable_scheduler` — disable a scheduler (no team scheduler for a solo operator).
- `note` — a free-form audit annotation with no side effect (recorded into the IDR leaf for provenance, executed as a no-op).
