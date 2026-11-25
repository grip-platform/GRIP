---
name: mcp-apps
description: GRIP Emergent UI components as MCP Apps for interactive conversations. Use when presenting options, confirmations, checklists, kanban boards, decision matrices, or other interactive elements within Claude.
status: Active
effort: high
version: 2.0.0
triggers:
  - presenting options to user
  - confirmation dialogs
  - interactive selection
  - task management
  - decision making
  - data display
  - progress tracking
  - brainstorming
  - retrospectives
  - security assessment
  - gap analysis
  - emergent UI
integrates:
  - answer-classifier
  - asking-users
  - creating-wizards
  - hitl
  - hitl-decision-matrix
---

# MCP Apps

GRIP Emergent UI components delivered as MCP Apps for rich interactive experiences within Claude conversations. 21 tools across 6 categories.

## Architecture

```
Claude/VSCode/ChatGPT
    |  (tool call with _meta.ui)
GRIP MCP Apps Server (src/index.ts)
    |  (serves ui:// resource)
Sandboxed Iframe (ui/{component}/index.html)
    |  (postMessage)
User Interaction
    |  (updateModelContext)
Claude receives selection/data
```

All components are self-contained HTML with embedded CSS/JS, use GRIP design tokens, support `?demo` mode, and are keyboard navigable with no external dependencies.

## Component Reference

### Decision Tools

#### grip_options_picker

Interactive selection from 2-8 options.

```typescript
{
  title: "Which approach?",
  options: [
    { id: "1", label: "Option A", description: "Details", recommended: true },
    { id: "2", label: "Option B", description: "Details" }
  ],
  multiSelect: false
}
```

**Returns**: `User selected: {labels}`

#### grip_confirmation

Yes/No confirmation for binary decisions.

```typescript
{
  title: "Delete this file?",
  context: "This action cannot be undone.",
  confirmLabel: "Delete",
  cancelLabel: "Keep",
  destructive: true
}
```

**Returns**: `User confirmed: {title}` or `User cancelled: {title}`

#### grip_decision_matrix

Weighted scoring table for structured evaluation. Rows = criteria, columns = options.

```typescript
{
  title: "Framework Selection",
  criteria: [
    { id: "perf", label: "Performance", weight: 8 },
    { id: "dx", label: "Developer Experience", weight: 6 }
  ],
  options: [
    { id: "react", label: "React", scores: { perf: 4, dx: 5 } },
    { id: "vue", label: "Vue", scores: { perf: 4, dx: 4 } }
  ]
}
```

**Interaction**: Click cells to edit scores (1-5), edit weights (0-10). Auto-calculates weighted totals. Highlights winner.

**Returns**: Full matrix state with scores, weights, and winner ID.

### Workflow Tools

#### grip_checklist

Stateful multi-step checklist with progress bar and collapsible sections.

```typescript
{
  title: "Release Checklist",
  sections: [
    {
      heading: "Pre-release",
      items: [
        { id: "tests", label: "All tests passing", checked: false },
        { id: "docs", label: "Documentation updated", description: "README and API docs" }
      ]
    }
  ]
}
```

**Interaction**: Click items to toggle. Sections collapse via heading click. Progress bar updates.

**Returns**: `{ checkedIds: string[], progress: number }`

#### grip_wizard

Multi-step guided workflow with branching logic and 5 interaction types.

```typescript
{
  title: "Project Setup",
  steps: [
    {
      id: "lang", title: "Language", type: "single_select",
      options: [
        { id: "ts", label: "TypeScript" },
        { id: "py", label: "Python", nextStep: "py_framework" }
      ]
    },
    { id: "name", title: "Project Name", type: "text_input", placeholder: "my-app" },
    { id: "confirm", title: "Review", type: "confirm" }
  ]
}
```

**Step types**: `single_select`, `multi_select`, `text_input`, `confirm`, `info`

**Branching**: Option with `nextStep` jumps to that step ID instead of sequential next.

**Returns**: `{ answers: { [stepId]: value }, completed: true }` or `{ cancelled: true }`

#### grip_phase_stepper

Sequential workflow stepper with gated transitions and permission levels. For RIPER, writing, synthesis, and learning workflows.

```typescript
{
  title: "RIPER Analysis",
  phases: [
    { id: "research", label: "Research", permission: "read-only", status: "complete", summary: "Identified 3 issues" },
    { id: "innovate", label: "Innovate", permission: "read-only", status: "active" },
    { id: "plan", label: "Plan", permission: "limited-write", status: "locked" },
    { id: "execute", label: "Execute", permission: "full-access", status: "locked" },
    { id: "review", label: "Review", permission: "read-only", status: "locked" }
  ],
  allowBacktrack: false
}
```

**Interaction**: Active phase glows. Complete phases show summary on click. Advance button triggers gate.

**Returns**: `{ action: 'advance'|'backtrack', fromPhase, toPhase }`

### Management Tools

#### grip_kanban

Drag-and-drop board with priority indicators and tag chips.

```typescript
{
  title: "Sprint Board",
  columns: [
    {
      id: "todo", heading: "To Do", color: "#3b82f6",
      cards: [
        { id: "1", title: "Auth flow", description: "OAuth2 implementation", tags: ["backend"], priority: "high" }
      ]
    },
    { id: "doing", heading: "In Progress", color: "#eab308", cards: [] },
    { id: "done", heading: "Done", color: "#22c55e", cards: [] }
  ]
}
```

**Interaction**: HTML5 drag-and-drop. Priority colours: low=green, medium=yellow, high=red, critical=pulsing red.

**Returns**: `{ columns: [{ id, cardIds: string[] }] }`

#### grip_inbox

Quick-capture widget for thoughts, links, and notes.

```typescript
{
  title: "Inbox",
  items: [
    { id: "1", content: "Research auth approaches", tags: ["research"], priority: "high", timestamp: "2026-02-09T10:00:00Z" }
  ],
  tags: ["research", "bug", "idea", "followup"]
}
```

**Interaction**: Text input + Add button, tag selector, priority toggle, delete items.

**Returns**: Full items array on add/delete.

#### grip_retrospective

Configurable retro board with voting. Default: Start/Stop/Continue.

```typescript
{
  title: "Sprint 12 Retro",
  columns: [
    { id: "start", heading: "Start", color: "#22c55e", icon: "+", items: [] },
    { id: "stop", heading: "Stop", color: "#ef4444", icon: "-", items: [] },
    { id: "continue", heading: "Continue", color: "#3b82f6", icon: "=", items: [] }
  ]
}
```

**Interaction**: Add items via text input per column. Upvote button on each item. Sorted by votes.

**Returns**: `{ columns: [{ id, items: [{ id, text, votes }] }] }`

### Data Display Tools

#### grip_data_table

Paginated, sortable, filterable data table.

```typescript
{
  title: "Session History",
  columns: [
    { key: "date", label: "Date", sortable: true, width: "120px" },
    { key: "task", label: "Task", sortable: true },
    { key: "status", label: "Status", sortable: true, width: "100px" }
  ],
  rows: [
    { date: "2026-02-09", task: "MCP expansion", status: "Complete" }
  ],
  pageSize: 10,
  searchable: true
}
```

**Interaction**: Search bar filters all columns. Click headers to sort. Pagination controls.

**Returns**: `{ sortColumn, sortDirection, searchQuery, currentPage }`

#### grip_digest

Expandable info cards for summaries and briefings.

```typescript
{
  title: "Session Recap",
  cards: [
    {
      id: "1", title: "MCP Apps Expansion", summary: "19 new components built",
      detail: "Full implementation details...", icon: "🚀", status: "success",
      actions: [{ id: "review", label: "Review PR" }]
    }
  ]
}
```

**Interaction**: Click cards to expand detail. Status border: info=blue, success=green, warning=yellow, error=red.

**Returns**: `{ cardId, actionId }` on action click.

#### grip_progress

Visual progress display with 3 styles: bar, ring, dots.

```typescript
{
  title: "Sprint Progress",
  tracks: [
    {
      id: "features", label: "Features", current: 7, total: 10, color: "#3b82f6",
      milestones: [{ at: 5, label: "MVP" }, { at: 10, label: "Release" }]
    }
  ],
  style: "bar"  // "bar" | "ring" | "dots"
}
```

**Display only** - no interaction, no postMessage. Animates on render.

#### grip_timeline

Horizontal timeline for milestones, phases, and deadlines.

```typescript
{
  title: "Project Roadmap",
  events: [
    { id: "1", label: "Kickoff", date: "2026-01-15", type: "milestone", status: "complete" },
    { id: "2", label: "Development", date: "2026-01-20", endDate: "2026-03-01", type: "phase", status: "active" },
    { id: "3", label: "Launch", date: "2026-03-15", type: "deadline", status: "upcoming" }
  ],
  todayMarker: true
}
```

**Shapes**: milestone=dot, phase=bar, deadline=diamond. **Status colours**: complete=green, active=blue, upcoming=grey, overdue=red.

**Returns**: `{ selectedEvent: id }` on click.

### Reflection Tools

#### grip_journal

Structured reflection with mood selector and prompts.

```typescript
{
  title: "Daily Standup",
  prompts: [
    { id: "done", label: "What did I accomplish?", placeholder: "Completed...", icon: "✓" },
    { id: "blockers", label: "What blocked me?", placeholder: "Issues...", icon: "!" },
    { id: "next", label: "What's next?", placeholder: "Planning to...", icon: "→" }
  ],
  moodSelector: true
}
```

**Interaction**: Textareas per prompt. Optional mood selector (5 faces). Submit button.

**Returns**: `{ entries: { [promptId]: text }, mood?: string, timestamp }`

#### grip_concept_map

Canvas-based node-link diagram with force-directed layout.

```typescript
{
  title: "System Architecture",
  nodes: [
    { id: "api", label: "API Gateway", type: "service" },
    { id: "db", label: "Database", type: "storage" },
    { id: "cache", label: "Redis", type: "storage" }
  ],
  edges: [
    { from: "api", to: "db", label: "queries", style: "solid" },
    { from: "api", to: "cache", label: "reads", style: "dashed" }
  ]
}
```

**Interaction**: Zoom via scroll, pan via drag. Click node to highlight connected edges. Force-directed layout if no x/y provided.

**Returns**: `{ selectedNode: id, connectedNodes: string[] }`

### Mode-Specific Tools

#### grip_threat_matrix (security mode)

STRIDE threat model with DREAD sub-scoring per entry point.

```typescript
{
  title: "API Security Assessment",
  entryPoints: [
    { id: "auth", label: "/api/auth", description: "Authentication endpoint" },
    { id: "upload", label: "/api/upload", description: "File upload endpoint" }
  ]
}
```

**Interaction**: 6 STRIDE columns (S/T/R/I/D/E) as toggle cells. DREAD scoring popup (5 sliders, 1-3 each) on applicable cells. Auto-computed priority (5-15). Colour bands: red (12-15), amber (8-11), green (5-7).

**Returns**: Full threat model JSON with STRIDE applicability, DREAD scores, and mitigations.

#### grip_scoring_rubric (research/review mode)

Multi-criteria quality assessment with tier classification. For CRAAP scoring, review severity, or QA gates.

```typescript
{
  title: "Source Evaluation",
  subject: "Technical Blog Post on WebSockets",
  criteria: [
    { id: "currency", label: "Currency", description: "How recent?", scale: 5, labels: ["Outdated", "Old", "Acceptable", "Recent", "Current"] },
    { id: "relevance", label: "Relevance", scale: 5 },
    { id: "authority", label: "Authority", scale: 5 },
    { id: "accuracy", label: "Accuracy", scale: 5 },
    { id: "purpose", label: "Purpose", scale: 5 }
  ],
  tiers: [
    { label: "Excellent", minScore: 20, color: "#22c55e" },
    { label: "Good", minScore: 15, color: "#3b82f6" },
    { label: "Borderline", minScore: 10, color: "#eab308" },
    { label: "Poor", minScore: 0, color: "#ef4444" }
  ]
}
```

**Returns**: `{ scores: Record<id, number>, composite, tier, notes }`

#### grip_idea_canvas (brainstorm mode)

Spatial idea generation with SCAMPER technique buttons, clustering, and convergence voting.

```typescript
{
  title: "Feature Brainstorm",
  phase: "diverge",  // "diverge" | "cluster" | "converge" | "develop"
  ideas: [],
  clusters: [],
  scamperEnabled: true
}
```

**Interaction**:
- **Diverge**: Rapid-entry input (type + Enter), SCAMPER prompt buttons
- **Cluster**: Drag cards onto each other to form named clusters
- **Converge**: Star toggle on each card for selection
- **Develop**: Expand cards to add feasibility, effort, next-steps

**Returns**: Full canvas state (ideas, clusters, selections).

#### grip_circuit_dashboard (automation mode)

Safety monitoring dashboard for autonomous operations.

```typescript
{
  title: "Automation Monitor",
  circuitBreakers: [
    { id: "api", label: "API Calls", state: "closed", triggerCount: 0 }
  ],
  rateLimits: [
    { id: "api_rate", label: "API calls/hr", consumed: 47, limit: 100, unit: "calls" }
  ],
  checkpoints: [
    { id: "cp1", label: "Pre-refactor", timestamp: "2026-02-09T10:00:00Z" }
  ],
  operationCount: 142,
  errorCount: 3
}
```

**Interaction**: Circuit breaker state cards (closed=green, half-open=amber, open=red). Rate limit gauges. Restore buttons on checkpoints. Emergency stop button.

**Returns**: `{ action: 'restore'|'emergency_stop', checkpointId? }`

#### grip_review_annotator (review/code mode)

Split-view artifact + annotation panel with severity classification.

```typescript
{
  title: "Code Review: auth.ts",
  artifact: {
    type: "code",
    content: "function login(user, pass) { ... }",
    language: "typescript"
  },
  annotations: []
}
```

**Interaction**: Left panel shows artifact with line numbers. Right panel for adding annotations. Types: issue (with severity), strength, question. Grouped by severity with counts.

**Returns**: Structured review JSON with all annotations.

#### grip_gap_visualiser (strategy/learning mode)

Current-vs-desired state comparison with proportional gap bars.

```typescript
{
  title: "Skills Gap Analysis",
  mode: "quantitative",
  dimensions: [
    { id: "ts", label: "TypeScript", current: 7, desired: 9 },
    { id: "k8s", label: "Kubernetes", current: 3, desired: 8 },
    { id: "sql", label: "SQL", current: 6, desired: 7 }
  ],
  scale: { min: 0, max: 10 }
}
```

**Modes**: `quantitative` (sliders with gap bars) or `qualitative` (text fields with severity selector).

**Gap colours**: green (small), amber (medium), red (large). Auto-sorted by largest gap. Aggregate score at top.

**Returns**: Full gap analysis with dimensions, scores, and priorities.

## Quick Reference

| Tool | Category | Interactive | Mode |
|------|----------|-------------|------|
| `grip_options_picker` | Decision | Yes | All |
| `grip_confirmation` | Decision | Yes | All |
| `grip_decision_matrix` | Decision | Yes | All |
| `grip_checklist` | Workflow | Yes | All |
| `grip_wizard` | Workflow | Yes | All |
| `grip_phase_stepper` | Workflow | Yes | analysis, writing, synthesis, learning |
| `grip_kanban` | Management | Yes | All |
| `grip_inbox` | Management | Yes | All |
| `grip_retrospective` | Management | Yes | All |
| `grip_data_table` | Data | Yes | All |
| `grip_digest` | Data | Yes | All |
| `grip_progress` | Data | No | All |
| `grip_timeline` | Data | Yes | All |
| `grip_journal` | Reflection | Yes | All |
| `grip_concept_map` | Reflection | Yes | All |
| `grip_threat_matrix` | Mode | Yes | security |
| `grip_scoring_rubric` | Mode | Yes | research, review |
| `grip_idea_canvas` | Mode | Yes | brainstorm |
| `grip_circuit_dashboard` | Mode | Yes | automation |
| `grip_review_annotator` | Mode | Yes | review, code |
| `grip_gap_visualiser` | Mode | Yes | strategy, learning |

## Design Tokens

```css
:root {
  --bg-primary: #0a0a0a;
  --bg-secondary: #1a1a1a;
  --bg-hover: #2a2a2a;
  --accent-blue: #3b82f6;
  --accent-green: #22c55e;
  --accent-red: #ef4444;
  --accent-yellow: #eab308;
  --accent-purple: #a855f7;
  --text-primary: #ffffff;
  --text-secondary: #a1a1aa;
  --text-muted: #71717a;
  --border-color: #27272a;
  --border-radius: 12px;
  --border-radius-sm: 8px;
  --font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  --font-mono: 'SF Mono', 'Fira Code', monospace;
  --shadow: 0 4px 6px -1px rgba(0,0,0,0.3);
  --transition: 150ms ease;
}
```

## Development

### Testing Components

```bash
open ~/.claude/mcp-servers/grip-apps/ui/checklist/index.html?demo
open ~/.claude/mcp-servers/grip-apps/ui/kanban/index.html?demo
open ~/.claude/mcp-servers/grip-apps/ui/threat-matrix/index.html?demo
```

### Adding New Components

1. Create tool definition: `src/tools/{name}.ts` (export `{name}Tool` + `{Name}Input` type)
2. Create UI: `ui/{name}/index.html` (single file, embedded CSS/JS, MCPApp class, `?demo` support)
3. Add one line to `toolRegistry` in `src/index.ts`
4. Build: `npm run build`

### Installation

```bash
cd ~/.claude/mcp-servers/grip-apps
npm install && npm run build
```

Register in Claude Code settings or Claude Desktop config:

```json
{
  "mcpServers": {
    "grip-apps": {
      "command": "node",
      "args": ["$HOME/.claude/mcp-servers/grip-apps/dist/index.js"]
    }
  }
}
```

## Related Skills

- `asking-users` - PARAMOUNT skill for user confirmation
- `ui-complete` - Unified UI development principles
- `design-principles` - SOLID, GRASP, DRY, KISS, YAGNI

## Changelog

| Version | Changes |
|---------|---------|
| 2.0.0 | Add 19 components (checklist, kanban, decision-matrix, wizard, inbox, data-table, digest, progress, journal, concept-map, timeline, retrospective, threat-matrix, phase-stepper, scoring-rubric, idea-canvas, circuit-dashboard, review-annotator, gap-visualiser). Refactor index.ts to data-driven registry. |
| 1.0.0 | Initial creation with Options Picker + Confirmation |
