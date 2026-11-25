# Ultraplan: GRIP Critical Analysis — Fix All Gaps (v2)

**Date**: 2026-04-04 (revised)   
**Origin**: Gen 491 self-audit (`docs/grip-critical-analysis.md`)   
**Research**: 6 parallel agents (confidence gate, asset counts, state sprawl,
HAL /council architecture, plain-language terminology brainstorm, agent tier mapping)   
**Scope**: 9 workstreams, ~35 discrete tasks, estimated 6-8 improvement sessions   
**Status**: Re-audited against `main` on 2026-11-24; see "Status on 24 November 2026" at the end. In April: confidence gate script built, 24 tests passing.

---

## Executive Summary

The Gen 491 audit identified 5 categories of gap. V>> expanded scope to 3 additional
workstreams: a HAL council-powered confidence measurement system, a plain-language
terminology rebrand across all user-facing GRIP surfaces, and tiered agent classification.

**Priorities**: P0 = credibility + plain language, P1 = engineering quality,
P2 = performance and hardening.

## V>> Decisions (2026-04-04)

1. **Confidence gate**: Build real multi-provider measurement via HAL /council.
   3-tier cascade: heuristic -> fast council (2 providers) -> full council (4+ providers).
   **DONE** — `lib/confidence_gate.py` built with 24 passing tests.
2. **Asset counting**: Count "abilities/capabilities" and "domains/presets" not skill.md
   files. Move away from jargon naming entirely.
3. **Agent discrepancy**: Tiered system (Core/Specialist/Infrastructure/Experimental).
   34 agents classified. 8 phantom agents in AGENTS.grip have no implementation.

---

## Workstream A: Confidence Gate — Wire the Dead Code (P0)

### The Problem

The confidence gate is GRIP's most-promoted feature. Infrastructure exists:
- YAML definition: `config/default-gates.yaml:39-58` (complete with test cases)
- Python evaluator: `lib/gates/evaluator.py:267-269` (`evaluate_confidence_threshold`)
- Context field: `EvaluationContext.confidence` (line 97, defaults to 1.0)

But `context-gate-hook.py` (the actual enforcement hook) has **zero references** to any
of this. The evaluator is never called. Confidence always defaults to 1.0. The gate
never fires.

### Fix Plan

| Task | File(s) | Description |
|------|---------|-------------|
| A1 | `hooks/context-gate-hook.py` | Import and call `evaluate_gates()` from `lib/gates/evaluator.py` within the gate pipeline. Add as a new `_gate_confidence()` function using the YAML evaluator, positioned after always-allow (Gate 0) and before dependency guardian (Gate 1). |
| A2 | `lib/gates/evaluator.py` | The `confidence` field defaults to 1.0. Since Claude Code hooks receive no confidence signal from the model, implement a **heuristic confidence proxy**: count uncertainty markers in the transcript (questions asked, hedging language, tool retries). This is honest — a heuristic proxy, not a fake confidence score. |
| A3 | `config/default-gates.yaml` | Reconcile threshold: documentation says 70%, CLAUDE.md says 99.9999999%. The 70% is the mechanical threshold. The 99.9999999% is the prompt instruction for the LLM. **Keep both** — document the distinction: mechanical gate fires at 70%, prompt instruction catches the 70-99% range. |
| A4 | Docs: `README.md`, `SAFETY-GATES.md`, `CLAUDE.md` | Update documentation to honestly describe the two-layer system: (1) mechanical heuristic gate at 70% and (2) prompt-based instruction at 99.9999999%. Remove any implication that a real confidence measurement exists. |
| A5 | `tests/` | Add tests: confidence evaluator unit tests, integration test that the gate fires in context-gate-hook when proxy returns < 0.7, test that default 1.0 passes. |

### Dependency Chain

A1 (wiring) is independent. A2 (proxy) blocks A1's usefulness. A3 and A4 can run in
parallel. A5 follows A1+A2.

### Open Question

Is a heuristic proxy honest enough, or should we remove the confidence gate claim
entirely and replace with "uncertainty detection"? The latter is more accurate but
less marketable. **Decision needed from V>> before implementation.**

---

## Workstream B: Asset Count Auto-Generation (P0)

### The Problem

11 locations across 7 files hardcode skill/agent/mode counts. They disagree:

| Asset | README | CLAUDE.md | .claude/CLAUDE.md | AGENTS.grip | Reality |
|-------|--------|-----------|-------------------|-------------|---------|
| Skills | ~150 | ~290 | ~290 | — | 209 dirs / 297 SKILL.md |
| Agents | 21 | 31 | 28 | 16 | 34 files |
| Modes | 30 | 30 | 30 | — | 30 YAMLs |

The agent count has **5 different values** across documents.

### Fix Plan

| Task | File(s) | Description |
|------|---------|-------------|
| B1 | `lib/asset_counter.py` (NEW) | Create a single-source-of-truth script that counts from the filesystem: `fd --max-depth 1 --type d . skills \| wc -l` for skills, `fd -t f -e md . agents \| wc -l` for agents, `fd -t f -e yml -e yaml . modes \| wc -l` for modes. Output JSON. |
| B2 | `scripts/update-asset-counts.sh` (NEW) | Shell script that runs B1 and patches all 11 locations using sed/python. Idempotent — safe to run repeatedly. |
| B3 | All 7 files (see list below) | Run B2 to update all hardcoded counts to match reality. |
| B4 | `docs/AGENTS.grip` | Reconcile: 34 agent .md files exist but only 16 are documented. Either document all 34 or explain the discrepancy (some may be deprecated/internal). |

**Files to update (B3):**
1. `README.md` lines 214, 466, 467
2. `CLAUDE.md` (global) line 101
3. `.claude/CLAUDE.md` line 13
4. `rules/system-capabilities.md` line 3
5. `skills/CLAUDE.md` line 3
6. `mcp-servers/grip-channel/src/guild-knowledge.ts` lines 25, 144
7. `docs/cir/CIR-001-agent-skills-frontmatter.md` line 7

### Counting Convention Decision

Skills: count directories (209) or SKILL.md files (297)? Some skills have nested
SKILL.md files. **Recommendation**: count directories (the unit of deployment), report
as "~210" to account for growth. Document the convention in `lib/asset_counter.py`.

---

## Workstream C: Code Quality Infrastructure (P1)

### The Problem

No `pyproject.toml`, `ruff.toml`, `mypy.ini`, or any packaging/linting config.
Python 3.10+ union syntax (`str | None`) used throughout but no version constraint.
`context-gate-hook.py` has 4 duplicate `re` imports and 6 `sys.path` manipulations.

### Fix Plan

| Task | File(s) | Description |
|------|---------|-------------|
| C1 | `pyproject.toml` (NEW) | Create with: `requires-python = ">=3.10"`, ruff config (line-length 120, select E/W/F/I), basic project metadata. Not a full package — just tooling config. |
| C2 | `hooks/context-gate-hook.py` | Remove 3 local `import re as _re` — use the top-level `import re` throughout. Single `sys.path` setup at top instead of 6 scattered insertions. |
| C3 | `hooks/secrets-hook.py` | Add logging to the bare `except (json.JSONDecodeError, EOFError): return` — at minimum log the parse failure before returning. |
| C4 | `hooks/context-gate-hook.py` | Replace `_get_cc_pid_for_gate()` subprocess call with `os.getppid()` chain (pure Python, no subprocess spawn). Falls back gracefully if PID ancestry is unavailable. |

### Risk

C2 is a refactor of the most critical file in GRIP. **Must be done in isolation with
full test coverage verification before and after.** Run the existing 576 tests before
and after to confirm no regression.

---

## Workstream D: State Consolidation (P1)

### The Problem

159 JSON/JSONL files across `state/`, `cache/`, and `data/`. `StateManager`
(`lib/state_manager.py`) exists and covers ~25% of state needs. JSONL logs grow
unbounded. `log_rotator.py` exists but isn't wired into anything. 80+ cc-release
files never cleaned.

### Fix Plan

| Task | File(s) | Description |
|------|---------|-------------|
| D1 | `lib/state_manager.py` | Extend StateManager to register cache/ files (currently only state/ is tracked). Add `CACHE_DIR` tracking parallel to `STATE_DIR`. |
| D2 | `lib/state_manager.py` | Add `cleanup_expired()` method with configurable TTL per file category. JSONL logs: 30-day TTL + 5MB max size. cc-releases: keep last 10. Destructive approvals: hard delete after TTL (currently soft-expired). |
| D3 | `lib/log_rotator.py` + hook | Wire `log_rotator.py` into a PostToolUse hook or session-start hook. Run on session start to rotate logs older than 30 days. Lightweight — check file mtime only, no content parsing. |
| D4 | `lib/state_manager.py` | Add schema versioning: each state file gets a `_schema_version` field. Migration functions registered per file. Prevents silent breakage when state format changes. |
| D5 | Audit + cleanup | Run StateManager migration: move remaining legacy dotfiles into `state/` hierarchy. Delete orphaned files that no code references. |

### Scope Limit

Do NOT consolidate `data/` files (guild data, genome evolution, copilot) — these are
domain-specific and actively evolving. Focus on `state/` and `cache/` only.

---

## Workstream E: Hook Performance (P2)

### The Problem

186ms per tool call. Over 200 calls = 37 seconds cumulative overhead. The cause:
Python startup + 20 imports + file I/O for state on every invocation.

### Fix Plan

| Task | File(s) | Description |
|------|---------|-------------|
| E1 | `hooks/context-gate-hook.py` | **Profile the import chain.** Measure time spent in each import using `importtime` (`python3 -X importtime`). Identify the top 3 costly imports. |
| E2 | `hooks/context-gate-hook.py` | **Lazy imports.** Move non-critical gate imports inside their gate functions (they already return early via fast-path checks). Only import what's needed for the gate that actually fires. |
| E3 | Research | **Investigate persistent daemon.** Claude Code hooks are stdin/stdout — could a long-running daemon accept hook invocations over a Unix socket? Measure the theoretical improvement. Document findings even if not implementable. |

### Target

Reduce per-call overhead from 186ms to < 80ms (imports alone should be ~50ms with
lazy loading). The 80ms target is based on: 50ms Python startup + 30ms fast-path
evaluation.

---

## Workstream F: Security Documentation (P2)

### The Problem

The audit identified 4 security vectors that are real but undocumented. Users should
understand the threat model's boundaries.

### Fix Plan

| Task | File(s) | Description |
|------|---------|-------------|
| F1 | `docs/SAFETY-GATES.md` | Add "Limitations" section documenting: (1) pattern-based secrets detection can miss base64-encoded or split secrets, (2) steering injection requires write access to state/session/ files, (3) Gate R protects against accidental access not intentional circumvention, (4) read cache is an efficiency mechanism not a security boundary. |
| F2 | `docs/SAFETY-GATES.md` | Add "Threat Model" section: what GRIP protects against (accidental agent misbehaviour, prompt injection escalation, secret leakage) vs what it doesn't (compromised filesystem, malicious operator, model-level attacks). |

---

## Workstream G: Meta-Fixes (P2)

### G1: Ultraplan Context Transfer

`/ultraplan` spawns a remote session via `RemoteTrigger` but doesn't pass the
originating session's conversation context. The remote session starts cold and has
to rediscover everything.

**Fix**: Modify the ultraplan skill to auto-include relevant context: (a) any plan
documents referenced in the conversation, (b) the session transfer payload, (c) the
specific task description. Pass as the remote trigger's prompt body.

### G2: Fractal Claim Validation

The self-similarity claim (harness wrapping harness wrapping harness) holds at 2-3
levels: Claude Code -> GRIP hooks -> HAL abstraction. To claim "fractal":
- Map whether EVERY new capability follows harness -> abstraction -> harness naturally
- Demonstrate at 4+ levels, not just 2-3
- If only 2-3 levels: reframe as "well-composed recursive abstraction" (still impressive,
  just not fractal)

**Fix**: Add a section to `docs/strategic-analysis-apr2026.md` that either proves 4+
levels with evidence or honestly qualifies the claim.

---

## Execution Order

```
Phase 1 (P0 — Credibility)     Phase 2 (P1 — Quality)       Phase 3 (P2 — Hardening)
┌─────────────────────┐        ┌─────────────────────┐      ┌─────────────────────┐
│ B1: asset counter    │        │ C1: pyproject.toml  │      │ E1: profile imports  │
│ B2: update script    │        │ C2: re/sys.path     │      │ E2: lazy imports     │
│ B3: patch all files  │        │ C3: secrets logging │      │ E3: daemon research  │
│ B4: agent reconcile  │        │ C4: PID fix         │      │ F1: limitations doc  │
│ A3: threshold docs   │        │ D1: extend SM       │      │ F2: threat model     │
│ A4: honest docs      │        │ D2: cleanup/TTL     │      │ G1: ultraplan fix    │
│                      │        │ D3: wire rotator    │      │ G2: fractal claim    │
│                      │        │ D4: schema versions │      │                      │
│                      │        │ D5: migration       │      │                      │
└─────────────────────┘        └─────────────────────┘      └─────────────────────┘
        │                              │                              │
   A1+A2: confidence gate        Depends on Phase 1           Depends on Phase 2
   wiring (needs V>> decision)   being complete               being complete
        │                              │                              │
        └──── A5: tests ──────────────→┘                              │
                                                                      │
                                                              All done: update
                                                              grip-critical-analysis.md
                                                              with resolution status
```

---

## Workstream H: Plain-Language Terminology Rebrand (P0)

### The Problem

GRIP speaks in jargon. "Skills", "agents", "modes", "gates", "RSI", "genome", "KONO",
"convergence", "Broly" — none of these mean anything to someone who hasn't studied GRIP.
Rule 17 translates some metrics but the structural vocabulary is untouched.

### Terminology Map (Brainstorm → V>> Decision Needed)

| Current | Proposed | Directory | CLI | Why |
|---------|----------|-----------|-----|-----|
| Skills | **Playbooks** | `playbooks/` (later) | `/playbook` | Actionable instructions, DevOps standard |
| Agents | **Workers** | `workers/` (later) | `/worker` | Things that do jobs, no mystique |
| Modes | **Presets** | `presets/` (later) | `/preset` | Saved configurations for work types |
| CLAUDE.md | **Instruction file** | Keep filename | N/A | Claude Code convention, rename in docs only |
| Gates | **Guards** | `guards/` (later) | N/A | Active protection, clear intent |
| Hooks | **Automated checks** | Keep `hooks/` | N/A | Industry standard, rename in docs only |
| RSI | **Improvement cycle** | N/A | `/improve` | Plain description |
| Genome | **Feature scorecard** | N/A | `/scorecard` | Performance tracking |
| KONO | **Knowledge base** | N/A | `/recall` (keep) | Already in Rule 17 |
| Convergence | **Iterative refinement** | N/A | `/converge` (keep) | Describe the loop |
| Broly | **Council** | N/A | `/council` | Multiple perspectives, deliberation |

### Implementation Strategy (3 Phases)

**Phase 1 (Immediate)**: Update all user-facing text. Add `_TERMINOLOGY` map to
`lib/plain_language.py`. Internal directories stay for backward compat.

**Phase 2 (Next major version)**: Add directory symlinks (`playbooks/ -> skills/`).

**Phase 3 (Eventually)**: Rename directories, update all internal references.

### Fix Plan

| Task | Description |
|------|-------------|
| H1 | Add `_TERMINOLOGY` dict to `lib/plain_language.py` with all translations |
| H2 | Update Rule 17 glossary in `rules/efficiency-rules.md` with full vocabulary |
| H3 | Update README.md — replace jargon with plain-language equivalents |
| H4 | Update CLAUDE.md (global + project) — user-facing sections only |
| H5 | Update all gate/hook error messages to use "guard" instead of "gate" |
| H6 | Update `docs/SAFETY-GATES.md` → `docs/SAFETY-GUARDS.md` (or rename in-place) |

---

## Workstream I: Agent Tier Classification (P0)

### Research Findings

34 agents classified into 4 tiers:

| Tier | Name | Count | Examples |
|------|------|-------|---------|
| 1 | **Core** | 7 | context-refresh, dependency-guardian, file-read-optimizer, pr-workflow, broly, history-mining, efficiency-auditor |
| 2 | **Specialist** | 14 | auth-debugging, DRY/KISS/SOLID/GRASP/YAGNI enforcers, rsi-orchestrator, sprint-loop, pair-worker |
| 3 | **Infrastructure** | 7 | worktree-reaper, checkpoint-archiver, kono-reaper, learning-janitor, genome-monitor, lesson-carousel, skill-graph-validator |
| 4 | **Experimental** | 4 | a2a-bridge, broly-gemini, ysh-enforcer, _base-protocol |

### Critical Finding: Phantom Agents

8 agents are declared in AGENTS.grip but have **no .md implementation file**:
design-reviewer, mobile-responsive-fixer, plan-persistence, node-cleanup,
image-optim, learning-agent, doc-updater, and one more.

### Fix Plan

| Task | Description |
|------|-------------|
| I1 | Add `tier` field to each agent .md frontmatter (1-4) |
| I2 | Remove phantom agents from AGENTS.grip (or create implementations) |
| I3 | Regenerate AGENTS.grip with all 34 agents, grouped by tier |
| I4 | Using new terminology: rename document to "Workers Reference" |
| I5 | Update asset counter to report by tier: "7 core, 14 specialist, 7 infrastructure, 4 experimental" |

---

## Decision Gates (Updated)

| ID | Question | V>> Decision | Status |
|----|----------|-------------|--------|
| D-1 | Confidence gate approach | Build real HAL council measurement | **RESOLVED** — script built |
| D-2 | Asset counting convention | Count capabilities/presets, not skill.md files | **RESOLVED** |
| D-3 | Agent discrepancy | Tiered system (Core/Specialist/Infra/Experimental) | **RESOLVED** |
| D-4 | Terminology: accept proposed names? | Keep the platform's names: skills, agents, hooks, modes, gates (2026-11-24, GRIP#9496) | **RESOLVED** |
| D-5 | Phantom agents: implement or remove? | Implemented: all seven exist in `agents/` | **RESOLVED** |

---

## Estimated Effort (Revised)

| Workstream | Tasks | Est. PRs | Complexity | Status |
|------------|-------|----------|------------|--------|
| A: Confidence Gate | 5 | 2-3 | High | **Script DONE**, wiring + docs remain |
| B: Asset Counts | 4 | 1-2 | Low | Pending |
| C: Code Quality | 4 | 2 | Medium | Pending |
| D: State Consolidation | 5 | 3-4 | Medium | Pending |
| E: Hook Performance | 3 | 1-2 | Medium | Pending |
| F: Security Docs | 2 | 1 | Low | Pending |
| G: Meta-Fixes | 2 | 1-2 | Low-Medium | Backlog item created |
| H: Terminology Rebrand | 6 | 2-3 | Medium | Brainstorm DONE |
| I: Agent Tiers | 5 | 1-2 | Low-Medium | Research DONE |
| **Total** | **~36** | **15-22** | **6-8 sessions** | |

---

## Success Criteria (Revised)

1. `python3 -m pytest tests/test_confidence_gate.py` — 24/24 pass (A — **DONE**)
2. `confidence` wired into `context-gate-hook.py` gate pipeline (A — remaining)
3. Asset counter auto-generates counts matching all documentation (B)
4. `lib/plain_language.py` contains `_TERMINOLOGY` map with 11+ entries (H)
5. Zero jargon in README.md, SAFETY docs, or error messages (H)
6. All agents have `tier:` frontmatter and AGENTS reference is current (I)
7. `time echo '{}' | python3 hooks/context-gate-hook.py` < 80ms (E)
8. `docs/SAFETY-GATES.md` contains "Limitations" and "Threat Model" sections (F)
9. All 576+ tests still pass after every workstream

---

## Status on 24 November 2026

V>> asked for this plan to be restarted on 24 November 2026. A Claude Code session
(Claude Opus 5.5, `claude-opus-5-5`) checked every task against `main` at `aa716407b`.
Each row below names the file or the measurement that decides its status.

17 tasks had already landed: 13 as planned and 4 in another form. The restart finishes
5 more: A3, A4, E1 and I1 in the pull request that adds this section, and C3 in the
Gate E pull request, GRIP#9501. 5 tasks are partly done, 4 are open, and 5 wait on decision D-4.
GRIP#9496 tracks the 14 unfinished tasks. Decision D-5 is settled: the seven agents it
named now exist.

Later on 24 November V>> accepted the session's recommendations for the open decisions
(GRIP#9496): log rotation deletes nothing (D3, done in GRIP#9519), the platform's names
stay (D-4), and C2 and E2 close. GRIP#9520 also sets both instruction files at 99.9999999%.
The rows below show the result: all 36 tasks are settled, 28 done and 8 closed by
decision. I5, G2, E3 and D5 were done later on 24 November.

| Task | Status | Evidence on `main` |
|------|--------|--------------------|
| A1 | Done | `_gate_confidence` is defined at `hooks/context-gate-hook.py:1977` and runs in the gate pipeline at `:2026`. |
| A2 | Done another way | `lib/confidence_gate.py` scores in three tiers: a heuristic on every call, then a two-model council, then a full council, each only when the tier before it is unsure. The default threshold is 0.70 (`:60`). |
| A3 | Done by this restart | `docs/SAFETY-GATES.md`, under "Default Gates", now explains the 70% gate beside the 99.9999999% and 95% instructions. |
| A4 | Done by this restart | The same paragraph. `README.md` makes no confidence claim, and the instruction files keep their numbers because they are the instructions. |
| A5 | Done | `tests/test_confidence_gate.py` (`:429` runs `_gate_confidence`), `tests/test_confidence_gate_env.py`, `tests/test_confidence_context_governor.py` and `tests/test_confidence_calibrator.py`. |
| B1 | Done | `lib/asset_counter.py`. |
| B2 | Done another way | `scripts/grip-stats.sh --fix` and `hooks/doc-drift-hook.py` regenerate the count line in `rules/system-capabilities.md`. |
| B3 | Done | No hand-typed count remains in the seven files listed under Workstream B. The count in `agents/CLAUDE.md` is kept current by `hooks/doc-drift-hook.py` (`tests/test_doc_drift_hook_counts.py`). |
| B4 | Done another way | `docs/AGENTS.grip` became `docs/AGENTS.md`, generated by `scripts/generate-reference-docs.py`, and it lists all 44 agent files. |
| C1 | Done | `pyproject.toml`: `requires-python = ">=3.10"`, ruff line length 120, rules E, W, F and I. |
| C2 | Closed by decision | No `import re as` alias remains. Six `sys.path.insert` calls remain (`hooks/context-gate-hook.py:66-68`, `:82`, `:304` and `:2273`); they cost nothing measurable, which does not justify changing GRIP's most critical hook. |
| C3 | Done by this restart, re-scoped | `hooks/secrets-hook.py` was never wired into the tracked settings; GRIP#9501 deletes it. The output scanner that runs is `hooks/secrets-output-hook.py` (Gate E), and GRIP#9501 makes it log a payload it cannot read. |
| C4 | Done | `_get_cc_pid_for_gate` no longer exists. The remaining `subprocess` calls run `git` or `gh`: `hooks/context-gate-hook.py:1322-1432`. |
| D1 | Done | `CACHE_DIR` at `lib/state_manager.py:112`, tracked at `:158`. |
| D2 | Done by this restart (GRIP#9519) | `cleanup_expired_cache` (`lib/state_manager.py:331`) covers `cache/`. For logs, decision D3 applies: GRIP#9519 rotates the launchd output logs. On the Mac where the re-audit ran, `logs/` held 573 MB, 218 MB of it in `logs/moat-anchor.out.log`. |
| D3 | Done by this restart (GRIP#9519) | Decision: rotation deletes nothing. `grip_log_rotate` (`lib/logging.sh`) gzips the whole log into `logs/archive/` before trimming it in place, and runs at session start on the launchd logs over 20 MB. It used to discard the overflow it said it archived. `lib/log_rotator.py` is still not called. |
| D4 | Done | `_schema_version` stamping at `lib/state_manager.py:255-256`, and `migrate_all` at `:378`. |
| D5 | Done | The last five root state files (`.improvement-queue.json`, `.insight-verifier-state.json`, `.knowledge-work-debounce.json`, `.orchestrator-state.json` and `.quality-debounce.json`) are now read and written under `state/`, at the paths `lib/state_paths.py` names. `state_file` copies a value left at an old root file once; `lib/orchestrator.sh` does the same for its file. `tests/test_state_paths.py` fails if production code names one of the old files again anywhere but the migration map, that shell copy-over, and the session-start cleanup that deletes a leftover file. |
| E1 | Done by this restart (the profile it asked for) | `echo '{}' \| python hooks/context-gate-hook.py`, ten runs: median 90 to 100 ms, against 186 ms in April. The 80 ms target belongs to E2 and is not met. Python starting accounts for about 30 ms. In two profile runs the slowest imports took 13 to 21 ms (`api_audit`), 9 to 19 ms (`file_lock`) and 6 to 12 ms (`traceback`). |
| E2 | Closed by decision | `confidence_gate` already loads lazily (`hooks/context-gate-hook.py:1990`). `api_audit` runs on every allowed call (`:2268`) and the state helpers use `file_lock` (`:267`, `:276`), so `traceback` (`:52`, used once at `:2176`) is the only import that can clearly move. The 80 ms target is not met. |
| E3 | Done | `docs/measurements/2026-11-24-hook-daemon-go-no-go.md` and `scripts/measure_hook_startup.py`: in interleaved pairs, starting the 18 Python PreToolUse hooks of a Bash call took a median 757.3 ms to 873.8 ms of CPU, and loading them in one process took 63.3 ms to 79.6 ms. Verdict: go for a one-process dispatcher per event as the first prototype (GRIP#9547), no-go for a plain shared daemon, a fork server only if the dispatcher gives its gain back; use the documented `if` and `async` hook settings first. |
| F1 | Done | `docs/SAFETY-GATES.md`, section "Limitations", six items. |
| F2 | Done | `docs/SAFETY-GATES.md`, section "Threat Model". |
| G1 | Done | `skills/ultraplan/SKILL.md` builds a self-contained prompt from the session's context. |
| G2 | Done, re-scoped | `docs/strategic-analysis-apr2026.md` no longer exists. No use of "fractal" in the six files was backed by evidence of four or more self-similar levels (full-converge names four levels, but the deepest nesting on record is three), so each now says in plain words what it means: /evo runs one `converge()` loop (`lib/evo.py:753`), Broly recurses at most three levels, full-converge names four levels that a session applies, and the agent gates cover two depths. Feature names such as "Fractal Facet Evolution" stay. |
| H1 | Done | `_TERMINOLOGY` at `lib/plain_language.py:165` holds every pair in the Workstream H table. |
| H2 | Closed: not wanted (D-4) | Rule 17's glossary (`rules/efficiency-rules.md:202`) keeps translating GRIP's own jargon; the platform's names stay. |
| H3 | Closed: not wanted (D-4) | `README.md` keeps the platform's names. |
| H4 | Closed: not wanted (D-4) | The instruction files keep the platform's names. |
| H5 | Closed: not wanted (D-4) | Gate messages keep saying "gate". |
| H6 | Closed: not wanted (D-4) | `docs/SAFETY-GATES.md` keeps its name. |
| I1 | Done by this restart | 42 of the 43 agent definitions already carried `tier:` in their frontmatter. This restart adds it to `agents/Explore.md`, the one without it. `agents/_base-protocol.md` is a protocol document, not an agent. |
| I2 | Done | All seven agents named under "Critical Finding" now exist in `agents/`. |
| I3 | Done another way | `docs/AGENTS.md` groups the agents by tier. |
| I4 | Closed: not wanted (D-4) | `docs/AGENTS.md` keeps its name. |
| I5 | Done | `count_agents_by_tier` in `lib/asset_counter.py` (`scripts/grip-stats.sh --tiers`) reads each agent's frontmatter `tier:`: 8 core, 23 specialist, 11 infrastructure and 1 experimental on 24 November. `agents/_base-protocol.md`, the protocol the agents link to, no longer counts as an agent, so the count is 43. |
