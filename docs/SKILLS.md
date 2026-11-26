# GRIP Skills

> Curated category map (which skill belongs to which group), **not** the canonical
> count. Hand-maintained and appended by `lib/learning.sh`; consumed by
> `scripts/generate-skills-index.py` to group `skills/INDEX.md`. Canonical skill
> total = `lib/asset_counter.py` `count_inventory()`, surfaced by `skills/INDEX.md`.

## session

- `authenticating-with-claude` — unified auth + identity wizard, @asking-users compliant
- `onboarding-users` — v2.2: bespoke config, redirects to /activate-grip
- `context-refresh` — mental model, 7-step
- `session-resume` — GRIP+--resume bridge
- `check-last-plan` — plan persistence
- `preplan` — intent injection
- `session-state-persistence` — auto state update
- `chat-history-search` — epoch timestamp search
- `project-grip-init` — new project GRIP setup + identity check

## dev-workflow

- `pr-automation` — gh CLI complete PR
- `gitops` — trunk-based, releases
- `branch-cleanup` — safe git prune
- `github-actions-setup` — CI/CD templates
- `github-secrets-setup` — secure upload
- `feature-complete` — feature-dev + design principles

## code-quality

- `design-principles` — SOLID/GRASP/DRY/KISS/YAGNI
- `markdown-expert` — MD040/022/031/032 fixes
- `batch-edit-enforcer` — MultiEdit enforcement
- `code-agentic` — verification gates

## testing

- `e2e-test-generation` — Playwright+Vitest+MSW

## ui-ux

- `mobile-responsive-ui` — mobile-first, dvh
- `figma-to-code` — 1:1 visual parity
- `image-optim` — macOS ImageOptim
- `wcag-image-accessibility` — WCAG AAA
- `ui-complete` — frontend-design + mobile-responsive unified

## docs

- `auto-update-documentation` — session→docs
- `medium-article-writer` — technical articles + anti-fluff rules
- `professional-pdf` — bespoke PDF branding via AskUserQuestion wizard
- `authentic-writing` — opt-in anti-AI-fluff for external content (secret, via onboarding)
- `grip-post` — public post in GRIP's voice: sourced, gated by the grip-post CLI, rendered, backed up, never posted
- `generating-changelogs` — daily git-to-changelog via broly FORK

## legal

- `legal-ops.formal` — attorney-level
- `legal-ops.simple` — plain language

## api

- `api-reverse-engineering` — DevTools→client

## improvement

- `self-improvement-engine` — PARAMOUNT: 10-step RSI cycle
- `recursive-learning` — PARAMOUNT: dialectical meta-learning
- `autonomous-learning` — PARAMOUNT: pattern detection, skill proposals
- `learning-new-skills` — PARAMOUNT meta-skill: auto skill creation from patterns
- `improvement-tracking` — PARAMOUNT: heatmap signals, auto-log
- `design-smell-response` — PARAMOUNT: response to [IMPROVEMENT HEATMAP]
- `meta-improvement-switch` — infra enhancement
- `agent-auto-creator` — agent templates
- `dogfood-domain` — land a newly announced model; never let it become a default

## efficiency

- `dependency-guardian` — ¬Read(deps), saves 50k+
- `concise-communication` — ¬preamble
- `bash-tool-enforcer` — rg>grep, fd>find
- `bash-command-safety` — ¬semicolon.after.subshell

## utilities

- `node-cleanup` — clean node_modules
- `flow-mode` — trust-based bypass permissions (journey-earned)

## system-ops

- `optimizing-system-resources` — browser tabs, IDEs, memory, processes - macOS-first
- `backing-up-grip-infrastructure` — tiered backups (quick/full/complete), auto-backup before bounce

## macos

- `launchd-automation` — self-reload agents

## paramount

- `asking-users` — PARAMOUNT: AskUserQuestion as core GRIP feature, 6 gates (confidence/uncertainty/assumption/destructive/multi-step/UI-UX)
- `skill-creation-best-practices` — PARAMOUNT gate for ALL skill creation, Anthropic best practices
- `creating-skills` — PARAMOUNT: guided skill creation wizard with quality gates (Gen 227)
- `creating-wizards` — meta-skill for AskUserQuestion wizard flows, anti-patterns, branching
- `creating-form-input` — the newer form schema: typed choice/text/number questions, and the house rule that every option keeps a description
- `hitl` — dispatch contract routing every interactive need to one primitive, plus the universal quality gates
- `hitl-decision-matrix` — batches N pre-computed decisions into one operator lock-in conversation
- `mcp-elicitation` — `elicitation/create`, the primitive that blocks one tool call rather than the whole turn
- `context-gate` — PARAMOUNT: 80% context monitoring, HALT + AskUserQuestion, never auto-serialize
- `sanitizing-public-releases` — PARAMOUNT: security gate for public releases, detects sensitive data (Gen 228)
- `lineage-awareness` — PARAMOUNT: self-knowledge of 6 founding insights, always-loaded (Gen 229)
- `improvement-tracking` — PARAMOUNT: RSI feedback loop, heatmap signals, auto-log improvements (Gen 253)
- `design-smell-response` — PARAMOUNT: response to [IMPROVEMENT HEATMAP], SRP review protocol (Gen 253)
- `self-improvement-engine` — PARAMOUNT: 10-step RSI cycle, pattern→skill→command→docs (Gen 253)
- `recursive-learning` — PARAMOUNT: effectiveness tracking, meta-learning, Layer 4 recursion (Gen 253)
- `autonomous-learning` — PARAMOUNT: dialectical reasoning, pattern detection, teaching moments (Gen 253)

## meta-mode

- `ultrathink` — v3.0.0 - 6 gates, @asking-users integration, expanded triggers
- `mode` — switch GRIP operating modes (code/strategy/legal/etc), loads domain-specific skills

## knowledge-work

- `reasoning-with-ai` — PARAMOUNT: articulate-first pattern, confidence display, emergent UI, human+AI thought partnership
- `idea` — one operator idea as a signed causal chain: settle its meaning, claim, test, record what came of it

## renewal

- `bouncing-instances` — Big Bounce pattern, cyclical reset preserving patterns

## leadership

- `leading-organisation` — founder operating system, decisions, delegation, technical authority
- `strat-ops` — strategy mode router - detects strategic context, routes to appropriate skill
- `pre-mortem-facilitation` — identify failure modes BEFORE they happen (Gary Klein technique)
- `decision-locking` — finalise and document strategic decisions to prevent drift

## contribution

- `reviewing-contributions` — self-aware PR review, GRIP evaluates changes to itself
