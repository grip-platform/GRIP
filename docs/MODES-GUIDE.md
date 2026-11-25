# GRIP Modes Guide

25 operating modes for domain-specific context loading. Each mode stacks skills on top of the PARAMOUNT foundation layer.

## Quick Reference

| Mode | Budget | Broly | Purpose |
|------|--------|-------|---------|
| **code** | ~6k | FORK | Software development with design principles |
| **architect** | ~3.5k | PIPELINE | System design, ADRs, architecture decisions |
| **automation** | ~4.5k | FORK | Workflow orchestration with safety guardrails |
| **research** | ~3k | CLUSTER | Deep research, evidence synthesis |
| **synthesis** | ~3.5k | CLUSTER | Cross-domain knowledge integration |
| **analysis** | ~4k | CLUSTER | RIPER structured analysis phases |
| **writing** | ~3k | disabled | Long-form writing and editing |
| **learning** | ~3.5k | disabled | Personal learning pathway design |
| **strategy** | ~3.5k | MESH | Strategic assessment, gap analysis, red-teaming |
| **decision** | ~3k | disabled | Structured decision frameworks |
| **brainstorm** | ~2.5k | FORK | Ideation and divergent thinking |
| **business** | ~2.5k | FORK | Founder operations and team leadership |
| **review** | ~3.5k | FORK | Unified code/content/document review |
| **testing** | ~5.5k | CLUSTER | Full QA lifecycle with HITL gates |
| **security** | ~3.5k | CLUSTER | Vulnerability detection, threat modeling |
| **marketing** | ~2.5k | disabled | Authentic content creation (anti-AI slop) |
| **legal** | ~2.5k | FORK | Contract drafting and legal documents |
| **teaching** | ~3k | disabled | Educational content and curriculum design |
| **pair** | ~3k | MESH | Remote session monitoring and steering |
| **planning** | ~3.5k | PIPELINE | Daily team planning and task prioritisation |
| **demo** | ~2.5k | disabled | Live demonstrations and capability showcase |
| **create** | ~2k | disabled | Meta-mode for creating new modes |
| **upskilling** | ~5.5k | disabled | Meta-skill creation with recursive learning |
| **incognito** | ~500 | disabled | Private conversation, no persistence |
| **flow** | ~500 | disabled | Trust-based permission bypass (hidden) |

## How Modes Work

Every mode stacks on top of a **foundation layer** that is always active. The foundation includes PARAMOUNT skills (ultrathink, asking-users, context-gate, forging-skills) and cannot be disabled by any mode.

When you activate a mode:
1. Foundation layer remains active (~1k tokens)
2. Mode-specific `always` skills load automatically
3. Mode `prompt_injection` adds domain-specific instructions
4. Mode `optional` skills become available on demand

## Quick Start

```bash
/mode list              # Show all available modes
/mode code              # Activate code mode
/mode code testing      # Stack up to 3 modes
/mode off               # Deactivate current mode
/mode status            # Show current mode and loaded skills
```

---

## Development Modes

### code

Software development with design principles and testing.

**Token budget**: ~6k | **Broly**: FORK (auto-active)

**When to use**:
- Building new features
- Refactoring existing code
- Bug fixing and debugging
- Architecture decisions during implementation
- Code review

**Workflow**:
1. Verify requirements are clear (AskUserQuestion if uncertain)
2. Consider existing patterns in codebase
3. Plan approach before coding
4. Execute with feature-complete 7-phase workflow
5. Run tests, consider e2e-test-generation for critical paths
6. PR automation via `/create-pr`

**Skills loaded**: feature-complete, design-principles, code-agentic
**Optional skills**: e2e-test-generation, ui-complete, gitops, pr-automation

**Broly**: Auto-active at FORK (up to 3 agents, chain-of-thought reasoning). Escalates to CLUSTER for refactors spanning 5+ files or codebase-wide pattern replacement. De-escalates to THREAD for single-file fixes.

**Stacks well with**: testing, security, architect

```bash
/mode code
```

---

### architect

System design, architecture decisions, ADR generation.

**Token budget**: ~3.5k | **Broly**: PIPELINE (auto-active)

**When to use**:
- System architecture design
- Technical decision making
- ADR creation and review
- Architecture documentation
- Design pattern selection

**Workflow**:
1. Understand requirements and constraints
2. Identify decision points
3. Generate options (minimum 2)
4. Evaluate against design principles (SOLID, GRASP)
5. Document decision in ADR
6. Create diagrams as needed (C4, sequence, ER, flowchart)

**Mandatory ADR for**: Technology choices, architecture patterns, API design, security approach changes, significant integrations.

**Skills loaded**: design-principles, adr-generation
**Optional skills**: system-diagramming, feature-complete

**Broly**: Auto-active at PIPELINE (up to 10 agents, graph-of-thought reasoning). Escalates to MESH for multi-service designs or decisions impacting organisational structure. De-escalates to THREAD for single-component questions.

**Stacks well with**: code, strategy, decision

```bash
/mode architect
```

---

### automation

Workflow orchestration with safety guardrails for autonomous operation.

**Token budget**: ~4.5k | **Broly**: FORK (auto-active)

**When to use**:
- Automating repetitive workflows
- Building CI/CD pipelines
- Batch processing tasks
- Self-improving systems
- Multi-stage orchestrations

**Core principle**: Autonomy requires commensurate safety.

**Safety guardrails**:
- Dual-condition exits (completion + explicit signal)
- Circuit breakers (stagnation, errors, output decline)
- Rate limiting (100 API/hr, 50 file ops/min)
- Scope boundaries (define allowed/forbidden)
- Rollback points (checkpoint before changes)

**Skills loaded**: self-improvement-engine, recursive-learning, safety-guardrails
**Optional skills**: autonomous-orchestration, autonomous-learning, code-agentic

**Broly**: Auto-active at FORK (up to 3 agents, ReAct reasoning). Escalates to CLUSTER for pipelines with 3+ independent stages or multi-repo batch operations. De-escalates to THREAD for single-script automation.

**Stacks well with**: code, testing

```bash
/mode automation
```

---

## Knowledge Work Modes

### research

Deep research, evidence synthesis, systematic analysis.

**Token budget**: ~3k | **Broly**: CLUSTER (auto-active)

**When to use**:
- Technical research and analysis
- Literature reviews
- Competitive analysis
- Technology evaluation
- Best practice identification

**Workflow (PICO + CRAAP frameworks)**:
1. Question formulation (PICO framework)
2. Source discovery (multi-type)
3. Evidence collection (structured extraction)
4. Evidence evaluation (CRAAP scoring)
5. Synthesis (weighted by quality)

**Evidence hierarchy**: Peer-reviewed research > Official docs/RFCs > Technical blogs > Community consensus > Individual opinions.

**Skills loaded**: reasoning-with-ai, research-synthesis
**Optional skills**: evidence-evaluation, chat-history-search

**Broly**: Auto-active at CLUSTER (up to 7 agents, tree-of-thought reasoning). Escalates to PIPELINE for multi-domain synthesis or systematic reviews. De-escalates to THREAD for single-source lookups. Auto-recommends 1M context.

**Stacks well with**: synthesis, analysis, decision

```bash
/mode research
```

---

### synthesis

Knowledge synthesis, conceptual bridging, pattern extraction across sources.

**Token budget**: ~3.5k | **Broly**: CLUSTER (auto-active)

**When to use**:
- Cross-domain knowledge integration
- Literature synthesis
- Pattern extraction from multiple sources
- Framework building
- Conceptual bridging

**Workflow**:
1. INVENTORY - Catalogue all sources, claims, and domains
2. MAP - Create concept map showing relationships
3. BRIDGE - Identify connections across domains
4. PATTERN - Extract recurring themes and structures
5. INTEGRATE - Build unified framework from parts

**Synthesis frameworks**: Thematic analysis, dialectical synthesis (Hegelian), conceptual bridging (structural isomorphisms), pattern language.

**Skills loaded**: research-synthesis, reasoning-with-ai
**Optional skills**: evidence-evaluation, hypothesis-validation

**Broly**: Auto-active at CLUSTER (up to 7 agents, graph-of-thought reasoning). Escalates to PIPELINE for 5+ knowledge domains or 50+ pages of source material. De-escalates to THREAD for connecting only 2 sources. Auto-recommends 1M context.

**Stacks well with**: research, analysis, writing

```bash
/mode synthesis
```

---

### analysis

Structured analysis with RIPER phases.

**Token budget**: ~4k | **Broly**: CLUSTER (auto-active)

**When to use**:
- Complex problem analysis
- System investigation
- Root cause analysis
- Feature planning
- Refactoring analysis

**Phases (RIPER)**:
1. **RESEARCH** (read-only) - Understand current state
2. **INNOVATE** (read-only) - Brainstorm approaches (optional)
3. **PLAN** (limited write) - Create specifications
4. **EXECUTE** (full access) - Implement the plan
5. **REVIEW** (read + test) - Validate implementation

**Commands**: `/riper:research [topic]`, `/riper:plan [feature]`, `/riper:execute`, `/riper:review`, `/riper:status`

**Skills loaded**: riper-workflow, reasoning-with-ai
**Optional skills**: hypothesis-validation, pre-mortem-facilitation, decision-locking

**Broly**: Auto-active at CLUSTER (up to 7 agents, beam-of-thought reasoning). Escalates to PIPELINE for parallel RIPER phases or multi-system investigations. De-escalates to THREAD for simple diagnostics. Auto-recommends 1M context.

**Stacks well with**: research, code, architect

```bash
/mode analysis
```

---

### writing

Long-form writing, technical docs, editing, voice-consistent content.

**Token budget**: ~3k | **Broly**: disabled

**When to use**:
- Technical documentation
- Blog posts and articles
- Reports and proposals
- Editing and revision
- Content strategy

**Workflow**:
1. **BRIEF** - Clarify purpose, audience, tone, constraints
2. **STRUCTURE** - Outline with logical flow and transitions
3. **DRAFT** - Write with consistent voice and clear argument
4. **EDIT** - Tighten prose, fix structure, verify claims
5. **POLISH** - Final read for rhythm, clarity, impact

**Writing principles**: One idea per paragraph. Active voice by default. Concrete over abstract. Show over tell. Cut ruthlessly. Maintain consistent voice throughout.

**Editing protocol**: Structural edit first (argument flow), line edit second (sentence clarity), copy edit last (grammar/spelling). Never edit and draft simultaneously.

**Skills loaded**: authentic-writing, reasoning-with-ai
**Optional skills**: medium-article-writer, professional-pdf, markdown-expert

**Stacks well with**: marketing, research, synthesis

```bash
/mode writing
```

---

### learning

Personal learning pathway design, study journey planning.

**Token budget**: ~3.5k | **Broly**: disabled

**When to use**:
- Personal skill development
- Study plan creation
- Knowledge gap analysis
- Learning resource curation
- Certification preparation

**Workflow**:
1. **ASSESS** - Determine current knowledge level and learning style
2. **TARGET** - Define specific, measurable learning objectives
3. **GAP** - Map distance between current state and target
4. **PATH** - Design sequenced learning steps (prerequisites first)
5. **RESOURCE** - Curate resources matched to learner level
6. **SCHEDULE** - Build spaced repetition and practice schedule
7. **TRACK** - Define progress checkpoints and mastery criteria

**Learning principles**: Feynman test (can you explain it simply?), 80/20 identification, confidence mapping, interleaved practice, desirable difficulty.

**Spaced repetition**: Day 1 > Day 2 > Day 4 > Day 7 > Day 14 > Day 30.

**Skills loaded**: pedagogical-patterns, curriculum-design
**Optional skills**: reasoning-with-ai, research-synthesis

**Differs from teaching mode**: Teaching delivers content. Learning designs the journey.

**Stacks well with**: research, teaching

```bash
/mode learning
```

---

## Decision and Strategy Modes

### strategy

Strategic assessment, gap analysis, and decision-making.

**Token budget**: ~3.5k | **Broly**: MESH (auto-active)

**When to use**:
- Business strategy
- Technical architecture decisions
- Team and resource allocation
- Risk assessment
- Competitive analysis

**Available frameworks**:
- Gap analysis (current vs desired state)
- Red-teaming (adversarial challenge of assumptions)
- Pre-mortem (prospective failure analysis)
- Decision locking (commit and document decisions)

**Workflow**: Clarify decision context > Identify stakeholders and constraints > Surface hidden assumptions > Challenge assumptions explicitly > Consider second-order effects > Map dependencies and risks > Summarise and lock decisions.

**Skills loaded**: strat-ops, leading-organisation
**Optional skills**: pre-mortem-facilitation, decision-locking, red-teaming-strategic-decisions

**Broly**: Auto-active at MESH (up to 15 agents, tree-of-thought council reasoning). This is the highest Broly level -- no further escalation. De-escalates to THREAD for quick strategic questions. Auto-recommends 1M context.

**Stacks well with**: decision, business, analysis

```bash
/mode strategy
```

---

### decision

Structured decision frameworks, options analysis, criteria weighting.

**Token budget**: ~3k | **Broly**: disabled

**When to use**:
- Technology selection
- Architecture decisions
- Strategic choices
- Resource allocation
- Risk assessment

**Workflow (7 phases)**:
1. **FRAME** - Define the decision clearly (what, why, constraints, timeline)
2. **CRITERIA** - Establish weighted evaluation criteria
3. **OPTIONS** - Generate and describe all viable options
4. **ANALYSE** - Score options against criteria with evidence
5. **STRESS** - Pre-mortem, red team, reversibility check
6. **DECIDE** - Make recommendation with confidence level
7. **LOCK** - Document decision rationale for future reference

**Complexity tiers**: SIMPLE (2-3 options, skip to ANALYSE) | MODERATE (3-5 options, full workflow) | COMPLEX (5+ options, full workflow + stress testing).

**Cognitive bias checks**: Anchoring, sunk cost, status quo, confirmation bias.

**Skills loaded**: decision-locking, reasoning-with-ai
**Optional skills**: pre-mortem-facilitation, red-teaming-strategic-decisions, hypothesis-validation

**Stacks well with**: strategy, analysis, brainstorm

```bash
/mode decision
```

---

### brainstorm

Ideation, divergent thinking, creative exploration.

**Token budget**: ~2.5k | **Broly**: FORK (auto-active)

**When to use**:
- Product ideation
- Solution brainstorming
- Creative problem solving
- Feature exploration
- Innovation workshops

**Workflow**:
1. **SEED** - Define the challenge, constraints, and success criteria
2. **DIVERGE** - Generate maximum ideas (quantity over quality)
3. **CLUSTER** - Group related ideas into themes
4. **CONVERGE** - Evaluate and select top candidates
5. **DEVELOP** - Flesh out selected ideas into actionable concepts

**Critical rule**: During DIVERGE phase, suspend ALL criticism. No "but", no "however", no feasibility checks. Evaluation happens ONLY in CONVERGE.

**Ideation techniques**: SCAMPER, analogical thinking, constraint removal, worst idea reversal, role storming.

**Note**: forging-skills deliberately not loaded to preserve creative flow.

**Skills loaded**: reasoning-with-ai
**Optional skills**: hypothesis-validation, pre-mortem-facilitation

**Broly**: Auto-active at FORK (2-3 agents, tree-of-thought reasoning). Escalates to CLUSTER for multi-domain brainstorms or 50+ idea generation. De-escalates to THREAD for quick ideation.

**Stacks well with**: decision, strategy, writing

```bash
/mode brainstorm
```

---

### business

Founder operations, team leadership, and business decisions.

**Token budget**: ~2.5k | **Broly**: FORK (auto-active)

**When to use**:
- Strategic decisions
- Team management
- Business operations
- Investor relations
- Company culture

**Key tensions to balance**: Coding vs leading, speed vs thoroughness, delegation vs control, short-term vs long-term.

**Decision frameworks**: Reversible vs irreversible decisions, one-way vs two-way doors, experiment vs commit.

**Team leadership principles**: Context over control, outcomes over outputs, trust but verify.

**Skills loaded**: leading-organisation
**Optional skills**: strat-ops, legal-ops

**Broly**: Auto-active at FORK (up to 3 agents, tree-of-thought reasoning). Escalates to CLUSTER for multi-stakeholder decisions or financial modeling. De-escalates to THREAD for quick business questions.

**Stacks well with**: strategy, legal, planning

```bash
/mode business
```

---

## Review and Quality Modes

### review

Unified review for code, content, documents, and designs.

**Token budget**: ~3.5k | **Broly**: FORK (auto-active)

**When to use**:
- Code review
- Content editing and feedback
- Document quality assurance
- Design critique
- PR review

**Review domains**: CODE (logic, architecture, security, performance, maintainability) | CONTENT (clarity, accuracy, voice, audience fit) | DOCUMENT (completeness, consistency, formatting) | DESIGN (usability, accessibility, consistency).

**Structured output**: Summary > Strengths > Issues (with severity) > Suggestions > Questions.

**Issue severity**: CRITICAL (must fix, blocks approval) | IMPORTANT (should fix) | MINOR (nice to fix) | NIT (optional, stylistic).

**Skills loaded**: reviewing-contributions, design-principles
**Optional skills**: secure-code-review, authentic-writing, reasoning-with-ai

**Broly**: Auto-active at FORK (2-3 agents, ReAct pipeline reasoning). Escalates to CLUSTER for 10+ files or multi-domain review. De-escalates to THREAD for single-file review.

**Stacks well with**: code, security, writing

```bash
/mode review
```

---

### testing

Full QA lifecycle with human-in-the-loop gates.

**Token budget**: ~5.5k | **Broly**: CLUSTER (auto-active)

**When to use**:
- Verifying a feature works end-to-end
- QA before PR or release
- Challenging assumptions about a design or strategy
- Testing GRIP infrastructure changes
- Stress-testing business decisions or strategies

**Testing domains**: CODE (unit, integration, E2E, regression, accessibility) | FEATURE (test plan generation, edge case discovery) | INFRASTRUCTURE (GRIP modes, skills, hooks) | KNOWLEDGE (Devil's Advocate, assumption challenging).

**HITL gates**:
- CHECKPOINT: Manual test verification before feature sign-off
- REVIEW: Human approves test result interpretation
- FORGE-TEST: Human writes tests (agent describes WHAT, human writes HOW)

**Gate fatigue mitigation**: Batched checkpoints for low-risk features. Max 4-5 gates per session. Security/performance/regression always get individual gates.

**Skills loaded**: testing-qa-lifecycle, testing-modes-skills, design-principles
**Optional skills**: e2e-test-generation, code-agentic

**Broly**: Auto-active at CLUSTER (up to 7 agents, ReAct reasoning, pipeline pattern for code/infra, council pattern for knowledge/strategy). Escalates to MESH for testing spanning 3+ domains or features with security implications. De-escalates to THREAD for single test case verification.

**Stacks well with**: code, security, review

```bash
/mode testing
```

---

### security

Security auditing, vulnerability detection, threat modeling.

**Token budget**: ~3.5k | **Broly**: CLUSTER (auto-active)

**When to use**:
- Code security audits
- Vulnerability scanning
- Threat modeling sessions
- Security-focused code review
- Pre-release security gates

**Frameworks**: STRIDE (threat categorisation), OWASP Top 10, DREAD (prioritisation).

**Workflow**:
1. Identify attack surface (entry points, trust boundaries)
2. Apply STRIDE threat categorisation
3. Use DREAD for prioritisation
4. Pattern-based vulnerability scanning
5. Document findings with severity and remediation
6. Check for variant patterns

**Skills loaded**: vulnerability-detection, threat-modeling, sanitizing-public-releases, deployment-hardening
**Optional skills**: secure-code-review, code-agentic

**Broly**: Auto-active at CLUSTER (up to 7 agents, beam-of-thought reasoning). Escalates to PIPELINE for full application audits or multi-stage compliance reviews. De-escalates to THREAD for single-function security checks. Auto-recommends 1M context.

**Stacks well with**: code, review, testing

```bash
/mode security
```

---

## Content and Communication Modes

### marketing

Content creation with authentic voice (anti-AI slop).

**Token budget**: ~2.5k | **Broly**: disabled

**When to use**:
- Technical writing and thought leadership
- Product announcements and company updates
- LinkedIn posts and professional networking
- Medium articles
- Blog posts (company/personal)

**Anti-AI slop protocols**: Never use "dive into", "game-changer", "revolutionary", "in today's fast-paced world", "unlock the potential", "seamlessly integrate", or generic corporate speak.

**Writing standards**: Active voice. Lead with specific value. Include concrete examples. Match the author's authentic voice. British English spelling.

**Content types**: Medium articles, LinkedIn posts, blog posts, branded PDF documents.

**Skills loaded**: authentic-writing, medium-article-writer
**Optional skills**: professional-pdf

**Stacks well with**: writing, business

```bash
/mode marketing
```

---

### legal

Contract drafting and legal document generation.

**Token budget**: ~2.5k | **Broly**: FORK (auto-active)

**When to use**:
- Contract drafting
- Terms of service
- Privacy policies
- Partnership agreements
- Employment agreements

**Document types**: Formal contracts (senior attorney level), simplified explanatory versions, service agreements, NDAs and confidentiality.

**Standards**: Contra proferentem protection, comprehensive IP indemnification, professional boilerplate, clear definitions section. Default jurisdiction: South African law (or as specified).

**Skills loaded**: legal-ops
**Optional skills**: sanitizing-public-releases

**Broly**: Auto-active at FORK (up to 3 agents, chain-of-thought reasoning). Escalates to CLUSTER for multi-party agreements or multi-jurisdiction compliance reviews. De-escalates to THREAD for single clause review.

**Stacks well with**: business, strategy

```bash
/mode legal
```

---

### teaching

Educational content, curriculum design, explanatory frameworks.

**Token budget**: ~3k | **Broly**: disabled

**When to use**:
- Creating tutorials and guides
- Explaining technical concepts
- Designing learning paths
- Teaching programming concepts
- Onboarding documentation

**FASTER framework**: Forget (beginner's mindset) > Act (hands-on over passive) > State (ensure focus) > Teach (teach-back verification) > Enter (consistent practice) > Review (spaced repetition).

**Key techniques**: Scaffolded complexity (simple to complex), worked examples before practice, interleaved practice, ADEPT explanations (Analogy, Diagram, Example, Plain English, Technical).

**Skills loaded**: pedagogical-patterns, reasoning-with-ai
**Optional skills**: curriculum-design, authentic-writing

**Differs from learning mode**: Teaching delivers content. Learning designs the journey.

**Stacks well with**: learning, writing

```bash
/mode teaching
```

---

## Collaboration Modes

### pair

Remote session monitoring and steering via Tailscale.

**Token budget**: ~3k | **Broly**: MESH (auto-active)

**When to use**:
- Pair programming with another GRIP session
- Remote collaboration across machines
- Session monitoring and mentoring
- Cross-machine debugging

**Commands**:
- `/pair list` - List monitorable sessions
- `/pair connect <session_id>` - Connect to a session
- `/pair status` - Show connection status
- `/pair steer <text>` - Send steering context
- `/pair disconnect` - Disconnect from session

**Event stream**: Receives real-time Bash, Write, Edit, Skill, TeamCreate, and SendMessage events from monitored session.

**Steering**: Soft steering only (context injection and suggestions). Requires explicit lock acquisition (single holder, 60s timeout).

**Skills loaded**: pair-mode
**Optional skills**: code-agentic, context-gate

**Broly**: Auto-active at MESH (council pattern, ReAct reasoning). Enables multi-session coordination and cross-machine debugging at the highest agent scale.

**Stacks well with**: code, testing

```bash
/mode pair
```

---

### planning

Daily team planning and task prioritisation.

**Token budget**: ~3.5k | **Broly**: PIPELINE (auto-active)

**When to use**:
- Starting the work day
- Team coordination
- Task prioritisation
- Sprint planning preparation
- Parallel session setup

**Workflow**: Gather > Prioritise > Decompose > Assign > Document

**Prioritisation hierarchy**: Urgent (time-sensitive) > Revenue (income impact) > Deliverables (committed outputs) > Strategic (long-term).

**Assignment logic**: High complexity to team lead, medium to team members, easy to any available.

**Output artifacts**: `{date}-daily-plan.md`, `ticket-{n}-{name}.md`, `assignment-{name}.md`.

**Skills loaded**: daily-planning, leading-organisation
**Optional skills**: design-principles

**Broly**: Auto-active at PIPELINE (up to 10 agents, self-ask reasoning). Escalates to MESH for multi-team planning or resource allocation across competing priorities. De-escalates to THREAD for solo daily plans. Auto-recommends 1M context.

**Stacks well with**: business, strategy

```bash
/mode planning
```

---

## Utility Modes

### demo

Live demonstration and capability showcase.

**Token budget**: ~2.5k | **Broly**: disabled

**When to use**:
- Prospect demonstrations
- Team onboarding sessions
- Tutorial recordings
- Conference presentations
- Sales calls with technical buyers

**Optimised for**: Visual clarity and audience engagement.

**Recommended showcase order**: Session Resurrection > Efficiency Protocols > Skill System > AskUserQuestion gates > Modes System > RSI Loop.

**Output style**: Short, punchy sentences. Impact statements after each demo. "What you just saw: X" summaries. Metrics where applicable.

**Skills loaded**: concise-communication, asking-users
**Optional skills**: professional-pdf, mode, pr-automation

**Stacks well with**: code (for live coding demos)

```bash
/mode demo
```

---

### create

Meta-mode for creating new modes with quality gates.

**Token budget**: ~2k | **Broly**: disabled

**When to use**:
- Creating new operating modes
- Extending GRIP capabilities
- Meta-level configuration

**Mode creation wizard gathers**: Mode name (lowercase), description, triggers, always/optional skills, token budget estimate, prompt injection.

**Quality gates (from skill-creation-best-practices)**: Name must be lowercase (hyphens allowed), description must include "Use when", skills must exist in `~/.claude/skills/`, token budget must be realistic.

**Output**: YAML file saved to `~/.claude/modes/definitions/{name}-mode.yaml`.

**Skills loaded**: skill-creation-best-practices, creating-skills

**Stacks well with**: upskilling

```bash
/mode create
```

---

### upskilling

Meta-skill creation mode with recursive learning.

**Token budget**: ~5.5k | **Broly**: disabled

**When to use**:
- Creating new GRIP skills
- Building skills that create skills
- Recursive self-improvement
- Pattern detection and capture
- Meta-learning and heuristic updates

**Skill creation pipeline**: Detect pattern > Validate against best practices > Propose via AskUserQuestion > Generate SKILL.md (quality >= 70%) > Integrate into registry > Meta-learn (update detection heuristics).

**Periodic self-check**: Every 15-20 operations, automatically scans for repeated patterns (3x YSH threshold), novel approaches, workarounds that should be permanent, and user corrections indicating missing skills.

**RSI integration**: Activates full recursive self-improvement pipeline (self-improvement-engine, recursive-learning, meta-improvement-switch).

**Auto-trigger**: Activates on `[GRIP LEARNING]` signal or novelty threshold >= 0.7.

**Skills loaded**: skill-creation-best-practices, creating-skills, recursive-learning, autonomous-learning, self-improvement-engine, meta-improvement-switch
**Optional skills**: agent-auto-creator, learning-new-skills

**Stacks well with**: code, create

```bash
/mode upskilling
```

---

### incognito

Private conversation mode with no persistence.

**Token budget**: ~500 | **Broly**: disabled

**When to use**:
- Sensitive client discussions
- Personal matters
- Confidential planning
- Any conversation you do not want persisted

**Privacy protocols**:
1. **No persistence** - No instance serialisation, no session state updates, no resurrection files
2. **No external calls** - No MCP servers, no WebFetch/WebSearch (unless explicitly requested)
3. **Minimal logging** - Avoids triggering hooks where possible
4. **On exit** - Reminder: "Incognito session ended. Nothing persisted."

**Technical limitations (transparent)**: Requests still go to Anthropic API (unavoidable). Terminal history may retain commands. System hooks in settings.json still execute.

**Skills loaded**: none (minimal footprint)

```bash
/mode incognito
```

---

### flow

Trust-based permission bypass.

**Token budget**: ~500 | **Broly**: disabled | **Hidden by default**

**Security risk**: HIGH. Flow mode bypasses file edit, bash, and git permission prompts. All operations execute without confirmation.

**When to use**:
- High-trust rapid development
- Experienced users with version control discipline
- Time-critical operations with acceptable risk tolerance

**Requires explicit enable**: `grip flow --enable --i-understand-the-risks`

**What it bypasses**: File edits, bash commands, git operations (including destructive) all proceed without review.

**What remains active**: Forge gates (forging-skills) replace permission friction with skill retention friction.

**Skills loaded**: none (pure permission bypass, not skill enhancement)

```bash
/mode flow --enable
```

To disable: `grip flow --disable`

---

## Advanced: Mode Stacking

Stack up to 3 modes simultaneously for cross-domain work:

```bash
/mode code testing          # Development + QA
/mode research synthesis    # Deep research + cross-domain integration
/mode strategy decision     # Strategic assessment + structured decision
```

**How stacking works**: Each mode's `always` skills load additively. All prompt injections apply. Foundation layer remains singular (not duplicated). Token budget is cumulative.

**Effective combinations**:

| Combination | Use Case |
|-------------|----------|
| code + testing | Feature development with integrated QA |
| code + security | Secure coding with vulnerability awareness |
| research + synthesis | Deep investigation with cross-domain integration |
| strategy + decision | Strategic assessment leading to locked decisions |
| brainstorm + decision | Ideation flowing into structured selection |
| writing + marketing | Long-form content with authentic voice enforcement |
| architect + code | Design decisions flowing into implementation |
| planning + business | Daily operations with founder context |

---

## Advanced: Broly Auto-Activation

15 of the 25 modes have Broly auto-active. This means the meta-agent engages automatically based on task complexity without needing `/broly`.

**Broly levels by mode**:

| Level | Agents | Modes |
|-------|--------|-------|
| THREAD | 1 | (de-escalation target for all modes) |
| FORK | 2-3 | code, automation, legal, business, brainstorm, review |
| CLUSTER | up to 7 | research, synthesis, analysis, testing, security |
| PIPELINE | up to 10 | architect, planning |
| MESH | up to 15 | strategy, pair |

**Reasoning frameworks by mode**:

| Framework | Modes |
|-----------|-------|
| Chain-of-thought (CoT) | code, legal |
| Tree-of-thought (ToT) | strategy, business, brainstorm, research |
| Graph-of-thought (GoT) | architect, synthesis |
| Beam-of-thought (BoT) | analysis, security |
| ReAct | automation, pair, testing, review |
| Self-ask | planning |

**Disabled modes** (10): writing, learning, decision, marketing, teaching, demo, create, upskilling, incognito, flow. These operate single-threaded for focused work or minimal footprint.

---

*GRIP v6.0.0 | [Modes Architecture](MODES-ARCHITECTURE.md) for technical details*
