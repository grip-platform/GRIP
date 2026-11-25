# Regression-Anchor Tests — Convention

This repository uses self-healing CI. Every bug fix ships with a new test
that **would have failed before the fix**. These are called regression
anchors.

## The Rule

For every bug-fix PR, there must be at least one new test that:

1. FAILS when run against the pre-fix commit
2. PASSES when run against the fix commit
3. Includes a docstring that references the issue (issue ID, test name,
   incident date — whichever is most stable)

If you cannot write an anchor, you cannot prove the fix works. Weaken the
fix scope or split the PR until you can.

## Why

A test that passes on pre-fix code proves the fix didn't break existing
behaviour. That is necessary but not sufficient. A regression anchor proves
the fix addresses the specific bug the PR claims to address.

Over time, the test suite becomes a living catalogue of every regression
the project has ever had — each caught by the exact test that would catch
it again. The suite grows monotonically with the project's bug history.
This is the central asset the self-healing CI system produces.

## Anatomy of an Anchor

```python
def test_issue_123_user_without_email_cannot_register():
    """
    Regression anchor for #123.

    Pre-fix behaviour: registering a user with email=None raised
    AttributeError in user_service.send_welcome(). This test would have
    caught that before the fix.
    """
    user = User.create(email=None)
    result = auth_service.register(user)
    assert result.status == "rejected"
    assert result.reason == "email_required"
```

Two properties matter:
- The docstring names the bug. Future readers know what broke.
- The assertions are specific to the bug, not generic smoke tests.

## Anti-patterns

- **Do not modify the originally failing test.** Add a NEW test alongside it.
- **Do not write a test that passes on pre-fix code.** That is not an anchor.
  CI will reject the PR.
- **Do not use `@skip`, `@xfail`, or mocks to sidestep the failure.** The
  council flags these automatically.
- **Do not weaken assertions.** `assertTrue(True)` and `assertEqual(x, x)`
  are detected by the council.

## CI Verification

For every fix PR, the CI council:

1. Checks out the PR branch
2. Runs the new test against the PR's base commit — it MUST FAIL
3. Runs the new test against the PR's head commit — it MUST PASS
4. Runs the full suite on the head commit — it MUST all pass
5. Scans the diff for skip/xfail/mock shortcuts

If any check fails, the PR is marked `REJECT` and cannot be merged until the
anchor is repaired.

## Human Review

CI never merges PRs. Every self-healed PR requires a human to:

1. Read the root cause explanation
2. Read the anchor docstring
3. Confirm the fix is reasonable
4. Merge (or close) the PR

Morning digest lists every PR with its council verdict so review is batched.
