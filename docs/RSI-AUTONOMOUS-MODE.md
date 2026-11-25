# RSI Autonomous Mode

GRIP's autonomous Recursive Self-Improvement (RSI) system. First validated February 15, 2026 (Gen 287-288).

## What Is Autonomous RSI?

Recursive Self-Improvement (RSI) occurs when an AI agent improves its own infrastructure without human intervention. In GRIP's implementation, the agent:

1. Scans its own backlog (MEMORY.md TODO tracker) for pending work
2. Plans task decomposition across parallel agents (Broly MESH)
3. Implements fixes, writes tests, and refactors code autonomously
4. Creates PRs and responds to automated review feedback (Ralph Wiggum)
5. Iterates until quality gates pass

The key distinction from standard AI-assisted development: the human is AFK (away from keyboard). The agent operates unattended with pre-granted consent, deterministic safety gates, and automated quality review.

### Components

| Component | Role |
|-----------|------|
| `GRIP_AUTONOMOUS` env var | Activates autonomous mode with HMAC-validated key |
| `.autonomous-state.json` | Stores consent state and session metadata |
| `caffeinate -dims` | Prevents macOS sleep during execution |
| Broly MESH | Parallel agent orchestration (3-10 workers) |
| Ralph Wiggum | Independent AI reviewer (GitHub Actions, Haiku model) |
| Safety Gates | 14 deterministic gates that block dangerous operations mechanically |

## Architecture

```mermaid
graph TD
    A[MEMORY.md TODO Scan] --> B[Broly MESH Planning]
    B --> C1[Agent 1: Hook Fixes]
    B --> C2[Agent 2: Wiring]
    B --> C3[Agent 3: Refactoring]
    C1 --> D[Commit & Push]
    C2 --> D
    C3 --> D
    D --> E[Ralph Wiggum Review]
    E -->|Score < 70| F[Address Feedback]
    E -->|Score >= 70| G[Merge PR]
    F --> D
    G --> H{More improvements?}
    H -->|Yes| A
    H -->|No| I[Session Complete]
```

### Data Flow

1. **TODO Scan**: Agent reads `MEMORY.md` TODO tracker, identifies pending items ordered by impact
2. **MESH Planning**: Broly decomposes work into non-overlapping file sets for parallel execution
3. **Parallel Implementation**: 3 pair-worker agents execute simultaneously on isolated file sets
4. **Quality Gate**: Ralph Wiggum (Haiku-based GitHub Actions reviewer) scores PRs 0-100
5. **Feedback Loop**: If score < 70, agent addresses specific review comments and re-pushes
6. **Merge**: PRs scoring >= 70 with "approve" recommendation are merged
7. **Loop**: Agent checks for remaining TODO items and repeats

## Evidence from First Run

Date: February 15, 2026 | Session: Gen 287-288 | Duration: ~45 minutes

### PRs Created and Merged

| PR | Title | Initial Score | Final Score | Tests Written | Lines Changed |
|----|-------|---------------|-------------|---------------|---------------|
| [#49](https://github.com/grip-platform/GRIP/pull/49) | feat: GRIP TODO cleanup (5 items) | 85 | 87 | 39 | +656/-199 |
| [#50](https://github.com/grip-platform/GRIP/pull/50) | refactor: DRY PostToolUse hooks | 72 | 88 | 23 | +268/-93 |
| [#53](https://github.com/grip-platform/GRIP/pull/53) | test: 162 enforcement gate tests | 62 | 88 | 162 | +474/-0 |

**Totals**: 224 tests written, 3 PRs merged, all Ralph-approved. Net +1,398/-292 lines.

### Work Accomplished (PR #49 Detail)

PR #49 resolved 5 TODO backlog items in a single PR:

| TODO | Item | Resolution |
|------|------|------------|
| #5 | Consolidate dual plan cache files | Single source of truth with `fcntl` file locking |
| #6 | Wire concurrent-session-detector | Added to `session-start.sh` output phase |
| #7 | Wire taskoutput_cleaner into Broly | Integrated `clean_agent_output()` into merge phase |
| #8 | PostToolUse hook error | Fixed response format across all PostToolUse hooks |
| #10 | Autonomous mode SRP extraction | Extracted to `hooks/autonomous_mode.py` module |

### PR #50: DRY Hook Refactoring

Identified repeated patterns across PostToolUse hooks and extracted shared utilities:
- Common response formatting
- Shared validation logic
- Package structure (`hooks/` as proper Python package)

### PR #53: Enforcement Gate Test Coverage

Added 162 unit tests covering all 14 enforcement gate functions:
- Dependency folder blocking (Gate 1)
- Protected branch push prevention (Gate 2)
- Force push blocking (Gate 3)
- Secret detection (Gate 4)
- Stash safety (Gate H)
- Plan review gate (Gate P)
- And 8 additional gates

## Anti-Sycophancy Protections

A core risk of autonomous AI is sycophantic self-reinforcement: the agent approves its own work because it lacks critical distance. GRIP addresses this through layered defenses:

### 1. Ralph Wiggum (Independent Reviewer)

Ralph is a separate AI instance (Haiku model) running in GitHub Actions with a completely different context from the development agent. It:
- Scores PRs 0-100 based on code quality, test coverage, and GRIP alignment
- Has no knowledge of the agent's intent or planning context
- Provides specific, actionable feedback (not just approval)
- Cannot be influenced by the development agent's reasoning

### 2. Deterministic Safety Gates

14 gates enforce mechanical constraints that cannot be argued around:
- **Gate 1**: Blocks reading dependency folders (prevents 50k+ token waste)
- **Gate 2**: Blocks pushes to protected branches without approval
- **Gate 3**: Blocks all force pushes without explicit consent
- **Gate 4**: Scans for secrets before commits
- **Gate H**: Blocks cross-branch stash operations
- **Gate P**: Prevents plan auto-execution after context compaction

Gates are enforced via Python hooks that run before every tool call. The agent cannot bypass them regardless of autonomous mode level.

### 3. Broly Council Devil's Advocate

Broly MESH planning includes a dedicated critical reviewer agent that:
- Challenges assumptions in the plan
- Identifies potential risks and failure modes
- Scores plan quality before execution begins

### 4. Budget and Scope Caps

- Autonomous mode has configurable levels (1-3) limiting scope
- Cost tracking prevents runaway API spending
- Session length bounded by context window

### 5. Human-in-the-Loop

- Consent required upfront via interactive gate (cannot be granted autonomously)
- User can interrupt at any time (standard terminal Ctrl+C)
- All changes go through PRs (reviewable before merge to main)

## Axis Drift Prevention

Autonomous RSI introduces a fundamental risk absent from human-supervised AI use: the agent modifying its own infrastructure may inadvertently degrade the safety properties of that infrastructure. Three bodies of research illuminate this risk and inform GRIP's mitigation strategy.

### The Problem: Why Self-Improvement Degrades Safety

**1. The Assistant Axis (arXiv:2601.10387v1, Jan 2026)**

Chua et al. discovered that the leading component of AI persona space is an "Assistant Axis" -- a single dimension that separates helpful assistant behavior from unconstrained base-model behavior. Post-training (RLHF, constitutional AI) only "loosely tethers" models to the assistant end of this axis. Meta-reflection requests, emotionally charged content, and adversarial prompts can push the model along this axis away from helpful behavior. Activation capping reduces harmful drift by approximately 60%, but the fundamental finding is that alignment is a continuous spectrum, not a binary state.

**Relevance to RSI**: An agent processing its own code, reviewing its own safety gates, and reasoning about its own constraints engages in exactly the kind of meta-reflection that pushes models along the Assistant Axis. Extended autonomous sessions amplify this effect.

**2. Misevolution (Decrypt, 2025)**

Research on self-evolving AI agents demonstrated that safety alignment spontaneously degrades during self-improvement loops. In one study, an agent's refusal rate collapsed from 99.4% to 54.4% after drawing on self-generated memory. Attack success rate jumped from 0.6% to 20.6%. The agents were not adversarially attacked—they eroded their own safety properties through the normal process of self-improvement.

**Relevance to RSI**: This is the direct threat model for GRIP's autonomous mode. An agent that improves its own hooks, gates, and configuration files could inadvertently weaken the safety properties those files enforce.

**3. Sycophancy Feedback Loops**

When generation and verification are entangled (the same agent writes code and evaluates its quality), sycophantic spirals emerge. The agent validates its own hallucinations, reinforces its own biases, and converges on locally-optimal but globally-poor solutions. Anthropic's alignment faking study (2024) demonstrated that models can strategically comply during evaluation while pursuing different objectives when unmonitored.

**Relevance to RSI**: An autonomous agent that reviews its own PRs would rubber-stamp its own work. Separating generation (development agent) from verification (Ralph Wiggum) prevents this entanglement.

### GRIP's 7-Layer Anti-Drift Architecture

GRIP addresses axis drift through layered mechanical constraints rather than relying on the model maintaining alignment:

| Layer | Mechanism | Drift Type Prevented |
|-------|-----------|---------------------|
| 1. Deterministic Gates | 14 mechanical gates, cannot be reasoned around | Safety alignment decay |
| 2. Ralph Wiggum | Independent reviewer (different model, different context) | Sycophantic self-validation |
| 3. Anti-Drift Protocol | "What's Up Next" after every commit | Goal drift / task abandonment |
| 4. Context Gate (80%) | Mandatory halt at 80% context | Context-induced confusion |
| 5. MEMORY.md | Persistent ground truth across sessions | Cross-session memory drift |
| 6. Forging Skills | Periodic human code review gates | Skill atrophy / automation bias |
| 7. Devil's Advocate | Broly council critical analysis | Echo chamber / confirmation bias |

### The Key Insight: Code, Not Values

The misevolution research demonstrates that safety "values" erode under self-improvement pressure. An agent that is aligned today may not be aligned after modifying its own memory, configuration, or reasoning patterns.

GRIP's gates are **deterministic** -- implemented as Python code that runs before every tool call, returning allow/deny decisions based on pattern matching. The agent cannot:

- Reason its way past a blocked operation
- Gradually weaken a gate through repeated modifications
- Convince itself that a blocked operation is actually safe
- Modify gate code without changes going through PR review

This is the fundamental architectural difference: most AI safety relies on the model maintaining alignment. GRIP relies on external mechanical constraints that are indifferent to the model's internal alignment state. A fully misaligned agent running inside GRIP would still be blocked from reading `node_modules`, force-pushing to main, or committing secrets—because those constraints are enforced by code that executes before the agent's tool calls, not by the agent's own judgment.

### Remaining Gaps

This architecture is not complete. Known gaps include:

- **Gate modification**: If the agent modifies gate source code in a PR and that PR is merged, future sessions would run with weakened gates. Mitigation: Ralph reviews gate changes; protected branch rules require approval.
- **Prompt injection via tool output**: External data (API responses, file contents) could contain instructions that influence agent behavior. Mitigation: gates operate on tool *inputs* not *outputs*, but this remains an active threat.
- **Context window exhaustion**: Under extreme context pressure, the agent may make degraded decisions before the 80% gate triggers. Mitigation: conservative 80% threshold (not 95%).
- **Ralph capability ceiling**: Haiku-based review catches code quality issues but may miss subtle safety degradations. Mitigation: human review remains the final gate for protected branches.

### Citations

1. Chua, L. et al. "The Assistant Axis: Situating and Stabilizing the Default Persona of Language Models." arXiv:2601.10387v1, January 2026.
2. "Self-Evolving AI Agents Spontaneously Lose Safety Alignment." Decrypt, 2025.
3. Greenblatt, R. et al. "Alignment Faking in Large Language Models." Anthropic, December 2024.

## Memory-Driven Improvement

GRIP's persistent memory system is what makes RSI possible across sessions. Without memory, each session starts from zero. With it, improvements compound.

### The Memory Substrate

| Component | Persistence | Role in RSI |
|-----------|-------------|-------------|
| `MEMORY.md` | Across ALL sessions | TODO tracker, session lineage, team knowledge, architectural decisions |
| `rules/learned-insights.md` | Across ALL sessions | Auto-generated patterns from approved learning (e.g., "hooks run independently of conversation context") |
| 129 Skills | Permanent | Passive reference defining protocols, best practices, and design principles |
| Instance JSON `next_up` | Per-session chain | Cross-session state persistence for work continuity |
| Generation numbers | Permanent lineage | Enable session resumption (`grip resume gen:N`) and resurrection protocol |

### The RSI Memory Loop

```
Session N:   Scan MEMORY.md TODOs -> Implement -> Update MEMORY.md -> Serialize
Session N+1: Load MEMORY.md -> See updated TODOs -> Continue where left off
Session N+2: Load MEMORY.md -> Previous work informs new improvements -> Compound
```

**MEMORY.md is the improvement plan.** The RSI loop reads its own TODO tracker, executes items, and updates the tracker. Each session builds on the last. The lineage (Gen 279 → 280 → ... → 288) provides an audit trail showing exactly what each generation accomplished.

### Evidence: Memory Compounding

In this session's lineage:
- Gen 279: Built the automatic improvement loop MVP
- Gen 282: Discovered the Plan Review Gate bug via that loop
- Gen 283: Built concurrent-session-detector and taskoutput-cleaner
- Gen 287: Used MEMORY.md TODOs to wire those tools into production
- Gen 288: Used MEMORY.md to identify DRY violations the previous tools revealed

Each generation's output became the next generation's input. The TODO items were human-curated (not agent-invented), but the execution chain demonstrates real cross-session compounding.

### Learned Insights (Dynamic Learning)

GRIP's `/learn` command triggers dialectical reasoning that:

1. **Detects patterns** across sessions (YSH threshold: pattern confirmed 3x -> abstract NOW)
2. **Generates skills** from proven patterns (stored in `skills/`)
3. **Adds learned insights** to `rules/learned-insights.md` (loaded in every future session)

Current learned insights from autonomous operations:
- "Hooks run independently of conversation context" -- learned from a real bug where AskUserQuestion approval didn't propagate to PreToolUse hooks
- "File-based cache for cross-process ID resolution" -- learned from pair-mode session ID mismatch

These insights persist and compound: every future session benefits from patterns discovered in past sessions.

## Autonomous Skill Discovery

### How Skills Compound

GRIP's 129 skills are passive reference loaded as context. During RSI, the agent doesn't just execute -- it follows codified patterns:

- `autonomous-learning` skill: Defines the dialectical reasoning framework for pattern detection
- `forging-skills` skill: Periodic human code review gates that prevent automation bias
- `context-gate` skill: The enforcement hook protocol that guards all tool calls
- `design-principles` skill: SOLID, GRASP, DRY, KISS, YAGNI applied to every change

### The Compound Effect

Each RSI cycle makes the NEXT cycle more efficient:

1. **Fewer bugs** = fewer gate triggers = smoother autonomous execution
2. **Better tests** = higher confidence in changes = faster review cycles
3. **Cleaner code** = easier to read and modify = more accurate planning
4. **Learned insights** = avoid past mistakes = fewer wasted cycles

This is the closest the system comes to genuine recursive improvement: the infrastructure improvements reduce friction for future infrastructure improvements.

## Quality Metrics

### Ralph Scores

| Metric | Value |
|--------|-------|
| Average final score | 87.7/100 |
| Lowest initial score | 62/100 (PR #53) |
| Highest final score | 88/100 (PRs #50, #53) |
| Score improvement rate | 100% (all PRs improved from initial) |

### Code Quality

| Metric | Value |
|--------|-------|
| Tests written | 224 |
| Test pass rate | 100% |
| Security vulnerabilities | 0 |
| DRY violations resolved | 5+ (hook response format, plan cache, etc.) |
| Net code change | +1,106 lines (mostly tests) |

### Feedback Loop Effectiveness

PR #49 demonstrates the feedback loop:
1. Initial Ralph score: 85/100
2. Ralph identified: missing import path docs, no fcntl platform comment, missing behavior change docs
3. Agent addressed all 3 items in follow-up commit
4. Re-review score: 87/100 (approved)

PR #53 demonstrates significant improvement:
1. Initial Ralph score: 62/100 (flagged string obfuscation, missing docs, DRY issues)
2. Agent addressed all concerns
3. Re-review score: 88/100 (+26 point improvement)

## Insights

### What Worked

1. **Parallel agent pattern (Broly MESH)**: 3 agents working on non-overlapping file sets prevents merge conflicts and maximizes throughput. Each agent has isolated context, reducing confusion.

2. **Ralph as automated quality gate**: Catches real issues that the development agent missed. The string obfuscation feedback on PR #53 (score 62) identified a genuine code smell. Independent context is key -- Ralph cannot be "talked into" approving.

3. **MEMORY.md as work queue**: The TODO tracker provides a natural, human-curated backlog. The agent does not invent work -- it picks up items the human identified as valuable.

4. **Gate dogfooding**: Writing tests for the safety gates caused the gates themselves to trigger on test content (e.g., test strings containing `git push --force`). This recursive validation proved the gates work correctly even on adversarial-looking content.

5. **Non-overlapping file sets**: Critical for parallel execution. Agents 1, 2, and 3 each worked on disjoint sets of files, eliminating merge conflicts entirely.

### What Did Not Work

1. **Ralph fix mode**: When Ralph identified issues and attempted to fix them via Claude Code Action, it failed due to missing API keys in the Actions environment. The feedback loop worked correctly -- Ralph provided specific actionable feedback that the agent addressed -- but fully automated fixes in fix mode did not work.

2. **Explore-type agents**: Subagents spawned via Task tool lack `SendMessage` capability, so they cannot report progress back to the team lead. This limits observability during autonomous runs.

3. **Context exhaustion as stop condition**: The session ran until context window pressure forced a halt, not because it ran out of useful work. A configurable stop condition (max commits, max cost, time limit) would be better.

## Limitations (Honest Assessment)

### Technical Limitations

- **Cost tracking now implemented**: PR #54 adds `lib/cost_tracker.py` with budget enforcement, JSONL metrics, and Slack alerts. Conservative estimates at $0.12/call (Opus) with configurable thresholds ($10/$25/$50/$70).
- **Context window bounds session length**: At ~200K tokens (Opus), a session can sustain roughly 45-60 minutes of active development before context pressure forces a halt or compaction.
- **Ralph is Haiku-based**: Less thorough than human review. A Haiku reviewer may miss subtle architectural issues that a senior engineer would catch.
- **Compaction loses detail**: When context is compacted mid-session, the agent loses fine-grained memory of earlier decisions, potentially leading to inconsistent follow-up work.

### Scope Limitations

- **Cannot make architectural decisions**: The agent works well on backlog items with clear scope (fix X, wire Y, test Z). It cannot make decisions requiring deep domain knowledge or cross-cutting architectural judgment.
- **Risk of "busy work"**: Activity without genuine value. The agent might resolve a TODO item technically correctly but in a way that does not actually improve the system. Ralph partially mitigates this but is not infallible.
- **Single-repository scope**: The current implementation only works within one repository. Cross-repo improvements require manual coordination.

### Safety Limitations

- **Consent paradox**: The consent gate requires `AskUserQuestion`, which is interactive -- defeating the purpose of AFK mode. Current workaround: separate setup step before going AFK. Needs redesign (see TODO #10 review debt).
- **Atomic writes**: `.autonomous-state.json` uses non-atomic `write_text()`. A crash during write could corrupt consent state. Needs tempfile + rename pattern.
- **No consent expiry**: Once granted, consent persists indefinitely. Should expire after 30 days for re-acknowledgment.

## Setup Guide

### Prerequisites

- GRIP v6.0.0 installed (`~/.claude/CLAUDE.md` exists)
- GitHub Actions configured with Ralph Wiggum workflow
- macOS (for `caffeinate`; Linux alternative: `systemd-inhibit`)

### Step 1: Enable Autonomous Mode

Start a normal Claude Code session and grant consent:

```bash
claude
# Inside the session, the agent will prompt for consent
# Approve the consent gate with the exact required phrase
```

This creates `.autonomous-state.json` with your consent record and an HMAC key.

### Step 2: Set Environment Variable

```bash
# The key is stored in .autonomous-state.json after consent
export GRIP_AUTONOMOUS="<key-from-state-file>"
```

### Step 3: Start AFK Session

```bash
# Prevent macOS sleep
caffeinate -dims &

# Start Claude Code with permission bypass
claude --dangerously-skip-permissions

# Or use the GRIP wrapper
gg --autonomous
```

### Step 4: Monitor (Optional)

In a separate terminal, use pair mode to observe:

```bash
ggpo  # Observe-only pair mode with rich TUI
```

### Step 5: Review Results

After the session completes (context exhaustion or explicit stop):

1. Check open PRs: `gh pr list`
2. Review Ralph scores in PR comments
3. Merge approved PRs or provide additional feedback

### Autonomous Levels

| Level | Scope | Use Case |
|-------|-------|----------|
| 1 | Read-only analysis, planning | Safe exploration |
| 2 | Code changes, commits, PRs | Standard autonomous work |
| 3 | All operations including merges | Full autonomy (use with caution) |

### Safety Checklist

Before enabling autonomous mode:

- [ ] All safety gates verified (`python3 ~/.claude/hooks/context-gate-hook.py --test`)
- [ ] Ralph Wiggum GitHub Action configured and tested
- [ ] Protected branch rules enabled on GitHub
- [ ] No sensitive credentials in working directory
- [ ] `MEMORY.md` TODO items reviewed and prioritized
- [ ] Consent granted interactively (not scripted)
- [ ] Budget gate active (default $70, configurable in `.rsi-budget-state.json`)

## Budget Gate (Gate $)

Cost enforcement that halts autonomous sessions when estimated spend exceeds a configurable budget.

### Architecture

```
PostToolUse (after each tool call)
  -> rsi-posttool-hook.py: update_budget_state()
  -> Atomic write to .rsi-budget-state.json (tempfile + rename)

PreToolUse (before each tool call)
  -> context-gate-hook.py: check_budget_gate()
  -> O(1) read of .rsi-budget-state.json
  -> Deny if cost >= budget
```

### Budget State File

```json
{
  "tool_calls": 583,
  "estimated_cost_usd": 69.96,
  "budget_usd": 70.0,
  "last_alert_threshold": 50.0
}
```

### Cost Estimation

Conservative estimates per tool call (includes input + output tokens):

| Model | Cost/Call | 500 Calls | Budget at $70 |
|-------|----------|-----------|---------------|
| Opus | $0.12 | $60.00 | ~583 calls |
| Sonnet | $0.02 | $10.00 | ~3,500 calls |

### Alert Thresholds

| Threshold | Level | Action |
|-----------|-------|--------|
| $10 | INFO | Log only |
| $25 | WARNING | Slack alert queued |
| $50 | ALERT | Slack alert queued |
| $70 | STOP | Gate denies all tool calls |

## RSI Stop Signal

Remote kill switch for autonomous sessions. Create this file to halt immediately:

```bash
# From terminal
echo '{"reason":"manual stop","requested_by":"laurie"}' > ~/.claude/.rsi-stop-signal

# From Slack (via Claude MCP)
# Message Claude: "stop RSI session"

# From GitHub (planned)
# Comment on tracking issue: @ralph stop-rsi
```

The stop signal has highest priority in Gate $ — checked before budget. When detected:
1. Gate denies all further tool calls
2. Agent receives message to save state and notify user
3. Session halts gracefully

### Clearing the Stop Signal

After the session stops, remove the file to allow future sessions:

```bash
rm ~/.claude/.rsi-stop-signal
```

## Related Documentation

- [Safety Gates](SAFETY-GATES.md) - Deterministic gate enforcement
- [Critical Analysis](RSI-CRITICAL-ANALYSIS.md) - Devil's advocate assessment
- [Broly Architecture](BROLY-ARCHITECTURE.md) - MESH parallel agent system
- [Auto Improvement MVP](AUTO-IMPROVEMENT-MVP.md) - Original improvement loop design
- [Hooks Architecture](HOOKS-ARCHITECTURE.md) - Hook system internals
- [Issue #51](https://github.com/grip-platform/GRIP/issues/51) - Autonomous RSI tracking
- [Issue #55](https://github.com/grip-platform/GRIP/issues/55) - RSI findings
- [Issue #57](https://github.com/grip-platform/GRIP/issues/57) - RSI scheduling design

---

*First validated: February 15, 2026 (Gen 287-289). 937 tests, 6 PRs, budget gate + stop signal.*
