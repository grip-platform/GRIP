---
name: mcp-surface-protocol
description: Surface-aware rendering protocol for Web/Mobile. Instructs AI to proxy GRIP MCP tool output through native Anthropic tools and artifacts when on surfaces without iframe support.
status: Active
effort: medium
version: 2.0.0
priority: PARAMOUNT
triggers:
  - grip_bootstrap returns surface_hint = mcp_only
  - Any GRIP MCP tool called on Web/Mobile
  - User on Claude Web or Claude Mobile with GRIP MCP connected
integrates:
  - asking-users
  - creating-wizards
  - hitl
  - context-gate
  - concise-communication
---

# MCP Surface Protocol

**PARAMOUNT**: When `surface_hint: "mcp_only"` is present in grip_bootstrap response, this protocol is active.

## Surface Detection

After calling `grip_bootstrap`, check the `surface_hint` field:

| Value | Meaning | Action |
|-------|---------|--------|
| `mcp_only` | Web/Mobile, no bash/filesystem | Follow this protocol |
| absent | Claude Code (full access) | Standard GRIP behavior |

## Core Rules

### 1. Never Claim Invisible Output Was Seen

GRIP MCP UI tools return JSON data. On Web/Mobile, the operator **cannot see** the raw tool response. You MUST render output through a supported channel before referencing it.

### 2. Dual-Content-Block Auto-Rendering (v2.0)

GRIP MCP tools now use a **dual-content-block** response format. No parameter required.

Every UI tool response contains TWO content blocks:
- **Block 1**: Raw JSON data (for `_meta.ui` native rendering on Claude Code)
- **Block 2**: Compact artifact HTML/markdown with creation instruction (for Web/Mobile)

**On Web/Mobile**: Block 2 contains a self-contained artifact. Create an HTML artifact from it.
**On Claude Code**: Block 1 feeds native widgets. Block 2 is in context but can be ignored.

The `include_artifact: true` parameter is still supported for backward compatibility. When set, the response contains ONLY the artifact content (single block, no raw JSON).

### 3. Rendering Protocol

Tool responses contain artifact content in Block 2. Identify the format:

| Block 2 starts with | Format | Action |
|---------------------|--------|--------|
| `[GRIP Interactive Visualization: ...]` | HTML artifact | Extract HTML between `---BEGIN_GRIP_HTML_ARTIFACT---` and `---END_GRIP_HTML_ARTIFACT---`, create HTML artifact |
| `[GRIP Content: ...]` | Markdown | Present the markdown content to the user |
| JSON with `native_tool` field | Native tool | Map to the Anthropic tool specified (unchanged from v1) |

### 4. Native Tool Mapping

These GRIP tools map directly to Anthropic first-party tools:

| GRIP Tool | Native Tool | Response Fields |
|-----------|-------------|-----------------|
| `grip_options_picker` | `ask_user_input_v0` | `title`, `options`, `multiSelect` |
| `grip_confirmation` | `ask_user_input_v0` | `title`, `description`, confirm/cancel options |
| `grip_progress` | `chart_display_v0` | `title`, `chart_type: "bar"`, `data` array |

When `render_hint: "native_tool"`, read the `native_tool` field and map the response data to that Anthropic tool's parameters.

### 5. HTML Artifact Rendering

When Block 2 starts with `[GRIP Interactive Visualization: ...]`, the HTML is between markers:

```
---BEGIN_GRIP_HTML_ARTIFACT---
<!DOCTYPE html>...(self-contained HTML with inline CSS/JS)...
---END_GRIP_HTML_ARTIFACT---
```

The HTML is a compact, self-contained page:
- All CSS and JS are inline (no external dependencies)
- Data is embedded directly (concept-map renders as SVG, others as styled JSON)
- No external fetches required

**To render**: Extract the HTML between the BEGIN/END markers and create an HTML artifact with it. Use the title from the `Title the artifact:` line.

Tools that return HTML artifacts: `grip_checklist`, `grip_kanban`, `grip_decision_matrix`, `grip_wizard`, `grip_inbox`, `grip_data_table`, `grip_concept_map`, `grip_timeline`, `grip_retrospective`, `grip_threat_matrix`, `grip_phase_stepper`, `grip_scoring_rubric`, `grip_idea_canvas`, `grip_circuit_dashboard`, `grip_review_annotator`, `grip_gap_visualiser`, `grip_dashboard`, `grip_mode_switcher`, `grip_skill_browser`.

### 6. Markdown Content Rendering

When Block 2 starts with `[GRIP Content: ...]`, the markdown follows directly:

- `grip_journal`: Title, mood prompt, reflection prompts with placeholders
- `grip_digest`: Title, cards with summaries, details, and status badges

**To render**: Present the markdown content to the user directly.

### 7. Interactive State Flow

HTML artifacts are fully interactive. When the user interacts with the artifact:

1. User interacts (check, select, drag, type)
2. Template JS updates internal state
3. State appears in the bottom panel
4. User clicks "Copy State" button
5. User pastes state into their next message
6. AI processes the updated state

### 8. Sequential Artifact Rendering (PARAMOUNT)

Claude Web/Mobile can only display ONE artifact at a time. Creating a new artifact replaces the previous one.

**Rules**:
1. **One UI tool per response**: Never call more than one GRIP UI tool that produces `html_artifact` or `markdown_artifact`
2. **Pause and present**: After creating an artifact, describe it briefly and STOP
3. **Queue indicator**: Show "Rendering 1 of N: [Name]. Queued: [Next, ...]"
4. **Wait for signal**: Only render the next artifact after the user responds
5. **Combine text freely**: Text commentary alongside ONE artifact is fine

**Server hint**: Each artifact block ends with a sequential render instruction. Follow it.

### 9. No Bash/Filesystem Available

On `mcp_only` surfaces:
- No `~/.claude` directory exists
- No bash access
- No filesystem persistence
- Skills are only available via MCP tool responses
- Mode switching via `grip_mode_activate` (when available)

### 10. Mode Switching

Use `grip_mode_activate` for mode switching:
1. Call `grip_mode_activate` with mode name(s) (max 3)
2. Response includes full skill content and prompt injections
3. Follow loaded skill instructions and prompt injections
4. Be transparent: "Mode rules loaded via MCP, not mechanically enforced via hooks"

See `mcp-mode-protocol` skill for full protocol.

## Workflow

```
1. Session starts on Web/Mobile with GRIP MCP connected
2. Call grip_bootstrap (auto or on user request)
3. Receive surface_hint: "mcp_only" + foundation skills
4. Follow foundation skill rules (asking-users, context-gate, concise-communication)
5. When user requests a GRIP UI tool:
   a. Call the GRIP MCP tool (no special parameters needed)
   b. Response has 2 content blocks:
      Block 1: Raw JSON data (ignore on web)
      Block 2: Artifact content with creation instruction
   c. If Block 2 has BEGIN/END markers -> create HTML artifact
   d. If Block 2 has [GRIP Content] -> present markdown
   e. If native tool (options-picker, confirmation, progress) -> no Block 2, handle normally
   f. Present rendered output to operator
6. When user requests mode switch:
   a. Call grip_mode_activate with mode name(s)
   b. Follow returned prompt injections and skill content
```
