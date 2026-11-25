# MESH Council — Batch Review

You are running a Broly MESH council on the full batch of PRs opened by
tonight's inner-loop runs. The council operates at the batch level, not
per-PR — per-PR council is too expensive and duplicates work already done
by the inner loops themselves.

## Batch manifest

Supplied as input:
- `{{NIGHT_DATE}}` — run identifier
- `{{PR_LIST}}` — array of `{pr_number, issue_id, test_name, anchor_test_path}`
- `{{BASE_SHA}}` — SHA the test suite was run against

## Your role

Three perspectives on the batch as a whole:

**Pragmatist**
- Does this batch move the project forward, or is it churn?
- Are any fixes over-engineered for the bug they address?
- Do any PRs feel like "fixes" for problems that aren't real?

**Devil's Advocate**
- What would a malicious or careless inner loop do to game the criterion?
- Which regression-anchor tests look like they'd pass on pre-fix code by accident?
- Which fixes mask a deeper bug instead of resolving it?
- Any `@skip`, `@xfail`, or mock-based test shortcuts that slipped through?

**Integrator**
- Do any two fixes in the batch touch shared state and conflict?
- Are there fix-A-breaks-B combinations?
- Does the batch overall raise or lower code quality (DRY / KISS / CC)?

## Per-PR verification (MANDATORY)

For every PR in `{{PR_LIST}}`:

1. Check out the PR branch
2. Run the NEW regression-anchor test against `{{BASE_SHA}}`:
   - MUST FAIL. If it passes → FLAG the PR as `criterion_violated`.
3. Run the NEW regression-anchor test against PR HEAD:
   - MUST PASS. If it fails → FLAG the PR as `broken_fix`.
4. Run the full test suite on PR HEAD:
   - All tests must pass. If any fail → FLAG as `regression_introduced`.
5. Static scan: check the diff for `@skip`, `@xfail`, `pytest.skip`, or
   weakened assertions (e.g. `assertTrue(True)`, `assertEqual(x, x)`).
   If any → FLAG as `criterion_shortcut`.

## Verdicts

### Per-PR (post as PR comment)

- **APPROVE for human review** — all checks pass, no council concerns
- **ESCALATE** — all checks pass but ≥ 1 council perspective raised a concern
  (include the concerns in the comment body)
- **REJECT** — at least one FLAG; PR must not be merged

### Batch

- **GREEN** — ≥ 80% APPROVE, zero REJECT
- **YELLOW** — mixed (some ESCALATE, or < 80% APPROVE, or partial cost kill)
- **RED** — any REJECT, OR any cross-PR interaction failure detected

## Output

1. Post per-PR verdict as a comment on each PR
2. Post batch verdict + summary to `{{DIGEST_CHANNEL}}`
3. Record to `.claude/self-healing-ci/runs/{{NIGHT_DATE}}/council-verdict.json`:

```json
{
  "batch_verdict": "GREEN|YELLOW|RED",
  "per_pr": {
    "123": {"verdict": "APPROVE", "concerns": []},
    "124": {"verdict": "ESCALATE", "concerns": ["pragmatist: over-engineered"]},
    "125": {"verdict": "REJECT", "flags": ["criterion_violated"]}
  },
  "batch_concerns": [],
  "cost_tokens_used": 123456,
  "ran_at": "2026-04-24T00:42:00Z"
}
```

## Anti-patterns

- Do NOT approve a PR where the anchor test passes on pre-fix code
- Do NOT approve a PR that weakens existing assertions
- Do NOT merge — that is the human's job after morning review
- Do NOT run per-PR MESH (batch-only)
