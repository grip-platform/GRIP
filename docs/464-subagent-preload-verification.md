# #464 — Subagent preload split: verification plan + falsifiers

Fixes: shrink the Task-tool subagent preload below the 200K ceiling so multi-file-read
agent workloads stop dying "Prompt is too long". Brief: `docs/ultraprompt-464-subagent-preload-reduction.md`.

## What shipped (this PR — held for V>> + AGORA)

| Artefact | Role |
|---|---|
| `config/rules-scope.json` | Single source of truth. Classifies the 53 `rules/*.md` into **safety-core** (18) vs **extended** (35). Conservative default: unlisted/new rule = safety-core. |
| `settings.json` `claudeMdExcludes` | Excludes the 35 extended rules from Claude Code's native memory load (removes them from *both* main and subagent preload). |
| `hooks/subagent-preload-main-inject.py` | SessionStart hook: re-injects the 35 extended rules into the **MAIN session only** via `additionalContext`. SessionStart never fires for subagents (that is `SubagentStart`), and re-fires on `source=startup\|resume\|clear\|compact` — so main keeps the full stack across `/compact`. |
| `lib/rules_scope.py` | Classifier + `--validate` / `--check-settings` / `--sync-settings` / `--projection`. |
| `tests/test_rules_scope.py` | 11 Goodhart-proof tests (mutation-sensitive — proven: moving `no-guardrail-evasion.md` to extended fails `--validate`). |

## Mechanism (documented CC behaviour — code.claude.com/docs)

- **memory doc:** `~/.claude/rules/*.md` are loaded natively "at launch with the same priority as `.claude/CLAUDE.md`". No `@`-import or hook does it — CC discovers the dir itself.
- **sub-agents "What loads at startup":** a non-fork subagent loads "every level of the CLAUDE.md hierarchy … including project rules" → the native rules stack IS the ~208K preload. It does NOT inherit main-conversation state (auto-memory, output-style, conversation history incl. SessionStart `additionalContext`).
- CC's own design agrees: *"most rules don't need to reach the subagent itself."*

Net: safety-core stays native → reaches subagents (safety preserved); extended is excluded from native → main-only via SessionStart. Static projection: safety-core ≈ **24.8K tok**; shed ≈ **79.5K tok** (live) → subagent preload ~208K → **~128K** (< 150K target, ~22K read headroom).

## Post-merge verification (verify-canonical — MUST run on the LIVE `~/.grip` tree)

Worktree/settings edits are INERT for spawns (definitions load from the live tree), and `claudeMdExcludes` + native rule load are applied at **session launch** — so this can ONLY be verified after merge to `main` + a fresh session:

1. **Confirm the exclusion is honoured (the #1 falsifier below):** in a fresh session, run `/context` and confirm the excluded rule files are ABSENT from the loaded CLAUDE.md/rules breakdown, and the top-level `~/.grip/CLAUDE.md` is still PRESENT.
2. **Probe subagent preload:** spawn a trivial zero-tool agent that reports its own context size (a 0-tool spawn lives). Confirm < 150K.
3. **Behavioural anchor (the real test):** spawn a heavy-read custom agent (design-reviewer over 6+ files). Confirm it COMPLETES instead of dying "Prompt is too long" mid-run.
4. **Main-session integrity:** confirm the main session still has the extended rules (via the SessionStart injector), including after a `/compact`.

## Falsifiers (kill or revise the fix if)

1. **`claudeMdExcludes` does not apply to USER-scope `~/.claude/rules/`** (only project `.claude/rules/`). This is the primary uncertainty — the CC doc example excludes a *project* rules dir; user-scope exclusion is inferred, not doc-confirmed. If step 1 shows the excluded rules STILL loaded → the mechanism fails → switch to the **relocation fallback** (below). The classifier already knows the extended set, so the fallback is a mechanical follow-up, not a redesign.
2. A subagent with verified preload < 150K still dies "Prompt is too long" on a small workload → the ceiling model is incomplete (re-investigate the burst driver, per brief Phase 1).
3. The split drops ANY safety rule from subagent loading (step: a mechanical-safety rule missing from a subagent's context) → SECURITY REGRESSION → revert immediately. (The `--validate` mutation guard + `test_hard_safety_rules_reach_subagents` defend against this at config time.)
4. `additionalContext` has a size cap below ~72K tok, so the injector fails to deliver the full extended stack to main → split the injection or reduce (secondary; GRIP's kono_preload already surfaces large SessionStart context, so unlikely).

## Relocation fallback (if falsifier 1 fires)

Move the 35 extended files `rules/<name>.md` → `rules-extended/<name>.md` (a dir CC does NOT natively load — guaranteed to remove them from every context). Point the SessionStart injector + `kono_preload`/`holistic_synthesiser` output at `rules-extended/`. Higher churn (prose cross-references + 2 generators) but mechanism-guaranteed. Deferred unless step 1 proves `claudeMdExcludes` ignores user rules.

## Rollback (fully reversible)

Delete `settings.json`'s `claudeMdExcludes` + `_claudeMdExcludes_doc` keys and the `subagent-preload-main-inject.py` SessionStart entry; remove the 4 new files. Next session reverts to byte-identical prior behaviour (all rules natively loaded everywhere).

## Hypothesis

Registered: subagent heavy-read (6+ file) workload success rate rises from ~0% (pre) to ≥90% (post) with subagent preload < 150K, AND zero safety-core rules dropped from subagent context. Deadline 2026-08-15.
