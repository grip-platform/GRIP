# How to Use /insights on Claude Code

A guide for checking how you (or your team) are using Claude Code and GRIP.

## What is /insights?

`/insights` is a built-in Claude Code command that analyzes your session history and generates a detailed report covering:

- How many sessions you've run, total messages, hours spent, and commits made
- What project areas you work on most
- Your interaction style and patterns
- What's working well (impressive workflows)
- Where things go wrong (friction analysis with specific examples)
- Concrete suggestions for improvement (CLAUDE.md additions, features to try, usage patterns)
- Forward-looking opportunities

The output is a shareable HTML report.

## How to Run It

### Step 1: Open Claude Code in your project

```bash
cd ~/your-project
claude
```

### Step 2: Run the command

```
/insights
```

That's it. Claude analyzes your session data and generates the report. It takes 1-2 minutes depending on how many sessions you have.

### Step 3: View your report

The report is saved at:

```
~/.claude/usage-data/report.html
```

Open it in your browser:

```bash
open ~/.claude/usage-data/report.html     # macOS
xdg-open ~/.claude/usage-data/report.html  # Linux
```

## What You'll See

### At a Glance

A quick summary of what's working, what's hindering you, quick wins, and ambitious next steps.

### Project Areas

Breakdown of where you spend your time. For example:
- Infrastructure and system maintenance
- Feature development
- Git operations and DevOps
- Team collaboration

Each area shows session count so you can see where effort is concentrated.

### Interaction Style

An honest assessment of how you use Claude Code:
- Are you a rapid-fire directive issuer or a detailed spec writer?
- Do you delegate aggressively or do everything hands-on?
- How often do you course-correct mid-session?

### What Works (Impressive Workflows)

Highlights of your most effective patterns. These are the workflows worth doubling down on.

### Friction Analysis

The most valuable section. Categorized by type:
- Wrong approach taken
- Git/branch mismanagement
- Tool or workspace confusion
- Excessive planning before action

Each category includes real examples from your sessions so you can recognize the patterns.

### Suggestions

Three types:
1. **CLAUDE.md additions** - Rules to add to your config to prevent recurring issues
2. **Features to try** - Claude Code features you might not be using (custom skills, hooks, headless mode)
3. **Usage patterns** - Workflow adjustments based on your data

### On the Horizon

Forward-looking opportunities based on your current capabilities and where the tools are heading.

## Tips for Getting the Most Out of It

1. **Run it periodically** - Weekly or bi-weekly gives you a feedback loop on your own habits
2. **Share with your team** - The HTML report is self-contained and shareable. Compare notes on what friction patterns are common across the team
3. **Act on one suggestion at a time** - Don't try to implement everything at once. Pick the highest-impact friction category and address it first
4. **Add the CLAUDE.md suggestions** - The report generates copy-paste-ready rules. Adding even one or two can eliminate entire classes of errors
5. **Track improvement** - Run /insights again after making changes. The friction counts should decrease if the changes are working

## For GRIP Users

If you're running GRIP, /insights captures richer data because GRIP sessions use modes, skills, converge loops, and structured workflows. Your report will show:
- Which GRIP modes you use most
- Skill invocation patterns
- RSI loop effectiveness
- Cross-session patterns that GRIP's memory system captures

## Privacy

The report is generated locally from your session data at `~/.claude/`. Nothing is uploaded or shared unless you manually send the HTML file to someone.

## Quick Reference

| What | Where |
|------|-------|
| Run it | `/insights` in any Claude Code session |
| Report location | `~/.claude/usage-data/report.html` |
| Raw data | `~/.claude/usage-data/facets/` |
| Frequency | Weekly recommended |
| Time to generate | 1-2 minutes |
| Sharing | Send the HTML file directly |
