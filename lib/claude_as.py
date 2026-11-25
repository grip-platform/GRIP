"""Run ONE `claude` process as one stored account, and nothing else as it.

A long-lived token exported from a shell profile makes every process started
from that shell a holder of it: every script, every agent session and every
child of those. This module hands the token to exactly one process instead. It
reads one label's file from the owner-only token store
(``lib/claude_token_store.py``), builds an environment that carries that token
and nothing that would outrank or redirect it, and hands both to ``claude``.

WHAT OUTRANKS THE TOKEN. The vendor's order of credentials
(code.claude.com/docs/en/authentication, "Authentication precedence", read
2026-10-01) puts four things above ``CLAUDE_CODE_OAUTH_TOKEN``:

1. a cloud provider, when ``CLAUDE_CODE_USE_BEDROCK``, ``CLAUDE_CODE_USE_VERTEX``
   or ``CLAUDE_CODE_USE_FOUNDRY`` is set;
2. ``ANTHROPIC_AUTH_TOKEN``;
3. ``ANTHROPIC_API_KEY`` ("In non-interactive mode (-p), the key is always
   used when present");
4. an ``apiKeyHelper`` in a settings file.

The first three are variables, and the child is started without them. The
fourth lives in a settings file, as can an ``env`` block that sets any of these
variables for every session. This module does not edit settings: it refuses,
and names the file. It refuses ``--bare`` as well, because "Bare mode does not
read CLAUDE_CODE_OAUTH_TOKEN" (same page), so the run would use something else
or nothing.

A SETTINGS FILE CAN ALSO REPLACE THE TOKEN. Measured 2026-10-02 with the real
command (2.1.283): a settings file's ``env`` block wins over the process
environment. With a made-up token in the block and a working one in the
environment, the call was refused with status 401. So an ``env`` block that
sets ``CLAUDE_CODE_OAUTH_TOKEN`` itself is refused too: the run would be billed
to whatever that block names, under this label's name. (An ``apiKeyHelper``
that is the empty string was measured the same day: it is ignored, the token is
used, and it is no reason to refuse.)

Three more variables are removed although they do not outrank the token:
``ANTHROPIC_BASE_URL`` (the token would be sent to whatever that names instead
of to the vendor), ``CLAUDE_CODE_SIMPLE`` (it switches bare mode on) and
``ANTHROPIC_PROFILE`` (so that "which credential ran this" has one answer).

NOT COVERED, and stated rather than solved:

- A signed-in Claude apps gateway session outranks everything above, and no
  variable switches it off.
- Managed (policy) settings are not read.
- A ``--settings`` value is checked only when it is a readable file or inline
  JSON. One that cannot be read is not judged.
- The token is in the environment of the process that runs. Anything that
  process starts inherits it. That is how the vendor designed this credential,
  and a wrapper cannot narrow it.
- The run still uses the caller's Claude Code directory (settings, hooks,
  history). Only the account that is billed changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import claude_token_store  # noqa: E402

TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"

#: Present in the environment, each of these is used INSTEAD of the token.
OUTRANKS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
            "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")

#: These do not outrank the token, but change where it goes or whether it is read.
REDIRECTS = ("ANTHROPIC_BASE_URL", "CLAUDE_CODE_SIMPLE", "ANTHROPIC_PROFILE")

#: Claude Code's own default directory. Not GRIP's (``CLAUDE_DIR``): the two
#: are usually the same place and are still two different settings.
_CLAUDE_CODE_DEFAULT_DIR = "~/.claude"
_USUAL_PLACES = ("~/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude")


class Refused(Exception):
    """This run would not use the stored account, or cannot be made safely."""


@dataclass
class Run:
    """One planned run: what is started, with what, and what was left out."""
    claude: str
    argv: list[str]
    env: dict
    label: str
    removed: list[str]
    digest: str


def child_env(base, token: str) -> dict:
    """``base`` without anything that outranks or redirects the token, plus
    the token itself. ``base`` is not changed."""
    dropped = set(OUTRANKS + REDIRECTS)
    env = {name: value for name, value in base.items() if name not in dropped}
    env[TOKEN_VAR] = token
    return env


def removed_from(base) -> list[str]:
    """Which of those variables were present and are not passed on. Names
    only: their values are never reported."""
    return sorted(name for name in OUTRANKS + REDIRECTS if name in base)


def read_label(label: str, env=None) -> str:
    """The token stored under ``label``. Refuses a name that is not a label,
    and a file that is missing, that others can read, or that holds no token."""
    problem = claude_token_store.label_problem(label)
    if problem:
        # Quoted back only after scrubbing: a token typed where the label goes
        # must not be repeated by the message that refuses it.
        named = claude_token_store.scrub(repr(label))[0]
        raise Refused(f"{named} cannot be a token label: {problem}")
    strict = claude_token_store.uses_default_store(env)
    token, source = claude_token_store.read_active_token(
        claude_token_store.labelled_path(label, env), strict=strict)
    if source.startswith("unsafe:"):
        raise Refused(f"the token file for {label!r} is not private "
                      f"({source.split(':', 1)[1]}); fix that before using it")
    if not token:
        raise Refused(f"no token is stored as {label!r}. See: grip-relay-bill list")
    if not claude_token_store.TOKEN_SHAPE.fullmatch(token):
        raise Refused(f"the file stored as {label!r} does not hold a token")
    return token


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def _is_inline(source: str) -> bool:
    return source.lstrip().startswith("{")


def _load_settings(source: str):
    """A settings document, from a file path or from inline JSON. None when it
    is neither readable nor JSON, which this module cannot judge."""
    text = source if _is_inline(source) else _read(Path(source).expanduser())
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def _settings_problem(settings) -> str | None:
    """What in one settings document would outrank, redirect or replace the token."""
    if not isinstance(settings, dict):
        return None
    if settings.get("apiKeyHelper"):
        return "an apiKeyHelper, which outranks the stored token"
    block = settings.get("env")
    names = set(block) if isinstance(block, dict) else set()
    if TOKEN_VAR in names:
        return (f"an env block that sets {TOKEN_VAR}, which would replace the stored token "
                "with its own")
    clash = sorted(names & set(OUTRANKS + REDIRECTS))
    if clash:
        return ("an env block that sets " + ", ".join(clash)
                + ", which this run is started without")
    return None


def user_settings_dir(env) -> Path:
    """Where Claude Code reads the user's settings for this run: the directory
    ``CLAUDE_CONFIG_DIR`` names, else Claude Code's own default."""
    named = (env.get("CLAUDE_CONFIG_DIR") or "").strip()
    return Path(named or _CLAUDE_CODE_DEFAULT_DIR).expanduser()


def settings_sources(args: list[str], config_dir: Path, cwd: Path) -> list[str]:
    """Every settings document this run would read that this module can see:
    the user's, the project's two, and any named with ``--settings``."""
    fixed = [config_dir / "settings.json", cwd / ".claude" / "settings.json",
             cwd / ".claude" / "settings.local.json"]
    spaced = [value for flag, value in zip(args, args[1:]) if flag == "--settings"]
    joined = [arg.split("=", 1)[1] for arg in args if arg.startswith("--settings=")]
    return [str(path) for path in fixed] + spaced + joined


def refusal(args: list[str], config_dir: Path, cwd: Path) -> str | None:
    """Why this run would not use the stored account, or None when it would."""
    if "--bare" in args:
        return "--bare was given, and bare mode does not read the stored token"
    for source in settings_sources(args, config_dir, cwd):
        problem = _settings_problem(_load_settings(source))
        if problem:
            where = "the --settings value" if _is_inline(source) else source
            return f"{where} has {problem}"
    return None


def find_claude(env) -> str:
    """The ``claude`` command: on PATH first, then where it is usually installed."""
    on_path = shutil.which("claude", path=env.get("PATH"))
    places = [on_path] if on_path else [str(Path(place).expanduser()) for place in _USUAL_PLACES]
    for place in places:
        if os.access(place, os.X_OK):
            return place
    raise Refused("the `claude` command was not found on PATH or in its usual places")


def plan(label: str, args: list[str], base_env, cwd: Path) -> Run:
    """Everything needed to run ``claude`` as ``label``, or raise ``Refused``.

    The settings are checked before the token is read, so a run that is going
    to be refused never touches the store.
    """
    why = refusal(args, user_settings_dir(base_env), cwd)
    if why:
        raise Refused(why)
    token = read_label(label, base_env)
    claude = find_claude(base_env)
    return Run(claude=claude, argv=[claude, *args], env=child_env(base_env, token),
               label=label, removed=removed_from(base_env),
               digest=hashlib.sha256(token.encode()).hexdigest()[:12])


def describe(run: Run) -> str:
    """What a run would do, for ``--check``. Never the token, and never a
    token that happens to be among the arguments either."""
    passed_on = claude_token_store.scrub(" ".join(run.argv[1:]))[0]
    return "\n".join([
        f"grip-claude-as: would run {run.claude} as {run.label!r}",
        f"  stored token:  digest {run.digest} (the token itself is never shown)",
        f"  not passed on: {', '.join(run.removed) or 'nothing that was set'}",
        f"  arguments:     {passed_on or '(none)'}",
    ])


USAGE = """usage: grip-claude-as <label> [--check] [--] [claude arguments...]

Runs one `claude` process as the account stored under <label>
(see: grip-relay-bill list). The token goes into that one process's
environment and nowhere else; it is never printed.

  --check   say what would be run and what would be left out, and run nothing
"""


def parse(argv: list[str]) -> tuple[str, bool, list[str]]:
    """Split the command line into the label, whether to only check, and what
    is passed on to ``claude``. Raises ``Refused`` with the usage text."""
    if not argv or argv[0].startswith("-"):
        raise Refused(USAGE.rstrip())
    label, rest = argv[0], list(argv[1:])
    check = rest[:1] == ["--check"]
    rest = rest[1:] if check else rest
    return label, check, rest[1:] if rest[:1] == ["--"] else rest


def main(argv: list[str], env=None, cwd: Path | None = None, execute=os.execve) -> int:
    """Run ``claude`` as the label, or say why not. Returns only when nothing
    was run: on a real run this process becomes ``claude``."""
    env = os.environ if env is None else env
    if argv[:1] in (["-h"], ["--help"]):
        print(USAGE.rstrip())
        return 0
    try:
        label, check, args = parse(argv)
        run = plan(label, args, env, cwd or Path.cwd())
    except Refused as why:
        print(f"[grip-claude-as] {why}", file=sys.stderr)
        return 2
    if check:
        print(describe(run))
        return 0
    execute(run.claude, run.argv, run.env)
    return 0
