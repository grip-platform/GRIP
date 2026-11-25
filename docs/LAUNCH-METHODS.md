# Launch Methods

All the ways to start a GRIP session, from zero-config to maximum reasoning depth.

## Choosing a Launch Method

```text
Need to start GRIP?
  |
  +-- First time?
  |     --> grip                         (auto-detects, runs onboarding)
  |
  +-- Continuing previous work?
  |     +-- Same session    --> grip resume latest
  |     +-- Specific gen    --> grip resume gen:N
  |     +-- Specific branch --> grip resume branch:name
  |
  +-- Fresh start with context?
  |     +-- Quick   (2k tokens)  --> gg     /  grip fresh latest 2000
  |     +-- Medium  (8k tokens)  --> gg+    /  grip fresh latest 8000
  |     +-- Maximum (12k tokens) --> gg++   /  grip fresh latest 12000
  |
  +-- Persistent session?   --> sesh    /  grip --hot
  +-- Flow mode?            --> grip --flow  (bypass permissions)
```

## Quick Reference

| Command | Context Tokens | Mode | Use Case |
|---------|---------------|------|----------|
| `grip` | auto | auto-detect | Default entry point |
| `gg` | 2,000 | standard | Quick fix, code review |
| `gg+` | 8,000 | standard | Feature work, debugging |
| `gg++` | 12,000 | extended | Deep reasoning, complex refactors |
| `ggr` | full session | resume | Continue exactly where you left off |
| `sesh` | varies | hot reload | Long coding sessions with `/refresh` |
| `grip --flow` | auto | flow | Bypass permission prompts |

---

## gg++ (Maximum Context Mode)

`gg++` is the highest-context quick-launch method. It starts a fresh GRIP session with 12,000 tokens of compressed context from your previous session, giving Claude the deepest possible understanding of prior work.

```bash
gg++
```

This is equivalent to:

```bash
grip fresh latest 12000
```

### Why 12,000 Tokens

The token budget controls how much of your previous session gets carried forward. Higher budgets mean more context, better continuity, and deeper reasoning about prior decisions.

| Budget | Alias | Typical Content | Best For |
|--------|-------|-----------------|----------|
| 2,000 | `gg` | Last 2-3 key messages | Quick fixes, isolated tasks |
| 8,000 | `gg+` | Last 10-15 messages, key decisions | Feature development |
| 12,000 | `gg++` | Last 20-30 messages, full decision chain | Complex refactors, multi-step work |

12k is the practical maximum before diminishing returns. Beyond this, the context overhead starts competing with the working space Claude needs for the current task.

### How Context Injection Works

When you run `gg++`, GRIP performs these steps before Claude starts:

1. **Resolve** -- `lib/session_resolver.py` finds your most recent session for the current project
2. **Compress** -- `lib/semantic_compressor.py` loads the session's message history and scores each message by relevance (not recency). Implementation details, key decisions, and error resolutions score highest.
3. **Select** -- Top-scoring messages are selected up to the 12k token budget and written to `contexts/{project}/resurrection.md`
4. **Launch** -- Claude Code starts. The `hooks/session-start.sh` hook detects the resurrection file and injects the context.
5. **Status** -- You see the compact status line:

```text
[GRIP] Gen 42 | a5e3570c, 25 msgs | 29 agents, 57 skills | code mode | main, 3 changes
```

The semantic compressor selects by **relevance**, not recency. A critical architectural decision from 50 messages ago scores higher than a routine file read from 3 messages ago. This means `gg++` captures the most important context, not just the latest.

### gg++ vs grip-ut

There are two related but distinct mechanisms:

| | `gg++` (shell alias) | `grip-ut` (binary) |
|---|---|---|
| **Location** | Shell alias in `.zshrc` | `~/.claude/bin/grip-ut` |
| **Invocation** | Terminal: `gg++` | VS Code processWrapper |
| **Context** | 12k tokens | 12k tokens |
| **Hot reload** | No (single session) | Yes (expect wrapper) |
| **Flow mode** | No (standard permissions) | Yes (`--dangerously-skip-permissions`) |
| **Env var** | Not set | `GRIP_EXTENDED_THINKING=1` |

**`gg++`** is the simple shell alias. It calls `grip fresh latest 12000` which compresses context, writes the resurrection file, and launches Claude.

**`grip-ut`** is the full wrapper binary designed for IDE integration. It does everything `gg++` does, plus:
- Sets `GRIP_EXTENDED_THINKING=1` (triggers capability detection in `lib/capability-detector.sh`)
- Enables hot reload via `expect` wrapper (auto-restarts on `/refresh`)
- Launches with flow mode (`--dangerously-skip-permissions`)

### VS Code / IDE Integration

To use the extended thinking wrapper in VS Code or compatible editors, add to your editor's `settings.json`:

```json
{
  "claudeCode.claudeProcessWrapper": "$HOME/.claude/bin/grip-ut"
}
```

This makes every Claude Code session in VS Code behave like `gg++` with hot reload and flow mode.

### Naming: gg++ and Ultrathink

The `gg++` name stands for "good game" -- internet-savvy, recognizable, no conflicts. The `++` suffix mirrors the C++ convention (enhanced version). The progressive hierarchy `gg` < `gg+` < `gg++` maps to increasing context budgets. The underlying `ultrathink` PARAMOUNT skill (`skills/ultrathink/SKILL.grip`) provides 6 mandatory verification gates:

1. Confidence verification
2. File read optimization
3. Concise communication
4. Destructive action prevention
5. Safety gate enforcement
6. Design principle verification

The `ultrathink` skill is always active in every GRIP session regardless of launch method. The `gg++` alias gives the session maximum context to reason with.

---

## grip CLI

The primary entry point with subcommands for every workflow.

### Session Commands

| Command | Description |
|---------|-------------|
| `grip` | Auto-detect: onboard if new, hot mode if available, fresh otherwise |
| `grip fresh <ref> [tokens]` | Fresh session with compressed context |
| `grip resume <ref>` | Resume existing session via `claude --resume` |
| `grip list [limit]` | List available sessions for current project |
| `grip branches` | List all session branches |
| `grip status` | Show currently active sessions |
| `grip tree` | Session lineage tree |
| `grip merge <refs...> [--into branch]` | Merge session branches |

### Operational Commands

| Command | Description |
|---------|-------------|
| `grip --hot` / `grip -H` | Hot reload mode (persistent session with `/refresh`) |
| `grip --flow` / `grip -F` | Flow mode (bypass permission prompts) |
| `grip bounce` | Cyclical reset (Big Bounce pattern) |
| `grip init` | Initialize GRIP for current project |

### Maintenance Commands

| Command | Description |
|---------|-------------|
| `grip doctor` | Diagnose installation health |
| `grip repair [--source PATH]` | Fix disconnected installations |
| `grip broly [cmd]` | Launch Broly recursive meta-agent |
| `grip --version` | Show version info |

### Reference Types

Commands that take a `<ref>` argument accept these formats:

| Format | Example | Description |
|--------|---------|-------------|
| `latest` | `grip resume latest` | Most recent session |
| `gen:N` | `grip resume gen:5` | Specific generation number |
| `branch:name` | `grip resume branch:alpha` | Specific branch |
| Instance ID | `grip resume a5e3570c` | Instance ID prefix |
| Session UUID | `grip resume 71c5db4e-...` | Full session UUID |

### Token Budgets for `grip fresh`

```bash
grip fresh latest 500      # Ultra-light (quick reminder)
grip fresh latest 2000     # Standard (default)
grip fresh latest 5000     # Extended
grip fresh latest 12000    # Maximum (equivalent to gg++)
```

---

## Shell Aliases

These aliases provide quick access to common GRIP operations. They are defined in `~/.zshrc`.

| Alias | Expansion | Purpose |
|-------|-----------|---------|
| `gg` | `grip fresh latest 2000` | Quick session with light context |
| `gg+` | `grip fresh latest 8000` | Deep session with medium context |
| `gg++` | `grip fresh latest 12000` | Maximum reasoning depth |
| `ggr` | `grip resume latest` | Resume last session |
| `ggl` | `grip list` | List available sessions |
| `sesh` | `grip --hot` | Hot reload mode |

### Adding Aliases

If you installed GRIP fresh and don't have these aliases, add to your `~/.zshrc` or `~/.bashrc`:

```bash
# GRIP Aliases
alias gg='grip fresh latest 2000'
alias 'gg+'='grip fresh latest 8000'
alias 'gg++'='grip fresh latest 12000'
alias ggr='grip resume latest'
alias ggl='grip list'
alias sesh='grip --hot'
```

Then reload: `source ~/.zshrc`

---

## Hot Reload Mode

Hot reload keeps your session alive across context refreshes. When you type `/refresh` inside Claude, the session serializes its state, generates fresh context, and restarts automatically.

```bash
grip --hot          # Start with hot reload
grip -H             # Short alias
grip --hot 5        # Limit to 5 restarts (default: 10)
sesh                # Shell alias
```

### How It Works

1. GRIP launches Claude inside an `expect` wrapper (`lib/hot-reload-wrapper.exp`)
2. The wrapper monitors for exit code 42 (the `/refresh` signal)
3. On exit code 42: serialize instance, compress context, restart Claude
4. On any other exit: end session normally
5. Repeats up to `max_restarts` times (default 10)

### When to Use

- Long coding sessions where context accumulates
- Iterative development with frequent context refreshes
- When you want `/refresh` to work seamlessly without manually restarting

### Requirements

Hot reload requires `expect` (usually pre-installed on macOS). If missing:

```bash
brew install expect
```

---

## Flow Mode

Flow mode bypasses Claude's permission prompts, allowing GRIP to execute tools without asking for approval on each action.

```bash
grip --flow         # Start with flow mode
grip -F             # Short alias
```

### Security Note

Flow mode uses `--dangerously-skip-permissions`. GRIP's safety gates (dependency guardian, destructive git, secrets detection) still run and enforce mechanically. Flow mode only bypasses Claude Code's built-in permission prompts, not GRIP's gates.

Flow mode is hidden by default and requires explicit enablement:

```bash
grip flow --explain     # Understand what it does
grip flow --enable      # Enable (requires completed onboarding)
grip flow --disable     # Disable
grip flow --status      # Check current status
```

---

## Direct Claude

You can always start Claude Code directly:

```bash
cd ~/your-project
claude
```

GRIP activates automatically via session-start hooks. The hooks:
- Detect GRIP infrastructure
- Attempt auto-resurrection from previous instance
- Inject semantic context if a resurrection file exists
- Load active mode
- Output the compact status line

This is the simplest entry point but provides no context injection from previous sessions unless you've previously run `grip fresh` which writes a resurrection file.

---

## Legacy: CIPS Binary

If you have an older Homebrew installation, you may have a legacy `cips` binary at `/opt/homebrew/bin/cips`. This was the predecessor to `grip` and output `[CIPS]` prefixes in its log messages.

To check:

```bash
which cips
```

If found, update to `grip`:

```bash
brew uninstall cips
```

All `cips` subcommands are identical to `grip` subcommands. The only difference is the binary name and log prefix.
