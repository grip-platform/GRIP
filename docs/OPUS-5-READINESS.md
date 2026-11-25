# Build for Opus 5: GRIP Obsolescence Analysis

*February 2026 | Issue #286*

## Premise

Boris Cherny (Anthropic): "Build for the model six months in the future."

GRIP was built across 347+ generations, compensating for model weaknesses as they were discovered.
The question: what survives when the model is 2-3x better at instruction following, self-organization,
context maintenance, and code correctness?

## Component Inventory

| Category | Count | Token Cost |
|----------|-------|------------|
| Rules (CLAUDE.md + rules/) | 11 files | ~16KB |
| Skills | 99 | ~200KB total |
| Agents | 19 | ~15KB |
| Modes | 27 | ~30KB |
| Lib modules | 80+ | ~500KB |

## Classification: KEEP / DEPRECATE / EVOLVE

### KEEP (Structural — model-independent)

These provide value regardless of model capability:

| Component | Why it survives |
|-----------|----------------|
| converge() kernel | Mathematical convergence is model-independent. Better models converge faster, not without the loop. |
| KONO memory substrate | Cross-session memory requires external storage. No model improvement eliminates this need. |
| Genome/DNA evolution | Skill fitness tracking across sessions. The evolutionary signal accumulates over time. |
| Git safety gates | Destructive action prevention is mechanical, not behavioral. |
| Session serialization | Context window limits exist regardless of model quality. |
| Subsystem bridge | Cross-module integration is architectural, not compensatory. |
| auto_converge.py | RSI orchestration coordinates external systems. |
| Delight metrics | Measurement infrastructure persists independent of what's measured. |

### DEPRECATE (Scaffolding — compensates for model weakness)

These exist because current models fail without them:

| Component | Why it's scaffolding | When to remove |
|-----------|---------------------|----------------|
| efficiency-rules.md (12 rules) | Models re-read files, waste tokens, add preamble | When model naturally optimizes token usage |
| bash-safety.md | Models use grep instead of rg, forget --exclude | When tool selection is reliable |
| concise-communication skill | Teaches "no preamble, no postamble" | When model defaults to concise |
| dependency-guardian | Blocks reading node_modules | When model never reads dependency dirs |
| file-read-optimizer | Caches reads, prevents re-reads | When model tracks its own reads |
| batch-edit-enforcer | Forces MultiEdit over sequential edits | When model batches naturally |
| forging-skills (periodic halt) | RSI safety: force user engagement | When autonomy trust is established |
| 27 mode YAML definitions | Each mode is ~50 lines of behavioral instruction | When model can derive behavior from mode name alone |
| Plan evaluation gate | "Is this change actually needed?" | When model doesn't blindly follow stale plans |

**Estimated savings**: ~8KB system prompt, 30+ skill files, 12 rule entries.

### EVOLVE (Adapt for new capabilities)

These provide value but need redesign:

| Component | Current form | Opus 5 form |
|-----------|-------------|-------------|
| CLAUDE.md | 282 lines of behavioral rules | Thin pointer file: 20 lines max |
| Skills system | 99 SKILL.md files loaded as context | Dynamic skill discovery: model reads on demand |
| Mode system | YAML definitions with explicit tool lists | Semantic mode switching: "I need architect mode" triggers minimal context |
| Context gate | Manual 85% threshold monitoring | Model self-manages context, asks for fresh session when needed |
| Safety gates | 10+ deterministic PreToolUse hooks | Keep gates, remove behavioral rules that duplicate them |
| Session protocol | Explicit resurrection, identity, anti-drift | Model remembers context natively; protocol becomes "load KONO, continue" |
| Ralph review | Detailed rubric, 15+ categories | Rubric stays, scoring simplifies as model review quality improves |

## v7 Architecture (Sketch)

```
GRIP v7 = Thin Coordination Layer

CLAUDE.md (20 lines):
  - Project identity
  - KONO connection string
  - Safety gate activation
  - "Read skills on demand from skills/"

Runtime:
  converge() kernel          — KEEP as-is
  KONO memory substrate      — KEEP as-is
  Genome evolution           — KEEP as-is
  Safety gates (hooks)       — KEEP as-is
  auto_converge.py           — KEEP as-is
  Subsystem bridge           — KEEP as-is

On-demand (loaded when needed):
  Skills                     — Model reads SKILL.md only when task matches
  Modes                      — Single-line activation, no YAML injection
  Rules                      — Gates enforce, model doesn't need behavioral rules

Removed:
  efficiency-rules.md        — Model handles naturally
  bash-safety.md             — Model handles naturally
  session-protocol.md        — Model handles naturally
  dependency-guardian         — Model handles naturally
  12+ behavioral skills      — Redundant with better instruction following
```

## The Key Insight

GRIP v6 is ~260KB of system context. A 2x better model needs ~50KB: the structural
components (converge, KONO, genome, gates) plus project-specific context. Everything
else is scaffolding that compensates for model limitations.

**What to build FOR (new model capabilities)**:
1. Multi-agent orchestration — better models enable more reliable sub-agent delegation
2. Longer autonomous runs — trust boundary expands, forging gates can relax
3. Cross-project learning — KONO can federate across repos
4. Real-time collaboration — better models can pair-program with less steering
5. Formal verification — better models can write proofs, not just tests

## Action Items

1. Tag every skill/rule with `scaffolding` or `structural` metadata
2. Add usage tracking to skills (which ones are actually loaded per session?)
3. Design CLAUDE.md v7 as a 20-line pointer file
4. Prototype on-demand skill loading (read SKILL.md only when task matches)
5. Measure: run a session with scaffolding rules removed, compare quality
