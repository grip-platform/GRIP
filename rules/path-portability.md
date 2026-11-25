---
paths:
  - "**/*.py"
  - "**/*.sh"
  - "**/*.bash"
  - "**/*.zsh"
  - "**/*.ps1"
  - "**/*.js"
  - "**/*.mjs"
  - "**/*.cjs"
  - "**/*.jsx"
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.swift"
  - "**/*.kt"
  - "**/*.kts"
  - "**/*.gradle"
  - "**/*.java"
  - "**/*.go"
  - "**/*.rs"
  - "**/*.rb"
  - "**/*.tf"
  - "**/*.json"
  - "**/*.yaml"
  - "**/*.yml"
  - "**/*.toml"
  - "**/*.plist"
  - "**/*.template"
  - "**/*.service"
  - "**/*.timer"
  - "bin/**"
  - "scripts/**"
---

# Path Portability

Never hardcode user paths (`~/`, `~/`, `C:/Users/`). Use `$HOME/.claude/` or `${CLAUDE_DIR:-$HOME/.claude}`. Source libraries after variable definition.

## Python: use `lib/claude_dir.py::get_claude_dir()`

The canonical shim for Python resolves the install directory with env-var
override. Use it instead of `Path.home() / ".claude"`:

```python
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent  # adjust .parent.parent for lib/sub/
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
```

Resolution: `$CLAUDE_DIR` (when set, non-empty after strip) → `$HOME/.claude`.

This is the de-facto canonical env var — already used by
`lib/grip_session_sync.py`, `lib/tip_injector.py`, `scripts/copilot-api.py`,
`scripts/mcp-generate.py`, et al. The shim is pure (no I/O beyond env reads),
hermetic, and backward-compat-by-construction (callers without `CLAUDE_DIR`
set get byte-identical behaviour).

**Hypothesis**: H-CLAUDE-DIR-PORT-1 — over 14 days post-merge, no new
`Path.home() / ".claude"` literals appear in `lib/`. Confirmed at deadline
if zero new literals + the migrated callsites remain shim-routed.
Falsified by any new literal landing on main during the window.
