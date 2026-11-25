# Getting Started with GRIP

Three levels. Start at Level 1. Go deeper when you're ready.

---

## Level 1: Up and Running (5 minutes)

**Installed from the gift bundle?** There is nothing to pull: your copy has
no git history. Skip to *Restart Claude Code* below, and when you want to
update, run `bash install.sh` from the bundle folder again. Launch with
`gg++`, GRIP's own launcher, which applies your plan tier's settings (the
model pin, the effort level, the first-run banner). A bare `claude` also
works, but skips them. README-FIRST.md in the bundle has the full first
steps.

### Pull Latest (git-clone installs)

```bash
cd ~/.claude
git pull origin main
```

### Verify Python

```bash
python3 --version   # Requires 3.10+
```

No pip installs needed — all GRIP libraries use Python's standard library.

### Windows Users

Run the setup script to fix hook paths:

```bash
python3 ~/.claude/scripts/grip-setup.py --check   # See issues
python3 ~/.claude/scripts/grip-setup.py --fix      # Auto-fix
```

Git Bash is recommended. PowerShell works after running `--fix`.

### Restart Claude Code

Settings changes take effect after restart. Open any project:

```bash
cd ~/your-project
gg++
```

You should see a GRIP status line. Done — you're running GRIP.

---

## Level 2: Try the Features (30 minutes)

GRIP ships 27 analysis libraries. All work on ANY git repository.

### Quick Smoke Test

Run these from any project directory:

```bash
python3 ~/.claude/lib/codebase_weather.py forecast     # How healthy is this codebase?
python3 ~/.claude/lib/commit_mood.py analyze            # What's the commit sentiment?
python3 ~/.claude/lib/project_scorecard.py score        # A+ to F rating
```

### By Category

Pick one category that interests you. Each library has a CLI with `--help`.

**Developer Wellbeing** (how's the team doing?)

| Library | Command | What It Shows |
|---------|---------|---------------|
| Achievement Badges | `python3 ~/.claude/lib/achievement_badges.py check` | 15 badge types, 4 tiers |
| Commit Mood | `python3 ~/.claude/lib/commit_mood.py analyze` | 8 mood categories |
| Focus Tracker | `python3 ~/.claude/lib/focus_tracker.py report` | Flow states, distractions |
| Collaboration Heatmap | `python3 ~/.claude/lib/collaboration_heatmap.py analyze` | Team patterns, bus factor |
| Project Scorecard | `python3 ~/.claude/lib/project_scorecard.py score` | 7-dimension A+ to F |

**Code Quality** (how's the code doing?)

| Library | Command | What It Shows |
|---------|---------|---------------|
| Codebase Weather | `python3 ~/.claude/lib/codebase_weather.py forecast` | SUNNY to STORMY |
| Tech Debt Mapper | `python3 ~/.claude/lib/tech_debt_mapper.py scan` | Prioritized debt items |
| Code Health Dashboard | `python3 ~/.claude/lib/code_health_dashboard.py report` | Unified health metrics |
| Complexity Profiler | `python3 ~/.claude/lib/complexity_profiler.py analyze` | Cyclomatic complexity |
| Architecture Explorer | `python3 ~/.claude/lib/architecture_explorer.py explore` | Structure and dependencies |

**Git Workflow** (better commits, PRs, releases)

| Library | Command | What It Shows |
|---------|---------|---------------|
| Smart Commit | `python3 ~/.claude/lib/smart_commit.py suggest` | AI-suggested commit messages |
| Changelog Generator | `python3 ~/.claude/lib/changelog_generator.py generate` | Auto-changelog from history |
| PR Impact Analyzer | `python3 ~/.claude/lib/pr_impact.py analyze` | Blast radius and risk score |
| Review Checklist | `python3 ~/.claude/lib/code_review_checklist.py generate` | Tailored review checklist |
| Release Notes | `python3 ~/.claude/lib/release_notes.py compose` | Release notes from commits |

**Team and Reporting**

| Library | Command | What It Shows |
|---------|---------|---------------|
| Sprint Velocity | `python3 ~/.claude/lib/sprint_velocity.py report` | Velocity tracking |
| Sprint Retro | `python3 ~/.claude/lib/sprint_retro.py generate` | Automated retrospective |
| Contributor Insights | `python3 ~/.claude/lib/contributor_insights.py analyze` | Contribution analytics |
| Pair Matcher | `python3 ~/.claude/lib/pair_matcher.py suggest` | Data-driven pairing |
| Onboarding Guide | `python3 ~/.claude/lib/onboarding_guide.py generate` | New-joiner guide |

### Inside Claude Code

You can also use these through GRIP commands during a session:

```text
/broly auto "What's the health of this codebase?"
/refresh-context
/create-pr
/mode code
```

---

## Level 3: Power Features (when you're ready)

### Broly (Recursive Analysis)

Broly runs analysis at 5 power levels. Start small:

```text
/broly auto "Analyze code quality and suggest top 3 improvements"
```

| Level | Agents | Use For |
|-------|--------|---------|
| `auto` | 1 | Quick analysis |
| `fork` | 2-3 | Research + implementation |
| `cluster` | 5-7 | Multi-angle analysis |
| `pipeline` | 7-10 | Full improvement cycles |
| `mesh` | 10-15 | Architecture decisions |

### RSI (Recursive Self-Improvement)

RSI runs autonomous improvement cycles: analyze, plan, implement, test, review.

**On any project:**

```bash
cd ~/your-project
claude
```

```text
# Start simple
/broly auto "Health check — what needs attention?"

# Focused improvement
/broly fork "Find and fix the top 3 code quality issues"

# Full cycle (set a time limit)
/broly pipeline "2-hour RSI: analyze, fix issues, add tests, create PR"
```

**Safety rules:**
- Start with `auto` or `fork` on unfamiliar codebases
- Set explicit time limits ("2 hours maximum")
- Review before merging — never auto-merge on projects you don't own
- Commit frequently for easy rollback

### Copilot (Scheduled Workflows)

GRIP Copilot runs automated rituals on a schedule:

| Ritual | When | What |
|--------|------|------|
| Morning Brief | Daily 07:00 | Overnight activity summary |
| Standup Summary | Daily 08:30 | Auto-generated standup notes |
| Codebase Weather | Daily 08:30 | Health forecast |
| PR Review Digest | Daily 14:00 | Open PR summary |
| Code Health Check | Weekly | Full codebase scan |

Rituals are configured in `data/copilot/rituals/active.json`.

### Modes

Switch GRIP's behavior for different tasks:

```text
/mode code        # Software development (default)
/mode strategy    # Strategic planning
/mode research    # Systematic research
/mode review      # Code/content review
/mode legal       # Contract drafting
```

26 modes available. Use `/mode` to list them.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Hooks not firing | `python3 ~/.claude/scripts/grip-setup.py --fix` then restart |
| Python not found | Windows: add to PATH. macOS: `xcode-select --install`. Linux: package manager |
| Merge conflicts on pull | `cp settings.json settings.json.bak && git pull origin main` |
| Library import errors | Run from `~/.claude` dir: `cd ~/.claude && python3 lib/<name>.py` |
| Windows path errors | Use Git Bash, or run setup script with `--fix` |

## Further Reading

- [Quick Start](QUICK-START.md) — Original setup guide
- [Safety Gates](SAFETY-GATES.md) — How mechanical enforcement works
- [Broly Guide](BROLY-GUIDE.md) — Deep dive on recursive analysis
- [RSI Guide](RSI-LOOP-GUIDE.md) — Full RSI loop documentation
- [Team Setup](TEAM_SETUP.md) — Multi-user configuration
- [Troubleshooting](TROUBLESHOOTING.md) — Extended problem solving
