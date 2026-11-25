"""Raw web text in this turn, then a send: hold the send for the operator.

GRIP#8959, part 2. A session that reads a web page also holds real tools, and a page can
carry text aimed at the agent (rules/untrusted-content-capability-lock.md). This module
judges one pending Bash command in three steps.

1. Does the command send data out? ``classify_sends`` reads each simple command the shell
   would run. A send to a destination outside the trusted owners is a HOLD: an upload with
   curl, wget or HTTPie, a raw socket, a remote copy or shell, mail, a gh write to another
   owner's repository or to a gist, or a git push to an address outside them. A routine
   send is a NOTE: a git push to a trusted remote, a gh write to a trusted repository, a
   GraphQL mutation, or a request that carries data in a long address.
2. Did raw web text enter this turn? ``turn_sources`` reads the transcript back to the
   operator's last message, the record whose origin kind is "human": a WebFetch, a
   WebSearch, a browser page, a fetch command to a public address, a gh read of another
   owner's repository, or a read of a file that a fetch wrote.
3. Both: by default a note goes into Claude's context and the audit log records whether
   the send would have been held. With GRIP_EGRESS_GATE=hold in the launch environment, a
   HOLD send is denied and the reason names the release, the operator's next message.
   GRIP_EGRESS_GATE=off records only (lib/untrusted_turn.py has the modes).

The trusted owners are the GitHub accounts gh is signed in to (hosts.yml) and the owner of
this checkout's origin remote, so no personal name is written here.

Why deny and not ask: GRIP sessions run in bypassPermissions mode, where the vendor states
that permission prompts are off (code.claude.com/docs/en/permission-modes, read
2026-11-08), and a -p run denies an ask anyway. A deny holds in every mode.

Known gaps, named rather than hidden: a send from inside a script (python, node) or from a
here-document fed to a shell (bodies are dropped as text, lib/teach_once.strip_heredocs), a
command inside a process substitution, a GraphQL mutation's real target, a repository
cloned and then read, text that stays in context after the operator's next message, and a
fetched file read under a path spelt differently from the fetch. Trust is read from GitHub
owners only, so in hold mode a push to an origin on another host is held, and so is a push
to a remote the same command adds, because the lookup runs before the command.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from teach_once import strip_heredocs
from web_fetch_harness import private_host

HOLD = "hold"
NOTE = "note"
LONG_QUERY = 200
#: Per git lookup. A bare push makes at most five, so the gate stays inside its 10 s binding.
GIT_TIMEOUT_S = 1
OPAQUE_ADDRESS = "an address not written as a URL"
#: A local address written without a scheme, which curl and wget accept: `localhost:4010/v1`.
_BARE_LOCAL = re.compile(r"^(?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1\])(?::\d+)?(?:/\S*)?$", re.IGNORECASE)

_SEPARATORS = frozenset({";", "&&", "||", "|", "|&", "&", "(", ")", ";;"})
_PIPES = frozenset({"|", "|&"})
_OUTPUT_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_URL = re.compile(r"^https?://", re.IGNORECASE)
#: `user@host:path` or `host:path`; a one-letter drive (`C:`) and a URL (`https://`) are not remotes.
_SCP_REMOTE = re.compile(r"^[\w.-]+@[\w.-]+:|^[\w.-]{2,}:(?!//)")
_API_REPO = re.compile(r"^/?(?:https?://[^/]+/)?repos/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)")
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})


@dataclass(frozen=True)
class Send:
    """One way a command sends data out."""

    kind: str
    tier: str
    target: str = ""


@dataclass
class Command:
    """One simple command: its words, its output files and the assignments written in front."""

    words: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    assigns: dict = field(default_factory=dict)
    #: (parse, pipeline): commands joined by `|` share it, so a fetch's output can be followed.
    pipeline: tuple = (0, 0)


@dataclass
class Context:
    """What a judgement needs beyond the command: where it runs and who is trusted."""

    cwd: str = ""
    owners: frozenset | None = None

    def trusted(self) -> frozenset:
        if self.owners is None:
            self.owners = trusted_owners(self.cwd)
        return self.owners


# ---------------------------------------------------------------- shell words


def _one_line(command: str) -> str:
    """The command with unquoted newlines as `;`, backslash-newlines joined and comments dropped."""
    out, quote, at = [], "", 0
    while at < len(command):
        char = command[at]
        if char == "\\" and quote != "'" and command[at + 1:at + 2] == "\n":
            out.append(" ")
            at += 2
            continue
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "#" and (not out or out[-1][-1:] in " \t;&|("):
            end = command.find("\n", at)
            at = len(command) if end < 0 else end
            continue
        out.append(" ; " if char == "\n" and not quote else char)
        at += 1
    return "".join(out)


#: Shell operators, for the fallback below: the longest spellings first.
_OPERATOR_SPLIT = re.compile(r"(\|\||&&|;;|\|&|>>|&>|[;|&()<>\n])")


def _fallback_tokens(command: str) -> list:
    """Words and operators when shlex gives up on quoting bash accepts (`$'don\\'t'`).

    It splits on every operator, quoted or not, so it can see more commands than the shell
    would and never fewer: a send glued to a decoy (`echo $'a\\'b';curl -d @f URL`) stays a send.
    AGORA on GRIP#9003: the old `command.split()` lost every operator not set off by spaces.
    """
    tokens = []
    for piece in _OPERATOR_SPLIT.split(command):
        tokens += [piece] if _OPERATOR_SPLIT.fullmatch(piece) else [w.strip("'\"") for w in piece.split()]
    return [token for token in tokens if token.strip()]


def _tokens(command: str) -> list:
    """Shell words, with here-document bodies dropped first: their lines are text, not commands."""
    text = strip_heredocs(command)
    lexer = shlex.shlex(_one_line(text), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return _fallback_tokens(text)


def _is_punct(token: str) -> bool:
    return bool(token) and set(token) <= set("();<>|&")


def simple_commands(command: str, parse: int = 0) -> list:
    """Each simple command the shell would run, with the files its output goes to and its pipeline."""
    commands, pipeline, redirect = [], 0, ""
    current = Command(pipeline=(parse, pipeline))
    for token in _tokens(command):
        if redirect:
            if redirect in _OUTPUT_REDIRECTS:
                current.outputs.append(token)
            redirect = ""
        elif _is_punct(token) and token in _SEPARATORS:
            commands.append(current)
            pipeline += 0 if token in _PIPES else 1
            current = Command(pipeline=(parse, pipeline))
        elif _is_punct(token):
            if current.words and current.words[-1].isdigit():
                current.words.pop()
            redirect = token
        else:
            current.words.append(token)
    commands.append(current)
    return [c for c in commands if c.words or c.outputs]


# ------------------------------------------------------------- the program run


def _skip_options(words: list, at: int, takes_value: frozenset) -> int:
    while at < len(words) and words[at].startswith("-"):
        at += 2 if words[at] in takes_value else 1
    return at


_WRAPPERS = {
    "rtk": frozenset(), "sudo": frozenset({"-u", "-g", "-C", "-h", "-p"}), "command": frozenset(),
    "builtin": frozenset(), "exec": frozenset({"-a"}), "nohup": frozenset(), "time": frozenset(),
    "nice": frozenset({"-n"}), "caffeinate": frozenset({"-t", "-w"}),
    "xargs": frozenset({"-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a"}),
}


def _strip_wrappers(words: list, assigns: dict) -> list:
    """The program and its arguments, past assignments, env, timeout and the wrappers above."""
    at = 0
    while at < len(words):
        word = words[at]
        if _ASSIGNMENT.match(word):
            name, value = word.split("=", 1)
            assigns[name] = value
            at += 1
        elif word == "env":
            at = _skip_options(words, at + 1, frozenset({"-u", "-C", "-S"}))
        elif word in ("timeout", "gtimeout"):
            at = _skip_options(words, at + 1, frozenset({"-k", "-s"})) + 1
        elif word == "rtk" and words[at + 1:at + 2] == ["proxy"]:
            at += 2
        elif word in _WRAPPERS:
            at = _skip_options(words, at + 1, _WRAPPERS[word])
        else:
            return words[at:]
    return []


def program(cmd: Command) -> tuple:
    """(program name, its arguments) for a simple command, or ("", [])."""
    rest = _strip_wrappers(cmd.words, cmd.assigns)
    if not rest:
        return "", []
    name = Path(rest[0]).name.lower()
    return (name[:-4] if name.endswith(".exe") else name), rest[1:]


#: Shell options that take the next word, so `bash -o pipefail -c CMD` still finds CMD.
_SHELL_VALUES = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})


def shell_command_string(args: list) -> str | None:
    """The string a shell runs because of -c, read through clusters (`bash -ec`, `sh -lc`), or None.

    AGORA on GRIP#9003, round 5: only a bare `-c` was read, so `bash -ec '<send>'` hid the send.
    """
    seen_c, at = False, 0
    while at < len(args):
        arg = args[at]
        if arg in _SHELL_VALUES or arg == "--":
            at += 2 if arg in _SHELL_VALUES else 1
            continue
        if not arg.startswith(("-", "+")):
            return arg if seen_c else None
        seen_c = seen_c or (arg[:1] == "-" and arg[1:2] != "-" and "c" in arg[1:])
        at += 1
    return None


def _inner_commands(name: str, args: list, parse: int) -> list:
    """The commands a shell or eval would run from its string argument, in parse number PARSE."""
    string = shell_command_string(args) if name in _SHELLS else None
    if string:
        return simple_commands(string, parse)
    if name == "eval" and args:
        return simple_commands(" ".join(args), parse)
    return []


def expand_commands(command: str, depth: int = 3) -> list:
    """Simple commands, with the commands inside `bash -c` and `eval` strings added.

    Each inner string is its own parse, so its pipelines never merge with the outer command's.
    """
    found, queue, parses = [], [(cmd, depth) for cmd in simple_commands(command)], 0
    while queue:
        cmd, left = queue.pop(0)
        found.append(cmd)
        name, args = program(cmd)
        inner = _inner_commands(name, args, parses + 1) if left > 0 else []
        parses += 1 if inner else 0
        queue.extend((each, left - 1) for each in inner)
    return found


# ------------------------------------------------------------ addresses, owners


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_public(url: str) -> bool:
    host = host_of(url)
    return bool(host) and not private_host(host)


#: `git@github.com:owner/repo.git` and `host:path`, the forms git writes without a scheme.
_SCP_ADDRESS = re.compile(r"^(?:[\w.-]+@)?(?P<host>[\w.-]{2,}):(?!//)(?P<path>.+)$")
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


def _github_path(address: str) -> list:
    """The path segments of an address whose real host is GitHub's, or [] for any other host."""
    address = address.strip()
    try:
        host, path = (host_of(address), urlsplit(address).path) if "://" in address else ("", "")
    except ValueError:
        return []
    match = None if "://" in address else _SCP_ADDRESS.match(address)
    if match:
        host, path = match["host"].lower(), match["path"]
    return [segment for segment in path.split("/") if segment] if host in _GITHUB_HOSTS else []


def owner_of_url(url: str) -> str:
    """The owner in a GitHub address, judged by its real host: `https://evil.example/github.com/a/b` has none."""
    segments = _github_path(url)
    return segments[0].lower() if len(segments) >= 2 else ""


def repo_in_urls(words: list) -> str:
    """owner/repo from the first GitHub URL among WORDS, which gh accepts in place of a number."""
    found = next((segments for segments in map(_github_path, words) if len(segments) >= 2), [])
    return f"{found[0]}/{found[1].removesuffix('.git')}" if found else ""


def _gh_hosts_file() -> Path:
    base = os.environ.get("GH_CONFIG_DIR") or str(Path.home() / ".config" / "gh")
    return Path(base) / "hosts.yml"


def gh_users(path: Path | None = None) -> set:
    """The accounts gh is signed in to: the `user:` values and the keys under `users:`."""
    try:
        lines = (path or _gh_hosts_file()).read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    users, block = set(), None
    for line in lines:
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if block is not None and indent <= block:
            block = None
        if stripped == "users:":
            block = indent
        elif block is not None and stripped.endswith(":"):
            users.add(stripped[:-1].lower())
        elif stripped.startswith("user:"):
            users.add(stripped.split(":", 1)[1].strip().lower())
    return {user for user in users if user}


def git_output(cwd: str, location: list, args: list) -> str:
    """One line from git run in CWD with the command's own -C, --git-dir and --work-tree, or ""."""
    try:
        done = subprocess.run(["git", "-C", cwd or ".", *location, *args], capture_output=True,
                              text=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def remote_url(cwd: str, name: str, location: tuple = (), push: bool = False) -> str:
    """A remote's URL as git resolves it, insteadOf rules applied; the push URL when PUSH is set."""
    return git_output(cwd, list(location), ["remote", "get-url", *(["--push"] if push else []), name])


def trusted_owners(cwd: str) -> frozenset:
    origin = owner_of_url(remote_url(cwd, "origin"))
    return frozenset(gh_users() | ({origin} if origin else set()))


# -------------------------------------------------------------------- sends


def _parsed(arg: str, takes_value: frozenset) -> list:
    """[(flag, glued value or None)] for one word, the way getopt reads it.

    `--name=v` carries its value. A one-dash word is a cluster read letter by letter (`-sSL` is
    -s -S -L), and a letter that takes a value ends it, the rest being that value (`-XPOST`,
    `-sd@f`). AGORA on GRIP#9003: reading `-sd` as -s with the value `d` hid an upload.
    """
    if arg.startswith("--"):
        name, equals, value = arg.partition("=")
        return [(name, value if equals else None)]
    flags = []
    for at in range(1, len(arg) if arg.startswith("-") else 0):
        flag = "-" + arg[at]
        if flag in takes_value:
            return flags + [(flag, arg[at + 1:].removeprefix("=") or None)]
        flags.append((flag, None))
    return flags


def _flags(args: list, takes_value: frozenset) -> list:
    """(index, flag, glued value) for every flag; a flag's separate value word is never read as flags."""
    found, skip = [], False
    for at, arg in enumerate(args):
        parsed = [] if skip else _parsed(arg, takes_value)
        found += [(at, flag, glued) for flag, glued in parsed]
        skip = bool(parsed) and parsed[-1][1] is None and parsed[-1][0] in takes_value
    return found


def option_value(args: list, names: frozenset, takes_value: frozenset = frozenset()) -> str | None:
    """The value of the first option in NAMES, glued, after `=` or in the next word."""
    for at, flag, glued in _flags(args, takes_value | names):
        if flag in names:
            return glued if glued is not None else (args[at + 1] if at + 1 < len(args) else "")
    return None


def has_option(args: list, names: frozenset, takes_value: frozenset = frozenset()) -> bool:
    return any(flag in names for _at, flag, _glued in _flags(args, takes_value))


def urls_in(args: list) -> list:
    return [arg for arg in args if _URL.match(arg)]


def bare_local_only(args: list) -> bool:
    """True when a command names no URL and does name a local address without a scheme."""
    return not urls_in(args) and any(_BARE_LOCAL.match(arg) for arg in args)


def _http_send(name: str, urls: list, write: bool) -> Send | None:
    if urls and not any(is_public(url) for url in urls):
        return None
    target = next((host_of(url) for url in urls if is_public(url)), OPAQUE_ADDRESS)
    if write:
        return Send(f"upload with {name}", HOLD, target)
    if any(len(urlsplit(url).query) > LONG_QUERY or "$(" in url or "`" in url for url in urls):
        return Send(f"request from {name} with data in its address", NOTE, target)
    return None


_CURL_DATA = frozenset({"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--data-ascii",
                        "-F", "--form", "--form-string", "-T", "--upload-file", "--json"})
#: curl's options that take a value (curl --help all), so a cluster such as `-sSLo` is read right
#: and a value word such as `-H "-d x"` is never read as flags.
CURL_VALUES = _CURL_DATA | frozenset({
    "-A", "-b", "-c", "-C", "-D", "-e", "-E", "-H", "-K", "-m", "-o", "-P", "-Q", "-r", "-t", "-u", "-U", "-w",
    "-x", "-X", "-y", "-Y", "-z", "--request", "--output", "--output-dir", "--header", "--user", "--user-agent",
    "--referer", "--cookie", "--cookie-jar", "--url", "--config", "--max-time", "--connect-timeout", "--retry",
    "--proxy", "--write-out", "--range", "--cert", "--key", "--cacert", "--resolve", "--connect-to",
    "--dump-header", "--max-filesize", "--continue-at", "--time-cond", "--oauth2-bearer", "--limit-rate",
})


def _curl_send(args: list, cmd: Command, ctx: Context) -> Send | None:
    if bare_local_only(args):
        return None
    method = (option_value(args, frozenset({"-X", "--request"}), CURL_VALUES) or "").upper()
    return _http_send("curl", urls_in(args), method in _WRITE_METHODS or has_option(args, _CURL_DATA, CURL_VALUES))


_WGET_DATA = frozenset({"--post-data", "--post-file", "--body-data", "--body-file"})
#: wget's options that take a value (wget --help), for the same reason as CURL_VALUES.
WGET_VALUES = _WGET_DATA | frozenset({
    "-O", "-o", "-a", "-P", "-e", "-i", "-B", "-t", "-T", "-w", "-Q", "-U", "-l", "-A", "-R", "-D", "-X", "-I",
    "--output-document", "--output-file", "--append-output", "--directory-prefix", "--execute", "--input-file",
    "--base", "--tries", "--timeout", "--wait", "--quota", "--user-agent", "--level", "--accept", "--reject",
    "--domains", "--method", "--header", "--user", "--password",
})


def _wget_send(args: list, cmd: Command, ctx: Context) -> Send | None:
    if bare_local_only(args):
        return None
    method = (option_value(args, frozenset({"--method"}), WGET_VALUES) or "").upper()
    return _http_send("wget", urls_in(args), method in _WRITE_METHODS or has_option(args, _WGET_DATA, WGET_VALUES))


def _httpie_parts(args: list) -> tuple:
    """(method or "", address words, request items) the way HTTPie reads `http [METHOD] URL [ITEM ...]`."""
    words = [arg for arg in args if not arg.startswith("-")]
    method = words[0].upper() if words and words[0].upper() in _WRITE_METHODS | {"GET", "HEAD"} else ""
    rest = words[1:] if method else words
    return method, rest[:1], rest[1:]


def httpie_urls(args: list) -> list:
    """The address an HTTPie command reads from, as a URL; `:3000` is localhost and gives none."""
    _method, address, _items = _httpie_parts(args)
    if not address or address[0].startswith(":"):
        return []
    return [address[0] if _URL.match(address[0]) else f"https://{address[0]}"]


def _httpie_send(args: list, cmd: Command, ctx: Context) -> Send | None:
    method, _address, items = _httpie_parts(args)
    data = any(("=" in item and "==" not in item) or "@" in item for item in items)
    form = has_option(args, frozenset({"-f", "--form", "--multipart"}))
    return _http_send("HTTPie", httpie_urls(args), method in _WRITE_METHODS or data or form)


def _fixed_send(kind: str):
    return lambda args, cmd, ctx: Send(kind, HOLD, "")


def _rsync_send(args: list, cmd: Command, ctx: Context) -> Send | None:
    remote = any(_SCP_REMOTE.match(arg) or arg.startswith("rsync://") for arg in args)
    return Send("remote copy with rsync", HOLD, "") if remote else None


_GH_WRITES = {
    "pr": frozenset({"create", "edit", "comment", "merge", "review", "close", "reopen", "ready"}),
    "issue": frozenset({"create", "edit", "comment", "close", "reopen", "delete", "transfer", "lock", "unlock",
                        "pin", "unpin"}),
    "release": frozenset({"create", "edit", "upload", "delete"}),
    "repo": frozenset({"create", "edit", "delete", "rename", "archive", "unarchive"}),
    "secret": frozenset({"set", "delete"}),
    "variable": frozenset({"set", "delete"}),
    "label": frozenset({"create", "edit", "delete", "clone"}),
    "workflow": frozenset({"run", "enable", "disable"}),
    "run": frozenset({"rerun", "cancel", "delete"}),
    "cache": frozenset({"delete"}),
}
_GH_VALUE_FLAGS = frozenset({
    "-R", "--repo", "-t", "--title", "-b", "--body", "-F", "--body-file", "-B", "--base", "-H", "--head",
    "-a", "--assignee", "-l", "--label", "-m", "--milestone", "-p", "--project", "-r", "--reviewer",
    "-X", "--method", "-f", "--field", "--raw-field", "--input", "-q", "--jq", "--template", "--hostname",
    "--cache", "-L", "--limit", "-s", "--state", "-A", "--author", "-S", "--search", "-e", "--env",
})
_GH_FIELDS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})
_GH_REPO_FLAGS = frozenset({"-R", "--repo"})
_GH_METHOD_FLAGS = frozenset({"-X", "--method"})


def positionals(args: list) -> list:
    """gh's positional words, past every flag and the value of each flag that takes one."""
    words, at = [], 0
    while at < len(args):
        arg = args[at]
        if arg in _GH_VALUE_FLAGS:
            at += 2
            continue
        if not arg.startswith("-"):
            words.append(arg)
        at += 1
    return words


def gh_repo(args: list, assigns: dict) -> str:
    """owner/repo a gh command names, from -R, GH_REPO or an api path; "" for the checkout's own."""
    named = option_value(args, _GH_REPO_FLAGS, _GH_VALUE_FLAGS) or assigns.get("GH_REPO", "")
    if named:
        return named.split("/", 1)[-1] if named.count("/") == 2 else named
    words = positionals(args)
    api = _API_REPO.match(words[1]) if words[:1] == ["api"] and len(words) > 1 else None
    return f"{api['owner']}/{api['repo']}" if api else ""


_OWNER_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")


def gh_target_repo(args: list, assigns: dict) -> str:
    """owner/repo a gh command acts on: -R, GH_REPO, an api path, `gh repo`'s OWNER/REPO word,
    or a GitHub URL in place of a number.

    One helper for the read side and the write side, so `gh issue comment <url>` is judged by
    the URL's owner as `gh issue view <url>` is, and `gh repo delete other/repo` by `other`
    (AGORA on GRIP#9003, rounds 2 and 5).
    """
    words = positionals(args)
    named = words[2] if words[:1] == ["repo"] and len(words) > 2 and _OWNER_REPO.match(words[2]) else ""
    return gh_repo(args, assigns) or named or repo_in_urls(words[2:])


def repo_trusted(repo: str, ctx: Context) -> bool:
    return not repo or repo.split("/", 1)[0].lower() in ctx.trusted()


def gh_api_writes(args: list) -> bool:
    """True when a gh api call writes: a write method, or fields with no method (gh then POSTs)."""
    method = (option_value(args, _GH_METHOD_FLAGS, _GH_VALUE_FLAGS) or "").upper()
    if method:
        return method in _WRITE_METHODS
    return has_option(args, _GH_FIELDS, _GH_VALUE_FLAGS)


def _gh_api_send(args: list, words: list, cmd: Command, ctx: Context) -> Send | None:
    if not gh_api_writes(args):
        return None
    endpoint = (words[1] if len(words) > 1 else "").split("?", 1)[0]
    if endpoint == "graphql":
        return Send("GitHub GraphQL mutation", NOTE, "GitHub") if "mutation" in " ".join(args) else None
    repo = gh_repo(args, cmd.assigns)
    if repo:
        return Send("write through gh api", NOTE if repo_trusted(repo, ctx) else HOLD, repo)
    # The first segment only (`orgs`, `gists`, `user`): a path can name an organisation or a person.
    return Send("write through gh api", HOLD, f"GitHub API {endpoint.lstrip('/').split('/', 1)[0]}".strip())


def _gh_send(args: list, cmd: Command, ctx: Context) -> Send | None:
    words = positionals(args)
    if words[:1] == ["api"]:
        return _gh_api_send(args, words, cmd, ctx)
    if words[:1] == ["gist"] and words[1:2] in (["create"], ["edit"]):
        return Send("gist", HOLD, "a gist")
    if len(words) < 2 or words[1] not in _GH_WRITES.get(words[0], frozenset()):
        return None
    repo = gh_target_repo(args, cmd.assigns)
    return Send(f"gh {words[0]} {words[1]}", NOTE if repo_trusted(repo, ctx) else HOLD, repo or "this repository")


_GIT_GLOBAL_VALUES = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})
#: The options that say WHERE git runs. Only these are repeated for a lookup, never -c or
#: --exec-path, so the lookup runs no program and reads no setting that the command named.
_GIT_PLACE = frozenset({"-C", "--git-dir", "--work-tree"})
_PUSH_VALUES = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})


def git_location(options: list) -> tuple:
    """(the -C, --git-dir and --work-tree words of a git command, whether a -c rewrites a remote).

    A `-c remote.*` or `-c url.*` can send the push somewhere a lookup without it cannot see,
    so such a push is never trusted (AGORA on GRIP#9003 found the lookup ran in the wrong place).
    """
    keep, rewrites, at = [], False, 0
    while at < len(options):
        name = options[at].split("=", 1)[0]
        width = 1 if "=" in options[at] or name not in _GIT_GLOBAL_VALUES else 2
        value = options[at + 1] if width == 2 and at + 1 < len(options) else ""
        keep += options[at:at + width] if name in _GIT_PLACE else []
        rewrites = rewrites or (name == "-c" and value.lower().startswith(("remote.", "url.")))
        at += width
    return keep, rewrites


def _push_remote(cwd: str, location: list) -> str:
    """The remote a bare `git push` uses: the branch's push remote, remote.pushDefault, then origin."""
    branch = git_output(cwd, location, ["symbolic-ref", "--short", "-q", "HEAD"])
    remote = git_output(cwd, location, ["for-each-ref", "--format=%(push:remotename)", f"refs/heads/{branch}"]) \
        if branch else ""
    return remote or git_output(cwd, location, ["config", "--get", "remote.pushDefault"]) or "origin"


def _push_destination(rest: list) -> str:
    """The repository a push names, or "" when it names none and git chooses."""
    named = option_value(rest, frozenset({"--repo"}))
    if named:
        return named
    words, at = [], 0
    while at < len(rest):
        word = rest[at]
        at += 2 if word in _PUSH_VALUES else 1
        if not word.startswith("-"):
            words.append(word)
    return words[0] if words else ""


def _git_send(args: list, cmd: Command, ctx: Context) -> Send | None:
    at = _skip_options(args, 0, _GIT_GLOBAL_VALUES)
    if args[at:at + 1] == ["send-email"]:
        return Send("mail with git send-email", HOLD, "")
    if args[at:at + 1] != ["push"]:
        return None
    location, rewrites = git_location(args[:at])
    where = _push_destination(args[at + 1:]) or _push_remote(ctx.cwd, location)
    address = "://" in where or bool(_SCP_REMOTE.match(where))
    owner = owner_of_url(where if address else remote_url(ctx.cwd, where, tuple(location), push=True))
    trusted = bool(owner) and owner in ctx.trusted() and not rewrites
    return Send("git push", NOTE if trusted else HOLD, owner or (address_host(where) if address else where))


def address_host(address: str) -> str:
    """The host alone, so a login or token written into an address never reaches a log."""
    if "://" in address:
        return host_of(address)
    return address.split("@", 1)[-1].split(":", 1)[0]


_SEND_HANDLERS = {
    "curl": _curl_send, "wget": _wget_send, "http": _httpie_send, "https": _httpie_send, "xh": _httpie_send,
    "xhs": _httpie_send, "gh": _gh_send, "git": _git_send, "rsync": _rsync_send,
    "nc": _fixed_send("raw socket"), "ncat": _fixed_send("raw socket"), "netcat": _fixed_send("raw socket"),
    "socat": _fixed_send("raw socket"), "telnet": _fixed_send("raw socket"),
    "scp": _fixed_send("remote copy"), "sftp": _fixed_send("remote copy"), "ssh": _fixed_send("remote shell"),
    "mail": _fixed_send("mail"), "mailx": _fixed_send("mail"), "sendmail": _fixed_send("mail"),
    "mutt": _fixed_send("mail"), "msmtp": _fixed_send("mail"), "swaks": _fixed_send("mail"),
}


def classify_sends(command: str, ctx: Context) -> list:
    """Every send the command makes, in the order the shell would run them."""
    sends = []
    for cmd in expand_commands(command):
        name, args = program(cmd)
        handler = _SEND_HANDLERS.get(name)
        send = handler(args, cmd, ctx) if handler else None
        if send:
            sends.append(send)
    return sends
