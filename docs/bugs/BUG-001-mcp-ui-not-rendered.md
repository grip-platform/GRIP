# BUG-001: MCP UI Components Not Rendered to Operator

**Severity**: High
**Surface**: Claude Web, Claude Mobile (iOS/Android)
**Discovered**: 2026-02-09 (GRIP monetisation brainstorm session)
**Status**: Open

## Description

When GRIP MCP tools (decision_matrix, concept_map, idea_canvas, phase_stepper, etc.) are called during a Claude Web/Mobile session, the tool returns JSON data but the interactive UI components do not render visually for the operator.

The operator sees nothing. The AI receives the JSON and uses it internally for reasoning, but the visual output — which is the entire value proposition of these tools — is invisible.

## Root Cause

Initial diagnosis was "iframes aren't supported on claude.ai." This is WRONG.

Claude.ai DOES render interactive UI — but only for first-party Anthropic tools with dedicated renderers hardcoded into the frontend:
- `ask_user_input_v0` -> interactive picker widget
- `chart_display_v0` -> inline chart
- `places_map_display_v0` -> map with markers
- `message_compose_v1` -> email/message composer

GRIP's MCP tools are third-party. They return `_meta.ui` with HTML expecting the client to spawn a sandboxed iframe. Claude.ai does not support `_meta.ui` iframe rendering for third-party MCP servers. The tool call executes, the server returns the JSON payload, but no iframe is spawned.

Key insight: The rendering gap is specifically for third-party MCP tool UIs. The client has a rendering pipeline — it just doesn't extend it to MCP servers.

## Impact

- 21 interactive tools are functionally invisible on Web/Mobile
- Operator cannot interact with decision matrices, kanban boards, wizards, etc.
- Session quality degrades silently — operator doesn't know what they're missing
- 9 Feb 2026 brainstorm session: 4 tool outputs the operator never saw

## Fix Strategy

Three options, ranked by quality:

### Option A: Proxy Through Native Tools (RECOMMENDED)

Use claude.ai's built-in first-party tools and artifact system as the rendering layer. GRIP MCP server stays as the data/logic layer; the AI renders outputs using native capabilities.

| GRIP MCP Tool | Native Proxy |
|---|---|
| `grip_options_picker` | `ask_user_input_v0` (direct replacement) |
| `grip_confirmation` | `ask_user_input_v0` (confirm/cancel as options) |
| `grip_progress` | `chart_display_v0` (bar chart) |
| `grip_decision_matrix` | `.html` artifact (rendered inline) |
| `grip_kanban` | `.jsx` React artifact |
| `grip_concept_map` | `.html` artifact with SVG |
| `grip_timeline` | `.html` artifact |
| `grip_data_table` | `.html` artifact |
| `grip_checklist` | `.html` artifact |
| `grip_phase_stepper` | `.html` artifact |
| `grip_journal` | `.md` artifact |
| `grip_retrospective` | `.html` artifact |
| `grip_scoring_rubric` | `.html` artifact |
| `grip_idea_canvas` | `.jsx` React artifact |
| `grip_digest` | `.md` artifact |
| `grip_inbox` | `.html` artifact |
| `grip_wizard` | `.jsx` React artifact (stateful) |
| `grip_threat_matrix` | `.html` artifact |
| `grip_review_annotator` | `.html` artifact |
| `grip_gap_visualiser` | `.html` artifact + `chart_display_v0` |
| `grip_circuit_dashboard` | `.html` artifact |

Protocol for AI on Web/Mobile:
1. Call GRIP MCP tool (for structured data + reasoning)
2. Read the returned JSON
3. Render via native tool or artifact (per mapping above)
4. NEVER claim the operator saw the GRIP tool output without rendering it natively

### Option B: HTML Fallback Files via present_files

Generate static HTML from the MCP tool response, save to `/mnt/user-data/outputs/`, present via `present_files`. Works but opens in a separate panel — not inline.

### Option C: Anthropic Ships Third-Party MCP UI Rendering

Out of our control. Monitor but don't wait.

## Workaround (Current)

On Web/Mobile, after calling any GRIP MCP UI tool:
1. Read the returned JSON data
2. Generate an `.html` or `.jsx` artifact from the data
3. Save and present — renders inline in conversation

## Fix Plan

See `docs/bugs/FIX-PLAN-001-002.md`
