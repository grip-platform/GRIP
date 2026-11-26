---
name: pr-automation
description: Efficient PR creation with gh CLI - automated branch, commit, push, and pr-create. Use when user asks to create PR, open pull request, or invokes /create-pr.
effort: low
status: Active
version: 1.1.0
triggers:
  - /create-pr
  - "create PR"
  - "open pull request"
---

# PR Automation Skill

**Purpose:** Automate the complete PR creation workflow using GitHub CLI (`gh`) with minimal token usage.

**Token Budget:** <2k per PR creation

**Reference:** See [reference.md](./reference.md) for full examples, error handling, and troubleshooting.

---

## Pre-Injected Context (DCI)

**Branch:** !`git branch --show-current 2>/dev/null`
**Default branch:** !`git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || echo "main"`

**Status:**
!`git status --short 2>/dev/null | head -20`

**Diff stat (vs default branch):**
!`git diff --stat $(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || echo "main")...HEAD 2>/dev/null || echo "(no diff)"`

**Commit log (since fork point):**
!`git log --oneline $(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || echo "main")..HEAD 2>/dev/null || echo "(no commits ahead)"`

**Open PRs from this branch:**
!`gh pr list --head $(git branch --show-current 2>/dev/null) --json number,title,state --jq '.[] | "#\(.number) [\(.state)]: \(.title)"' 2>/dev/null || echo "none"`

**Remote tracking:**
!`git rev-list --left-right --count HEAD...@{upstream} 2>/dev/null | awk '{print "ahead: "$(1)", behind: "$(2)}' || echo "no upstream (needs push -u)"`

Use the above context to skip discovery. Proceed directly to PR title/body generation.

---

## Core Principles

1. **Efficiency First** - Single command chains, no redundant operations
2. **Concise PRs** - Bullet points, not essays
3. **HEREDOC for bodies** - Proper formatting without escaping issues

---

## Prerequisites

```bash
which gh >/dev/null || echo "gh not installed"
gh auth status
git rev-parse --is-inside-work-tree
```

---

## Standard Workflow

### Step 1: Analyze Changes

```bash
git status --short
git diff --stat
```

### Step 2: Create Feature Branch (if needed)

```bash
CURRENT=$(git branch --show-current)
if [[ "$CURRENT" == "main" || "$CURRENT" == "develop" ]]; then
  git checkout -b feature/descriptive-name
fi
```

**Branch naming:**
- `feature/` - New features
- `fix/` - Bug fixes
- `refactor/` - Code refactoring
- `docs/` - Documentation only
- `chore/` - Maintenance tasks

### Step 3: Stage Changes

```bash
git add file1.tsx file2.tsx && git rm old.jpg
```

### Step 4: Commit with HEREDOC

```bash
git commit -m "$(cat <<'EOF'
Redesign auth pages for visual consistency

- Unified login/register layouts with landing page
- Reduced card sizes, improved typography
- Created RedrLinkButton component (DRY)
EOF
)"
```

**Critical:** Use `<<'EOF'` (quoted) to prevent variable expansion.

### Step 5: Push to Remote

```bash
git push -u origin feature-branch-name
```

### Step 6: Create PR

```bash
gh pr create \
  --base main \
  --head feature-branch-name \
  --title "Concise PR title" \
  --body "$(cat <<'EOF'
## Changes
- Bullet point summary of changes
- What was modified and why

## Files Modified
- `path/to/file.tsx` - Brief description

## Testing
- ✅ Test criterion 1
- ✅ Test criterion 2
EOF
)"
```

---

## PR Body Template

```markdown
## Changes
- High-level summary (WHY, not just WHAT)

## Files Modified
- `path/to/file` - Brief description

## Visual Changes (if UI)
- Before/after comparison

## Testing
- ✅ Criterion 1
- ✅ Criterion 2
```

**Keep body <300 words.** Reviewers skim.

---

## Efficiency Optimizations

### Batch Git Operations

```bash
# Bad
git add file1.tsx
git add file2.tsx

# Good
git add file1.tsx file2.tsx
```

### Use HEREDOC for Multi-line

```bash
# Bad
git commit -m "Title\n\n- Point 1"

# Good
git commit -m "$(cat <<'EOF'
Title

- Point 1
EOF
)"
```

### Skip Interactive Prompts

Always use `--title` and `--body` flags.

---

## Token Budget Breakdown

| Step | Tokens |
|------|--------|
| Analyze changes | ~200 |
| Create branch | ~50 |
| Stage files | ~100 |
| Generate commit msg | ~300 |
| Commit | ~100 |
| Push | ~100 |
| Generate PR body | ~500 |
| Create PR | ~200 |
| **Total** | **~1550** |

---

## Common Errors

| Error | Solution |
|-------|----------|
| `gh: command not found` | `brew install gh` |
| `not authenticated` | `gh auth login` |
| `no commits between main and head` | Make changes first |
| `pull request already exists` | `gh pr view` |

---

## Best Practices

### 1. Small, Focused PRs
One feature/fix per PR.

### 2. Descriptive Titles
**Good:** "Redesign auth pages for mobile responsiveness"
**Bad:** "Update files"

### 3. Test Before Creating PR

```bash
npm run lint && npm run build && npm test
```

### 4. Clean Commit History

```bash
git rebase -i HEAD~3  # Squash before PR
```

### 5. Update Branch Before PR

```bash
git checkout main && git pull
git checkout feature-branch && git rebase main
```

---

## Large Refactor Hygiene (Gen 276)

When PRs involve architectural changes, file removals, or substantial additions:

### Atomic PR Principle

| Diff Size | Action |
|-----------|--------|
| < 500 lines | Single PR acceptable |
| 500-1000 lines | Consider splitting by concern |
| > 1000 lines | MUST split into atomic PRs |

**Split strategy:**
1. Infrastructure changes first (new utilities, helpers)
2. Feature implementation second
3. Cleanup/removal last (separate PR)

### Migration Path Requirement

When removing files (agents, configs, utilities):

```markdown
## Breaking Changes

### Removed Files
- `agents/old-agent.md` - Functionality merged into `new-agent.md`

### Migration
Users with custom configurations should:
1. Move X to Y
2. Update references from A to B
```

**NEVER** remove user-facing files without documented migration.

### Test Coverage Gate

Substantial additions require:

| Addition Type | Required Tests |
|---------------|----------------|
| New utility functions | Unit tests |
| New components | Integration tests |
| New workflows | E2E smoke tests |
| New APIs | Contract tests |

**Rule:** No merge until test coverage verified.

### Changelog Discipline

Every PR with user-facing changes MUST include:

```markdown
## Changelog Entry

### Added
- New hybrid search combining vector + keyword matching

### Changed
- Agent count reduced from 16 to 14 (duplicates removed)

### Removed
- `duplicate-agent.md` - merged into `primary-agent.md`
```

Add to `CHANGELOG.md` or PR description.

---

## Integration

| Skill | Usage |
|-------|-------|
| `chat-history-search` | Reuse successful PR structures |
| `github-actions-setup` | Add CI workflow to new repos |
| `code-agentic` | Verification gate before PR |

---

**Skill Status:** ✅ Active
**Maintainer:** V>>
**Last Updated:** 2026-01-28


