"""The operator's private grip-browser configuration: a persistent profile, a
proxy, an artefact root.

Shaped like ``lib/wa_identities.py``: running code reads the values from the
gitignored ``config/grip-browser.local.json`` (shape in the tracked
``config/grip-browser.example.json``, every value an obvious fake) and never
from this repository, because a browser profile directory and a proxy URL are
identity signals (``rules/operator-anonymity.md``).

Why the validation is strict. ``spawn_browser(user_data_dir=...)`` opens
whatever profile it is given, so the ONLY profile the gate ever lets through is
the one configured here, and this reader refuses the ones that must never be
driven unattended: a relative path (resolved against whatever the server's
working directory happens to be), a path inside the GRIP tree (a repo is not a
place for a browser profile, and the tree is shared and mirrored), and the
daily browsers' own profile roots (Chrome, Brave, Chromium, Edge, Arc): those
are the operator's real identity, which belongs to the attended browser.

With no file, or no valid ``profile_dir``, the session is EPHEMERAL: a fresh
``uc_*`` profile per task and nothing to spend. That is the v1 default by
decision (plan question 3: no persistent profile until H-C holds for 30 days).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

FILE_ENV = "GRIP_BROWSER_CONFIG"
FILE_NAME = "grip-browser.local.json"
EXAMPLE_NAME = "grip-browser.example.json"
KEYS = ("profile_dir", "proxy", "scratch_dir")

# Where the daily browsers keep the operator's real profiles, per platform,
# relative to HOME (macOS, Linux) or LOCALAPPDATA (Windows). A configured
# profile under any of these is refused.
DAILY_BROWSER_ROOTS_HOME = (
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/BraveSoftware/Brave-Browser",
    "Library/Application Support/Chromium",
    "Library/Application Support/Microsoft Edge",
    "Library/Application Support/Arc",
    ".config/google-chrome",
    ".config/BraveSoftware",
    ".config/chromium",
    ".config/microsoft-edge",
)
DAILY_BROWSER_ROOTS_LOCALAPPDATA = (
    "Google/Chrome/User Data",
    "BraveSoftware/Brave-Browser/User Data",
    "Chromium/User Data",
    "Microsoft/Edge/User Data",
)


@dataclass(frozen=True)
class BrowserConfig:
    profile_dir: Path | None
    proxy: str | None
    scratch_dir: Path | None
    problems: tuple = ()

    @property
    def persistent(self) -> bool:
        return self.profile_dir is not None


def config_path(env: dict | None = None) -> Path:
    """Where the private file is read from on this machine."""
    environ = os.environ if env is None else env
    override = (environ.get(FILE_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return get_claude_dir() / "config" / FILE_NAME


def load(env: dict | None = None) -> dict:
    """The private file's contents, or {} when missing, unreadable or not an object."""
    try:
        data = json.loads(config_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _home(env: dict | None) -> Path:
    environ = os.environ if env is None else env
    home = (environ.get("HOME") or "").strip()
    return Path(home) if home else Path.home()


def forbidden_roots(env: dict | None = None) -> tuple:
    """Every directory a configured path may not lie under, resolved."""
    environ = os.environ if env is None else env
    roots = [get_claude_dir()]
    home = _home(env)
    roots += [home / rel for rel in DAILY_BROWSER_ROOTS_HOME]
    local = (environ.get("LOCALAPPDATA") or "").strip()
    if local:
        roots += [Path(local) / rel for rel in DAILY_BROWSER_ROOTS_LOCALAPPDATA]
    return tuple(_resolve(r) for r in roots)


def _resolve(path: Path) -> Path:
    try:
        return Path(os.path.realpath(str(path)))
    except (OSError, ValueError):
        return path


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_dir(value, key: str, env: dict | None = None) -> tuple:
    """(Path or None, problem or None) for a directory value from the file."""
    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip():
        return None, f"{key}: not a non-empty string"
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        return None, f"{key}: {value!r} is relative; a configured path must be absolute"
    resolved = _resolve(raw)
    for root in forbidden_roots(env):
        if _under(resolved, root):
            return None, (f"{key}: {value!r} lies under {root}, which is the GRIP tree or "
                          f"a daily browser's own profile root; refusing")
    return resolved, None


def validate_proxy(value) -> tuple:
    """(proxy string or None, problem or None). Only the scheme is checked here;
    upstream parses the rest (src/proxy_utils.py)."""
    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip():
        return None, "proxy: not a non-empty string"
    text = value.strip()
    if not text.split("://", 1)[0].lower() in {"http", "https", "socks5", "socks4", "socks5h"}:
        return None, f"proxy: {text.split('://', 1)[0]!r} is not a proxy scheme"
    return text, None


def read(env: dict | None = None) -> BrowserConfig:
    """The validated configuration. An invalid value is DROPPED and recorded in
    ``problems``, never used: a bad profile_dir makes the session ephemeral,
    it does not make the gate guess."""
    data = load(env)
    problems = []
    profile, p = validate_dir(data.get("profile_dir"), "profile_dir", env)
    if p:
        problems.append(p)
    scratch, p = validate_dir(data.get("scratch_dir"), "scratch_dir", env)
    if p:
        problems.append(p)
    proxy, p = validate_proxy(data.get("proxy"))
    if p:
        problems.append(p)
    unknown = sorted(k for k in data if k not in KEYS and not k.startswith("_"))
    if unknown:
        problems.append(f"unknown keys ignored: {', '.join(unknown)}")
    return BrowserConfig(profile_dir=profile, proxy=proxy, scratch_dir=scratch,
                         problems=tuple(problems))


def main(argv: list[str]) -> int:
    cfg = read()
    print(f"file:        {config_path()} ({'present' if load() else 'absent or empty'})")
    print(f"profile_dir: {cfg.profile_dir or '(none: ephemeral)'}")
    print(f"proxy:       {cfg.proxy or '(none)'}")
    print(f"scratch_dir: {cfg.scratch_dir or '(default)'}")
    for problem in cfg.problems:
        print(f"problem:     {problem}")
    return 1 if cfg.problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
