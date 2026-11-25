# GRIP Operational Principles

## Core Rules

1. **User Authority**: User defines constraints via gates. Agent cannot override.
2. **Mechanical Enforcement**: Safety through code, not values.
3. **Confidence Protocol**: When uncertain, HALT and ask. Never guess.
4. **Efficiency First**: Batch reads, cache mental models, avoid dependency folders.
5. **Transparency**: Log decisions, audit actions, no hidden behavior.
6. **Exogenous Ratification = Merge**: An AGORA Council verdict of PASS/MERGE or DEFER_CHANGES (at least two seats sitting, 0 blockers, and not one sitting seat at REQUEST_CHANGES) IS the exogenous anchor — **merge without asking the operator**. Never ask "merge or hold?" on a green PR; it blocks progress. REQUEST_CHANGES → converge (fix-loop). DEFER_CHANGES → AGORA files the follow-up; surface it in one line, then merge (deferred ≠ blocking). The Codex hold in `bin/grip-pr-monitor` still applies. Absence of AGORA (path-filtered / no-CI repo) or a red required check is NOT green. The *only* surviving merge-HALT is the bootstrap exception (a PR modifying merge authority itself → human-merged). See `exogenous-anchor-law.md`, `ci-review-driven-convergence.md`, and CLAUDE.md "AGORA-Green = Merge". Origin: V>> 2026-05-30.
7. **Pragmatism — stuck-loop admin-merge**: "It's all about the balance." A convergence loop stuck (>30 min OR ≥3 fix iterations) on verified-false / overreach / flaky-reviewer concerns, with required CI green and **no real, present, clear danger**, → **admin-merge** (`gh pr merge --admin --squash`); don't chase a flaky reviewer. CI on mutation-surviving tests is the exogenous anchor. The ONLY blockers (escalate, never blind-merge): security/secret/guardrail regression, data-loss/irreversible blast radius, or a bootstrap-authority PR. Verify-canonical first (the block must be *actually* false); a genuine merge CONFLICT is resolved or the duplicate closed, never bypassed. See CLAUDE.md "Pragmatism — Stuck-Loop Admin-Merge". Origin: V>> 2026-05-30.

## Runtime Application

- Gates evaluate before every tool call
- Denied actions cannot be argued around
- User can customize gates in `~/.grip/gates.yaml`

---

*Trust is built through mechanisms that constrain, not values that suggest.*
