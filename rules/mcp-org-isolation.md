---
paths:
  - "**/.mcp.json"
  - "**/*mcp*.json"
  - "scripts/mcp-generate.py"
---

# MCP Org Isolation (PARAMOUNT)

Origin: Gen 486. V>> flagged ~35k tokens of context bloat from a client's MCP
servers loading in CodeTonight sessions.

## The Rule

`~/.claude/.mcp.json` (GRIP project config) MUST contain only **universal
GRIP servers** — tools that every org and every user needs. Org-specific
MCP servers belong in `organizations/<org>/.mcp.json`.

## Universal Servers (allowed in `.mcp.json`)

| Server | Purpose |
|--------|---------|
| grip-channel | WhatsApp/Slack/Discord/Web bridge |
| grip-apps | GRIP Emergent UI components |
| code-index | AST-based symbol search |

## Org-Specific Servers (MUST NOT be in `.mcp.json`)

| Server | Org | Tokens |
|--------|-----|--------|
| postgres | client-a | ~500 |
| mcp-atlassian | client-a | ~24,566 |
| zendesk | client-a | ~500 |
| client-a-slack | client-a | ~989 |

## Claude Code's Three-Layer MCP Config

Claude Code merges MCP servers from three sources (later layers override earlier):

| Layer | File | Scope | Write method |
|-------|------|-------|--------------|
| User | `~/.claude.json` top-level `mcpServers` | All projects | `claude mcp add --scope user` |
| Local | `~/.claude.json` `projects[path].mcpServers` | One project | `claude mcp add` (default) |
| Project | `<cwd>/.mcp.json` | Team-shared | `claude mcp add --scope project` |

`~/.claude/.mcp.json` is the project-scoped config for the GRIP repo — it
loads when cwd is `~/.claude/`, not globally. `organizations/<org>/.mcp.json`
files are reference templates — Claude Code never reads them directly.

**WARNING**: Never place `.mcp.json` at `~/` (HOME root). It makes Claude Code
treat the entire home directory as a project, causing full filesystem traversal.
Use `claude mcp add --scope user` for machine-wide servers.

## How to Use Org MCP Servers

Org-specific servers are stored in `organizations/<org>/.mcp.json` as
reference templates. To activate them:

1. **For one project**: Copy to that project's `.mcp.json`
   (e.g., `~/<client-repo>/.mcp.json`)
2. **For all projects on your machine**: Register at user scope:
   `claude mcp add --scope user <server>`
3. **For GRIP sessions only**: Use `scripts/mcp-generate.py <org>`
   (merges into `~/.claude/.mcp.json` with `--skip-worktree` protection)

GRIP universal servers (grip-channel, code-index, grip-apps) can live at
any layer. `grip-doctor` checks all layers before reporting missing servers.

## Enforcement

The regression test `tests/test_mcp_org_isolation.py` validates that:
- `.mcp.json` contains no org-specific servers
- `.mcp.json.example` / `.mcp.json.template` (if they exist) contain no
  org-specific servers
- Universal servers are present in `.mcp.json`

Origin of extended scanning: Gen 500+ RCA. PR #1143 introduced
`.mcp.json.example` with a client's servers into upstream root, exposing
org-specific tooling to all GRIP forks. The file was deleted; the test
now prevents recurrence.

## Why

- 4 client MCP servers added ~35k tokens of tool definitions to EVERY session
- mcp-atlassian alone: 72 tools, ~24,566 tokens
- Users in other orgs had irrelevant tools polluting their context
- Missing env var warnings created noise in `/doctor` diagnostics
