"""ultraplan_remote -- the two launch shapes /ultraplan hands to the cloud.

/ultraplan plans in a fresh cloud session. Claude Code 2.1.283 offers two ways to
start one, and this module builds both -- it performs neither:

1. ``claude --cloud "<prompt>"`` (the default): one cloud session, cloned from the
   current branch on GitHub, with nothing left behind afterwards.
   :func:`cloud_command` returns that argv.
2. A one-time routine. ``RemoteTrigger(action="create")`` saves a ROUTINE, and a
   session exists only when it fires. :func:`build_routine` returns the create
   body. The API cannot delete a routine (anthropics/claude-code#83066, closed as
   not planned), so only ``run_once_at`` is ever built: it fires once and then
   disables itself. ``cron_expression`` would keep firing with no way to stop it
   from here.

The body shape is the "Create body shape" section of the /schedule skill bundled
with Claude Code 2.1.283, the only place it is documented. The body skills/ultraplan
documented before this module ({"name", "prompt", "max_turns"}) was rejected by the
API (GRIP#6859). tests/test_ultraplan_remote.py pins the shape against the CLI.

Both paths need the claude.ai login, and an API credential in the environment
takes precedence over it. :func:`refuse_failover_key_export` says so before either
path is used, naming variables only. This module never reads a credential's value,
never calls RemoteTrigger, and makes no network call; ``--exec`` hands the process
to ``claude`` and nothing else.

Run it under the GRIP venv:
    grip-py lib/ultraplan_remote.py --prompt-file plan.md
    grip-py lib/ultraplan_remote.py --routine --prompt-file plan.md \\
        --environment-id <id> --run-once-at 2026-10-03T08:07:00Z
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from itertools import accumulate
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

REPO_ROOT = _LIB.parent
GITATTRIBUTES = REPO_ROOT / ".gitattributes"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_TOOLS = ("Read", "Grep", "Glob")
# The routines troubleshooting page names both variables as taking precedence
# over a claude.ai login (code.claude.com/docs/en/routines).
LOGIN_OVERRIDES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
EXIT_REFUSED = 2


def _unset_prefix() -> tuple[str, ...]:
    prefix = ["env"]
    for name in LOGIN_OVERRIDES:
        prefix += ["-u", name]
    return tuple(prefix)


UNSET_PREFIX = _unset_prefix()


# --- git-crypt paths: ciphertext in any cloud clone ---------------------------

# One .gitattributes line: optional indent, a pattern that is not a comment, then
# attributes, one of which starts with filter=git-crypt (any named key).
_CRYPT_LINE = re.compile(r"^[ \t]*(?!#)(\S+)(?:[ \t]+\S+)*?[ \t]+filter=git-crypt", re.M)


def encrypted_patterns(gitattributes: Path = GITATTRIBUTES) -> tuple[str, ...]:
    """The patterns on .gitattributes lines that set ``filter=git-crypt*``.

    A later line that unsets the filter is not modelled. None exists today, and
    the error would be the safe one: a path refused, never one let through. A
    repository with no .gitattributes has no encrypted paths.
    """
    try:
        text = Path(gitattributes).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    return tuple(match.group(1) for match in _CRYPT_LINE.finditer(text))


_GLOB_TOKEN = re.compile(r"/\*\*\Z|\*\*/|\*\*|\*|\?|\[!?[^\]/]+\]|/|[^*?\[/]+|\[")
_GLOB_FIXED = {"/**": "(?:/.*)?", "**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}


def _glob_part(token: str) -> str:
    if token in _GLOB_FIXED:
        return _GLOB_FIXED[token]
    if len(token) > 2 and token[0] == "[" and token[-1] == "]":
        body = token[1:-1].replace("\\", "\\\\")
        return "[^" + body[1:] + "]" if body.startswith("!") else "[" + body + "]"
    return re.escape(token)


def _pattern_source(pattern: str) -> str:
    anchored = "/" in pattern.rstrip("/")
    body = "".join(_glob_part(token) for token in _GLOB_TOKEN.findall(pattern.strip("/")))
    return ("" if anchored else "(?:.*/)?") + body + r"\Z"


def pattern_regex(pattern: str) -> re.Pattern[str]:
    """One gitattributes glob as a regex over a repository-relative path.

    gitignore rules: a slash before the last character anchors the pattern at the
    repository root; without one it matches at any depth. ``dir/**`` also matches
    ``dir`` itself, so a prompt that names the directory is caught as well.
    """
    return re.compile(_pattern_source(pattern))


_SPLIT = re.compile(r"""[\s,;:=()<>{}"'`|]+""")


def _path_like(text: str) -> Iterable[str]:
    """Tokens that could name a file: they carry a slash or a dot. Bare words are
    skipped, because ``prep`` and ``drafts`` are English as often as paths."""
    for raw in _SPLIT.split(text):
        token = raw.strip("[]").rstrip(".!?")
        if "/" in token or "." in token:
            yield token.rstrip("/")


def _suffixes(token: str) -> list[str]:
    """``a/b/c`` -> ``c``, ``b/c``, ``a/b/c``: every trailing part of a path."""
    parts = [part for part in token.split("/") if part not in ("", ".")]
    return list(accumulate(reversed(parts), lambda tail, part: f"{part}/{tail}"))


def git_crypt_references(text: str, patterns: Iterable[str]) -> list[tuple[str, str]]:
    """Each ``(token, pattern)`` where a path in ``text`` names an encrypted path.

    A token counts when it, or any trailing part of it, matches -- so
    ``~/.claude/drafts/x.md`` and an absolute path into the checkout are caught.
    All patterns are tried as one alternation; ``lastgroup`` names the winner.
    """
    patterns = tuple(patterns)
    if not patterns:
        return []
    union = re.compile("|".join(
        f"(?P<p{index}>{_pattern_source(pattern)})" for index, pattern in enumerate(patterns)
    ))
    hits = []
    for token in _path_like(text):
        match = next(filter(None, map(union.match, _suffixes(token))), None)
        if match:
            hits.append((token, patterns[int(match.lastgroup[1:])]))
    return hits


def _refuse_git_crypt(text: str, what: str, patterns: tuple[str, ...]) -> None:
    hits = git_crypt_references(text, patterns)
    if hits:
        named = "; ".join(f"{token} (git-crypt pattern {pattern})" for token, pattern in hits)
        raise ValueError(
            f"{what} names a git-crypt path: {named}. A cloud clone holds those files "
            "as ciphertext, so the cloud session cannot read them. Remove the reference."
        )


# --- the prompt and the cloud command -------------------------------------------

def _check_prompt(prompt: object, patterns: tuple[str, ...]) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("The prompt is empty, so there is nothing for the cloud session to plan.")
    _refuse_git_crypt(prompt, "The prompt", patterns)
    return prompt.strip()


# `claude --cloud X` CREATES a session only when X is a description: a session id
# or a claude.ai/code URL means "attach", and a leading "-" reads as a flag.
_NOT_A_DESCRIPTION = re.compile(r"-.*|(?:session_|cse_)\S+|(?:https?://)?claude\.ai/code/\S+", re.S)


def cloud_command(prompt: str, *, gitattributes: Path = GITATTRIBUTES) -> list[str]:
    """``["claude", "--cloud", prompt]`` -- one new cloud session -- or ValueError."""
    text = _check_prompt(prompt, encrypted_patterns(gitattributes))
    if _NOT_A_DESCRIPTION.fullmatch(text):
        raise ValueError(
            "claude --cloud would not create a session from this prompt: a leading '-' "
            "reads as a flag, and a bare session id or claude.ai/code URL means 'attach'. "
            "Start the prompt with words."
        )
    return ["claude", "--cloud", text]


# --- the one-time routine body ------------------------------------------------------

def _parse_instant(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("run_once_at is required: a UTC time such as 2026-10-03T08:07:00Z.")
    text = value.strip()
    text = text[:-1] + "+00:00" if text[-1] in "zZ" else text
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"run_once_at {value!r} is not an RFC 3339 time such as 2026-10-03T08:07:00Z."
        ) from None


def utc_run_once_at(value: object, now: datetime | None = None) -> str:
    """``value`` in the API's form, YYYY-MM-DDTHH:MM:SSZ, or ValueError.

    Refused: a time with no offset (ambiguous), a non-zero offset (not UTC), and
    any time not strictly after ``now`` -- the API rejects a one-time run in the
    past. ``now`` must be timezone-aware; it defaults to the current UTC time.
    """
    when = _parse_instant(value)
    if when.tzinfo is None or when.utcoffset() != timedelta(0):
        raise ValueError(
            f"run_once_at {value!r} is not UTC. Convert it to UTC and end it with Z, "
            "for example 2026-10-03T08:07:00Z."
        )
    when = when.astimezone(timezone.utc).replace(microsecond=0)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if when <= current:
        raise ValueError(
            f"run_once_at {when:%Y-%m-%dT%H:%M:%SZ} is not in the future "
            f"(it is now {current:%Y-%m-%dT%H:%M:%SZ})."
        )
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _environment(environment_id: object) -> str:
    if not isinstance(environment_id, str) or not environment_id.strip():
        raise ValueError(
            "No cloud environment id. A routine must name the environment it runs in. "
            "/schedule lists your environments with their ids; record the one you use "
            "in rules/operational-context.md (git-crypt), never in a plaintext file."
        )
    return environment_id.strip()


def _model(model: object) -> str:
    if not isinstance(model, str) or not model.strip():
        raise ValueError("No model named for the routine.")
    return model.strip()


# GitHub owners cannot contain a dot, so "github.com/x" is never read as OWNER/REPO.
_SHORT_REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")


def normalise_repo_url(url: object) -> str:
    """https://github.com/OWNER/REPO from that form, OWNER/REPO, an SSH remote or a .git URL."""
    text = url.strip().rstrip("/") if isinstance(url, str) else ""
    text = text[:-4] if text.endswith(".git") else text
    if text.startswith("git@github.com:"):
        text = "https://github.com/" + text[len("git@github.com:"):]
    elif _SHORT_REPO.fullmatch(text):
        text = "https://github.com/" + text
    if not text.startswith("https://") or text.count("/") < 4:
        raise ValueError(f"{url!r} is not a repository URL such as https://github.com/OWNER/REPO.")
    return text


def origin_repo_url(repo_root: Path = REPO_ROOT) -> str:
    """The checkout's ``origin`` remote, normalised. Local git only, no network."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"No --repo-url given and {repo_root} has no origin remote.") from exc
    return normalise_repo_url(done.stdout.strip())


def _tools(allowed_tools: object, patterns: tuple[str, ...]) -> list[str]:
    if isinstance(allowed_tools, str):
        raise ValueError("allowed_tools must be a list of tool names, not one string.")
    tools = list(allowed_tools or ())
    if not tools or not all(isinstance(tool, str) and tool.strip() for tool in tools):
        raise ValueError(
            "allowed_tools must name at least one tool. An empty list is refused "
            "because the API does not document what it means."
        )
    _refuse_git_crypt(" ".join(tools), "A tool rule", patterns)
    return [tool.strip() for tool in tools]


def _user_event(prompt: str) -> dict:
    return {"data": {
        "uuid": str(uuid.uuid4()),
        "session_id": "",
        "type": "user",
        "parent_tool_use_id": None,
        "message": {"content": prompt, "role": "user"},
    }}


def build_routine(
    prompt: str,
    environment_id: str,
    repo_url: str,
    run_once_at: str | datetime,
    model: str = DEFAULT_MODEL,
    allowed_tools: Iterable[str] = DEFAULT_TOOLS,
    *,
    gitattributes: Path = GITATTRIBUTES,
    now: datetime | None = None,
) -> dict:
    """The RemoteTrigger create body for ONE planning run, or ValueError.

    Shape: the /schedule skill's "Create body shape" (Claude Code 2.1.283) in its
    one-time form. ``mcp_connections`` is left out, so the run reaches no
    connector; the default tools are read-only. Every call mints a fresh v4 uuid.
    """
    patterns = encrypted_patterns(gitattributes)
    instant = utc_run_once_at(run_once_at, now)
    return {
        "name": "ultraplan-" + instant.replace("-", "").replace(":", ""),
        "run_once_at": instant,
        "enabled": True,
        "job_config": {"ccr": {
            "environment_id": _environment(environment_id),
            "session_context": {
                "model": _model(model),
                "sources": [{"git_repository": {"url": normalise_repo_url(repo_url)}}],
                "allowed_tools": _tools(allowed_tools, patterns),
            },
            "events": [_user_event(_check_prompt(prompt, patterns))],
        }},
    }


# --- the failover key: an API credential outranks the claude.ai login ---------------

def credentials_in(env: Mapping[str, str]) -> list[str]:
    """Names, never values, of the login-overriding variables set non-empty in ``env``."""
    return [name for name in LOGIN_OVERRIDES if (env.get(name) or "").strip()]


def default_marker_path() -> Path:
    """The file ~/.zshrc tests before exporting ANTHROPIC_API_KEY: the auth-failover marker.

    Same resolution as the shell: $GRIP_AUTH_FAILOVER_MARKER, else
    <CLAUDE_DIR>/state/session/auth-failover.json.
    """
    override = os.environ.get("GRIP_AUTH_FAILOVER_MARKER", "").strip()
    if override:
        return Path(override).expanduser()
    return get_claude_dir() / "state" / "session" / "auth-failover.json"


def refuse_failover_key_export(env: Mapping[str, str], marker_path: Path) -> str | None:
    """A refusal message when a cloud launch would run on an API credential, else None.

    ``claude --cloud`` and RemoteTrigger need the claude.ai login, and an API key
    or auth token takes precedence over it. Two ways that happens here: the
    credential is already in ``env``, or the auth-failover marker exists, so
    ~/.zshrc exports ANTHROPIC_API_KEY into every new shell -- including the one a
    printed command is pasted into. The message names variables, never values.
    """
    present = credentials_in(env)
    engaged = Path(marker_path).is_file()
    if not present and not engaged:
        return None
    causes = []
    if present:
        causes.append(", ".join(present) + (" is" if len(present) == 1 else " are")
                      + " set in this environment")
    if engaged:
        causes.append(f"the auth-failover marker {marker_path} exists, so ~/.zshrc "
                      "exports ANTHROPIC_API_KEY into every new shell")
    return (
        "Refusing the cloud launch -- " + "; ".join(causes) + ". An API credential "
        "takes precedence over the claude.ai login, and claude --cloud then fails with "
        "'Unable to get organization UUID'. Either clear the marker with "
        "`grip-auth-failover --reset` (local sessions return to the subscription too), "
        "or pass --unset-api-key to launch under `env -u ANTHROPIC_API_KEY -u "
        "ANTHROPIC_AUTH_TOKEN`."
    )


# --- command line -------------------------------------------------------------------

_ROUTINE_ONLY = (
    ("environment_id", "--environment-id"), ("run_once_at", "--run-once-at"),
    ("repo_url", "--repo-url"), ("model", "--model"), ("allowed_tools", "--allowed-tool"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ultraplan_remote",
        description="Print what /ultraplan needs to plan in the cloud. Launches nothing "
                    "unless --exec is given, and never creates a routine itself.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--cloud", dest="mode", action="store_const", const="cloud",
                      help="print the claude --cloud command (the default)")
    mode.add_argument("--routine", dest="mode", action="store_const", const="routine",
                      help="print the RemoteTrigger create body for a one-time routine")
    parser.set_defaults(mode="cloud")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--prompt", help="the planning prompt")
    source.add_argument("--prompt-file", help="read the prompt from this file; '-' reads stdin")
    parser.add_argument("--environment-id", help="routine: the cloud environment id")
    parser.add_argument("--run-once-at", help="routine: UTC fire time, e.g. 2026-10-03T08:07:00Z")
    parser.add_argument("--repo-url", help="routine: repository (default: this checkout's origin)")
    parser.add_argument("--model", help=f"routine: the model (default {DEFAULT_MODEL})")
    parser.add_argument("--allowed-tool", dest="allowed_tools", action="append",
                        help="routine: a tool the run may use; repeat "
                             f"(default {' '.join(DEFAULT_TOOLS)})")
    parser.add_argument("--unset-api-key", action="store_true",
                        help="answer a failover refusal: --cloud launches under env -u for "
                             "both variables; --routine is accepted when only the marker exists")
    parser.add_argument("--exec", dest="exec_", action="store_true",
                        help="cloud: replace this process with the command instead of printing it")
    return parser


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        return args.prompt
    if args.prompt_file == "-":
        return sys.stdin.read()
    return Path(args.prompt_file).read_text(encoding="utf-8")


def _launch_refusal(args: argparse.Namespace, env: Mapping[str, str]) -> str | None:
    refusal = refuse_failover_key_export(env, default_marker_path())
    if refusal is None or not args.unset_api_key:
        return refusal
    present = credentials_in(env)
    if args.mode == "routine" and present:
        return (
            f"--unset-api-key cannot help a routine: this environment carries "
            f"{', '.join(present)}, and RemoteTrigger uses this Claude Code session's own "
            "login. Relaunch the session with `env -u ANTHROPIC_API_KEY -u "
            "ANTHROPIC_AUTH_TOKEN claude`, then retry."
        )
    return None


def _emit_cloud(args: argparse.Namespace, prompt: str) -> int:
    given = [flag for dest, flag in _ROUTINE_ONLY if getattr(args, dest)]
    if given:
        raise ValueError(
            ", ".join(given) + " only shape a routine. claude --cloud takes its repository "
            "and branch from the current checkout and its environment from /remote-env. "
            "Pass --routine, or drop them."
        )
    argv = list(UNSET_PREFIX) * bool(args.unset_api_key) + cloud_command(prompt)
    if not args.exec_:
        print(shlex.join(argv))
        return 0
    print("ultraplan_remote: launching " + shlex.join(argv[:-1]) + " <prompt>", file=sys.stderr)
    os.execvp(argv[0], argv)
    return 0


def _emit_routine(args: argparse.Namespace, prompt: str) -> int:
    if args.exec_:
        raise ValueError("--exec launches claude --cloud; a routine is created by passing "
                         "the printed body to RemoteTrigger, so --exec has no meaning here.")
    body = build_routine(
        prompt, args.environment_id, args.repo_url or origin_repo_url(), args.run_once_at,
        model=args.model or DEFAULT_MODEL,
        allowed_tools=tuple(args.allowed_tools or DEFAULT_TOOLS),
    )
    print(json.dumps(body, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        refusal = _launch_refusal(args, os.environ)
        if refusal:
            print(refusal, file=sys.stderr)
            return EXIT_REFUSED
        prompt = _read_prompt(args)
        emit = _emit_routine if args.mode == "routine" else _emit_cloud
        return emit(args, prompt)
    except (ValueError, OSError) as exc:
        print(f"ultraplan_remote: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
