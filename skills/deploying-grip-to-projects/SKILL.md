---
name: deploying-grip-to-projects
description: >
  Automated project onboarding that explores a GitHub repo, generates 7 deliverables
  (CLAUDE.md, GitHub issue, prompts, Slack, architecture docs, novel features, PR),
  and self-scores quality via LLM-as-judge.
  Use when onboarding any new project into the GRIP ecosystem.
status: Active
effort: medium
version: 1.0.0
created: 2026-03-06
generation: 459
triggers:
  - /grip-onboard
  - /deploy-grip
  - "onboard project"
  - "deploy GRIP to"
integrates:
  - project-grip-init
  - converge
  - delegating-team-work
  - asking-users
  - creating-wizards
  - hitl
  - ultrathink
  - autonomous-learning
  - styling-slack-blocks
see_also:
  - preplan-driven-development
  - sprint-loop
  - shipit
origin:
  team_member: "V>>"
  projects: ["redr-property-search", "twr", "google-vibe-os", "SchipOTP"]
  evidence: "4 successful deployments, codified from empirical pattern"
---

# Deploying GRIP to Projects

Automated 5-phase project onboarding pipeline that investigates a repo, generates
comprehensive deliverables, scores them, delivers via GitHub + Slack, and evolves
the genome.

## When to Use

- Onboarding any new project into the GRIP ecosystem
- Re-onboarding a project after major architecture changes
- Auditing GRIP coverage across an organisation's repos

## Command

```
/grip-onboard <repo-url> --lead K>> [--slack C0EXAMPLE01] [--depth 3] [--dry-run]
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--lead` | Required | Team member signature (K>>, A>>) |
| `--slack` | C0EXAMPLE01 | Slack channel ID for notifications |
| `--depth` | 2 | Converge depth for inner loops |
| `--dry-run` | false | Generate without pushing/posting |
| `--admin` | false | Auto-merge PR with `--admin` bypass |

## 5-Phase Pipeline

### Phase 1: EXPLORE

Shallow-clone the repo and build a `RepoAnalysis` model:

| Step | Action | Output |
|------|--------|--------|
| 1.1 | Clone `--depth 1` | Local copy |
| 1.2 | Detect stack | `StackProfile` (languages, frameworks, build tools, package managers) |
| 1.3 | Count LOC | Per-language line counts |
| 1.4 | Map architecture | Component map, entry points, API surface |
| 1.5 | Identify quality gaps | Missing tests, CI, types, logging, security issues |
| 1.6 | Check existing GRIP | Existing `.claude/CLAUDE.md`, open PRs/issues |
| 1.7 | Map IPC/API surface | Handlers, endpoints, routes |

Key function: `explore_repo(repo_path) -> RepoAnalysis`

### Phase 2: GENERATE

Template-driven generation of 7 deliverables:

| # | Deliverable | File | Content |
|---|------------|------|---------|
| D1 | CLAUDE.md | `.claude/CLAUDE.md` | Project context, architecture, quality roadmap, ultrathink++ |
| D2 | GitHub Issue | (API) | Master execution plan with Mermaid diagrams, Fibonacci sprints |
| D3 | GRIP Prompts | `docs/GRIP-PROMPTS.md` | Copy-paste converge prompts for the lead |
| D4 | Slack Message | (API) | Styled Block Kit with architecture overview |
| D5 | Architecture Analysis | `docs/ARCHITECTURE-ANALYSIS.md` | Strengths, gaps, competitor analysis |
| D6 | Novel Features | `docs/NOVEL-FEATURES.md` | Feature proposals with effort/impact |
| D7 | PR | (API) | Branch + commit + push + PR creation |
| D8 | Self-Healing CI (opt-in) | `.github/workflows/self-healing-nightly.yml` + `.claude/self-healing-ci/` | See D8 section below |

### D8: Self-Healing CI (Opt-in)

If the repo has a test suite, ask the lead — no default:

```
AskUserQuestion:
  question: "Install self-healing CI into this repo?"
  options:
    - "Enable" — nightly regression-healing with MESH council review
    - "Skip" — do not install
    - "Defer" — file as a tracked issue for later
```

On "Enable", invoke `/self-healing-ci install` (interface `^1.0`) as a sub-step,
inheriting `--lead` and `--slack` arguments. The sub-step opens a second PR for
the CI install — kept separate from the main onboarding PR so bot-identity setup
and API key provisioning don't block delivery of D1–D7.

See `skills/self-healing-ci/SKILL.md` for the full protocol, cost ceilings, and
the non-gamable regression-anchor criterion.

Key constraint: All deliverables adapt to the lead's interaction mode:
- `concise` (K>>): Dense, no fluff
- `explanatory` (A>>): Thorough, educational
- `direct` (V>>): Action-first
- `confirm-first`: Include decision points
- `supportive`: Encouraging, detailed feedback

### Phase 3: SCORE

LLM-as-judge scoring with 4 dimensions and anti-attractor protection:

| Dimension | Weight | Criterion |
|-----------|--------|-----------|
| Accuracy | 30% | CLAUDE.md matches actual repo architecture |
| Completeness | 30% | All quality gaps captured, no missed handlers/endpoints |
| Actionability | 20% | Lead can copy-paste prompts and execute immediately |
| Delight | 20% | Team member would be impressed by quality |

Anti-attractor rule: require at least 10-point spread across dimensions.
If all scores land at 85-90, scoring is suspect — recalibrate.

Key function: `score_deliverables(analysis, deliverables) -> Scorecard`

### Phase 4: DELIVER

Push deliverables to GitHub and notify via Slack:

| Step | Action | Tool |
|------|--------|------|
| 4.1 | Create branch | `git checkout -b feat/grip-{project}-roadmap` |
| 4.2 | Commit files | `git commit` (no AI attribution) |
| 4.3 | Push branch | `git push origin` |
| 4.4 | Create PR | `gh pr create` |
| 4.5 | Create issue | `gh issue create` with Mermaid diagrams |
| 4.6 | Post Slack | Block Kit styled message to channel |
| 4.7 | Auto-merge | `gh pr merge --admin` if flag set |

### Phase 5: EVOLVE

Update GRIP genome with learnings:

| Step | Action |
|------|--------|
| 5.1 | Create/update gene for this skill |
| 5.2 | Record outcome in converge metrics |
| 5.3 | Update fitness scores based on scorecard |
| 5.4 | Export genome snapshot |

## Quality Gate

Before delivery, every CLAUDE.md must pass:
- [ ] Has `ultrathink++` directive at top
- [ ] Architecture section matches actual repo structure
- [ ] IPC/API reference covers all discovered handlers/endpoints
- [ ] Quality roadmap addresses all identified gaps
- [ ] GRIP commands cheat sheet present
- [ ] Cross-platform notes included
- [ ] Interaction mode matches lead's preference
- [ ] **Dual-Gate Coding Rule included** (see below)

## JIT Triple-Gate Coding Architecture (PARAMOUNT P0)

Every onboarded project MUST include the JIT triple-gate quality protocol in its
CLAUDE.md or project memory. This is the single strongest quality lever — mechanical
enforcement dominates model capability (Gen 470, CV=0.015).

### Gate 0: JIT (During Writing) — PRIMARY

Code is correct-by-construction. All 8 principles applied INLINE during generation:
- Dispatch tables over if/else chains by default
- Extract helpers on first duplication (not third)
- O(n log n) algorithms by default
- Functions < 30 lines as written (decompose during generation)
- Dependencies injected, not hardcoded

Applies to: main agent, ALL subagents, ALL modes. No exceptions.

### Gate 1: Pre-Commit Verification (8 Principles)

| Principle | Action | Check |
|-----------|--------|-------|
| DRY | DENY | No duplicated patterns at 3+ occurrences |
| KISS | DENY | Functions < 30 lines, no over-engineering |
| BIG-O | WARN | No O(n^2)+ without justification |
| YAGNI | DENY | Only implement what was requested |
| CC | DENY | No function exceeds CC 15 |
| ISP | WARN | No God Classes |
| DIP | WARN | No high-level depending on low-level details |
| Feature Envy | WARN | Methods operate on own data |

### Gate 2: Post-Review Refactor

Fix all Gate 1 violations, verify tests pass, check no new violations introduced.

### Key Evidence

- CV=0.015: Quality converges within 3% across models (8B to 70B) after enforcement
- Cohen's d=-0.77: 8 principles catch God Classes invisible to 5-principle sets
- Surface violations (DRY, YAGNI): 0% recurrence — single-pass correction works
- Structural violations (KISS, BIG-O): 50% recurrence — need extra scrutiny

Source: GRIP Gen 470 Pre-Registered Falsification Sprint. Paper: mechanical-quality-enforcement-v4.pdf

## Parallel Execution

Multiple repos can be onboarded simultaneously using worktree isolation:

```python
# Two repos in parallel
Task(prompt="onboard repo-a", subagent_type="direct-implementation")
Task(prompt="onboard repo-b", subagent_type="direct-implementation")
```

Each task runs in `/tmp/{project}-onboard/` — no branch contamination.

## Examples

### Onboard a New Project

```
/grip-onboard CodeTonight-SA/SchipOTP --lead K>> --slack C0EXAMPLE01
```

### Dry Run (No Push/Post)

```
/grip-onboard CodeTonight-SA/google-vibe-os --lead A>> --dry-run
```

### Parallel Onboarding

```
/grip-onboard CodeTonight-SA/google-vibe-os --lead A>> &
/grip-onboard CodeTonight-SA/SchipOTP --lead K>>
```

## Origin

Pattern empirically discovered across 4 projects:
1. REDR Property Search (Gen 430) — first full onboarding
2. TWR (Gen 435) — pattern confirmed, YSH threshold reached
3. Googol-Vibe (Gen 459) — parallel execution proven
4. SchipOTP (Gen 459) — concurrent with Googol-Vibe

Codified per YSH: pattern proven 3x, abstract NOW.

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~500 |
| EXPLORE phase | ~3000 |
| GENERATE phase | ~4000 |
| SCORE phase | ~500 |
| DELIVER phase | ~1000 |
| EVOLVE phase | ~500 |
| **Total** | **~9500** |
