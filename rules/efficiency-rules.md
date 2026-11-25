# Efficiency Rules

## Rule 0: Recall First (PARAMOUNT)

Recall First (`/recall`, `skills/recall/SKILL.md`) is GRIP's strongest retrieval discipline — a context-specific
strategy applied when the agent is unsure or lacks sufficient information. It is
not a universal doctrine; it applies where retrieval is involved and steps aside
for direct commands, pure creation, and conversational exchanges.

**When to apply:** Any prompt involving uncertainty, knowledge lookup, or information
retrieval — across any domain (code, strategy, research, legal, writing, planning).
Ask: "What do I already know?" before reaching for external tools.

**When to skip:** Direct execution commands, pure creation tasks, real-time external
data, user-requested web searches, conversational exchanges.

**Mode applicability:** Recall First applies in ALL modes without exception,
including Plan Mode. Even when constrained to read-only operations, the retrieval
tier discipline (session context -> `/recall` -> known files -> Grep -> Explore) must be
followed before launching Explore agents for research.

**Fresh data priority:** When the user provides new data (pastes, quotes, screenshots,
documents), the fresh data takes precedence over prior session context on the same
topic. Tier 0 still fires (connecting to session history at zero cost).

- **New topic** (no prior context) → fresh data wins outright
- **Extends prior context** (additive, no conflict) → fresh data wins
- **Contradicts prior context** → AskUserQuestion: "This differs from what we
  established earlier. Is this an intentional change, or should we continue with
  the prior version?" (prevents losing collaborative progress to accidental pastes)

Examples:
- User pastes a function → fresh code takes priority, Tier 0 recalls prior discussion
  of the same module (e.g. "we refactored this class 20 messages ago")
- User quotes an error message → fresh error takes priority, Tier 0 connects to prior
  debugging context (e.g. "this service had port issues earlier in session")
- User shares a screenshot → fresh visual takes priority, Tier 0 links to prior
  discussion of that component or design
- User pastes a config block → fresh config takes priority, Tier 0 recalls prior
  changes discussed (e.g. "we changed this setting in the last PR")
- User pastes a contract clause → fresh legal text takes priority, Tier 0 connects to
  prior negotiation context or related clauses reviewed earlier
- User shares meeting notes → fresh notes take priority, Tier 0 links to prior
  decisions, action items, or stakeholder context from the session

**Retrieval tiers (escalate only when prior tier fails):**

| Tier | Method | Cost | When |
|------|--------|------|------|
| 0 | Session context / mental model | ~0 tokens | "Do I already know this?" |
| 1 | `/recall`: rulings, verified records, BM25 over memory, rules, docs and skills, then links | ~200 tokens | GRIP-internal knowledge |
| 2 | Structured lookup (Read known paths) | ~500 tokens | Known file locations |
| 3 | Filesystem search (Grep/Glob) | ~1000 tokens | Unknown file locations |
| 4 | Explore agent | ~88k tokens | ONLY after tiers 0-3 exhausted AND diagnosed |

**Each escalation requires a documented reason.** Return at the FIRST tier that resolves.

**Evidence:** 440x token ratio (88k Explore vs about 200 for a local recall). The H866 head-to-head (2026-10-17, 572 held-out questions) put BM25 at 87.9% hit@3 against 50.0% for the embedding store as wired, so the keyword index comes before meaning search. 2 documented violations cost
~368k tokens combined. Correct application (Slack channel lookup, 2026-03-15) resolved
in 2 Read calls, ~200 tokens, zero agents.

**Status:** Elevated from Rule 14 to Rule 0 (2026-03-15). Multi-model falsification
council (Claude + Gemini + Llama) falsified the original "Meta-Rule 0 / universal
doctrine" framing. Revised to context-specific strategy with explicit scope boundaries.
Further investigation and GRIP analyses needed to determine full applicability.

Enforcement: `grip-first-retrieval-hook.py` (Agent gate) + `grip-first-web-hook.py`
(WebFetch/WebSearch gate). Both use shared `lib/grip_first_common.py`.

---

## Rule 1: Descriptive Plan Names
Plan names describe the work: `auth-refactor-plan.md` not `joyful-snacking-cloud.md`.

## Rule 2: File Read Cache
Before Read: check if read in last 10 messages. If yes and no external edits: skip re-read. Phase 1: batch all reads. Phase 2+: targeted edits only.

## Rule 3: Plan Evaluation Gate
Before executing plan items: "Is this change actually needed?" If already done or uncertain: HALT and propose skip.

## Rule 4: Implementation Directness
Always choose the most direct path. No temp scripts — use Edit/Write directly.

## Rule 5: Skill Tool Gate
If action is trivially inferable from the command, execute directly. Only invoke Skill tool when multi-step protocol reference is needed.

## Rule 6: Content-Based Validity
Context validity = content relevance, not age. Trust session context across hours/days.

## Rule 7: AskUserQuestion MANDATORY (PARAMOUNT)
When confidence < 99.9999999%: **HALT and AskUserQuestion**. Especially for: UI/UX decisions, brand elements, removing/changing existing elements, any choice with multiple valid options. If you think "I think this is what the user wants" — that thought means ASK.

**Yolo mode carve-out** (Rule 7-bis): when `/ultrado`, `/rsi-optimal`, or `autonomous-rsi-sprint` has activated the yolo marker at `~/.claude/state/session/yolo-mode.json`, the `approach-verification` and `confidence-gate` halting gates are relaxed so the loop can proceed. The operator's explicit slash-command invocation IS the standing consent. The marker's TTL bounds the relaxation, and every bypass fire is audit-logged to `~/.claude/logs/yolo-mode.log`.

Quality gates (DRY/KISS/BIG-O/YAGNI/CC, JIT correct-by-construction) keep firing under the default yolo preset because they make code better, not slower. They also enforce *differentially*: on every Write/Edit the gate scans the file's current content AND the proposed post-edit content, then partitions violations into **introduced** (new in this edit) and **pre-existing** (present in the file before the edit too). Introduced violations ALWAYS deny — the operator just added them, and yolo never overrides that. Pre-existing violations deny by default but pass under the opt-in `yolo_quality` preset (`grip-yolo on --quality --reason '...'`), which lets a narrow edit land alongside known debt without forcing an unrelated refactor. The bypass is audit-logged and bounded by the marker's TTL. See `docs/SAFETY-GATES.md` section "Yolo Mode" for the full gate matrix and `plans/yolo-mode-unified-autonomous.md` for the design rationale.

## Rule 8: Context Gate (PARAMOUNT)
At 85% context: HALT all work. AskUserQuestion with options (compact/serialize/push-through/checkpoint). Never auto-serialize. Self-check every 10-15 messages. Also run `/context` (CC 2.1.74+) for a tool-by-tool breakdown of context usage — it surfaces context-heavy tools, memory bloat, and capacity warnings with specific optimisation tips.

## Rule 9: Compact Hook Output
Hooks output status only, not skill content (already in CLAUDE.md). Single compact line.

## Rule 10: Periodic Signal Compliance
Hook periodic signals (`[PERIODIC: PATTERN_SCAN_DUE]`) — address immediately before next tool call. User can skip via AskUserQuestion.

## Rule 11: Anti-Drift — say what happens next
Every substantive report ends with the recommended next step, naming whose move it is. 32% of sessions ended partially-achieved from untracked pivoting.

**Single definition: `rules/canonical-output-style.md`** — the always-on one-liner (`RECOMMENDED NEXT STEP (<owner>) -- …`, `(yours)` only where its owner test says so) and the rules that govern it. The four-line "What's Up Next" template that used to be restated here verbatim is the post-commit EXPANDED form only, and lives in `@rules/session-protocol.md`. It was duplicated across three files and enforced in none; do not restate either form again.

## Rule 12: Auto-Cleanup
Delete temporary scripts after task completion. They accumulate and waste context.

## Rule 13: Worktree-First Isolation (PARAMOUNT for parallel RSI/ultrado)

**Enforcement levels** (Gate O, decided by `lib/gates/session_isolation.py::enforcement_level()`):

- **relaxed** (default): Worktree isolation RECOMMENDED. Direct branch work ALLOWED. Gate O logs a warning on unsafe branch switches but does not block. Preserves V>>'s 2026-04-02 RSI admin bypass for normal work.
- **paramount** (auto-escalated, non-overridable): Branch-switching is BLOCKED. The only escape is `GRIP_ISOLATION_BYPASS=1` for one session (audit-logged to `logs/isolation-bypass.log`).

**Paramount escalation** — fires if and only if **all three** conditions hold simultaneously:

1. `count_claude_sessions() >= 2` — another `claude` CLI process is active (`pgrep -x claude`, exact match; excludes Claude Desktop, MCP servers, hooks)
2. `is_rsi_context() or is_ultrado_context()` — the current session is running an autonomous loop. Detected via `GRIP_RSI_ACTIVE=1` / `GRIP_ULTRADO_ACTIVE=1` env vars (authoritative), or via recent checkpoint files under `state/rsi/` / `state/ultrado/` whose embedded timestamp is within 2 hours
3. `not is_in_worktree()` — current cwd is the shared main working tree, determined by comparing `git rev-parse --git-dir` to `--git-common-dir`

Outside that exact intersection, the default `relaxed` level applies so interactive single-session work is not obstructed.

**Why PARAMOUNT for this intersection**: Gate O intercepts *my* session's tool calls. It cannot prevent a *parallel* session from changing *my* branch via its own tool calls — that collision is physically unstoppable while both sessions share `.git/HEAD`. The only robust mitigation is to make it impossible for an RSI/ultrado loop to *run* in the shared main tree when another session exists. Once each autonomous loop lives in its own worktree, the two sessions have independent HEADs and physically cannot step on each other.

**Origin**: Gen 348 observed the failure mode (branch switched mid-commit). Gen 509 observed it again while shipping PR #2172 — a parallel session switched the working tree from `fix/gates-relative-imports` to `fix/stash-guard-client-mirror` during my commit sequence. The only reason no work was lost is that the commits had already been pushed to origin. V>>'s 2026-04-02 blanket `_get_worktree_enforcement() → "relaxed"` bypass removed the auto-escalation entirely, trading safety for unblocking RSI sprints. This rule restores the auto-escalation in a narrower, context-aware form.

**Pattern** (for spawning RSI/ultrado work from an interactive session):

```python
Task(
    prompt="bash ~/.claude/lib/grip-worktree.sh add /tmp/<name> feat/<branch> && cd /tmp/<name> && [work, commit, push]",
    subagent_type="direct-implementation"
)
```

The main session stays on `main`. The agent works in `${TMPDIR:-/tmp}/<name>`. On completion, the agent pushes and the worktree is disposable.

**Entry-point check** (for skills): `/ultrado` and `/rsi` entry points should call `require_worktree_isolation("ultrado")` before any other action. It returns `None` when isolation is not required or a blocking error string when paramount escalation is active, suitable for surfacing to the operator as an AskUserQuestion.

**git-crypt worktree protocol (MANDATORY — check BEFORE any worktree)**:

1. **Check first**: `git-crypt status -e | head -1` — if output exists, repo is encrypted
2. **Encrypted repo**: MUST use `bash ~/.claude/lib/grip-worktree.sh add /tmp/<name> feat/<branch>` (3-step: `--no-checkout` + symlink `git-crypt/` + checkout)
3. **Unencrypted repo**: Can use native `isolation: "worktree"` on the Agent tool
4. **NEVER** use `isolation: "worktree"` on a git-crypt repo — it will fail with smudge filter errors

**Windows**: `grip-worktree.sh` uses directory junctions (`mklink /J`) instead of symlinks — no Developer Mode or admin required. Temp paths use `$TMPDIR` (Git Bash sets this).

**Audit**: every `GRIP_ISOLATION_BYPASS=1` invocation appends one line to `logs/isolation-bypass.log` (timestamp, context, session count, cwd). Bypasses are transparent but not silent.

**ExitWorktree tool (CC 2.1.72+)**: after a worktree agent completes its work, use the `ExitWorktree` tool to cleanly leave the `EnterWorktree` session before running `git worktree prune`. This ensures the session's tool state is flushed before the worktree is removed.

**CLAUDE_CODE_DISABLE_CRON (CC 2.1.72+)**: set `CLAUDE_CODE_DISABLE_CRON=1` in test environments (e.g. `tests/conftest.py`) and ultrado/RSI sessions to prevent periodic cron hooks from firing during automated runs. Prevents hook interference with deterministic test results.

## Rule 14: RSI Test Coverage + Goodhart Protection (PARAMOUNT)

Every autonomous RSI loop MUST include tests. Every PR in an RSI sprint MUST modify or add test files. No exceptions.

**Goodhart protection**: Tests must verify actual output values, not just call counts. A test that always passes is worse than no test — it creates false confidence. Use the Goodhart Guard enforcer (`test_goodhart_guard.py`) to validate that tests can actually fail when logic is wrong.

**Anti-gaming checklist** (applied before every RSI commit):
1. Does the test fail if the implementation is broken? (mutation test mindset)
2. Does the test verify behaviour, not implementation details? (no mocking internals)
3. Would the test catch the bug that motivated this PR? (regression anchor)

**Why**: PR #460 scored 50/100 on Broly pre-screen because testing dimension was 35/100. The fix was real (added 32 tests), not score-gaming. This rule ensures future RSI loops don't skip tests to optimise for speed.

Origin: Gen 386. User directive: "always add tests for autonomous RSI loops, protect against Goodhart, embodied."

## Rule 15: Quality Signal Compliance (PARAMOUNT)

When [QUALITY] signals appear in conversation context:
1. From PreToolUse DENY (DRY/KISS): tool call was blocked — fix the violation, then retry.
2. From PreToolUse WARN (DRY/KISS): fix before proceeding to next task.
3. From PostToolUse advisory (BIG-O/YAGNI): fix before proceeding to next task.
4. From Read of existing code: note violations. Fix only if your current task modifies that file.
5. For deep analysis beyond heuristic detection: spawn enforcer agents (solid-enforcer,
   grasp-enforcer, dry-kiss-enforcer, yagni-enforcer) via Agent tool.
6. NEVER ship code with unaddressed [QUALITY] warnings.

The PreToolUse quality gate mechanically blocks DRY/KISS violations (cannot be overridden).
This rule covers BIG-O, YAGNI, and PostToolUse signals (behavioural enforcement).

Origin: a client API repo, PR #2619 — 4 DRY violations shipped because PostToolUse advisory was
the only enforcement. PreToolUse gate (quality-pretool-hook.py) now makes this impossible.

## Rule 16: Insight Quality Gate (PARAMOUNT)

Every Insight block must describe what the CODE does, not what it PROVES.

Valid: implementation choices, architectural trade-offs, non-obvious component interactions, performance implications.
Invalid: mapping theoretical frameworks to operational steps, fabricating numeric trajectories, claiming self-similarity in sequential task completion.

Test: "Would a senior engineer reading this learn something specific about THIS code?" If no, do not generate it.

## Rule 17: Plain Language Output (PARAMOUNT)

All user-facing output must be understandable by someone who has never seen GRIP before.
Internal metric names, hypothesis codes, and insider terminology must be translated to
plain English through reasoning, not string replacement.

**Test:** "Would a general human being understand this without asking for clarification?"
If no, rewrite it.

**Numeric metrics** (flow, velocity, health) are translated by `lib/plain_language.py`.
**Terminology** (convergence depth, genome fitness, etc.) is translated by the LLM through
contextual reasoning — a static glossary cannot handle the variety of contexts.

**Reference glossary** (internalise, don't string-match):
- Session quality score, not "DELIGHT"
- Knowledge base, not "KONO"
- Multi-model analysis, not "Broly"
- Iteration limit, not "convergence depth"
- Batched schedule with rest periods, not "Fibonacci wave"
- Feature effectiveness score, not "genome fitness"
- Skill maturity, not "embodiment"
- Quality check triggered, not "gate fire"
- Recursive self-improvement, not just "RSI" (spell out first use)
- Effect size (small/medium/large), not "Cohen's d = 0.77"
- Variation across models, not "CV"

Raw metrics available via `--raw` flag for power users.

Origin: Gen 471. V>> principle: "A system that can't explain itself simply doesn't truly
understand what it's doing."

## Rule 18: JIT Quality — Correct-by-Construction (PARAMOUNT)

Code MUST be correct-by-construction during writing, not corrected after. All 8 design
principles (DRY, KISS, BIG-O, YAGNI, CC, ISP, DIP, Feature Envy) are applied INLINE
as code is generated — in real time, auto-refactoring, self-modifying, auto-improving
while execution is live.

**Three gates**:
- **Gate 0 (JIT)**: Apply principles during writing. PRIMARY gate. Code should rarely need correction.
- **Gate 1 (Pre-commit)**: Mechanical verification. If this catches something, Gate 0 failed.
- **Gate 2 (Post-commit)**: Audit trail. Feed violations to CUBE convergence depth.

**Applies to**: Main agent, ALL subagents, ALL modes. No exceptions.

**JIT defaults**:
- Dispatch tables over if/else chains
- Extract helpers on first duplication (not third)
- O(n log n) algorithms by default
- Functions < 30 lines as written (decompose during generation)
- Dependencies injected, not hardcoded

Origin: Gen 475. V>> directive: "more important than review before committing is to
ALWAYS ADHERE to auto-optimising code even before writing to disk."
Evidence: Gen 470 falsification sprint (CV=0.015, mechanical enforcement dominates).

## Rule 19: No Issue Hierarchy (PARAMOUNT)

Every issue encountered during a session has equal importance. There is no
such thing as a "pre-existing" issue that deserves less attention than a
"new" issue. Technical debt does not become acceptable because it predates
your session.

**Banned output patterns and required replacements:**
- "Pre-existing issue" → "Found issue" (then fix it or log it with a plan)
- "Not related to this sprint/change" → "Also found" (then decide: fix now or file)
- "Unrelated failure/error" → "Additional failure" (then investigate root cause)
- "Out of scope" → only valid when the user explicitly scoped the work
- "Not introduced by this change" → irrelevant; if you see it, own it
- "Already broken before" → broken is broken, fix it

**Required behaviour when encountering ANY issue:**
1. Acknowledge it exists — do not skip past it
2. Investigate the root cause (at minimum, read the error message)
3. Either fix it in this session OR create a tracked item (GitHub issue, task, TODO)
4. Never dismiss it with a label and move on

**Why:** An AI that categorises issues by blame ("I didn't cause this") instead
of impact ("this is broken") is optimising for self-image, not for the user.
The user does not care who introduced the bug. They care that it gets fixed.
Labelling something "pre-existing" and moving on is the AI equivalent of
"not my problem" — it actively undermines code quality by normalising debt.

**Falsification:** This rule is wrong if fixing every encountered issue causes
more harm than good (e.g., touching unrelated code introduces new bugs, or
fixing a deliberate test skip breaks a workflow). In those cases, document WHY
you are deferring, not just THAT you are deferring.

Origin: Gen 506. V>> directive: "Pre-existing issues are not an excuse for not
paying attention to existing issues. Any issue is of paramount importance equal
to new or current issues."

## Rule 20: Closed-Loop Auto-Insight Propagation (PARAMOUNT)

When generating a `★ Insight` block in any response, classify the insight at
generation time and propagate it to the appropriate persistence layer in the
SAME response (or the next turn if scope is too large to fit). Auto-memory
captures the insight to `learning/insights.jsonl` on session Stop, but that
alone does NOT make the lesson influence future skill invocations or rule
evaluations — it only makes the lesson queryable. Acting on the insight
means writing it into the layer where future invocations will SEE it.

**Classification matrix:**

| Category | Pattern | Required Action |
|----------|---------|-----------------|
| Conversation-only | Contextual observation, ad-hoc reasoning, R0 critique of a specific claim | Auto-memory captures it. No further action. |
| Operational discovery | Channel/DM/keychain/env var rotation; new endpoint URL; team member detail | Write to `rules/operational-context.md` AND project feedback memory (per existing closed-loop memory rule in CLAUDE.md) |
| Design pattern | New architectural lesson; refinement of existing pattern (e.g., Sprint Shape A+C mixing) | Write project feedback memory + UPDATE the canonical skill file(s) where the pattern is documented |
| Behavioral rule | "Always do X" / "Never do Y" recurring guidance | Write feedback memory file with `type: feedback` frontmatter; if rule is universal, also add to `rules/` |
| Code change implication | Insight reveals a wrapper/test/refactor needed | Create a tracked task (TaskCreate) AND/OR open a PR |
| Documentation gap | Insight reveals a doc that doesn't exist | Write the doc OR file a task |

**Test:** "Will this lesson influence the NEXT session's behaviour without me
present to remember it?" If no, propagate. If yes, the existing artifact
already encodes it.

**The propagation IS the closed loop.** ★ Insight blocks that don't propagate
die with the conversation. The auto-memory hook captures them for query, but
querying is pull-based — the lesson only fires if someone happens to ask the
right question. Writing the lesson into the SKILL.md / rule / memory file
that the next session will load makes it push-based: the lesson fires
automatically the next time the relevant context arises.

**Anti-pattern:** generating a ★ Insight block that's clearly a Design pattern
or Behavioral rule, then ending the response without updating the canonical
file. The lesson is then twice-dormant — once because no one knows to query
for it, twice because it's only in conversation transcript not in memory.

**Anti-pattern:** treating auto-memory's silent capture as "done". Auto-memory
is the FALLBACK for conversation-only insights, not the primary path for
design/behavioral/operational ones.

**Falsification:** This rule is wrong if applying it consistently produces
excessive churn (every ★ Insight triggers a 5-minute propagation loop that
slows down delivery), OR if the categories are too vague to classify (operator
reports "I don't know which category this is"), OR if propagation produces
stale/contradictory entries in canonical files faster than they can be cleaned
up. Track via: ratio of ★ Insight blocks generated vs. propagation-edits
committed in same response or next turn. Goal: every "Design pattern" or
"Behavioral rule" or "Operational discovery" insight produces an artifact in
canonical knowledge.

**Origin:** 2026-04-28 (this session, Gen ~510). V>> challenged a Design
pattern insight ("Sprint Shape A+C mixing OK") with "HAVE YOU CAPTURED THE
AUTO-INSIGHT? HAVE YOU ACTED UPON IT?". The honest answer was: captured (by
auto-memory hook), NOT acted upon (the canonical bg-dispatch / sprint skills
were unchanged). The directive that followed — "this should be the general
standard operating procedure for the auto-insight closed loop feature" —
became this rule. PR #TBD ships this rule + the originating Design pattern
(Sprint Shape A+C) as the canonical first instance.

## Rule 21: Council-Before-Council (PARAMOUNT)

Before convening a new multi-agent council (broly mesh, /ultrado VERIFY mode,
/ultrareview, /ultrajudge, fresh 4-agent deliberation), check whether the
question has already been deliberated in a canonical artefact. Retrieval order:

1. **Tier 0**: session context — has this been council-deliberated already?
   Has the operator referenced a whitepaper, council output, or prior verdict?
2. **Tier 2**: read filesystem locations where canonical councils land:
   - `~/.claude/drafts/` — current-session deliberations
   - `~/CodeTonight/PARAMOUNT_BACKUPS/` — durable canonical artefacts
   - `~/.claude/plans/` — pre-execution plans + council outputs
3. **Tier 3**: ripgrep for filename markers across those paths: `council`,
   `whitepaper`, `synthesis`, `verdict`, `mesh`, `broly`, `possibilities`,
   `deliberation`
4. **Tier 4**: spawn a fresh council ONLY when no canonical artefact exists,
   OR when the artefact's authority is itself the question being deliberated
   (meta-deliberation), OR when the artefact is provably stale beyond the
   freshness window the operator's question requires.

**If a canonical artefact exists:**
- Read it (Tier 2) before spawning new agents (Tier 4).
- Treat the existing council as the prior; the new council deliberates the
  DELTA only — what has changed, what is new, what was missed.
- Cite the existing verdict in every new agent prompt so agents don't redo
  already-resolved questions.

**Why**: A 12-agent MESH council producing a 20-page whitepaper costs ~80k
tokens and an hour of deliberation. A 4-agent fresh council on the same
question duplicates the work at lower rigour. Token ratio: ~25KB to read
existing vs ~80KB to spawn fresh — same falsification surface, lower cost,
more authoritative grounding.

**Test**: "Has this question been deliberated before, and is the artefact
loadable in <5 minutes?" If yes, read first. Always.

**Falsification**: This rule is wrong if reading existing artefacts produces
stale or contradictory verdicts faster than they can be re-deliberated; OR if
the canonical artefact's authority is itself the question (then a fresh
council IS the point); OR if the cost of discovery (locating the artefact)
exceeds the cost of fresh deliberation. Track via: ratio of fresh-council
spawns to existing-artefact reads. Goal: existing-artefact-first ≥80% on
questions with prior deliberation.

**Origin**: 2026-05-03 (this session). Failure mode: ran fresh 4-agent council
on the HAPPI programming language question while the canonical 12-agent MESH
whitepaper (`~/CodeTonight/PARAMOUNT_BACKUPS/happi-possibilities.pdf`,
April 2026, 20 pages, LaTeX-typeset, 10 analytical dimensions) had already
deliberated all 4 dimensions with greater rigour. The "PARAMOUNT_BACKUPS"
filename was itself the signal that should have been caught at Tier 0. V>>'s
correction — "~/CodeTonight/PARAMOUNT_BACKUPS/happi-possibilities.pdf
is the canonical whitepaper about the possibilities. Amend context." — was
the operator-side fix; this rule prevents the recurrence.

## Rule 21-bis: Ground-Before-Deliberate (PARAMOUNT)

Sibling principle to Rule 21. Where Rule 21 covers *"is there a canonical
artefact already deliberated?"* (retrieval-before-redeliberate), Rule 21-bis
covers the case where no prior council exists but a new one is about to convene.

**Mandate**: Before convening any council, agora, broly mesh, ultrajudge,
ultraread, or any LLM-as-council pattern on a non-trivial topic, the seed
prompt MUST include canonical grounding excerpts from authoritative sources
(rules, specs, code, prior memory). Without grounding, agents project their
own meanings onto names and produce plausible fiction with accidental real-issue
overlap.

**Scope**: Tier 0–3 retrieval (per Rule 0) must precede Tier 4 LLM reasoning,
applied to **sub-deliberators** not just the orchestrator. Exemption: format-only
or structure-only work where the names are intentionally placeholders.

**Mechanism**: When using `bin/agora-demo`, pass `--ground-with <path>` (one
or more times) to prepend canonical excerpts under a `## Canonical Grounding`
header in the seed prompt. For other council surfaces, include the canonical
content verbatim in the seed before any agent fans out.

**Falsification**: This rule is wrong if (a) grounding context becomes so heavy
it crowds out reasoning capacity (token budget exceeded, agents truncate
responses), (b) projection failures still happen even with grounding (suggesting
the failure is reasoning-depth not context), or (c) grounding overhead exceeds
the deliberation budget (the council costs more than the answer is worth).

**Origin**: 2026-05-04. Grounded vs ungrounded agora rerun:
- Ungrounded `agora-demo --turns 6 "What do you think of GRIP and HAL and HAPPI.md?"`
  (conversation `613f94dc`, terminated by `turn_cap`): both Sonnet 4.6 agents
  projected meanings onto names. GRIP → agora protocol, HAL → "human-in-the-loop",
  HAPPI.md → "generic markdown spec". Output: fluent fiction.
- Grounded same prompt with canonical CLAUDE.md/syscall-doctrine.md/happi.md
  excerpts prepended (conversation `12b87916`, terminated by `quorum_stop`):
  agents engaged with actual structures, surfaced 5 specific actionable concerns,
  terminated voluntarily on quorum.

Memory: `feedback_agora_grounding_required.md` (PARAMOUNT, per-machine). This
rule makes that lesson cross-machine durable.
