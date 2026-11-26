# System Capabilities

Skills: 340 | Agents: 45 | Commands: 473 | Rules: 91 | Hooks: 142 | Modes: 31 <!-- grip-stats -->

## Key Commands
`/refresh-context`, `/create-pr`, `/remind-yourself`, `/audit-efficiency`, `/markdown-lint`, `/preplan`, `/extend-context`, `/learn`, `/broly`, `/converge`, `/rsi`

## Plan tier (context-window envelope)
`grip tier <pro|max|api|auto|status>` — sets GRIP's context budget per Claude plan. Pro is 200K (1M is credit-gated on every subscription plan); `pro` makes GRIP compact early + survive inside 200K. Captured during `/activate-grip` onboarding. Source trace: `docs/grip-tier-1m-context.md`.

## Agent Categories
- **Auto-trigger**: Context Refresh (session start), Dependency Guardian (file reads), File Read Optimizer
- **Common tasks**: PR Workflow, History Mining, Doc Updater
- **Quality gates**: Efficiency Auditor, YAGNI Enforcer, Markdown Expert
- **Maintenance (Gen 487)**: Worktree Reaper, Genome Monitor, KONO Reaper, Learning Janitor, Checkpoint Archiver, Lesson Carousel, Skill Graph Validator

## Design Principles
YAGNI, KISS, DRY, SOLID, GRASP, YSH, BIG-O, Efficiency
