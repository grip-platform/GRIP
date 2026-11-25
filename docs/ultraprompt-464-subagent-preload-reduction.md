# /ultraprompt — #464: Shrink the subagent preload below the 200K ceiling

> Self-contained brief for a FRESH, focused session. You have no memory of the
> session that wrote this (2026-07-17). Everything you need is below. Verify-canonical
> anything you act on — these findings are point-in-time.

## Mission

Make GRIP custom-agent (Task-tool subagent) dispatch reliable by getting the spawn
**preload below the 200K ceiling**, so multi-file-read agent workloads stop dying
mid-run with **"Prompt is too long"**. Ship the durable structural fix as a
**V>>+AGORA-gated PR**.

## Why it matters (external value — not internal theatre)

Subagent dispatch is a core GRIP capability: Explore, design-reviewer,
security-healer, broly councils, Workflow `agent()` fan-outs, claude-code-guide.
It is currently **BROKEN for any real workload** — the zero-tool spawn preload is
~208K, sitting on the 200K subagent ceiling, so an agent dies after its first batch
of reads. This blocks all substantive delegation. On 2026-07-17 a `claude-code-guide`
agent died on this while trying to investigate CC internals — the bug blocks even
investigating itself. Fixing it unblocks every future multi-agent workflow.

## Established findings — DO NOT re-derive (verify-canonical before acting)

1. **Preload ≈ 207,920 tokens** zero-tool (measured 2026-07-14, design-reviewer),
   within a few thousand of the 200K window. **0-tool spawns live; any multi-file
   read workload dies** (~6–8s in, after the Reads + per-tool-call gate injections).
2. **Subagents run at 200K, NOT the main session's 1M. Model pin does NOT fix it** —
   sonnet, haiku, and `inherit` ALL died on the same 6-file audit. This is NOT the
   1M-model / `[1m]` issue from #514/#518 (that's for the interactive REPL + child
   sessions; a Task-tool subagent cannot reach 1M via the model field). Confirm this
   still holds — don't burn time re-pinning models.
3. **`rules/*.md` are auto-loaded NATIVELY by Claude Code** — verified 2026-07-17:
   NO `@rules` import in any CLAUDE.md, NO rules/import config in `settings.json`.
   CC discovers `~/.grip/rules/*.md` itself and injects the FULL stack identically
   into the main session (1M — fine) and every subagent (200K — fatal). The stack:
   `rules/*.md` (~73k, dominant) + `HOLISTIC.md` (~30k) + `MEMORY.md` +
   `session-context.md` + `CLAUDE.md` (~8.7k) + skills catalogue (~16.5k) + the
   mandated 4-skill agent frontmatter + tool schemas.
4. **The preload MOVES** — `HOLISTIC.md` / `MEMORY.md` / `session-context.md`
   regenerate mid-session (memory writes fatten later spawns). Failures correlate
   with **kernel size + tool-count**, not model / gateway / credit.
5. **Worktree agent-def edits are INERT** — definitions load from the LIVE tree
   (`~/.grip/agents/`). Verify WHICH file a spawn actually reads before claiming any
   fix works (the trap that produced a falsely-"verified" fix in 2026-07-14).
6. **Interim least-bad shape** (already on design-reviewer, PR #4030):
   `model: haiku` + `effort: low` + minimal tools. Rescues only LIGHT spawns, not
   heavy-read ones. Not the durable fix.

## Hard constraints

- **DEPTH-0 ONLY.** A subagent CANNOT investigate this — the preload bug IS the
  subject. Run all probes on the main thread.
- **V>>+AGORA-GATED.** This is a guardrail-class shared-config change
  (`dispatch-economics.md`). Ship as a PR held for V>> + AGORA; NEVER silent-merge.
- **NEVER weaken the safety core.** Dropping a safety rule from subagent loading is
  a SECURITY REGRESSION — the fix must keep every mechanical/PARAMOUNT safety
  doctrine loaded in subagents.
- **verify-canonical for config** — confirm the file/dir a spawn loads (finding 5).

## Phase 1 — Investigate CC's per-context memory-loading (THE CRUX)

The durable fix needs the main session to load MORE rules than a subagent. So:
does CC support per-context (main-vs-subagent) memory scoping, and HOW does it
discover `~/.grip/rules/*.md`?

- **WebFetch** the Claude Code docs: memory (CLAUDE.md) loading + precedence,
  `@`-imports, subagent/Task context assembly, `additionalDirectories`,
  agent-definition frontmatter fields, and the changelog for any per-agent memory
  control. (`docs.claude.com/claude-code`.)
- **Empirically probe (depth-0):** move a throwaway test rule out of `rules/` to a
  sibling dir; check whether it still loads into context. Establish the EXACT
  discovery rule (all of `rules/*.md`? only `CLAUDE.md` + `@`-imports? a memory-dir
  convention?).
- **Determine the lever:** is there ANY way for a subagent to load a SMALLER rule
  set than the main session (agent frontmatter field, a subagent-scoped CLAUDE.md,
  a CC config)? If none exists, the fix is "physically relocate non-safety rules to
  a path the main session `@`-imports but subagents don't" — confirm that path
  difference is real.

## Phase 2 — Design the safety-core / extended split

- Classify `rules/*.md`: **SAFETY-CORE** (always load everywhere — e.g.
  `no-guardrail-evasion`, `untrusted-content-capability-lock`,
  `no-sensitive-info-in-grip`, `bash-safety`, the gate/`operational-context` safety
  bits) vs **EXTENDED** (non-safety behavioural/output-style/dispatch doctrines a
  subagent doesn't strictly need at spawn).
- Design loading so safety-core always loads (main + subagent) and extended loads
  only where 1M is available (main). Mechanism per Phase 1 (relocate extended to a
  main-only-`@`-imported path, or a CC per-agent memory config if one exists).
- **Target: subagent preload < 150K** (headroom for read workloads).

## Phase 3 — Ship gated

- Implement. **verify-canonical the RESULT**: a zero-tool probe agent reporting its
  own context size confirms the preload dropped **< 150K**, AND a heavy-read agent
  (6+ files) now COMPLETES. Measure real behaviour, not just config.
- **Goodhart-proof test**: assert (a) every safety-core rule is present in a
  subagent's load, AND (b) the preload is under the ceiling — plus a behavioural
  anchor (a heavy-read agent finishing). A test that only checks config is gameable.
- Open a PR **held for V>> + AGORA**. Register a falsifiable hypothesis (subagent
  heavy-workload success rate, before/after).

## Falsifiers (kill or revise the fix if)

- A subagent with verified preload well under 150K still dies "Prompt is too long"
  on a small workload → the ceiling model is incomplete; re-investigate.
- The split drops ANY safety rule from subagent loading → SECURITY REGRESSION →
  revert immediately.
- Path-scoping ships and heavy-read agents still fail at <150K preload → the preload
  isn't the real cause; re-investigate the true burst driver.

## References

- Memory `feedback-subagent-preload-ceiling-prompt-too-long` — the probe findings.
- Rule `dispatch-economics.md` — the floor breakdown + the "path-scope non-safety
  rules, V>>+AGORA-gated" lever statement.
- PR #4030 — the interim design-reviewer shape.
- Task **#464**. Sibling context: #514/#518 (the 1M-model work — DISTINCT from this;
  don't conflate).
