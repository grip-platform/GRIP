---
name: mcp-elicitation
description: MCP servers requesting user input mid-execution via the `elicitation/create` protocol. Enables stateful multi-step wizards through grip-channel, grip-apps, grip-pair. Documents the pattern, the JSON-RPC surface, the three-action response model, and GRIP-specific integration checkpoints.
status: Active
effort: medium
version: 1.0.0
triggers:
  - mcp elicitation
  - elicitation/create
  - mcp wizard
  - multi-step mcp
integrates:
  - asking-users
  - hitl
  - creating-wizards
  - creating-form-input
  - grip-channel
  - mcp-apps
  - recall
---

# MCP Elicitation

MCP elicitation lets a server **pause** mid-execution and ask the client for structured user input via `elicitation/create`. The client renders a form from the server's JSON schema, collects a response, and returns one of three actions (`accept` / `decline` / `cancel`). The server then continues processing with the new information.

It is the protocol-level answer to: "a tool call fired but I need one more thing from the user before I can finish."

## When to Use

| Scenario | Why elicitation fits |
|---|---|
| Dynamic parameter gathering | Tool discovers it needs a value not in the original call (e.g. which repo from a list of 12) |
| Multi-step wizard flows | A workflow has branching questions whose next question depends on the previous answer |
| Missing-context recovery | Tool started, hit ambiguity (two records match "john"), needs disambiguation before proceeding |
| Confirmation before destruction | `delete_all`, `force_push`, `send_broadcast` — server insists on explicit accept |
| Form-based data capture | Server needs a structured object (name + email + role) that doesn't fit tool-argument shape |

## When NOT to Use

| Scenario | Use instead |
|---|---|
| Sensitive information (password, secret, PII) | OAuth / keychain / environment — **spec: MUST NOT** elicit secrets |
| Simple Yes/No that fits in tool args | Make it a required boolean parameter |
| Information the server already has | Persist it (keychain, session state) — don't re-ask |
| High-frequency prompts (every call) | Cache the answer for the session |
| Synchronous chat fallback | Use the normal assistant response channel |

## Protocol Surface

### Capability declaration (client side)

Clients that support elicitation **MUST** declare it during initialisation:

```json
{ "capabilities": { "elicitation": {} } }
```

Servers discover support from the client's capability set and silently fall back to prompting via tool arguments or assistant text if absent.

### Request (server → client)

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "elicitation/create",
  "params": {
    "message": "Which repository should I push to?",
    "requestedSchema": {
      "type": "object",
      "properties": {
        "repo": {
          "type": "string",
          "title": "Repository",
          "enum": ["grip-platform/GRIP", "grip-platform/GRIP-GUI"],
          "enumNames": ["GRIP (runtime)", "GRIP-GUI (electron app)"]
        }
      },
      "required": ["repo"]
    }
  }
}
```

### Response (client → server)

Three actions, verbatim from spec:

| Action | Meaning | `content` |
|---|---|---|
| `accept` | User explicitly submitted | Present — matches `requestedSchema` |
| `decline` | User explicitly declined | Typically omitted — server should offer alternative path |
| `cancel` | User dismissed (Esc, outside click) | Typically omitted — server should treat as "not now, maybe later" |

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "action": "accept",
    "content": { "repo": "grip-platform/GRIP-GUI" }
  }
}
```

### Supported schema primitives

Flat objects only — **no nested objects, no arrays-of-objects**. Deliberate simplicity so clients can auto-render forms.

| Type | Extras |
|---|---|
| `string` | `minLength`, `maxLength`, `format` ∈ {`email`, `uri`, `date`, `date-time`} |
| `number` / `integer` | `minimum`, `maximum` |
| `boolean` | `default` |
| `string` with `enum` + `enumNames` | Parallel arrays for machine values vs display labels |

Every property should carry `title` and `description` — clients use them as form labels and helper text.

## GRIP Integration Patterns

### Pattern 1 — grip-channel disambiguation

**Scenario**: WhatsApp message arrives, `channel_reply` tool fires, but two contacts match "mark". Without elicitation, the server has to decline or guess. With elicitation:

```
WhatsApp: "send mark the invoice"
  ↓
channel_reply called → finds 2 matches
  ↓
server.elicitation/create({
  message: "Two contacts match 'mark' — which one?",
  requestedSchema: {
    properties: { contact: { type: 'string', enum: [jid1, jid2], enumNames: ['Mark Griffioen (+27...)', 'Mark Johnson (+44...)'] } },
    required: ['contact']
  }
})
  ↓
User picks → server resumes → invoice sent
```

### Pattern 2 — grip-apps destructive confirmation

**Scenario**: `grip_file_delete` fires from a Web surface. Destructive. Instead of relying on the UI layer to confirm, the MCP server itself elicits:

```
grip_file_delete({ path: '/cache/genome.json' })
  ↓
elicitation/create({
  message: "Deleting /cache/genome.json is irreversible. Confirm?",
  requestedSchema: { properties: { confirm: { type: 'boolean', default: false } }, required: ['confirm'] }
})
  ↓
action='accept' + confirm=true  → delete
action='accept' + confirm=false → skip
action='decline' / 'cancel'     → skip
```

The `decline` and `cancel` paths must be treated identically to `accept + confirm=false` — three distinct "no" signals.

### Pattern 3 — grip-pair device selection

**Scenario**: `grip_pair_steer` fires, but the operator has paired with 3 devices. The tool doesn't know which to steer. Elicits an enum of device IDs + human names.

### Pattern 4 — wizard cascade (branching)

**Scenario**: Onboarding new GRIP operator. Q1 asks role (founder / engineer / PM / other). Q2 depends on Q1. Without elicitation, this is one giant form or a sequence of tool calls. With elicitation, the server fires `elicitation/create` repeatedly, each schema shaped by the previous answer. Keep the total to ≤ 4 prompts per session (spec-recommended rate limiting).

## Security Guardrails (from spec — enforce in GRIP)

1. **MUST NOT** request sensitive information (passwords, tokens, PII beyond what's already session-known).
2. **SHOULD** make clear which server is requesting — GRIP MCP servers include `[grip-channel]`, `[grip-apps]` prefixes in the `message`.
3. **SHOULD** validate user input server-side against the schema before acting — do not trust client validation alone.
4. **SHOULD** rate-limit — no more than 4 elicitations per tool-call chain; cache answers within the session.
5. **SHOULD** respect decline / cancel — never re-prompt in the same call.
6. **SHOULD** allow decline from any stage — even mid-wizard.

## Integration Checklist (per GRIP MCP server)

For each MCP server in `~/.claude/mcp-servers/` that should support elicitation:

- [ ] Check the client's declared capabilities at `initialize` — only elicit if `capabilities.elicitation` is present
- [ ] Implement an `elicitation/create` request sender (JSON-RPC `id` correlation matters; don't use the tool-call id)
- [ ] Handle all three response actions — never assume `accept`
- [ ] Cache answers per session (or per tool-call chain) to avoid redundant prompts
- [ ] Log every elicitation to the server's audit trail (timestamp + message + action — NOT the content, to avoid leaking user input)
- [ ] Fall back gracefully when elicitation capability is absent (assistant-text path)

## Testing

Without a real client that supports elicitation yet:

1. **Unit-level**: mock the client as a function that returns one of `{action: 'accept', content: {...}}` / `{action: 'decline'}` / `{action: 'cancel'}` and assert server branches.
2. **Integration-level** (when a supporting client ships): drive a real wizard through `grip-apps` and confirm the form renders matching the schema.
3. **Security test**: attempt an elicitation with a `password` field — assert the server refuses to send it per the MUST-NOT rule.

## Current GRIP Adoption

| MCP server | Elicitation support | Next step |
|---|---|---|
| grip-channel | Not yet | Pattern 1 (contact disambiguation) is the first target |
| grip-apps | Not yet | Pattern 2 (destructive confirmation) — highest safety leverage |
| grip-pair | Not yet | Pattern 3 (device selection) — smallest scope, good first build |
| code-index | N/A | Pure search, no user-facing branches |
| slack (built-in) | N/A | Third-party server — can't modify |

Claude Code's client-side elicitation support: **CONFIRMED PRESENT** (2026-04-18 W4 audit). GRIP's `settings.json` already declares hooks for `Elicitation` (2 handlers) and `ElicitationResult` (2 handlers), which means the CC client registers `capabilities.elicitation` during initialisation. Server implementations can rely on it; servers SHOULD still gracefully degrade to assistant text for third-party clients that might not.

The corresponding hook events (`Elicitation`, `ElicitationResult`) let the operator audit every elicitation round-trip — integrate server-side dispatches with these hooks so the audit trail is complete (see `docs/hooks-lifecycle-audit-2026-04-18.md`).

## Differences from `asking-users` (GRIP's existing pattern)

`asking-users` is the **main-agent** AskUserQuestion flow — it runs inside the model's turn, uses the Claude Code HITL tool, and blocks the conversation.

`mcp-elicitation` is the **MCP-server** flow — it runs inside a tool call, uses the JSON-RPC `elicitation/create` method, and blocks only that specific tool. The user sees it as a mid-tool prompt, not a new assistant turn.

Use `asking-users` for high-level decisions the model wants to make. Use `mcp-elicitation` when a tool realises it needs one more thing to finish.

## Origin

- Pattern sighted in `luongnv89/claude-howto` catalogue scan (2026-04-18 harness report).
- Spec: https://modelcontextprotocol.io/docs/concepts/elicitation
- Triggered by GRIP v2.1 harnessing cycle post-`/remotion-video-creation` v2.0.0.

## References

- MCP elicitation spec (authoritative)
- `rules/operational-principles.md` — User Authority rule (elicitation must not override user's gate denials)
- `skills/asking-users/SKILL.md` — sibling pattern at main-agent level
- `skills/creating-wizards/SKILL.md` — multi-step wizard design principles (applies here for Pattern 4 cascades)
