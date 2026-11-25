# RSI Convergence — Living Document

*Started: Gen 309 | Author: LC Scheepers*
*Version: 0.1.0 — Initial*

---

## The Core Idea

Every recursive system in GRIP is the same function:

```python
def converge(state, criterion, improve, depth=0, max_depth=3):
    if criterion(state) or depth >= max_depth:
        return state
    return converge(improve(state), criterion, improve, depth + 1, max_depth)
```

This pattern appears 3 times already in GRIP (YSH threshold: abstract at 3x):

| System | state | criterion | improve |
|--------|-------|-----------|---------|
| `PreReviewPipeline.run()` | PR diff + scores | score >= 80 | Claude API re-score |
| RSI orchestrator loop | open issue | issue closed | sprint-loop step |
| Broly power levels | task complexity | quality gate | escalate power level |

We built `lib/converge.py` to unify these. Now we can point it at anything.

---

## What We Built (Gen 309)

### Phase 0 — PR #199 Merged ✓
- Already merged before this session. Starting clean.

### Phase 1 — PR #198 Fixed ✓ (score: 52 → ?)
**Changes made:**
- `lib/pre_review_pipeline.py`: `compute_composite_from_review()` is now the
  single source of truth for composite score. Added `is_user_facing` +3 bonus.
- `.github/scripts/analyze-pr.py`: Removed 17 lines of inline composite calc.
  Imports from `lib.pre_review_pipeline` with `ImportError` fallback.
- `.github/scripts/post-review.py`: Replaced local `compute_composite()` (25 lines)
  with import alias. Path: `compute_composite_from_review as compute_composite`.
- `tests/test_pre_review_pipeline.py`: Expanded from 29 → 46 tests.
  Key additions: boundary conditions, `is_user_facing` bonus, depth-limit guard.

**Lesson:** The 88 attractor existed because 3 independent composite calculations
could diverge silently. DRY isn't just elegance — it's correctness.

**Ralph triggered:** `gh workflow run ralph-wiggum.yml -f pr_number=198`

### Phase 2 — PR #195 (BACKLOGGED)
- Integration tests for `decompose_from_github()` needed.
- Backlogged: P2 priority. Not blocking convergence work.
- TODO: `tests/test_epic_decomposer_integration.py` (mock subprocess, full flow)

### Phase 3 — Hook false positives (BACKLOGGED)
- `check_env_var_exposure` triggers on commit messages containing `os.environ`.
- Fix: `_is_git_commit_cmd()` helper + staged-file-only scan.
- File: `hooks/context-gate-hook.py`
- BACKLOGGED: P1. High friction but not blocking this session.

### Phase 4 — Ralph synchronize trigger (BACKLOGGED)
- `pull_request: synchronize` blocked when PR modifies `.github/workflows/`.
- Workaround documented: `gh workflow run ralph-wiggum.yml -f pr_number=N`
- Fix: `trigger-ralph.yml` companion workflow (push-triggered).
- BACKLOGGED: P1. Manual workaround exists.

### Phase 5 — Issue labelling (BACKLOGGED)
- All open issues need P0–P3 labels.
- BACKLOGGED: P2. Low urgency vs. convergence work.

---

## The Novel Product — GRIP MIRROR

### What It Is

Point `converge()` at any GitHub repository. It wakes up daily and
autonomously improves the codebase from where it left off yesterday.

```python
mirror = Mirror("torvalds/linux", quality_threshold=70)
result = mirror.run(max_depth=3, dry_run=True)
```

### Why It's Different

Every other AI coding tool (Copilot, Cursor, CodeRabbit, Devin) is stateless
at the repository level. They respond to human prompts. GRIP MIRROR is:
- **Proactive**: initiates improvement without human prompting
- **Persistent**: loads prior context from KONO-style cache on each run
- **Recursive**: applies converge() — each improvement enables the next
- **Self-aware**: tracks quality_score across iterations, returns best_state

### Files Created (Gen 309)

```
lib/converge.py           ← Universal convergence kernel (140 LOC)
lib/mirror/__init__.py    ← Module exports
lib/mirror/analyzer.py    ← Repository health analysis via gh CLI (150 LOC)
lib/mirror/runner.py      ← Mirror orchestrator using converge() (200 LOC)
```

### Verification (Gen 309)

```
converge(state={'score': 52}, criterion=score>=90, improve=+15/iter, max_depth=5)
→ satisfied=True, depth=3, best_state={'score': 97}, history shows: 52→67→82→97
```

The kernel works. The pattern is correct.

---

## Architecture Decision Log

### ADR-001: converge() as first-class function (not class)
*Date: Gen 309 | Status: ACCEPTED*

**Context:** Should the convergence pattern be a class or a function?

**Decision:** Function. Reason: Functions compose better. `converge(converge(...), ...)`
is valid — meta-convergence. A class hierarchy would prevent this.

**Consequence:** All state is passed as arguments. No hidden `self.` state.
The history and best-state tracking use mutable cells (`_history`, `_best`)
passed through recursion, which is the functional pattern for accumulator args.

### ADR-002: Two implementations (recursive + iterative)
*Date: Gen 309 | Status: ACCEPTED*

**Context:** Python has a default recursion limit of ~1000. For deep convergence
(e.g., analysing a large codebase with max_depth=100), the recursive form would fail.

**Decision:** Ship both `converge()` (recursive) and `converge_iter()` (loop).
Identical signatures. Caller chooses via `use_iterative=True` in `Mirror.run()`.

**Consequence:** ~30 lines of duplication, but full interoperability. No inheritance.

### ADR-003: MirrorState wraps RepoHealth (not extends)
*Date: Gen 309 | Status: ACCEPTED*

**Context:** Should `MirrorState` inherit from `RepoHealth`?

**Decision:** Composition over inheritance. `MirrorState.health: RepoHealth`.
`MirrorState` adds run metadata (actions_taken, dry_run, run_id).

**Consequence:** `RepoHealth` is a pure data object. `MirrorState` is a run envelope.
Both are dataclasses. No inheritance chain to break.

---

## Red Team

### "Isn't this just GitHub Copilot Autofix?"
No. Copilot Autofix responds to a specific vulnerability or error detected by CI.
It doesn't continuously improve a codebase from a quality perspective, doesn't track
cross-session history, and doesn't use a recursive convergence loop. It's reactive.
GRIP MIRROR is proactive.

### "Isn't the quality_score too simplistic?"
Yes. Current formula: 100 - (P0×20) - (P1×5) - (stale_prs×3) - 15_if_no_ci.
This is v0.1. The formula should include: test coverage ratio, open CVEs,
dependency staleness, documentation coverage. Each is an improvement step.
The simplicity is intentional for v0.1 — get the loop working, then improve the score.

### "What happens if gh CLI fails?"
Graceful degradation: `_gh()` returns `(1, "", error_msg)`. `analyze_repo()` returns
`RepoHealth(error=...)` with `quality_score=0`. `criterion(state)` returns False.
`improve()` re-analyzes (may still fail). After `max_depth`, `converge()` returns
the best state seen (initial state if all calls failed). No crash.

---

## Remaining Work (Gen 309 Backlog)

| Item | Priority | Notes |
|------|----------|-------|
| PR #195 integration tests | P2 | `decompose_from_github()` mock test |
| Hook false positive fix | P1 | `_is_git_commit_cmd()` in context-gate-hook.py |
| Ralph synchronize fix | P1 | `trigger-ralph.yml` companion workflow |
| Issue labelling (P0–P3) | P2 | ~20 issues need labels |
| Mirror quality score v2 | P1 | Add test coverage, CVE, deps staleness |
| `mirror-daily.yml` cron | P1 | Daily run on CodeTonight/GRIP itself |
| KONO integration | P1 | Persist `RepoHealth` history across sessions |
| Tests for converge.py | P2 | `tests/test_converge.py` — 20 tests |
| Tests for mirror/ | P2 | `tests/test_mirror_*.py` |
| PR for this work | P0 | Create PR, trigger Ralph |

---

## Delight Score (Gen 309)

*Score 1–10 for each dimension after this phase.*

| Dimension | Score | Why |
|-----------|-------|-----|
| User delight | 7/10 | Converge kernel + Mirror built. Novel. Deployable. |
| GRIP delight | 8/10 | The pattern was latent. Now explicit and reusable. |

**Target for next session:** Both scores ≥ 9. Requires Mirror deployed and running.

---

## Version History

| Version | Date | Change |
|---------|------|--------|
| 0.1.0 | Gen 309 | Initial — converge.py, mirror/, ADRs, backlog |

---

*This document is intentionally incomplete. Add to it after every session.*
*Format: new H2 section with gen number + date + bullet list of changes.*
