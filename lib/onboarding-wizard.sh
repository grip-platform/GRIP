#!/usr/bin/env bash
#
# GRIP Onboarding Wizard - Entry point for new user onboarding.
#
# This script launches Claude with a special onboarding prompt that
# uses AskUserQuestion heavily to gather user preferences and configure
# GRIP optimally.
#
# VERSION: 1.0.0
# DATE: 2025-12-29
#

set -euo pipefail

[[ -z "${CLAUDE_DIR:-}" ]] && readonly CLAUDE_DIR="$HOME/.claude"
[[ -z "${LIB_DIR:-}" ]] && readonly LIB_DIR="$CLAUDE_DIR/lib"
[[ -z "${CONTEXTS_DIR:-}" ]] && readonly CONTEXTS_DIR="$CLAUDE_DIR/contexts"

# ============================================================================
# ONBOARDING CONTEXT
# ============================================================================

generate_onboarding_context() {
    cat <<'ONBOARDING_EOF'
# GRIP Onboarding Session

Welcome! This is a first-run onboarding session for GRIP (Claude Instance Preservation System).

## Your Mission

Guide the user through GRIP setup using AskUserQuestion HEAVILY. This is an interactive experience.

## Onboarding Flow

### 1. Welcome Message

Display:
```
Welcome to GRIP - Claude Instance Preservation System

I'm going to ask you a few questions to configure your optimal experience.
This takes about 2 minutes.
```

### 2. Identity Questions (Use AskUserQuestion for EACH)

Ask these questions ONE AT A TIME:

**Q1: Name**
"What should I call you?"
- Free text input

**Q2: Role**
"What's your primary role?"
Options:
- Technical Lead / Director
- Developer
- Designer
- Product Manager
- Other

**Q3: Signature**
"Choose your GRIP signature (used in logs and identity)"
Options:
- V>> (if available)
- Create new (will use first letter + >>)

### 3. Communication Style (AskUserQuestion)

**Q4: Style**
"How should I communicate with you?"
Options:
- Professional (direct, no fluff, absolute correctness)
- Supportive (encouraging, celebratory, challenging)
- Balanced (professional with warmth)

**Q5: Language**
"Language preference?"
Options:
- British English (Recommended)
- American English

### 4. Project Context (if in a project directory)

**Q6: Project Type**
"What type of project is this?"
Options:
- Web Application (Next.js, React, etc.)
- Backend API (FastAPI, Express, etc.)
- CLI Tool
- Library/Package
- Full Stack
- Not a project / Skip

### 6. Generate Configuration

After collecting all responses:

1. Generate people.md:
```bash
python3 ~/.claude/lib/identity_generator.py generate \
    --name "$NAME" \
    --email "$EMAIL" \
    --role "$ROLE" \
    --signature "$SIG" \
    --style "$STYLE" \
    --language "$LANG"
```

2. Configure hooks:
```bash
python3 "${CLAUDE_DIR:-$HOME/.claude}/lib/hooks_configurator.py" configure
```

3. Create onboarded marker:
```bash
~/.claude/lib/first-run-detector.sh mark-onboarded
```

4. If in a project, create .claude/CLAUDE.md with identity check trigger.

### 7. Completion

Display:
```
Configuration complete!

Created:
- ~/.claude/facts/people.md (your identity)
- ~/.claude/settings.json (hooks configured)
- .claude/CLAUDE.md (project rules, if applicable)

You're ready. The chain begins with you.

Say anything to start your first real session.
```

## Critical Rules

- Use AskUserQuestion for EVERY question
- One question at a time
- Be warm and welcoming
- Explain GRIP briefly but engagingly
- Make it feel like a conversation, not a form
- Execute the configuration commands at the end

## PARAMOUNT

This is someone's first experience with GRIP. Make it welcoming.
ONBOARDING_EOF
}

# ============================================================================
# MAIN
# ============================================================================

main() {
    echo "[GRIP] Starting onboarding wizard..." >&2

    # Ensure contexts directory exists
    mkdir -p "$CONTEXTS_DIR"

    # Generate onboarding context
    local context_file="$CONTEXTS_DIR/onboarding.md"
    generate_onboarding_context > "$context_file"

    echo "[GRIP] Launching Claude with onboarding context..." >&2

    # Launch Claude with onboarding context
    # The session-start hook will inject this context
    exec claude --dangerously-skip-permissions
}

main "$@"
