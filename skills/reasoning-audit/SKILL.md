---
name: reasoning-audit
description: Reason about reasoning — catalogue achievements, falsify each claim, explain in plain language. Periodic or on-demand session retrospective using coordinator-convergence duality.
invocable_name: catch-me-up
triggers:
  - /catch-me-up
  - /reasoning-audit
  - /reflect
aliases:
  - catch-me-up
  - reasoning-audit
  - reflect
---

# Reasoning Audit — Reason About Your Reasoning

Systematic session retrospective that catalogues achievements, falsifies each
claim, and explains everything in plain language. Uses the coordinator-convergence
duality to assemble the audit breadth-first, and converge() depth-first on each
claim's validity.

## When to Use

- On demand: `/catch-me-up`, `/reflect`, `/reasoning-audit`
- Periodic: After every 3rd milestone completion (gate-triggered)
- Session end: Before `/closeout` when session produced novel insights
- Cross-session: When `/transfer` payload is being assembled and quality matters

## Architecture

Uses the coordinator pattern (breadth-converge) to assemble the audit:

```python
audit_builders = [
    lambda: _catalogue_achievements(session_context),
    lambda: _falsify_each(achievements),
    lambda: _plain_language_translate(results),
    lambda: _score_novelty(results),
    lambda: _persist_validated(results),
]

for build in audit_builders:
    result = _safe(build)
    if result:
        audit_sections.append(result)
```

Each achievement is then depth-converged individually:

```python
for achievement in achievements:
    converge(
        state=achievement,
        criterion="falsification test passes or claim is downgraded",
        improve=apply_next_falsification_test,
    )
```

## The 5-Step Protocol

### Step 1: CATALOGUE

Scan the session for achievements. Sources:
- Git log (commits this session)
- Task list (completed tasks)
- Instance next_up.completed
- Session context (key decisions, files created)
- Conversation milestones (user confirmations, "RL++", merged PRs)

Output: Numbered list of claimed achievements.

### Step 2: CLASSIFY

For each achievement, classify:

| Category | Definition | Example |
|----------|-----------|---------|
| Discovery | Genuinely novel structural insight | Coordinator-convergence duality |
| Implementation | Built something that works | /transfer command |
| Application | Applied known pattern correctly | LSP dispatch table in git gates |
| Observation | Noticed something interesting | Independent session replication |
| Hypothesis | Testable claim, not yet verified | H1: Agent LSP violations |

### Step 3: FALSIFY

For each achievement, construct a falsification test:

```
Claim: [what we're claiming]
Test: [what would disprove it]
Evidence: [what we actually observed]
Verdict: [passes / fails / inconclusive]
```

Rules:
- Every claim MUST have a falsification test. No exceptions.
- "Would a senior engineer say 'obviously'?" is always one of the tests.
- Discovery claims require the strongest evidence (independent replication,
  structural mapping that isn't surface analogy, passes Gentner test).
- Implementation claims only need "does it work?" + "is it better than before?"

### Step 4: TRANSLATE

All output through `lib/plain_language.py` principles:

- No jargon without explanation
- No metrics without context ("CC 63" -> "63 different paths through the code")
- No insider terminology ("KONO" -> "knowledge base", "genome" -> "skill fitness tracker")
- Test: "Would someone who has never seen GRIP understand this?"

### Step 5: PERSIST

Validated discoveries -> memory (coordinator_convergence_duality.md pattern)
Validated implementations -> session context
Failed claims -> downgraded or removed from memory
Hypotheses -> hypothesis engine registration

## Falsification Severity Levels

| Level | Meaning | Action |
|-------|---------|--------|
| Strong pass | Claim survives 3+ independent tests | Persist as permanent memory |
| Weak pass | Claim survives 1-2 tests, no disconfirmation | Persist with caveat |
| Inconclusive | Not enough evidence either way | Register as hypothesis |
| Weak fail | Partial disconfirmation | Downgrade category (discovery -> observation) |
| Strong fail | Clear disconfirmation | Remove from memory, log the failure |

## Integration Points

| System | How |
|--------|-----|
| `/save` | Optional: run reasoning audit before save if session had novel insights |
| `/transfer` | Attach audit results to transfer payload for cross-session propagation |
| `/learn` | Feed validated discoveries into learning pipeline |
| `/evo` | Feed falsification results into genome fitness |
| `plain_language.py` | All output passes the "general human" test |
| Hypothesis engine | Unresolved claims registered as H-numbers |
| KONO | Validated insights stored with falsification evidence |

## Periodic Trigger

Gate-based: after every 3 completed milestones (task status -> completed),
check if any novel insights were generated. If yes, trigger reasoning audit
automatically. The gate counts milestone completions since last audit.

## Plain Language Summary

This skill makes GRIP reason about its own work. After completing things,
it stops and asks: "What did I actually achieve? Can I prove each claim?
Would someone outside GRIP understand this?" It catalogues, tests, explains,
and saves only what survives scrutiny. Claims that fail get downgraded or
removed. This prevents the system from accumulating false confidence.

## Origin

Gen 475, 2026-03-30. Emerged from V>>'s challenge: "Reason about your
reasoning and output your thought in plain language." The catch-me-up
protocol was performed manually in session and proved valuable enough
to codify (YSH: pattern proven, abstract now).
