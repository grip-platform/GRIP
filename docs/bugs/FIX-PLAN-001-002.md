# Fix Plan: BUG-001 + BUG-002

**Author**: V>> (via Claude Web session, 9 Feb 2026)
**Bugs**: BUG-001 (MCP UI not rendered), BUG-002 (Mode switching unavailable)
**Common Root Cause**: GRIP assumes filesystem access. Web/Mobile only has MCP.
**Amended**: 2026-02-09 — Revised Phase 1 after discovering claude.ai renders first-party tools natively.

---

## Diagnosis

Both bugs stem from the same architectural assumption:

```
GRIP v6.0.0 was built for Claude Code.
Claude Code has: bash, filesystem, ~/.claude/, mode-loader.sh
Claude Web/Mobile has: MCP tool calls only.
```

The MCP server (grip-apps) exposes 21 UI tools but nothing else. No mode management. No skill loading. No session state. No fallback rendering.

Critical correction: Claude.ai DOES render interactive UI — but only for first-party Anthropic tools (`ask_user_input_v0`, `chart_display_v0`, `places_map_display_v0`, `message_compose_v1`) and artifact types (`.html`, `.jsx`, `.md`, `.svg`, `.mermaid`). Third-party MCP `_meta.ui` is not supported. The fix should proxy through native tools and artifacts, not build a parallel rendering system.

---

## Execution Order

| Phase | Bug Fixed | Effort | Priority |
|-------|-----------|--------|----------|
| 3 (Bootstrap) | Partial 002 | 0.5 days | Do first — unblocks everything |
| 2 (Mode MCP) | 002 fully | 2-3 days | Do second — highest user impact |
| 1 (UI Proxy) | 001 fully | 3-4 days | Do third — most work, clear workaround exists |

Total: ~6-7.5 days of development.

---

## Phase 3: Auto-Bootstrap Protocol (DO FIRST)

Problem: On Claude Code, GRIP bootstraps via CLAUDE.md load. On Web/Mobile, nothing happens automatically.

Fix: Enhance existing `grip_bootstrap` tool + add auto-trigger instruction.

The `grip_bootstrap` tool already exists. The bug is it's never called automatically on Web/Mobile.

1. Add to user preferences: "When GRIP MCP tools are available, call grip_bootstrap at conversation start"
2. Enhance `grip_bootstrap` to auto-detect modes, load foundation skills, return CLAUDE.md content
3. "GRIP LOAD" manual trigger already documented

Estimate: 0.5 days

---

## Phase 2: Mode Management via MCP (DO SECOND)

Problem: Modes require mode-loader.sh + filesystem. Web/Mobile has neither.

Fix: Add mode management tools to the MCP server.

### New MCP Tools

| Tool | Purpose |
|------|---------|
| `grip_mode_activate` | Activate 1-3 modes, return skill list + token budget + skill content |
| `grip_mode_status` | Return current active modes |
| `grip_mode_deactivate` | Deactivate all modes |
| `grip_mode_list` | List available modes with descriptions |

### Skill Content Delivery

When a mode activates, its skills must be injected into the AI's context. On MCP, the `grip_mode_activate` response returns the full text of all loaded skills.

Implementation: Bundle skills into MCP server at build time. `scripts/bundle-skills.ts` reads all `skills/*.md` and `modes/definitions/*.yaml`, outputs `dist/skill-bundle.json`. Mode activation reads from the bundle.

### Mode State

Ephemeral per-session (in-memory on MCP server). Acceptable because:
- Web/Mobile sessions are shorter
- Mode activation is fast enough to repeat
- Don't over-engineer persistence

### Scope

- 1 build script (skill bundler)
- 1 module (mode manager)
- 3-4 new MCP tools
- 1 new skill (mcp-mode-protocol)
- Port mode YAML parsing from bash to TypeScript

Estimate: 2-3 days

---

## Phase 1: Native Tool Proxy (DO THIRD)

Problem: Tools return JSON. Third-party `_meta.ui` iframes don't render. Operator sees nothing.

Fix: On Web/Mobile, the AI uses GRIP MCP tools for data/logic, then renders the output through claude.ai's native rendering capabilities.

### Two rendering paths:

**Path A: Direct native tool substitution**

Some GRIP tools map 1:1 to Anthropic native tools:

| GRIP Tool | Native Replacement |
|---|---|
| `grip_options_picker` | `ask_user_input_v0` |
| `grip_confirmation` | `ask_user_input_v0` (confirm/cancel as options) |
| `grip_progress` | `chart_display_v0` (bar chart) |

For these, the AI can skip the GRIP tool entirely on Web/Mobile and use the native tool directly, OR call the GRIP tool for data then render via native tool.

**Path B: Artifact rendering**

Complex GRIP tools render as inline artifacts:

| GRIP Tool | Artifact Type |
|---|---|
| `grip_decision_matrix` | `.html` with styled table |
| `grip_kanban` | `.jsx` React component |
| `grip_concept_map` | `.html` with SVG |
| `grip_timeline` | `.html` |
| `grip_data_table` | `.html` |
| `grip_idea_canvas` | `.jsx` React component |
| `grip_wizard` | `.jsx` React component (stateful) |
| `grip_checklist` | `.html` |
| `grip_phase_stepper` | `.html` |
| `grip_journal` | `.md` |
| `grip_retrospective` | `.html` |
| `grip_scoring_rubric` | `.html` |
| `grip_digest` | `.md` |
| `grip_inbox` | `.html` |
| `grip_threat_matrix` | `.html` |
| `grip_review_annotator` | `.html` |
| `grip_gap_visualiser` | `.html` + `chart_display_v0` |
| `grip_circuit_dashboard` | `.html` |

### Server-side implementation:

1. Add surface detection (check for `_meta.ui` support in client metadata)
2. When surface = Web/Mobile, include `artifact_template` in tool response — a pre-built HTML/JSX string the AI can save as an artifact
3. Include a `render_hint` field: `"native_tool"`, `"html_artifact"`, `"jsx_artifact"`, or `"markdown_artifact"`

```typescript
// Example tool response for Web/Mobile surface
{
  data: { /* structured matrix data */ },
  render_hint: "html_artifact",
  artifact_template: "<html><!-- self-contained decision matrix with inline CSS -->...</html>"
}
```

### AI-side (new skill: mcp-surface-protocol):

```
## Surface-Aware Rendering (PARAMOUNT)

When operating on Claude Web or Claude Mobile:
1. Call the GRIP MCP tool for structured data
2. Check the render_hint in the response
3. If native_tool: use the mapped Anthropic tool (ask_user_input_v0, chart_display_v0)
4. If html_artifact: save the artifact_template as .html, present to operator
5. If jsx_artifact: save as .jsx, present to operator
6. If markdown_artifact: save as .md, present to operator
7. If no render_hint: generate HTML from the JSON data manually
8. NEVER claim the operator saw a GRIP tool output without rendering it through a supported channel
```

Claude Code (no change): `_meta.ui` iframe rendering continues to work as-is.

### Scope

- Server: 1 surface detection utility + artifact_template generators per tool (21 tools)
- Skills: 1 new skill (mcp-surface-protocol)
- AI behaviour: follow skill instructions on Web/Mobile
- Existing `ui/` HTML templates can be adapted as artifact templates

Estimate: 3-4 days

---

## Interim Workarounds (Use Now)

### BUG-001 Workaround

On Web/Mobile, after calling any GRIP MCP UI tool:

1. Read the returned JSON data
2. Generate an `.html` or `.jsx` artifact from the data
3. Save and present — renders inline in conversation

### BUG-002 Workaround

On Web/Mobile mode switch request:

1. Call `grip_load_skill` for each skill the mode requires
2. Manually adhere to loaded skill instructions
3. Be transparent: "Mode rules loaded via skill files, not mechanically enforced"

---

## Success Criteria

- [ ] Operator on Claude Web can call grip_decision_matrix and see a rendered visual (inline artifact)
- [ ] Operator on Claude Web can type /mode code and get mode-enforced behaviour
- [ ] grip_bootstrap fires automatically on new Web/Mobile conversations with GRIP MCP connected
- [ ] All 25 modes work on Web/Mobile with skill content injected
- [ ] All 21 UI tools have native proxy rendering for Web/Mobile
- [ ] No false claims of mode activation
- [ ] grip_options_picker on Web uses ask_user_input_v0 natively

---

## Open Questions

1. **Artifact vs present_files**: Artifacts render inline (better UX) but cost tokens. present_files opens in a panel. Recommendation: artifacts for interactive tools, present_files for large data exports.
2. **Token budget for skill injection**: Full skill text in grip_mode_activate response could be 3-8k tokens. May need compact skill format for mobile.
3. **MCP _meta.ui support timeline**: If Anthropic ships third-party UI rendering for Web/Mobile, Phase 1 becomes optional. Build it anyway — can't wait on platform.
4. **Git identity on MCP server**: grip_git_commit fails from Web because no user.email configured. Configure you@example.com on the MCP server's git config.
