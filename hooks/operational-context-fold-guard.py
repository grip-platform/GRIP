"""PostToolUse hook: OCF (Operational Context Fold) size guard.

Fires after Write/Edit completes. Checks if operational-context.md grew
past the 40,000-char MAX_CHARS threshold established by the OCF invariant.

OCF invariant:
    full-context ≡ OCF-kernel (this file) + fold-guard (hooks/) + KONO-substrate (insights.jsonl)
    H-FOLD-7 applied: local kernel size is independent of KONO substrate size.
    Fold property: ℱ(file) = file — idempotent.

Architecture:
- PostToolUse (async=true — never blocks the agent turn)
- Reads the file the tool just wrote (never a $CLAUDE_DIR copy) to measure its size
- Emits systemMessage with compaction command when threshold exceeded

Version: 1.0.0
"""

import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from hook_utils import parse_hook_input, respond, extract_tool_info  # noqa: E402

MAX_CHARS = 40_000
TARGET_DIR = "rules"
TARGET_NAME = "operational-context.md"
INSTALL = Path(__file__).resolve().parent.parent


def _edited_target(file_path: str):
    """The file the tool just wrote, when it is a rules/operational-context.md.

    The guard measures THAT file, never a copy resolved from $CLAUDE_DIR.
    GRIP#6375 resolved the tree through get_claude_dir(), which is right only
    when $CLAUDE_DIR names the worktree being edited; a session in a worktree
    usually has it pointing at the shared tree, so the guard measured the wrong
    copy (measured 2026-10-15: a worktree re-fold to 47,918 characters was
    reported as the shared copy's 49,858). The mirror case follows: a worktree
    edit over the cap is not flagged while the shared copy is under it.

    Matched on whole path components. A string suffix also matched
    notrules/operational-context.md and then advised folding a different
    file (Codex on GRIP#8378).
    """
    if not file_path:
        return None
    path = Path(file_path)
    if path.name != TARGET_NAME or path.parent.name != TARGET_DIR:
        return None
    return path


def _compactor_cmd(target: Path) -> str:
    """The command that folds the edited tree's copy, quoted for a POSIX shell.

    The tree goes to the compactor as --root, never as CLAUDE_DIR: bin/grip-py
    looks for its venv under $CLAUDE_DIR, and a worktree has none, so the
    advice exited 1 in the very case it exists for (Codex on GRIP#8378).
    grip-py and the compactor come from this hook's own tree, so the advice
    never names a flag that tree's compactor lacks. Every token is quoted,
    because the path comes from the tool input and may hold shell syntax.
    """
    return shlex.join([
        str(INSTALL / "bin" / "grip-py"),
        str(INSTALL / "lib" / "operational_context_compactor.py"),
        "--root",
        str(target.parent.parent),
    ])


def main() -> None:
    hook_input = parse_hook_input()
    _, tool_input, _ = extract_tool_info(hook_input)

    target = _edited_target(tool_input.get("file_path", ""))
    if target is None:
        respond()
        return

    try:
        size = len(target.read_text(encoding="utf-8"))
    except OSError:
        respond()
        return

    if size <= MAX_CHARS:
        respond()
        return

    overage = size - MAX_CHARS
    respond(
        f"[OCF-GUARD] operational-context.md is {size:,} chars "
        f"({overage:+,} over the 40,000-char OCF invariant MAX_CHARS). "
        f"Run the compactor to restore the fold-property: `{_compactor_cmd(target)}`. "
        f"Canonical facts belong in the structured sections above; "
        f"session discoveries belong in KONO via /recall."
    )


if __name__ == "__main__":
    main()
