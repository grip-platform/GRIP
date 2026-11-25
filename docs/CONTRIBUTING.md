# Contributing to GRIP

Welcome to the GRIP codebase. This guide gets you from clone to working in 5 minutes.

## Quick Start

```bash
# 1. Clone into ~/.claude (GRIP's home directory)
git clone https://github.com/grip-platform/GRIP.git ~/.claude

# 2. Run the installer (sets up PATH, shell functions, Python venv, MCP servers)
~/.claude/scripts/install.sh

# 3. Reload your shell (or restart your terminal)
source ~/.zshrc   # or source ~/.bashrc

# 4. Launch GRIP
gg++

# 5. Run the setup wizard (creates your identity and settings)
/activate-grip
```

After setup, you can start GRIP from anywhere by typing `gg++`.

### What install.sh does

- Adds `~/.claude/bin` to your PATH (makes `grip` and `gg++` available)
- Sources `lib/shell-functions.sh` (makes `gogrip` function available)
- Creates the `gg++` alias (shortcut for `gogrip`)
- Sets up a Python virtual environment (for GRIP's Python libraries)
- Configures MCP server connections
- Detects your platform (macOS, Linux, or Windows via Git Bash)

### What gg++ does

`gg++` navigates to `~/.claude` and starts a fresh Claude Code session with full session continuity. It is the recommended way to launch GRIP.

Under the hood: `gg++` is an alias for `gogrip()`, which runs `cd ~/.claude && grip fresh latest 12000`. The `grip` CLI handles session resurrection, compressed context injection, and branch management.

## About Encrypted Files

GRIP uses git-crypt to protect sensitive data (personal details, credentials, genome data). When you clone, some files appear as binary — this is normal and expected.

**You do NOT need to unlock git-crypt.** The encrypted files contain the repo owner's personal data. Your copy of GRIP works without them.

### What is encrypted (and why you can skip it)

| Path | Contains | You need it? |
|------|----------|-------------|
| `facts/cv.md` | Owner's CV | No |
| `docs/restricted/` | Confidential business docs | No |
| `data/genomes/` | Genome evolution data | No |
| `rules/operational-context.md` | Slack IDs, credentials | No |
| `organizations/*/dit-scores.jsonl` | Developer scores | No |

### What is NOT encrypted (and what you actually use)

| Path | Contains |
|------|----------|
| `skills/` | All 285 skills |
| `agents/` | All 38 worker agents |
| `lib/` | Core Python libraries |
| `mcp-servers/` | MCP server code |
| `rules/*.md` (most) | Operating rules |
| `facts/*.md.template` | Identity templates |
| `.claude/CLAUDE.md` | Project instructions |

## Setting Up Your Identity

The `/activate-grip` wizard creates these files locally (gitignored, never shared):

- `facts/identity.md` — your name, signature, mode
- `facts/team.md` — your team config
- `settings.json` — your Claude Code settings

These are based on the `.template` files in `facts/` which are always readable.

## Git Operations

Standard git commands work on all non-encrypted files:

```bash
git status          # see your changes
git add <file>      # stage specific files
git commit          # commit normally
git push            # push to your branch
git pull --rebase   # stay up to date
```

### Creating a Branch

```bash
git checkout -b feat/your-feature
# make changes
git add <files>
git commit -m "feat: description of change"
git push -u origin feat/your-feature
```

Then create a PR on GitHub.

## If You See Binary Files

Binary-looking files in `facts/`, `docs/restricted/`, or `data/genomes/` are git-crypt encrypted. They are NOT broken. They are encrypted because they contain private data.

**Do not** try to fix, delete, or modify these files. They work correctly for the repo owner.

## Common Issues

### "gg++ command not found"

You need to reload your shell after running install.sh:

```bash
source ~/.zshrc   # or source ~/.bashrc
```

If that doesn't work, check your shell config contains the GRIP lines:

```bash
grep "GRIP" ~/.zshrc   # should show GRIP PATH and GRIP SHELL FUNCTIONS
```

If missing, re-run `~/.claude/scripts/install.sh`.

### "grip: command not found"

Same fix as gg++ above. The `grip` CLI lives at `~/.claude/bin/grip` and requires `~/.claude/bin` in your PATH.

### "I can't read facts/identity.md"

You don't need to. Run `/activate-grip` — it creates YOUR identity from the template.

### "Some files look like garbage"

Those are git-crypt encrypted files. They contain the repo owner's private data. Your GRIP works without them.

### "git push fails"

Check you have write access to the repo. Ask the repo admin for an invite.

### "Claude Code doesn't load GRIP"

Make sure you cloned into `~/.claude` (not a subdirectory). GRIP must be at `~/.claude/CLAUDE.md`.

### "install.sh fails with permission error"

Make sure the script is executable: `chmod +x ~/.claude/scripts/install.sh`. On Windows (Git Bash), you may need to run `bash ~/.claude/scripts/install.sh` instead.

## Platform-Specific Install

### macOS

The default experience. Install Claude Code, clone, and run install.sh:

```bash
# Install Claude Code (if not already installed)
curl -fsSL https://claude.ai/install.sh | bash

# Clone and install GRIP
git clone https://github.com/grip-platform/GRIP.git ~/.claude
~/.claude/scripts/install.sh
source ~/.zshrc
gg++
```

### Linux

Same steps as macOS. Shell config file is usually `~/.bashrc` instead of `~/.zshrc`:

```bash
curl -fsSL https://claude.ai/install.sh | bash
git clone https://github.com/grip-platform/GRIP.git ~/.claude
~/.claude/scripts/install.sh
source ~/.bashrc
gg++
```

### Windows (Git Bash)

Windows requires [Git Bash](https://git-scm.com/downloads) (comes with Git for Windows):

```bash
# In Git Bash:
git clone https://github.com/grip-platform/GRIP.git ~/.claude
bash ~/.claude/scripts/install.sh
source ~/.bashrc
gg++
```

**Windows notes:**   
- Use `python` instead of `python3` (Git Bash convention)   
- Forward slashes work in Git Bash paths   
- For git-crypt: `choco install git-crypt` (via Chocolatey)   
- Symlinks may require Developer Mode or admin privileges   

## Verifying Your Installation

Run the verification script to check everything is working:

```bash
~/.claude/scripts/verify-install.sh         # full check (20 tests)
~/.claude/scripts/verify-install.sh --quick  # essential checks only (6 tests)
```

The script checks: GRIP directory, Claude CLI, PATH setup, gg++ availability, Python, venv, git repo, identity, MCP servers, and all core directories.

## For Repo Admins (git-crypt unlock)

If you DO need access to encrypted files (team admins only):

```bash
# Option 1: Symmetric key (from repo admin)
git-crypt unlock /path/to/symmetric-key

# Option 2: GPG key (admin adds your key to the repo)
git-crypt unlock
```

Contact V>> for access.
