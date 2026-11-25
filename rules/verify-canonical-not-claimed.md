# Verify Canonical, Not Claimed (PARAMOUNT)

Self-instance (2026-05-16): the /ultraplan honest-gaps analysis falsely asserted a
HAL-GRIP gap by reading dirty local trees and misreading a keychain `subprocess.run(`
as a claude bridge. Origin/main audit (GRIP@094d5c09) refuted every claimed gap —
#1842-1847 were genuinely done: the doctrine's own creation is its first instance.

## The Rule

This cuts BOTH ways: do not trust a status label / merge notice / agent summary to
assert work is **done**; and do not trust a dirty local working tree to assert a
**gap exists**. A dirty local tree is evidence of intent, never of canonical state.
Verify against the canonical source whose authority matches the claim — origin/main
blob + the live endpoint + an e2e — before asserting either DONE or GAP.

Scope: binds only to assertions that gate a decision (a "done", a "gap exists", a
"ship it"), not every incidental statement — verifying every one would cost more
than the false claims it prevents.

- **Code**: `git show origin/main:<path>` — not a local dirty tree or stale clone.
- **Service**: HTTP probe on the endpoint — not pm2 `status: online` or a log line.
- **Task**: a concrete artefact (branch, test count, commit SHA) — not `task.status`.
- **Task description**: its referenced paths/PRs/branches decay like any other
  claim — verify each against its matching source (origin/main, `gh pr view`,
  `git ls-remote`) before driving a wave from it. `/task-reconcile` automates
  this, returning PROVABLY-DONE / PROVABLY-OPEN / UNVERIFIABLE-STALE.

## Self-Describing Artefacts Are Claims, Not Evidence

A system's account of its own operation — a return code, a status field, an
audit record it wrote — is a claim, not evidence, unless it can be checked
without trusting the claimant (a signature or checksum can be; a return code
cannot). Verifying a write means reading the file it acted on, not the
record the write made about itself. Full discussion:
`docs/doctrine/verify-canonical-not-claimed.md`.

## Sibling Rules

- `agent-dispatch-protocol.md` — dispatch half: agent must produce a concrete
  artefact, not intent prose, in its terminal turn.
- `ci-review-driven-convergence.md` — external-criterion half: CI green + council
  PASS is the criterion. This rule is the **canonical-state half**.

## Falsification

Wrong if, over N >= 10 canonical-state audits, claimed state always matches
canonical tree — gap is illusory. Downgrade to advisory; remove hot-path steps.

Narrative (origin, incidents, measurements): `docs/doctrine/verify-canonical-not-claimed.md`.
