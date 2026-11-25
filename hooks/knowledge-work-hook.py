"""Knowledge-Work Quality Gate — PostToolUse hook for GRIP Power.

Fires on Write/Edit of non-code documents (legal, analysis, strategy, brainstorm).
Runs two systems:
  1. wire_knowledge_work() — 8 quality domains scored into CUBE
  2. external_reviewer — Gemini/Groq independent validation (async, non-blocking),
     for a file GRIP already shares and nothing else (see _may_leave)

Origin: Gen 475 falsification. V>> confirmed these were merged but never wired.
This hook closes the gap between code existing and code executing.

Version: 1.2.0 (2026-10-05: the outside review was sending the first 3,000
characters of ANY high-stakes document, wherever it lived, so private and
legal files reached the provider. It now sends only a file inside a GRIP
checkout that git does not ignore. Rule: rules/personal-matters-stay-private.md.)
Version: 1.1.0 (2026-08-18: wire_knowledge_work now takes claude_dir; the TypeError
fallback that monkeypatched CLAUDE_DIR is gone — the primary path is the path).
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from hook_utils import parse_hook_input, respond, extract_tool_info
from grip_checkout import git as _git, grip_checkout_root  # noqa: E402
from state_paths import FEATURE_KNOWLEDGE_WORK_DEBOUNCE, state_file  # noqa: E402

# Document extensions that trigger knowledge-work scanning
KNOWLEDGE_EXTENSIONS = {
    ".md", ".html", ".txt", ".tex", ".adoc", ".rst",
    ".json", ".yaml", ".yml", ".csv",
}

# High-stakes patterns that trigger external review
HIGH_STAKES_PATTERNS = [
    "agreement", "contract", "legal", "bond", "mortgage",
    "purchase", "sale", "deposit", "retainer", "clause",
    "warranty", "breach", "indemnif", "liability",
    "shareholder", "director", "company",
    "hypothesis", "claim", "evidence", "falsif",
    "decision", "strategy", "analysis",
]

# Minimum content length to trigger (avoid noise on tiny edits)
MIN_CONTENT_LENGTH = 200

# Debounce: don't re-scan same file within this window
DEBOUNCE_SECONDS = 30
# Under state/features/ since April audit task D5; it was a dotfile at the install root.
DEBOUNCE_FILE = state_file(FEATURE_KNOWLEDGE_WORK_DEBOUNCE)

# Log file
LOG_FILE = Path.home() / ".claude" / "logs" / "knowledge-work.jsonl"


def _load_debounce():
    try:
        return json.loads(DEBOUNCE_FILE.read_text())
    except Exception:
        return {}


def _save_debounce(data):
    try:
        DEBOUNCE_FILE.write_text(json.dumps(data))
    except Exception:
        pass


def _should_debounce(file_path: str) -> bool:
    data = _load_debounce()
    last = data.get(file_path, 0)
    now = time.time()
    if now - last < DEBOUNCE_SECONDS:
        return True
    data[file_path] = now
    # Prune old entries
    data = {k: v for k, v in data.items() if now - v < 300}
    _save_debounce(data)
    return False


def _is_knowledge_file(file_path: str) -> bool:
    ext = Path(file_path).suffix.lower()
    return ext in KNOWLEDGE_EXTENSIONS


def _is_high_stakes(content: str) -> bool:
    content_lower = content.lower()
    matches = sum(1 for p in HIGH_STAKES_PATTERNS if p in content_lower)
    return matches >= 3


def _log_entry(entry: dict):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _run_knowledge_work_scan(claude_dir: Path = None):
    """Run wire_knowledge_work() — wire measured knowledge dimensions into CUBE.

    Dimensions whose source is missing are skipped with ERROR logs (fail-loud);
    the CUBE never receives invented default scores (fix(cube) e8c274a8, same
    disease as fix(genome) #4263).

    Args:
        claude_dir: Path to CLAUDE_DIR. Defaults to ~/.claude.
    """
    if claude_dir is None:
        claude_dir = Path.home() / ".claude"
    try:
        import cube_session_wire
        return cube_session_wire.wire_knowledge_work(claude_dir=claude_dir)
    except Exception as e:
        return {"error": str(e)}


def _may_leave(file_path: str) -> bool:
    """True only for a file GRIP already shares: inside a GRIP checkout and not git-ignored.

    Everything else stays on this machine: a legal folder, the private guardian
    repository, session memory (git-ignored), another project, a scratch file.
    Fails closed: when git cannot answer, the file stays. lib/grip_checkout.py.
    """
    path = Path(file_path).expanduser()
    top = grip_checkout_root(path)
    if top is None:
        return False
    code, _ = _git(top, "check-ignore", "-q", str(path))
    return code == 1  # 0 = ignored, 1 = not ignored, anything else = unknown


def _run_external_review_async(content: str, context: str, review_type: str):
    """Launch external review as background subprocess (non-blocking).

    Both texts are scrubbed whole and only then cut: a cut first could split a
    name in two, and the shield the reviewer applies later recognises neither half.
    """
    try:
        from review_redaction import redact
    except Exception:  # noqa: BLE001 -- without the shield, nothing is sent
        return False
    review_script = Path.home() / ".claude" / "hooks" / "_external_review_runner.py"
    payload = json.dumps({"content": redact(content)[:3000], "context": redact(context)[:300],
                          "type": review_type})
    try:
        proc = subprocess.Popen(
            [sys.executable, str(review_script), payload],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return proc.pid > 0
    except Exception:
        return False


def _should_skip(tool_name, tool_input):
    """Check if this tool call should be skipped (wrong tool, wrong file, debounced)."""
    if tool_name not in ("Write", "Edit"):
        return True
    file_path = tool_input.get("file_path", "")
    if not file_path or not _is_knowledge_file(file_path):
        return True
    grip_paths = ["/hooks/", "/skills/", "/agents/", "/modes/", "/rules/", "/lib/"]
    if any(gp in file_path for gp in grip_paths):
        return True
    return _should_debounce(file_path)


def _extract_content(tool_input):
    """Extract document content from tool input."""
    content = tool_input.get("content", "") or tool_input.get("new_string", "")
    return content


def _classify_review_type(content):
    """Classify content into external review type."""
    content_lower = content.lower()
    if any(w in content_lower for w in ["agreement", "contract", "legal", "clause", "breach"]):
        return "DECISION"
    if any(w in content_lower for w in ["hypothesis", "claim", "falsif", "evidence"]):
        return "CLAIM"
    return "KNOWLEDGE"


def _build_message(kw_scores, review_sent):
    """Build human-readable output message from scan results.

    ``review_sent`` is whether an outside review was actually dispatched, which
    since 1.2.0 is not the same as the content being high-stakes.
    """
    parts = ["[GRIP POWER] Knowledge-work scan complete"]
    if isinstance(kw_scores, dict) and "error" not in kw_scores:
        avg = sum(kw_scores.values()) / max(len(kw_scores), 1)
        parts.append(f"8-domain avg: {avg:.0%}")
        low = [k for k, v in kw_scores.items() if v < 0.4]
        if low:
            parts.append(f"Low: {', '.join(low)}")
    if review_sent:
        parts.append("External review dispatched (async)")
    return " | ".join(parts)


def main():
    hook_input = parse_hook_input()
    if not hook_input:
        respond()
        return

    tool_name, tool_input, _tool_output = extract_tool_info(hook_input)
    if _should_skip(tool_name, tool_input):
        respond()
        return

    content = _extract_content(tool_input)
    if len(content) < MIN_CONTENT_LENGTH:
        respond()
        return

    file_path = tool_input.get("file_path", "")
    kw_scores = _run_knowledge_work_scan()
    high_stakes = _is_high_stakes(content)
    send = high_stakes and _may_leave(file_path)

    log = {"ts": time.time(), "file": file_path, "tool": tool_name,
           "content_length": len(content), "kw_scores": kw_scores, "high_stakes": high_stakes}

    if send:
        review_type = _classify_review_type(content)
        context = f"File: {Path(file_path).name}, Type: {review_type}"
        launched = _run_external_review_async(content, context, review_type)
        log["external_review"] = "launched" if launched else "failed"
    elif high_stakes:
        log["external_review"] = "withheld: not a shared GRIP file"

    _log_entry(log)
    respond(_build_message(kw_scores, send))


if __name__ == "__main__":
    main()
