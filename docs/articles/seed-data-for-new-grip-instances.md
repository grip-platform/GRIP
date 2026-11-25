# Seed Data for New GRIP Instances: Pre-Filling High-Value Learnings

*February 2026 | GRIP v6.0.0*

---

## The Cold Start Problem

When GRIP is installed fresh on a new machine, it starts with empty memory:
- No MEMORY.md TODO tracker or session lineage
- No learned insights (rules/learned-insights.md is blank)
- No KONO memories (database starts at 0 entries)
- No converge metrics (no compound proof data)
- No evolutionary genome (converge_dna bootstraps from filesystem scan, but with neutral 0.5 fitness)

This means a new user's first 10-20 sessions are spent rediscovering patterns that the GRIP development team already validated across 340+ generations. The knowledge exists in GRIP's rules files, CLAUDE.md, and MEMORY.md, but the *experiential* weight behind those rules is missing.

**The question**: What high-value learnings can be pre-seeded so a new GRIP instance starts at generation 20 rather than generation 0?

---

## Taxonomy of Transferable Knowledge

Not all learnings transfer equally. We classify GRIP's accumulated knowledge by portability:

### Tier 1: Universal (applies to any Claude Code user)

These patterns are environment-independent and empirically validated:

| Learning | Source | Evidence |
|----------|--------|----------|
| Never read dependency folders (node_modules, venv, etc.) | Gen 1+ | Prevents 50K+ token waste per violation |
| Batch file reads in Phase 1, targeted edits in Phase 2+ | Gen 283 | 5-10K tokens saved per session |
| `rg` not `grep`, `fd` not `find` in bash | Gen 1+ | Hook enforcement since Gen 1 |
| No semicolons after `$()` in zsh eval contexts | Gen ~50 | Parse errors in hook pipelines |
| `--` before variable paths in `fd`/`rg` | Gen ~100 | Paths with leading dashes interpreted as flags |
| AskUserQuestion when confidence < 99.9999999% | Gen 191 | "YSH" incident — wrong icon assumed without asking |
| Content-based context validity, not time-based | Gen 182 | 60-second threshold was arbitrary and broke continuity |

### Tier 2: Architecture Patterns (applies to teams using Claude Code)

Proven across multi-session, multi-agent work:

| Pattern | Source | Validation |
|---------|--------|------------|
| Lean coordinator: delegate all work to workers | Gen 293 | 75% efficiency vs 42% with monolithic sessions |
| Meta-work cap at 25% | Gen 305 | Prevents infrastructure consuming delivery capacity |
| Feature-first priority (features > bugs > meta) | Gen 303 | User directive, validated across 50+ sessions |
| Progressive disclosure for onboarding (3 levels) | Gen 304 | Level 1 (5 min) gets people productive |
| Anti-drift "What's Up Next" after every commit | Gen 274 | 32% of sessions ended partially-achieved without it |
| Context gate at 85% with mandatory user choice | Gen 226 | Prevents context loss and auto-serialization failures |

### Tier 3: Tool-Specific (applies to GRIP users)

These require GRIP's specific infrastructure:

| Pattern | Dependency | Transfer Method |
|---------|-----------|-----------------|
| Session serialization on exit | hooks/session-end.sh | Installed by grip-setup.py |
| Plan persistence across sessions | lib/plan-persistence.sh | Installed by grip-setup.py |
| Gate enforcement (11 gates) | hooks/context-gate-hook.py | Installed by grip-setup.py |
| Converge() loop with per-dimension scoring | skills/converge/SKILL.md | Loaded as skill context |
| DNA genome evolutionary tracking | lib/converge_dna.py | Bootstrap-or-load on session start |

### Tier 4: Project-Specific (does NOT transfer)

- Team member identities and signatures (facts/team.md)
- Project-specific git branch strategies
- Deployment URLs and service configurations
- API keys, tokens, credentials
- KONO memories about specific codebase decisions

---

## Seed Data Architecture

### What to Pre-Fill

**1. MEMORY.md Template** (~100 lines)

Ship a starter MEMORY.md with:
- Empty lineage table (user fills on first session)
- The permanent "Never Bypass Safety Guardrails" section
- Empty TODO tracker with column headers
- The "RSI Loop Key Learnings" section pre-filled with Tier 1+2 patterns
- Empty Deliverable Summary section

```markdown
<!-- GRIP-BRIEF-START -->
# Session Lineage

| Gen | Achievement |
|-----|-------------|
| 1   | (first session) |

Gen 1 | Branch: main
<!-- GRIP-BRIEF-END -->

## CRITICAL: Never Bypass Safety Guardrails (PERMANENT)
[...pre-filled...]

## Key Learnings (Pre-Seeded from GRIP v6.0.0)
1. Lean coordinator pattern (75% vs 42%)
2. Feature-first priority ordering
3. Meta-work cap at 25%
4. Anti-drift protocol after every commit
5. Content-based context validity, not time-based
[...etc...]
```

**2. learned-insights.md** (~20 insights)

Pre-fill with the highest-confidence insights from 340 generations:

```markdown
## Hooks Run Independently
**When**: Hooks run independently of conversation context
**Do**: AskUserQuestion approval does not propagate to PreToolUse hooks
Source: pre-seeded | Score: 0.90 | Seen: 50x+

## Variable Ordering in Bash Hooks
**When**: Writing bash hooks with set -euo pipefail
**Do**: Always define variables before first use. Undefined vars kill the entire hook.
Source: pre-seeded | Score: 0.95 | Seen: 100x+

## Dual-Content-Block Breaks MCP Apps
**When**: Returning tool results in MCP servers
**Do**: Use type: "resource" with mimeType: "text/html" (EmbeddedResource), not multiple text content blocks.
Source: pre-seeded | Score: 0.85 | Seen: 10x+
```

**3. skill-proposals.jsonl** (deduped, top 10)

Ship the top 10 most universally applicable skill proposals, deduped and scored:

```jsonl
{"name": "variable-ordering-in-bash-hooks", "score": 0.95, "description": "Always define variables before first use in set -euo pipefail scripts"}
{"name": "content-based-validity", "score": 0.90, "description": "Context validity by content relevance, not by age/time"}
{"name": "lean-coordinator-pattern", "score": 0.90, "description": "Delegate all implementation to workers; coordinator stays light"}
```

**4. Converge Metrics Baseline** (~5 exemplar runs)

Ship 5 anonymized converge run records showing the expected scoring progression (45 -> 72 -> 91 pattern). This gives the compound analysis baseline data immediately:

```jsonl
{"run_id": "seed-001", "task_type": "code", "depths": [{"depth": 0, "score": 45}, {"depth": 1, "score": 72}, {"depth": 2, "score": 91}], "satisfied": true}
```

### What NOT to Pre-Fill

| Category | Reason |
|----------|--------|
| KONO memories | Project-specific; would pollute semantic search |
| Genome with evolved fitness | Fitness values are environment-dependent |
| Session transcripts | Privacy; contain user conversations |
| Team facts | Different team on every machine |
| Cached plans | Stale immediately on new machine |
| Instance index | UUIDs are machine-specific |

---

## Implementation: grip-setup.py Integration

The seed data should be installed during `grip-setup.py --install`:

```python
def install_seed_data(grip_dir: Path):
    """Install pre-filled high-value learnings for fresh instances."""
    seed_dir = grip_dir / "seed"
    memory_dir = grip_dir / "projects" / encode_project_path(os.getcwd()) / "memory"

    # Only seed if MEMORY.md doesn't exist (don't overwrite existing)
    memory_file = memory_dir / "MEMORY.md"
    if not memory_file.exists():
        memory_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(seed_dir / "MEMORY-template.md", memory_file)

    # Append seed insights (idempotent — check for "pre-seeded" marker)
    insights_file = grip_dir / "rules" / "learned-insights.md"
    if insights_file.exists():
        content = insights_file.read_text()
        if "pre-seeded" not in content:
            content += (seed_dir / "seed-insights.md").read_text()
            insights_file.write_text(content)

    # Seed converge metrics (only if empty)
    metrics_file = grip_dir / "learning" / "converge-metrics.jsonl"
    if not metrics_file.exists() or metrics_file.stat().st_size == 0:
        shutil.copy(seed_dir / "seed-metrics.jsonl", metrics_file)
```

### Directory Structure

```
~/.claude/
  seed/
    MEMORY-template.md          # Starter MEMORY with key learnings
    seed-insights.md            # Top 20 learned insights
    seed-proposals.jsonl        # Top 10 skill proposals
    seed-metrics.jsonl          # 5 exemplar converge runs
    README.md                   # What's in here and why
```

---

## Quantified Impact

### Without Seed Data (current)

| Session Range | Pattern Discovery Rate |
|---------------|----------------------|
| Gen 1-10 | Rediscovers tool substitution (rg/fd), batch reads, dependency blocking |
| Gen 10-20 | Discovers context gate, anti-drift, lean coordinator |
| Gen 20-50 | Develops project-specific learnings |
| Gen 50+ | Compound improvement begins |

### With Seed Data (proposed)

| Session Range | Pattern Discovery Rate |
|---------------|----------------------|
| Gen 1 | Tool substitution, batch reads, dependency blocking already known |
| Gen 1-5 | Context gate, anti-drift, lean coordinator already known |
| Gen 5-10 | Develops project-specific learnings |
| Gen 10+ | Compound improvement begins |

**Estimated acceleration**: 5-10x faster to productive compound improvement.

---

## Risk Analysis

### Over-Seeding Risk

Pre-filling too much creates the "textbook problem": the new instance has knowledge it hasn't earned through experience, leading to:
- **Goodhart's Law**: Optimizing for seeded metrics rather than actual improvement
- **Stale patterns**: Seeded learnings may not apply to the user's specific environment
- **Cognitive overload**: Too many pre-filled rules reduce signal-to-noise ratio

**Mitigation**: Keep seed data to ~100 lines of MEMORY.md, ~20 insights, and 5 metric exemplars. Quality over quantity. The `Score` field in learned-insights.md naturally decays unused insights over time.

### Under-Seeding Risk

Too little seed data means the cold start problem persists. The first 20 sessions are still spent rediscovering well-known patterns.

**Mitigation**: Focus on Tier 1 (universal) and Tier 2 (architecture) patterns only. These have the highest probability of being relevant regardless of project.

### Staleness Risk

Seed data becomes outdated as GRIP evolves. A v7.0 seed file shipping v6.0 patterns creates conflicts.

**Mitigation**: Version the seed data directory. On `grip-setup.py --upgrade`, offer to re-seed with updated learnings (user approval required).

---

## Conclusion

GRIP has accumulated 340+ generations of empirical learnings. The most valuable subset — universal patterns, architecture principles, and tool-specific gotchas — can be pre-filled for new instances without privacy concerns or over-engineering risk.

The implementation is straightforward: a `seed/` directory with 4 files, integrated into `grip-setup.py --install`. The expected acceleration is 5-10x faster time-to-compound-improvement.

The key design principle: **seed the patterns, not the memories.** Patterns are universal; memories are personal. A new GRIP instance should know *that* lean coordinators work better than monolithic sessions, but it shouldn't inherit *which specific project* discovered this.

---

*Produced by converge() transcript archaeology, Gen 340. Source data: 4 session transcripts (20MB total), MEMORY.md (340 gen lineage), learned-insights.md, skill-proposals.jsonl, converge-metrics.jsonl.*
