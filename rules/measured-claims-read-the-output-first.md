# A Measured Claim Is Read From the Output, Never Typed From Expectation (PARAMOUNT)

## The law

> **A number that describes a measurement -- a test count, a mutant count, a
> merge time -- is copied from the output that reported it, after that output
> has been read. It is never written from what the run was expected to say.**

## The mechanical floor

`hooks/measured-claim-gate.py` (PreToolUse on Bash, over
`lib/measured_claim_gate.py`) reads the text a command is about to publish --
`git commit -m/-F`, `gh pr create|edit|close|comment|review` and
`gh issue comment|close` with `--body`, `--body-file`, `-F`, `--comment` --
including a heredoc the same command writes to that path, and since 2026-11-25
a heredoc that feeds `-F -` on stdin in the same command (that common form was
read as a file named `-`, so its counts were never checked). Every count in the
test-runner vocabulary ("12 passed", "3 failed", "fails 12 tests") is compared
with the counts in test-runner summary lines inside the tool results of the
session's transcript (the lines pytest prints as `9 passed, 1 failed in 0.23s`,
and since GRIP#9128 the lines vitest prints as `Tests  9 passed (9)`).
Prose in the transcript never counts, because the wrong number is usually
already in the session's own earlier text.

A worker dispatched with the Agent tool shares its parent's session, and its
payload's `transcript_path` is the parent's transcript. When the payload
carries an `agent_id`, the gate reads the worker's own transcript
(`<session>/subagents/agent-<agent_id>.jsonl`) and falls back to the parent's
only when that file is missing. The reason names the transcript it read
(GRIP#8331: before this, every worker's true count was denied with the parent's
numbers).

- A claimed count that no summary reports is **denied**, and the reason names
  the counts that transcript did hold.
- A transcript with no summary at all **warns**, because it cannot check.
- `GRIP_MEASURED_CLAIM_GATE=0` on the command allows it and is logged, for a
  count that came from outside the transcript the gate reads (a worker's run
  that its parent quotes, a run before `/clear`).
- It fails open on its own errors and logs them to
  `logs/measured-claim-gate.jsonl`, with the `agent_id` and the transcript read.

Escape-hatch audit (`rules/gate-resolution-escape-hatch.md`, Invariant 1): the
denied set is one command; the resolution is to correct the number, which no
gate refuses.

## Falsification

Wrong if, over the next ~50 denials, more than 10% block a count that was true
(a run the transcript genuinely held in a form the summary regex misses): then
widen the summary shapes, never drop the gate. Also wrong if a false count still
reaches a merged commit by a route the gate does not read (a PR body edited in
the browser, a message built by a script): widen the routes. Track: denials per
week and the share overturned by the override, from the audit log.

## Scope

Every Bash command that publishes text, in every session. Sits beside
`prove-it-claims-canonical.md` (a claim needs provenance),
`verify-canonical-not-claimed.md` (a status is a claim, not evidence) and
`tests-must-be-able-to-fail.md`. Shim: `lib/measured_claim_gate.py` ·
`hooks/measured-claim-gate.py` · `tests/test_measured_claim_gate.py`.

Narrative (origin, incidents, measurements): `docs/doctrine/measured-claims-read-the-output-first.md`.
