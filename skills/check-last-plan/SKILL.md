---
name: check-last-plan
description: Persist plan context across sessions via background caching
command: /check-last-plan
deprecated: true
superseded_by: /recall
linked_agent: plan-persistence
status: active
effort: high
auto_generated: false
generation_date: 2025-12-12
---

# Check Last Plan Skill

> Superseded for next-session brief selection by /ultraprompt-picker (surfaces the most-recent unexecuted ultraprompts — up to the `_PENDING_LIMIT` cap of 6 — for an operator-gated pick). This skill remains for caching the newest plan when a session ends and retrieving it in a later session.

**Purpose:** Cache plan context when a session ends, and retrieve it in a later session.

**Activation:**

- Automatic: session end caches the newest plan file (`hooks/session-end.sh`, `cache_plan`)
- Automatic: session start links the new session to the cached plan (`link_session_to_plan`); it prints nothing
- Manual: `/check-last-plan`, or `/recall`, which also reads the cache

**Token Budget:** ~200 tokens (minimal overhead)

---

## Unified Architecture Pattern

This skill demonstrates the canonical pattern for skill/command/agent integration in Claude-Optim:

| Component | Role | Activation |
|-----------|------|------------|
| **Skill** (this file) | Passive reference, protocol definition | Context loading |
| **Command** (`/check-last-plan`) | Explicit user invocation | Manual |
| **Agent** (`plan-persistence`) | Background execution | On request |
| **Library** (`lib/plan-persistence.sh`) | Core bash functions | Sourced by hooks |

### Relationship

```text
Skill (defines WHAT and WHY)
  ├── Command (HOW to invoke manually)
  ├── Agent (WHEN to run automatically)
  └── Library (IMPLEMENTATION details)
```

Use this pattern as reference when creating new skill/command/agent trios.

---

## Problem

Plans created in plan mode are lost when sessions end. Users must manually track what was planned and re-establish context in new sessions.

**Impact:**

- Cold start problem for ongoing work
- Context loss across sessions
- Manual effort to recall plan state
- Duplicate planning if forgotten

---

## Solution

Cache the newest plan automatically when a session ends, and read it back on request in a later session.

### Cache Architecture

**Location:** `~/.claude/cache/last-plan.json`

**Structure:**

```json
{
  "plan_id": "lucky-orbiting-allen",
  "plan_path": "~/.claude/plans/lucky-orbiting-allen.md",
  "plan_content": "...",
  "cached_at": "2025-12-12T20:00:00Z",
  "project_path": "~/.claude",
  "project_encoded": "-Users-laurie--claude"
}
```

**Design Decision:** Global cache with project identifier (see architectural critique in plan file).

---

## Protocol

### On Session End

1. `hooks/session-end.sh` runs `cache_plan`
2. Source `lib/plan-persistence.sh`
3. Find the newest plan file via `get_latest_plan_file`
4. Call `cache_current_plan "$plan_file"`
5. Log `Plan cached: {plan_id}` through the hook's `log_info`

### On Session Start

`hooks/session-start.sh` calls `link_session_to_plan` (in `hooks/startup/grip.sh`), which links the new session to the cached plan in the plan registry and prints nothing. No hook prints a `[PLAN-FOUND]` line, and no hook caches a plan on `ExitPlanMode`: run `/check-last-plan` or `/recall` to see the cached plan.

### On /check-last-plan

1. Read `~/.claude/cache/last-plan.json`
2. Display plan summary (id, cached time, project)
3. Show plan content (or summary)
4. Offer to continue with plan or start fresh

---

## Usage

### Manual Invocation

```text
/check-last-plan
```

### Automatic Activation

The cache is written automatically when a session ends (`hooks/session-end.sh`). Reading it back is manual: `/check-last-plan` or `/recall`.

### Library Functions

```bash
source ~/.claude/lib/plan-persistence.sh

# Cache a plan
cache_current_plan "/path/to/plan.md"

# Retrieve for current project
plan_json=$(retrieve_cached_plan)

# Retrieve any plan (global)
plan_json=$(retrieve_any_cached_plan)

# Check if cache exists
if has_recent_plan_cache; then
    echo "Plan available for current project"
fi

# Diagnose
./lib/plan-persistence.sh diagnose
```

---

## Examples

### /check-last-plan Output

```text
=== Cached Plan ===
ID: lucky-orbiting-allen
Project: ~/.claude
Cached: 2025-12-12T20:00:00Z (2 hours ago)

## Plan Summary
Create a unified skill/command/agent trio that persists plan context...

Continue with this plan? (The plan file is at ~/.claude/plans/lucky-orbiting-allen.md)
```

---

## Integration

### With session-end.sh

```bash
cache_plan() {
    if [[ ! -f "$LIB_DIR/plan-persistence.sh" ]]; then
        return 0
    fi

    # shellcheck source=/dev/null
    source "$LIB_DIR/plan-persistence.sh"

    local plan_file
    plan_file=$(get_latest_plan_file 2>/dev/null) || true

    if [[ -n "$plan_file" ]] && [[ -f "$plan_file" ]]; then
        if cache_current_plan "$plan_file" 2>/dev/null; then
            log_info "Plan cached: $(basename "$plan_file" .md)"
        fi
    fi
}
```

### With CLAUDE.md

Registered in Skills System section and Slash Commands table.

---

## Metrics

**Token Budget:** ~200 tokens (session start check + display)

### Success Criteria

- Plans automatically persist across sessions
- No manual caching required
- A new session is linked to the cached plan
- `/check-last-plan` works as documented
- Pattern documented as reference for future trios

### Tracking

`hooks/session-end.sh` logs `Plan cached: {id}` through its `log_info` when it caches a plan, and `hooks/startup/grip.sh` logs `Linked session to plan: {id}` when it links one.

---

## Notes

- Cache expires after 24 hours (configurable in library)
- Project-aware: default retrieval only matches current project
- Global access via `retrieve_any_cached_plan()` for cross-project queries
- Cross-platform: macOS (BSD) and Linux (GNU) stat formats handled

### Related

- `lib/plan-persistence.sh` - Core implementation
- `commands/check-last-plan.md` - Slash command definition
- `agents/plan-persistence.md` - Agent definition
- `hooks/session-end.sh` - Integration point (caching)
- `hooks/session-start.sh` and `hooks/startup/grip.sh` - Integration point (linking a new session to the cached plan)

---

**Skill Status:** Active
**Maintainer:** GRIP Core Team
**Last Updated:** 2026-10-26
**Architecture Pattern:** Unified skill/command/agent reference implementation
