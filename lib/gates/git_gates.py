"""Git-related gates -- extracted from context-gate-hook.py (#933).

Gates 2, 2a, 3, H, I: Destructive git, push gate, secrets detection,
stash safety, index lock detection.
"""

import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .bash_gates import strip_heredocs
from .git_invocation import repository_options, without_global_options
from .patterns import (
    DESTRUCTIVE_GIT_PATTERNS,
    ENV_SAFE_SUFFIXES,
    GIT_LOCK_STALE_THRESHOLD_SECONDS,
    GIT_PUSH_PATTERN,
    GIT_WRITE_OPS,
    LONG_STRING_THRESHOLD,
    PROTECTED_BRANCHES,
    SENSITIVE_FILE_PATTERNS,
    STATE_DOTFILE_PATTERNS,
)

# Branch-protection bypass: light-touch yolo mode for the single legitimate
# direct-to-main push case. Mirrors the GRIP_ISOLATION_BYPASS pattern: opt-in
# via env var or inline command prefix, audit-logged, never silent.
#
# SAFETY INVARIANT: this bypass NEVER covers force push. Force push stays on
# the hard-blocked path with approval/autonomous-bypass only. Rewriting remote
# history is a different risk class to appending a commit on main.
_BRANCH_PROTECTION_BYPASS_ENV = "GRIP_BRANCH_PROTECTION_BYPASS"
_BRANCH_PROTECTION_BYPASS_LOG = Path.home() / ".claude" / "logs" / "branch-protection-bypass.log"
_BRANCH_PROTECTION_INLINE_PATTERN = re.compile(
    r"\bGRIP_BRANCH_PROTECTION_BYPASS=(?:1|true|yes)\b",
    re.IGNORECASE,
)
_BRANCH_PROTECTION_BYPASS_VALUES = {"1", "true", "yes"}


def is_destructive_git(command: str) -> str | None:
    """Detect a destructive git operation (force-push, hard reset, clean -fd,
    discard checkout/restore, stash drop), including one written with git's
    global options before the subcommand (``git -C <path> restore <file>``).

    DETECTION ONLY. Enforcement AND the autonomous/RSI/yolo bypass live one
    layer up in ``bash_orchestrator._run_destructive_git``, which calls
    ``is_autonomous_bypass_fn(gate)`` and ALLOWs the op for autonomous loops
    (and honours an explicit force-push approval). The 2026-04-02 ``return
    None`` disable put the bypass in the wrong layer: it crippled detection for
    ALL sessions — including interactive ones that *should* be gated — not just
    RSI sprints. Re-enabled 2026-06-24 (V>>--<<V): real detection here, the
    existing orchestrator-layer bypass keeps autonomous/yolo loops unblocked.

    Returns a human-readable description of the matched operation, or None.
    """
    if not command:
        return None
    view = without_global_options(command)
    for pattern in DESTRUCTIVE_GIT_PATTERNS:
        match = re.search(pattern, view)
        if match:
            return f"destructive git operation: {match.group(0).strip()}"
    return None


def _check_sensitive_patterns(cmd_lower: str) -> str | None:
    """Check command against known sensitive file patterns."""
    for pattern in SENSITIVE_FILE_PATTERNS:
        if pattern.lower() in cmd_lower:
            if pattern == '.env' and any(
                suffix in cmd_lower for suffix in ENV_SAFE_SUFFIXES
            ):
                continue
            return pattern
    return None


# Combine individual patterns into single regex — O(n) instead of O(n*m)
_COMBINED_STATE_DOTFILE = re.compile(
    "|".join(p.pattern for p in STATE_DOTFILE_PATTERNS)
)


def _check_state_dotfile_args(command: str) -> str | None:
    """Check individual file args against state dotfile patterns."""
    for part in command.split():
        basename = os.path.basename(part)
        if _COMBINED_STATE_DOTFILE.match(basename):
            return f"state dotfile: {basename}"
    return None


def _check_bulk_staging(command: str) -> str | None:
    """Warn when git add -A/. would stage many untracked files."""
    if not re.search(r"git\s+add\s+(-A|--all|\.\s*$)", command):
        return None
    try:
        import subprocess as _sp
        result = _sp.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        untracked = [
            ln[3:] for ln in result.stdout.splitlines()
            if ln.startswith("??")
        ]
        if len(untracked) > 10:
            return (
                f"bulk staging ({len(untracked)} untracked files). "
                f"Use 'git add -u' or stage specific files"
            )
    except Exception:
        pass
    return None


# A commit message (-m / --message value) is prose, never a file path.
# The sensitive-FILE scan substring-matches keyword patterns (api_key,
# private_key, signing-key, ...) which legitimately occur in message prose
# and in env-var NAMES mentioned there (e.g. ANTHROPIC_API_KEY). File paths
# reach git via `git add <path>` (no -m), so excising the message before the
# file-pattern scan cannot let a sensitive file through. Fail-safe: unbalanced
# quotes / unmatched flag leave the message in -> scanned in full -> >= prior
# safety. State-dotfile + bulk-staging checks still run on the full command.
_COMMIT_MESSAGE_ARG = re.compile(
    r"(?:^|\s)(?:--message|-[A-Za-z]*m)(?:=|\s+)"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s'\"]+)"
)


def _strip_commit_message(command: str) -> str:
    """Drop -m/--message argument values so the sensitive-FILE scan does not
    false-positive on keyword substrings inside commit-message prose."""
    return _COMMIT_MESSAGE_ARG.sub(" ", command)


# --------------------------------------------------------------------------
# Gate 3 precision core (issue: secrets-gate false positives).
#
# The prior scan substring-matched the whole command TEXT, so a `.env` (or any
# sensitive token) appearing in COMMIT-MESSAGE prose, a documented path, or a
# NON-git string literal (`python3 -c "... git add x ... foo.env"`) tripped the
# gate even though no secret file was being staged. The precise signal is the
# set of PATHSPEC ARGUMENTS the git add/commit invocation actually stages;
# these helpers resolve that set and match each file's BASENAME. `.env` is
# matched by the dotenv CONVENTION (basename is exactly `.env` or begins
# `.env.`), so a file that merely ENDS in `.env` (foo.env) is not a hit.
# --------------------------------------------------------------------------

# A leading `VAR=value` inline env-assignment (skipped before the program word).
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Shell statement separators that end one command segment (shlex emits these as
# their own tokens). A `git` word only starts an invocation at a segment head.
_SEGMENT_SEP_TOKENS = frozenset({"&&", "||", ";", "|", "&", "(", ")"})

# git add/commit options that consume the NEXT token as their value (a message,
# file, dir, author, or commit-ish) -- that token is never a staged pathspec.
# `--opt=value` forms carry the value inline (one token) and need no skip.
# Deliberately EXCLUDES -S/--gpg-sign (its key-id is attached-only: `-Skeyid`),
# so `git commit -S -m msg` does not mis-consume the following -m.
_GIT_PATHSPEC_VALUE_OPTS = frozenset({
    "-m", "--message", "-F", "--file", "-C", "--reuse-message",
    "-c", "--reedit-message", "--author", "--date",
    "--pathspec-from-file", "--chmod", "--squash", "--fixup",
})

# A clustered short-flag whose LAST letter consumes the next token, e.g.
# `-am` -> -a -m<msg>, `-F` file, `-C` commit. Pure short-flag clusters only.
_SHORT_FLAG_VALUE_TAIL = re.compile(r"^-[A-Za-z]*[mFCc]$")


def _dotenv_secret(low_basename: str) -> bool:
    """True if a lowercased basename is a dotenv-convention secret file.

    Matches ``.env`` exactly and ``.env.<name>`` (``.env.local``,
    ``.env.production``), MINUS the safe-example suffixes. A file that merely
    ends in ``.env`` (``foo.env``) is NOT matched -- its basename is not itself
    a dotenv file, so it is not treated as a secret.
    """
    if low_basename == ".env":
        return True
    if low_basename.startswith(".env."):
        return not any(low_basename.startswith(s) for s in ENV_SAFE_SUFFIXES)
    return False


def _sensitive_basename(basename: str) -> str | None:
    """Return the matched sensitive pattern for one file's basename, or None.

    ``.env`` uses the dotenv convention (``_dotenv_secret``); every other
    pattern matches as a substring of the basename (``server.pem``, ``id_rsa``,
    ``credentials.json``) -- scoped to the staged FILE NAME, never the whole
    command, which is what removes the message/literal false positives.
    """
    low = basename.lower()
    if _dotenv_secret(low):
        return ".env"
    for pattern in SENSITIVE_FILE_PATTERNS:
        if pattern == ".env":
            continue  # handled by the dotenv convention above
        if pattern.lower() in low:
            return pattern
    return None


def _consumes_value(flag: str) -> bool:
    """True if an option token consumes the following token as its value."""
    return flag in _GIT_PATHSPEC_VALUE_OPTS or bool(_SHORT_FLAG_VALUE_TAIL.match(flag))


def _git_addcommit_start(tokens: list) -> int:
    """Index of the first argument after a command-position ``git add|commit``.

    Returns -1 when this token segment is not such an invocation: skips leading
    ``VAR=value`` assignments, requires ``git`` (or ``.../git``) as the program
    word, steps over global ``-C <dir>`` / ``-c <cfg>`` options, then requires
    the ``add`` or ``commit`` subcommand. A ``git`` appearing only inside a
    quoted string is never a bare token here, so it is correctly ignored.
    """
    i, n = 0, len(tokens)
    while i < n and _ENV_ASSIGN.match(tokens[i]):
        i += 1
    if i >= n or not (tokens[i] == "git" or tokens[i].endswith("/git")):
        return -1
    i += 1
    while i < n and tokens[i].startswith("-"):
        i += 2 if tokens[i] in ("-C", "-c") and i + 1 < n else 1
    if i < n and tokens[i] in ("add", "commit"):
        return i + 1
    return -1


def _collect_pathspecs(args: list) -> list:
    """Positional pathspec tokens from a git add/commit argument list.

    Skips flags and any value a value-taking flag consumes; everything after a
    literal ``--`` is a pathspec. This is the set of files actually staged.
    """
    paths, i, n = [], 0, len(args)
    after_ddash = False
    while i < n:
        tok = args[i]
        if not after_ddash and tok == "--":
            after_ddash = True
            i += 1
        elif not after_ddash and tok.startswith("-"):
            i += 2 if _consumes_value(tok) else 1
        else:
            paths.append(tok)
            i += 1
    return paths


def _pathspecs_in_segment(tokens: list) -> list:
    """Pathspecs staged by a single shell segment ([] if it is not add/commit)."""
    start = _git_addcommit_start(tokens)
    return _collect_pathspecs(tokens[start:]) if start >= 0 else []


def _extract_staged_paths(command: str) -> list | None:
    """All pathspecs a command stages via git add/commit, or None if unparseable.

    Heredoc bodies are stripped first (they never define a staged path and
    would break the parser), then the command is tokenised with quote-aware
    shlex so message/literal prose stays inside single tokens. None signals a
    parse failure (unbalanced quotes) -> caller applies the conservative
    prior-behaviour fallback (fail toward the safety floor).
    """
    try:
        tokens = shlex.split(strip_heredocs(command), comments=False, posix=True)
    except ValueError:
        return None
    paths, segment = [], []
    for tok in tokens:
        if tok in _SEGMENT_SEP_TOKENS:
            paths.extend(_pathspecs_in_segment(segment))
            segment = []
        else:
            segment.append(tok)
    paths.extend(_pathspecs_in_segment(segment))
    return paths


def _first_sensitive_path(paths: list) -> str | None:
    """First staged path whose basename is a sensitive file, else None."""
    for path in paths:
        hit = _sensitive_basename(os.path.basename(path))
        if hit:
            return hit
    return None


def _first_state_dotfile(paths: list) -> str | None:
    """First staged path whose basename is a runtime state dotfile, else None."""
    for path in paths:
        basename = os.path.basename(path)
        if _COMBINED_STATE_DOTFILE.match(basename):
            return f"state dotfile: {basename}"
    return None


def _legacy_sensitive_scan(command: str) -> str | None:
    """Conservative prior behaviour: whole-command substring scan.

    Used ONLY when the command cannot be tokenised (unbalanced quotes). It
    over-blocks rather than under-blocks (message excision + substring match),
    so an unparseable command still fails toward the safety floor.
    """
    if not re.search(r"git\s+(add|commit)", command, re.IGNORECASE):
        return None
    return (
        _check_sensitive_patterns(_strip_commit_message(command).lower())
        or _check_state_dotfile_args(command)
        or _check_bulk_staging(command)
    )


def contains_sensitive_file(command: str) -> str | None:
    """Gate 3: Check if a git command would stage/commit a sensitive file.

    Resolves the PATHSPEC arguments the git add/commit invocation actually
    stages and matches each file's basename -- so a sensitive token appearing
    only in a commit message, a documented path, or a non-git string literal no
    longer false-positives, while genuinely staging ``.env`` / ``.env.local`` /
    ``*.pem`` / ``id_rsa`` / a credentials file still blocks. Layers:
    1. Sensitive file patterns on each staged basename (dotenv-aware for .env).
    2. State dotfile patterns on each staged basename.
    3. Bulk staging heuristic (git add -A/. with many untracked files).

    Falls back to the conservative whole-command scan when the command cannot
    be tokenised (unbalanced quotes -> fail toward the safety floor).
    """
    if not command:
        return None
    # Hot-path early-out: this gate runs on EVERY non-fast-path Bash command.
    # A command with no `git` word cannot be add/commit/bulk-staging, so skip
    # the tokenise/heredoc-strip work entirely (a `digit`-style false hit just
    # falls through the parser to None -- correctness is unaffected either way).
    if "git" not in command:
        return None
    paths = _extract_staged_paths(command)
    if paths is None:
        return _legacy_sensitive_scan(command)
    return (
        _first_sensitive_path(paths)
        or _first_state_dotfile(paths)
        or _check_bulk_staging(command)
    )


# Shell statement separators, used to scope push-arg classification and the
# printing-word check in is_string_literal_context. Naive on purpose: quoted
# separators over-split, which only ever SHRINKS a segment. For push args that
# errs toward not-force — the safe direction for a false-match fix (approval
# still guards real force pushes at the orchestrator layer). For the
# printing-word check it errs toward "a real command", so the push gate asks
# for approval rather than taking the push for text.
_SHELL_SEPARATORS = re.compile(r"&&|\|\||[;|&\n]")


def _args_after_push(tokens: list) -> list:
    """Tokens after the first ``push`` that follows a ``git`` word in one
    shell segment; empty when the segment holds no ``git push``. Single
    linear scan — each token is visited exactly once."""
    saw_git = False
    for i, tok in enumerate(tokens):
        if tok == "git" or tok.endswith("/git"):
            saw_git = True
        elif tok == "push" and saw_git:
            return tokens[i + 1:]
    return []


def _push_args(command: str) -> list:
    """Argument tokens following the ``push`` subcommand of each actual
    ``git push`` invocation (a ``git`` word must precede ``push`` in the
    same shell segment). Only these tokens are force-classified."""
    args = []
    for segment in _SHELL_SEPARATORS.split(command):
        args.extend(_args_after_push(segment.split()))
    return args


def is_force_push(command: str) -> bool:
    """Check if command is a force push.

    Scoped to the ``git push`` invocation's own arguments (issue #3804): the
    previous whole-command regex false-matched ``-f`` in unrelated compound
    segments (``rm -f "$GIT_INDEX_FILE" && git push origin sha:refs/...``).
    Force signals: ``--force`` / ``--force-with-lease`` / ``--force-if-includes``,
    a bare or clustered ``-f`` short flag, and a leading-``+`` force refspec.
    """
    if not command or "push" not in command:
        return False
    for tok in _push_args(command):
        if tok.startswith("--force"):
            return True
        if re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*", tok):
            return True
        if tok.startswith("+"):
            return True
    return False


# Options of ``git push`` that consume the NEXT token as their value, so the
# destination scan must skip it (--opt=value forms carry no separate token).
_PUSH_VALUE_OPTIONS = frozenset({
    "-o", "--push-option", "--repo", "--receive-pack", "--exec",
})


def _push_destinations(push_args: list) -> list:
    """Branch names a ``git push`` invocation actually TARGETS, resolved
    from its own argument tokens: the RHS of any refspec (``src:dst`` —
    including delete-refspecs ``:dst``), otherwise the bare branch token;
    ``refs/heads/`` prefixes stripped. Flags and their values are skipped;
    the first positional token is the remote, never a destination."""
    dests = []
    positional = 0
    skip_next = False
    for tok in push_args:
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("-"):
            if tok in _PUSH_VALUE_OPTIONS:
                skip_next = True
            continue
        positional += 1
        if positional == 1:
            continue  # the remote
        ref = tok.rsplit(":", 1)[-1] if ":" in tok else tok
        ref = ref.removeprefix("refs/heads/")
        if ref:
            dests.append(ref)
    return dests


def is_protected_branch_push(command: str) -> bool:
    """True only when a push actually TARGETS a protected branch.

    Issue #4382: the previous word-boundary regex over the joined arg
    string (``\\brelease\\b`` etc.) denied any branch whose NAME merely
    contains a protected word — ``release/agpl-prep``,
    ``feat/agpl-estate-release``, ``fix/main-menu`` — four independent
    false fires on 2026-08-06. The destination REF, resolved exactly, is
    what the gate protects; a protected word inside a longer branch name
    is not a protected branch.
    """
    dests = _push_destinations(_push_args(command))
    if dests:
        return any(d in PROTECTED_BRANCHES for d in dests)
    # No explicit destination (bare ``git push`` / ``git push origin`` /
    # tag-only pushes) -- conservatively fall back to the current branch of
    # the repository each push acts on, which ``-C``, ``--git-dir`` and
    # ``--work-tree`` can move away from the current directory.
    if "push" not in command:
        return False
    selections = repository_options(command, "push") or [[]]
    return any(_current_branch_is_protected(options) for options in selections)


def _current_branch_is_protected(options) -> bool:
    """Whether the repository these git options select has a protected
    branch checked out. ``None`` means the push names a repository the gate
    cannot read, so it fails safe."""
    if options is None:
        return True  # Fail safe
    try:
        result = subprocess.run(
            ["git", *options, "branch", "--show-current"],
            capture_output=True, text=True, timeout=5
        )
    except Exception:
        return True  # Fail safe
    if options and result.returncode != 0:
        return True  # Fail safe: the named repository cannot be read
    return result.stdout.strip() in PROTECTED_BRANCHES


_PRINTING_COMMAND = re.compile(r"\b(?:echo|print|printf|log|warn|info|error)\s")


def _ends_inside_quotes(text: str) -> bool:
    """Whether ``text`` ends inside a quoted string, reading quotes as the
    shell does: one kind of quote inside the other is a plain character, and
    a backslash escapes the next character outside single quotes."""
    quote = None
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif ch == quote:
            quote = None
        elif quote is None and ch in "'\"":
            quote = ch
    return quote is not None


def is_string_literal_context(command: str, match_start: int) -> bool:
    """Check if a regex match is inside a string literal, not an actual command.

    A printing word counts only in the same command as the match: in
    ``git log -1 && git push origin main`` the push is a command of its own
    (GRIP#8660).
    """
    prefix = command[:match_start]
    if _ends_inside_quotes(prefix):
        return True
    # An argument of echo, print, printf, log ... in the same command
    if _PRINTING_COMMAND.search(_SHELL_SEPARATORS.split(prefix)[-1]):
        return True
    # Inside heredoc
    if '<<' in prefix and 'EOF' in command[match_start:]:
        return True
    return False


def spawn_approval_reminder(action: str, message: str):
    """Spawn background process that sends a single macOS notification.

    Uses terminal-notifier with fallback to osascript.
    Dedup: skips if marker exists and is < 60s old.
    """
    import subprocess as _sp

    marker = Path.home() / ".claude" / ".pending-approval"

    if marker.exists():
        try:
            existing = json.loads(marker.read_text())
            ts = datetime.fromisoformat(existing.get("timestamp", ""))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age < 60:
                return
            old_pid = existing.get("notifier_pid")
            if old_pid:
                try:
                    os.kill(old_pid, 9)
                except (OSError, ProcessLookupError):
                    pass
        except (json.JSONDecodeError, ValueError, KeyError):
            pass

    proc = _sp.Popen(
        ["bash", "-c", """
sleep 30
if [ -f "$HOME/.claude/.pending-approval" ]; then
    msg=$(python3 -c "import json; print(json.load(open('$HOME/.claude/.pending-approval'))['message'])" 2>/dev/null || echo "Approval pending")
    if command -v terminal-notifier >/dev/null 2>&1; then
        terminal-notifier -title "GRIP Approval Needed" -message "$msg" \
            -sound default -group "grip-approval" \
            -open "x-apple.terminal://" 2>/dev/null
    else
        HAL_BIN="${HAL_HOME:-$HOME/.hal}/bin/hal"
        if [ -x "$HAL_BIN" ] && "$HAL_BIN" osa run notification \
            --param "title=GRIP Approval Needed" \
            --param "body=$msg" >/dev/null 2>&1; then
            :
        else
            osascript -e "display notification \\"$msg\\" with title \\"GRIP Approval Needed\\"" 2>/dev/null
        fi
    fi
fi
"""],
        start_new_session=True,
        stdout=_sp.DEVNULL,
        stderr=_sp.DEVNULL,
    )

    marker.write_text(json.dumps({
        "action": action,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "notifier_pid": proc.pid,
    }))


def _is_branch_protection_bypassed(command: str) -> bool:
    """True if GRIP_BRANCH_PROTECTION_BYPASS is set via env or inline prefix.

    Accepted values (case-insensitive): ``1``, ``true``, ``yes``.

    Two invocation styles:
      - session-wide env:    ``GRIP_BRANCH_PROTECTION_BYPASS=1 claude``
      - per-command inline:  ``GRIP_BRANCH_PROTECTION_BYPASS=1 git push origin main``

    The inline form is parsed from the command string because PreToolUse
    hooks run in Claude Code's parent env; a shell-level prefix on the
    tool-call command does not reach the hook process via os.environ.
    """
    env_val = os.environ.get(_BRANCH_PROTECTION_BYPASS_ENV, "").strip().lower()
    if env_val in _BRANCH_PROTECTION_BYPASS_VALUES:
        return True
    return bool(_BRANCH_PROTECTION_INLINE_PATTERN.search(command))


def _log_branch_protection_bypass(command: str) -> None:
    """Append a one-line audit record when the yolo bypass fires.

    Best-effort: disk errors are swallowed so a missing log directory
    never blocks the push. The record is deliberately plain text so it
    can be grepped without tooling.
    """
    try:
        _BRANCH_PROTECTION_BYPASS_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z") or time.strftime("%Y-%m-%dT%H:%M:%S")
        cwd = os.getcwd()
        # Truncate long commands so a giant URL-stuffed push never balloons the log
        redacted = command.strip().replace("\n", " ")[:200]
        with _BRANCH_PROTECTION_BYPASS_LOG.open("a") as f:
            f.write(f"{ts}\t{cwd}\t{redacted}\n")
    except OSError:
        pass  # Audit log is best-effort, never a blocker


def _build_push_deny(command: str, reason: str, log_hook) -> tuple[str, str]:
    """Construct the deny tuple + log entry for a blocked push."""
    log_hook("BLOCK", f"Git push gate ({reason}): {command}")
    hint = ""
    if reason == "protected branch":
        hint = (
            " To yolo a non-force push to main for a single command: "
            "GRIP_BRANCH_PROTECTION_BYPASS=1 git push origin main "
            "(audit-logged to ~/.claude/logs/branch-protection-bypass.log)."
        )
    return ("deny",
            f"[GUARDRAIL: GIT PUSH GATE] Push blocked ({reason}): the push's "
            f"resolved destination is a protected branch (main/master/develop/"
            f"release/production). Push a feature branch and merge via PR."
            f"{hint}")


def _consume_push_approval(command: str, check_approval, clear_approval, log_hook) -> bool:
    """Return True if a pending manual approval covers this push."""
    if not check_approval("git push"):
        return False
    log_hook("ALLOW", f"Git push approved (protected/force): {command}")
    clear_approval("force_push" if is_force_push(command) else "git_push")
    Path.home().joinpath(".claude", ".pending-approval").unlink(missing_ok=True)
    return True


def _check_protected_or_force_push(command: str, *, check_approval,
                                   is_autonomous_bypass, log_hook,
                                   clear_approval) -> tuple[str, str] | None:
    """Evaluate the bypass chain for a protected or force push.

    Called only when ``is_force_push(command) or is_protected_branch_push(command)``.
    Returns None if allowed, or a (decision, reason) tuple if denied.
    """
    force = is_force_push(command)
    auto_gate = "destructive-git-force" if force else "git-push-protected"
    if is_autonomous_bypass(auto_gate):
        return None

    # Light-touch yolo bypass: protected-branch push ONLY, never force push.
    # Force push stays on the hard-blocked path because rewriting remote
    # history is a different risk class to appending a commit on main.
    if not force and _is_branch_protection_bypassed(command):
        _log_branch_protection_bypass(command)
        log_hook(
            "ALLOW",
            f"Git push gate bypassed via {_BRANCH_PROTECTION_BYPASS_ENV}: {command}",
        )
        return None

    if _consume_push_approval(command, check_approval, clear_approval, log_hook):
        return None

    reason = "force push" if force else "protected branch"
    return _build_push_deny(command, reason, log_hook)


def check_git_push_gate(command: str, *, check_approval, is_autonomous_bypass,
                        log_hook, clear_approval) -> tuple[str, str] | None:
    """Gate 2a: Block force push and protected branch push.

    Returns (decision, reason) tuple or None if allowed.
    Callbacks avoid circular imports with the main hook.

    Bypass priority (first match wins):
      1. String-literal context (e.g. echo '... git push main ...') — allow.
      2. GRIP_AUTONOMOUS matching gate set — allow (via is_autonomous_bypass).
      3. GRIP_BRANCH_PROTECTION_BYPASS for non-force protected-branch push
         only — allow and audit-log. NEVER covers force push.
      4. Pending manual approval — allow and clear approval.
      5. Otherwise deny with PR-workflow guidance.
    """
    if not command:
        return None

    # Match on the command with git's global options removed, so
    # ``git -C <path> push`` is seen; judge the push on the full command.
    view = without_global_options(command)
    match = GIT_PUSH_PATTERN.search(view)
    if not match:
        return None

    if is_string_literal_context(view, match.start()):
        log_hook("ALLOW", f"Git push in string literal context: {command}")
        return None

    if is_force_push(command) or is_protected_branch_push(command):
        return _check_protected_or_force_push(
            command,
            check_approval=check_approval,
            is_autonomous_bypass=is_autonomous_bypass,
            log_hook=log_hook,
            clear_approval=clear_approval,
        )

    log_hook("ALLOW", f"Git push auto-allowed (feature branch): {command}")
    return None


INFRA_DIRS = ("hooks/", "lib/", "rules/", "skills/")
INFRA_TEST_MARKER = Path.home() / ".claude" / ".infra-tests-passed"
INFRA_TEST_FRESHNESS_SECONDS = 3600  # 1 hour


def _get_changed_infra_files() -> list[str]:
    """Get Python files in infra dirs changed in unpushed commits. Fail-open."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "@{u}..HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~1..HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return []
    except Exception:
        return []
    return [
        f for f in result.stdout.strip().split("\n")
        if any(f.startswith(d) for d in INFRA_DIRS) and f.endswith(".py")
    ]


def _is_marker_fresh() -> bool:
    """Check if infra test marker exists and is within freshness window."""
    if not INFRA_TEST_MARKER.exists():
        return False
    marker_age = time.time() - INFRA_TEST_MARKER.stat().st_mtime
    return marker_age < INFRA_TEST_FRESHNESS_SECONDS


def _format_infra_block(infra_files: list[str]) -> str:
    """Format the infra gate block message."""
    file_list = ", ".join(infra_files[:5])
    if len(infra_files) > 5:
        file_list += f" (+{len(infra_files) - 5} more)"
    return (
        f"[GUARDRAIL: INFRA TEST GATE] Push blocked — {len(infra_files)} infrastructure "
        f"file(s) changed: {file_list}. "
        f"Run tests first: `python3 -m pytest tests/ -x --tb=short` then retry. "
        f"Tests must pass within the last hour."
    )


def check_infra_test_gate(command: str, *, log_hook) -> str | None:
    """Gate T: Block push when infra files changed without recent tests.

    NOTE: Disabled by V>> for RSI sprint admin bypass (2026-04-02).
    """
    return None


def mark_infra_tests_passed():
    """Create/touch the marker file indicating tests passed recently."""
    INFRA_TEST_MARKER.parent.mkdir(parents=True, exist_ok=True)
    INFRA_TEST_MARKER.touch()


def _parse_stash_branch(options=()) -> str | None:
    """Extract the branch name from the most recent stash entry of the
    repository the git ``options`` select. Fail-open.

    Git records ``WIP on <branch>: ...`` for a stash with no message, and
    ``On <branch>: <message>`` for ``git stash push -m <message>``.
    """
    try:
        stash_info = subprocess.run(
            ["git", *options, "stash", "list", "-1", "--format=%s"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return None
    if not stash_info:
        return None
    match = re.search(r'\b(?:WIP on|On)\s+([^:]+):', stash_info)
    if not match:
        return None
    branch = match.group(1).strip()
    return None if ':' in branch else branch


def _get_current_branch(options=()) -> str | None:
    """Get the current branch of the repository the git ``options`` select.
    Fail-open."""
    try:
        return subprocess.run(
            ["git", *options, "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or None
    except Exception:
        return None


# Gates H, H2 and O read the repository a command acts on, which git's global
# options can name (``git -C <path> stash pop``). They match on the command
# with those options removed, and run their git lookups with the options
# ``repository_options`` reads. A quoted path of LONG_STRING_THRESHOLD or more
# characters reaches the gates blanked to "", so they cannot read that
# repository, and they fail safe. A ``cd <folder>`` before git counts as a
# leading ``-C <folder>`` (GRIP#8657). ``repository_options`` gives one entry
# per invocation: ``[]`` for the current folder, ``None`` for a blanked path or
# a change of folder it cannot follow. The ``or [[]]`` after it only covers a
# command in which it finds no invocation. A named repository that git cannot
# open from the gate, such as a Git Bash path (``/c/...``) on Windows, is
# unreadable too (``_cannot_open``, GRIP#8685).
_UNREADABLE_REPOSITORY = (
    "names a repository this gate cannot read: a quoted path of {limit} or "
    "more characters, which the gates see blanked, a path git cannot open from "
    "the gate (a folder that does not exist, or a Git Bash path such as `/c/...` "
    "on Windows), or a change of folder the gates do not follow (a `cd` that may "
    "not run before git, one next to `||`, a pipe or `&`, one in a closed "
    "subshell, a `case` or behind a keyword, `cd -`, `popd`, or a folder the gate "
    "cannot find). So this gate cannot read the repository. Name it directly "
    "instead, for example `git -C ~/repo {example}`, or with a `C:/...` path on "
    "Windows, so the gate can check it."
)


def _unreadable_repository(example: str) -> str:
    """The reason a gate gives when it cannot read the repository a command
    names."""
    return _UNREADABLE_REPOSITORY.format(limit=LONG_STRING_THRESHOLD, example=example)


def _shared_tree() -> Path:
    """GRIP's shared tree: ``CLAUDE_DIR``, else ``~/.claude``."""
    return Path(os.environ.get("CLAUDE_DIR") or str(Path.home() / ".claude"))


def _top_level(options) -> Path | None:
    """The top folder of the work tree the git ``options`` select, or ``None``
    when git cannot name one."""
    try:
        result = subprocess.run(
            ["git", *options, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    top = result.stdout.strip()
    return Path(top) if result.returncode == 0 and top else None


def _cannot_open(options) -> bool:
    """Whether the git ``options`` name a repository that git cannot open from
    the gate, so that every lookup in it fails (GRIP#8685). Only a named
    repository is judged, as in ``_current_branch_is_protected``: the current
    folder is left to git."""
    if not options:
        return False
    try:
        result = subprocess.run(
            ["git", *options, "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return True  # Fail safe
    return result.returncode != 0


_STASH_POP = re.compile(r"git\s+stash\s+(pop|apply)")


def check_stash_pop_safety(command: str, *, is_autonomous_bypass,
                           log_hook) -> str | None:
    """Gate H: Warn when stash pop targets a different branch, in the
    repository the command acts on."""
    if not command or not _STASH_POP.search(without_global_options(command)):
        return None
    for options in repository_options(command, "stash") or [[]]:
        finding = _stash_pop_finding(options)
        if finding and not is_autonomous_bypass("stash-safety"):
            log_hook("BLOCK", finding[0])
            return finding[1]
    return None


def _stash_pop_finding(options) -> tuple[str, str] | None:
    """For a stash pop in the repository the git ``options`` select: the log
    line and the reason to confirm it first, or ``None`` when it need not be."""
    if options is None or _cannot_open(options):
        return ("Stash pop safety: the named repository cannot be read",
                "[GUARDRAIL: STASH SAFETY] This stash pop " + _unreadable_repository("stash pop"))
    current = _get_current_branch(options)
    stash_branch = _parse_stash_branch(options)
    if not stash_branch or not current or stash_branch == current:
        return None
    return (
        f"Stash pop safety: stash from '{stash_branch}', current '{current}'",
        f"[GUARDRAIL: STASH SAFETY] Stash was created on '{stash_branch}' "
        f"but current branch is '{current}'. "
        f"Cross-branch stash pop can silently lose changes. "
        f"Use AskUserQuestion to confirm this is intentional.",
    )


_STASH_PROTECTED_PATHS = ("learning/", "data/genomes/", "cache/genome.json")
_STASH_DROP = re.compile(r"git\s+stash\s+(drop|clear)")


def _resolve_stash_ref(command: str, options=()) -> str | None:
    """Determine which stash ref is targeted by a drop/clear command, in the
    repository the git ``options`` select. Fail-open."""
    drop_match = re.search(r"stash@\{(\d+)\}", command)
    if drop_match:
        return f"stash@{{{drop_match.group(1)}}}"
    if "clear" in command:
        try:
            stash_list = subprocess.run(
                ["git", *options, "stash", "list"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            return "stash@{0}" if stash_list else None
        except Exception:
            return None
    return "stash@{0}"


def _holds_bytes(path: Path, data: bytes) -> bool:
    """Whether the file at ``path`` exists and holds exactly ``data``."""
    return path.exists() and path.read_bytes() == data


def _stash_work_tree(options):
    """The work tree a stash drop compares with: the shared tree when no
    repository is named, as before, else that repository's own work tree, or
    ``None`` when git cannot name one. Looked up once per stash, not per file."""
    return _top_level(options) if options else _shared_tree()


def _stash_differs_from_worktree(stash_ref: str, path: str, options, work_tree) -> bool:
    """True if stash's copy of `path` differs from BOTH working tree AND HEAD.

    Safe to drop (returns False) when the stash content is either:
    1. byte-identical to the current working tree, OR
    2. byte-identical to HEAD (already committed and therefore durable).

    Both pathways eliminate real false-positive blocks:
    - Case 1 fires after `git stash pop` that git kept for bookkeeping
      reasons even though every file landed cleanly.
    - Case 2 fires after a successful PR-merge + pull-rebase where the
      stash's learning content has already been committed to HEAD (and
      pushed) — dropping the stash loses nothing because the canonical
      copy lives in the commit graph.

    The stash and HEAD belong to the repository the git ``options`` select,
    and ``work_tree`` is that repository's work tree (``_stash_work_tree``),
    or ``None`` when git cannot name one.

    Fail-closed: any read/compare error assumes the content is at risk.
    """
    try:
        stash_blob = subprocess.run(
            ["git", *options, "show", f"{stash_ref}:{path}"],
            capture_output=True, timeout=5,
        )
        if stash_blob.returncode != 0:
            return True  # File only in stash (or read error) -- fail closed
        stash_bytes = stash_blob.stdout

        # Case 1: working tree byte-match
        if work_tree and _holds_bytes(work_tree / path, stash_bytes):
            return False

        # Case 2: HEAD byte-match (content already durably committed)
        head_blob = subprocess.run(
            ["git", *options, "show", f"HEAD:{path}"],
            capture_output=True, timeout=5,
        )
        if head_blob.returncode == 0 and head_blob.stdout == stash_bytes:
            return False

        # Genuinely different from both -- fail closed
        return True
    except Exception:
        return True


def _find_protected_stash_files(stash_ref: str, options=()) -> list[str]:
    """Get learning data files from a stash entry that would genuinely be lost.

    A file is flagged as at-risk only if its stash content differs from the
    current working-tree content. If they byte-match, popping already
    restored the data and dropping the stash is safe. This eliminates the
    false-positive block when `git stash pop` succeeds but git keeps the
    stash entry (which happens after rebase autostash when the apply is
    not atomic even though every file landed cleanly).
    """
    try:
        result = subprocess.run(
            ["git", *options, "stash", "show", "--name-only", stash_ref],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        candidates = [
            f for f in result.stdout.strip().split("\n")
            if any(f.startswith(p) or f == p.rstrip("/") for p in _STASH_PROTECTED_PATHS)
        ]
        if not candidates:
            return []
        work_tree = _stash_work_tree(options)
        return [f for f in candidates
                if _stash_differs_from_worktree(stash_ref, f, options, work_tree)]
    except Exception:
        return []


def check_stash_drop_learning_data(command: str, *, log_hook) -> str | None:
    """Gate H2: Block stash drop/clear when stash contains learning data, in
    the repository the command acts on.

    Origin: TODO #132, Gen 486. User directive after learning data loss.
    """
    if not command or not _STASH_DROP.search(without_global_options(command)):
        return None
    for options in repository_options(command, "stash") or [[]]:
        finding = _stash_drop_finding(command, options)
        if finding:
            log_hook("BLOCK", finding[0])
            return finding[1]
    return None


def _stash_drop_finding(command: str, options) -> tuple[str, str] | None:
    """For a stash drop in the repository the git ``options`` select: the log
    line and the reason to refuse it, or ``None`` when no learning data would
    be lost."""
    if options is None or _cannot_open(options):
        return ("Stash drop blocked: the named repository cannot be read",
                "[GUARDRAIL: STASH DATA PROTECTION] This stash drop "
                + _unreadable_repository("stash drop"))
    stash_ref = _resolve_stash_ref(command, options)
    if not stash_ref:
        return None
    protected = _find_protected_stash_files(stash_ref, options)
    if not protected:
        return None
    file_list = ", ".join(protected[:5])
    return (
        f"Stash drop blocked: learning data in stash: {protected}",
        f"[GUARDRAIL: STASH DATA PROTECTION] Stash contains learning data: {file_list}. "
        f"Dropping this stash will permanently lose this data. "
        f"First run 'git stash show -p {stash_ref}' to inspect, then "
        f"'git stash pop' to restore the data before dropping.",
    )


def _count_claude_sessions() -> int:
    """Count active Claude Code CLI sessions; at least 1 (the current session).

    Asks ``session_isolation.count_claude_sessions``, the counter Gate O's
    escalation already uses, so a block message shows the number the decision
    was made on. That counter also works on Windows, where Git Bash has no
    ``pgrep`` (GRIP#8835). Any error fails safe to 1.
    """
    try:
        from .session_isolation import count_claude_sessions
        return count_claude_sessions()
    except Exception:
        return 1  # Fail safe: assume single session


def _get_worktree_enforcement(cwd=None) -> str:
    """Determine worktree enforcement level via session_isolation delegation.

    Default ``relaxed`` preserves V>>'s 2026-04-02 RSI admin bypass for
    normal interactive work and single-session autonomous loops. Escalates
    to ``paramount`` only when the narrow three-way collision precondition
    holds: concurrent claude sessions, an RSI or ultrado context active,
    and the current tree is the shared main tree (not a worktree). See
    ``lib/gates/session_isolation.py`` and Rule 13 for the policy contract.

    ``cwd`` is the work tree a command switches when it names one with git's
    global options; ``None`` means the current directory.
    """
    try:
        from .session_isolation import enforcement_level
        return enforcement_level(cwd)
    except Exception:
        return "relaxed"  # Fail-safe: detection error must not over-block


def _is_safe_checkout(args: str) -> bool:
    """Check if checkout args are safe (file restore, main branch, dot)."""
    if args.startswith("-- ") or args.startswith("--"):
        return True
    if args == ".":
        return True
    return bool(re.match(r'^(origin/)?(main|master)\s*$', args))


def _is_safe_switch(args: str) -> bool:
    """Check if switch args are safe (main branch, previous branch)."""
    if args == "-":
        return True
    return bool(re.match(r'^(origin/)?(main|master)\s*$', args))


def _enforce_branch_gate(cmd: str, args: str, *,
                         enforcement: str, log_hook) -> str | None:
    """Apply warn/block logic for a branch switch. Shared by checkout/switch."""
    target = args.split()[0] if args else "unknown"
    if enforcement == "relaxed":
        log_hook("WARN", f"Branch switch in main session (relaxed): {target}")
        return None
    sessions = _count_claude_sessions()
    log_hook("BLOCK", f"Branch switch in main session ({sessions} session(s), {enforcement}): {target}")
    if enforcement == "paramount":
        return f"[GUARDRAIL: WORKTREE ISOLATION — PARAMOUNT] `{cmd} {args}` blocked — {sessions} concurrent claude session(s) detected AND an autonomous loop is active in the shared main tree (Rule 13). Enter a worktree first: `bash ~/.claude/lib/grip-worktree.sh add /tmp/<name> feat/<branch> && cd /tmp/<name>`. Override for one session only: `GRIP_ISOLATION_BYPASS=1` (audit-logged)."
    return f"[GUARDRAIL: WORKTREE ISOLATION] `{cmd} {args}` blocked — {sessions} concurrent session(s) detected. Use `Agent(isolation=\"worktree\")` for unencrypted repos, or `bash lib/grip-worktree.sh add /tmp/<name> feat/<branch>` for git-crypt repos."


# Dispatch: (regex, safe_check, git_cmd) — LSP: both subtypes honour same contract
_BRANCH_SWITCH_HANDLERS = [
    (r"git\s+checkout\s+(.*)", _is_safe_checkout, "git checkout"),
    (r"git\s+switch\s+(.*)", _is_safe_switch, "git switch"),
]


def check_branch_switch(command: str, *, log_hook) -> str | None:
    """Gate O: Block or warn on branch switching based on enforcement level,
    judged for the work tree the command switches.

    Origin: Gen 348, reinforced Gen 470, 475.
    """
    if not command:
        return None

    view = without_global_options(command)
    for pattern, is_safe, cmd in _BRANCH_SWITCH_HANDLERS:
        match = re.search(pattern, view)
        if not match:
            continue
        if is_string_literal_context(view, match.start()):
            log_hook("ALLOW", "Branch switch in string literal context")
            return None
        args = match.group(1).strip()
        if is_safe(args):
            return None
        return _enforce_branch_gate(
            cmd, args, enforcement=_switch_enforcement(command, cmd), log_hook=log_hook,
        )
    return None


def _switch_enforcement(command: str, cmd: str) -> str:
    """The strictest enforcement level over the work trees the command switches
    with ``cmd`` (``git checkout`` or ``git switch``)."""
    selections = repository_options(command, cmd.split()[-1]) or [[]]
    levels = [_get_worktree_enforcement(_switched_tree(options)) for options in selections]
    return next((level for level in levels if level != "relaxed"), "relaxed")


def _switched_tree(options) -> Path | None:
    """The work tree a branch switch acts on: ``None`` for the current
    directory, else the top folder of the repository the git ``options``
    select. A tree that cannot be read is judged as the shared tree, the
    strictest case."""
    if options == []:
        return None
    top = _top_level(options) if options else None
    return top or _shared_tree()


def find_git_dir(start_path: Path = None) -> Path | None:
    """Find .git directory by walking up from start_path or cwd."""
    path = start_path or Path.cwd()
    for parent in [path] + list(path.parents):
        git_dir = parent / ".git"
        if git_dir.is_dir():
            return git_dir
    return None


def get_lock_owner_pid(lock_path: Path) -> str:
    """Best-effort PID detection for lock holder via lsof.

    Unix-only. Returns descriptive string.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["lsof", str(lock_path)],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 2:
                    return f"Held by PID {parts[1]} ({parts[0]})"
    except (FileNotFoundError, PermissionError, Exception):
        pass
    return "Owner PID unknown"


def _resolve_git_dir(command: str) -> Path | None:
    """Find the .git directory, respecting -C flag if present."""
    c_match = re.search(r"git\s+-C\s+(\S+)", command)
    if c_match:
        target = Path(c_match.group(1)).expanduser()
        if target.is_dir():
            found = find_git_dir(target)
            if found:
                return found
    return find_git_dir()


def _get_lock_age(lock_path: Path) -> float | None:
    """Get age of lock file in seconds. Returns None if no lock."""
    try:
        if not lock_path.exists():
            return None
        return time.time() - lock_path.stat().st_mtime
    except (FileNotFoundError, OSError):
        return None


def check_git_index_lock(command: str) -> str | None:
    """Gate I: Detect .git/index.lock before git write operations.

    NOTE: Disabled by V>> for RSI sprint admin bypass (2026-04-02).
    """
    return None
