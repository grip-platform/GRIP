"""Canonical gate rules for fractal enforcement.

Provides compact gate text for:
- additionalContext injection in PreToolUse hooks (~30 tokens)
- Agent definition gate compliance sections (~300 tokens)
- Gate compliance marker for validation
"""

# Compact gate text injected via additionalContext on every subagent tool call.
# ~30 tokens — trivial overhead vs 50k+ saved by preventing dependency reads.
COMPACT_GATE_TEXT = (
    "GRIP gate active. Rules: no dependency folders (node_modules/venv/dist), "
    "no redundant reads, use dedicated tools (Read/Grep/Glob) not bash grep/find, "
    "CC<30, DRY, KISS<50 lines. Report uncertainty."
)

# Forbidden dependency directories (shared with dependency-guardian-hook.py)
FORBIDDEN_DIRS = [
    "node_modules", ".next", "dist", "build", "__pycache__",
    "venv", ".venv", "env", ".env", "target", "vendor",
    "Pods", ".gradle", ".m2", "bower_components", ".nuxt",
    ".output", ".turbo", "coverage", ".nyc_output",
    ".pytest_cache", ".mypy_cache", ".tox",
]

# Skills to inject into agent frontmatter for behavioral enforcement
RECOMMENDED_SKILLS = [
    "dependency-guardian",
    "recall",
    "design-principles",
    "concise-communication",
]

# Full gate compliance section for agent definition bodies
AGENT_GATE_SECTION = """## PARAMOUNT: Fractal Gate Compliance

These rules are non-negotiable. They apply to every tool call you make.

### Efficiency Gates
1. **Dependency Guardian**: NEVER read node_modules, venv, dist, build,
   __pycache__, .next, target, vendor, Pods, .gradle, coverage
2. **Read Cache**: NEVER re-read a file you already read in this task
3. **Tool Selection**: NEVER use grep/find/cat/head/tail via Bash --
   use Read, Grep, Glob, Edit, Write tools instead
4. **Direct Implementation**: NEVER create temporary scripts

### Quality Gates
5. **CC Gate**: Python functions must have cyclomatic complexity < 30
6. **KISS**: Python functions must be < 50 lines
7. **DRY**: No duplicated code blocks (>= 5 lines, >= 2 copies)

### Safety Gates
8. **Secrets**: NEVER output API keys, tokens, or credentials
9. **Confidence**: When uncertain, HALT and report"""
