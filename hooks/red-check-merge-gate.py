#!/usr/bin/env python3
"""PreToolUse gate: DENY `gh pr merge` while the Codex reviewer holds the pull
request by the merge sweep's rule, while a GitHub Actions job on its head is
red and the base branch's newest finished reading of that job is not, while
a run on the head has not finished, or while its target cannot be told
because a shell variable or command substitution has not yet been filled in.

Origin: V>> 2026-10-15 chose, for the merge sweep, to hold a pull request for a
red non-required check unless main carries the same red (GRIP#8343). Within
twenty minutes of that hold going live it held GRIP#8377, and a session merged
the pull request by hand three minutes after AGORA's verdict, past the same
red. A session's own merge command was the path the rule did not reach. This
gate closes it with the sweep's judgement, lib/red_check.py, so the two cannot
disagree: the head's runs by commit, the base's reading by workflow id, and a
red that is the base's own passes.

IT INVENTS NO POLICY. CLAUDE.md's AGORA-green block already says a red check
that is not main's own is not a green, and the sweep already holds on it.
Since V>>'s 2026-10-26 decision (GRIP#8682) the sweep also holds while a run on
the head has not finished, because GitHub waits only for the required checks
and GRIP#8678 merged two minutes before a slower job went red on main. A merge
a session types follows the same decision here, judged after the red check in
the sweep's order.

THE CODEX HOLD (GRIP#9087)
--------------------------
GRIP#9084 was merged by hand 22 seconds after the Codex reviewer posted a
finding that held, because this gate read only the Actions runs, while the
sweep's codex_hold would have held it. A merge a session types now asks the
sweep the same question first (`grip-pr-monitor --codex-hold N`, in the
sweep's order): an unresolved Codex thread denies, and so does the Codex wait,
while Codex has not reviewed, for up to 20 minutes after the pull request
opened or left draft, in a repository where Codex touched one of the last 10
merged pull requests. A Codex state that cannot be read, a repository on
another host and a spent budget allow with a note, as every uncertainty here
does, and the override covers this hold too.

WHAT IS JUDGED
--------------
Every merge in the command, not only the first: `gh pr merge 11 && gh pr merge
12` is two merges, and each segment up to the next shell operator is judged on
its own target (Codex on GRIP#8454). The command is read as the shell reads
it: `rtk gh`, `grip-gh` and a full path all count as gh, a redirection and its
file are not arguments (`2>&1` is not pull request 2), and the words `gh pr
merge` inside a quoted string are not a command. A newline ends a command
unless a backslash continues it, a comment ends at its newline, and `-R` may
come before `pr` or between `pr` and `merge`, as gh allows (Codex, round
four), in every form gh reads: `-R X`, `-RX`, `-R=X`, `--repo X`, `--repo=X`
and the end of a cluster such as `-sdR X` (Codex, round six). A `gh` in an
argument's place counts too: `echo gh pr merge 11 | sh` feeds a shell, and
xargs, timeout or nohup put gh there, so a merge example written unquoted
is judged as well; quote it, or use the override. The target is read the way
gh reads `gh pr merge [<number> | <url> | <branch>] [flags]`: a flag that
takes a value keeps it, so `--subject 123 11` merges 11, a pull request URL
names its own repository, a branch name resolves through gh, and no target
means the current branch's pull request, on the checkout's own host.
`GH_REPO` and `GH_HOST` written in front of gh choose the repository, as gh's
own environment does, `gh.exe` is gh, and `--disable-auto`, which cancels an
armed auto-merge and merges nothing, is left alone (Codex, round seven).
`--auto`, in any spelling gh reads as true, is refused outright: it
defers the merge to GitHub past the moment this gate reads the head, so a job
still running could turn red after the reading (AGORA on GRIP#8454); drop it
and merge once the checks have finished.

THE UNREADABLE TARGET (GRIP#10064)
-----------------------------------
A target or repository the shell fills in -- `gh pr merge "$n"`, `$n`,
`${n}`, or `gh pr merge $(cat number) ...` -- is not merely unresolved: the
value this gate reads is the two characters `$n`, never the pull request the
shell would later substitute, so it can never be told which pull request is
meant. Seen live 2026-11-26 (HAL#908 and HAL#910, merged in one loop over a
shell variable): the gate let the merge through with a note, and neither
hold ran. V>> decided the same day that this denies outright, with a reason
naming the fix: type the literal pull request number. A bare `gh pr merge`
(the current branch's own pull request) and a pull request URL are read
exactly as before; only a target or repository the shell would still have to
expand is refused.

WHY DENY, AND WHY FAIL OPEN
---------------------------
A merge past the pull request's own red is what the rule exists to stop, and
it is effectively irreversible on a shared main, so the gate denies, and it
denies `--admin` too: the stuck-loop admin merge needs CI green and never goes
past a real current failure. Every uncertainty resolves to allow, but never in
silence: when `gh` is missing, slow, unauthenticated or answers an unexpected
shape, or when the whole judgement runs out of its budget (BUDGET_SECONDS,
under the binding's 60 s, shared by every read the gate makes), the merge
proceeds with an audit row and a one-line advisory that names the cause and
says the rule was not applied. The unattended sweep fails closed on the same
reads, but a person at the keyboard should not deadlock on an API hiccup. A
run the head's listing showed still going denies even when another read then
failed: it was seen, not guessed (Codex, round six). The reading is a point
in time, as the sweep's is: a push between it and the merge is not seen, and
`--match-head-commit` pins a merge to the head it was judged on.
`GRIP_RED_CHECK_GATE=0` in front of a merge (`GRIP_RED_CHECK_GATE=0 gh pr merge
11`, or after `env`) allows that merge alone, and in the environment allows
every merge. Each is logged, and read before anything is resolved, so it is
logged even when gh cannot name the repository. Written anywhere else, in a
`--body` value or another command, it is only text (Codex, round six).

DEADLOCK CHECK (rules/gate-resolution-escape-hatch.md, Invariant 1)
-------------------------------------------------------------------
Blocked condition: a merge command for a pull request whose head carries a red
job that is not red on its base. Its resolutions are a push that fixes or
re-runs the job (not gated here), the base's next finished run of the job
showing the same red (outside the session), or the logged override. None of
them is a merge command, so the gate cannot trap the session that trips it.
The unfinished hold resolves the same way: the run finishes on its own, or
`gh run cancel` ends one that can never finish (a cancelled run has finished),
and neither is a merge command. The Codex hold resolves when its thread is
resolved (`gh api graphql` with resolveReviewThread), when Codex reviews, or
when the wait ends, and none of those is a merge command either. A target or
repository the shell fills in denies the same way: its resolution is to type
the literal pull request number, which this gate reads in full and never
denies for being unreadable, or the logged override.

Judgement: lib/red_check.py and bin/grip-pr-monitor --codex-hold (both shared
           with the merge sweep)
Tests:     tests/test_red_check_merge_gate.py
Audit:     ~/.claude/logs/red-check-merge-gate.jsonl
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
sys.path.insert(0, str(CHECKOUT / "lib"))

AUDIT_PATH = CLAUDE_DIR / "logs" / "red-check-merge-gate.jsonl"
ENV_SWITCH = "GRIP_RED_CHECK_GATE"
GH_TIMEOUT_SECONDS = 10
#: The merge sweep, whose --codex-hold mode is the Codex judgement (GRIP#9087).
MONITOR = CHECKOUT / "bin" / "grip-pr-monitor"
#: What the sweep prints when the Codex state cannot be read, and what every
#: failure to ask it reads as.
_CODEX_UNREAD = "its Codex review state could not be read"


def _budget() -> float:
    """The whole judgement's budget in seconds, under the binding's 60 s, so
    running out of time is an advisory and an audit row rather than a killed
    hook. GRIP_RED_CHECK_BUDGET_SECONDS overrides it, so a tighter binding can
    be tested without editing this line (AGORA on GRIP#8454); a value that is
    not a number is ignored."""
    try:
        return float(os.environ.get("GRIP_RED_CHECK_BUDGET_SECONDS") or 40)
    except ValueError:
        return 40.0


BUDGET_SECONDS = _budget()

# A cheap, permissive pre-filter, not the decision: every Bash call pays it.
# gh also takes its inherited flags before or between the subcommand words
# (`gh -R X pr merge`, `gh pr -R X merge`; Codex on GRIP#8454).
_GH_PR_MERGE = re.compile(r"\bgh\b[^\n;&|]*?\spr\b[^\n;&|]*?\smerge(?![-\w])")
#: The override, as the word it is in front of the merge it lets through.
_OVERRIDE = f"{ENV_SWITCH}=0"
#: The shell operators that end one command's arguments.
_OPERATORS = frozenset({";", "&&", "||", "|", "|&", "&", "(", ")"})
#: A file descriptor glued to a redirection (`2>&1`, `11>log`) is dropped
#: before tokenising: the lexer would hand the digits back as a word.
_FD = re.compile(r"(?<!\S)\d+(?=[<>])")
#: The redirection operators the lexer emits as tokens; each takes the next
#: token as its file.
_REDIRECT = re.compile(r"^(?:[<>]{1,3}&?|&>>?)$")
#: gh pr merge's flags that take a value, so the value is never read as the target.
_VALUE_FLAGS = frozenset({"-R", "--repo", "-t", "--subject", "-b", "--body", "-F", "--body-file",
                          "--match-head-commit", "-A", "--author-email"})
#: The one-letter forms of those flags, which pflag also reads glued (`-Rx`,
#: `-R=x`) or at the end of a cluster (`-sdR x`).
_VALUE_SHORTS = frozenset("RtbFA")
_PR_URL = re.compile(r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)")
#: A word the shell expands before gh sees it: a parameter or a command substitution.
_EXPANDS = re.compile(r"[$`]")
_REPO_URL = re.compile(r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def _bail() -> None:
    """Every uncertain path that has nothing to say ends here: silent, non-blocking."""
    sys.exit(0)


def _audit(record: dict) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with AUDIT_PATH.open("a") as handle:
            handle.write(json.dumps(record) + "\n")
    except Exception:  # noqa: BLE001 -- auditing must never block a merge
        pass


def _is_gh(token: str) -> bool:
    """True for the gh binary however it is spelled: `gh`, `grip-gh`, `/bin/gh`,
    and Windows' `gh.exe` (Codex on GRIP#8454)."""
    name = token[:-4] if token.lower().endswith(".exe") else token
    return name == "gh" or name.endswith(("-gh", "/gh"))


def _as_one_line(command: str) -> str:
    """The command with its line structure made explicit, as the shell reads
    it: a backslash before a newline joins two lines, a comment runs to its
    newline, and every other newline outside quotes ends a command, so it
    becomes `;`. shlex alone drops a newline, which read `gh pr merge 11` and a
    second `gh pr merge 12 -R other/repo` line as one merge aimed at
    other/repo (Codex on GRIP#8454)."""
    out: list[str] = []
    quote = ""
    escaped = comment = False
    for char in command:
        if comment:
            comment = char != "\n"
            out.append("" if comment else " ; ")
        elif escaped:
            escaped = False
            if char == "\n":
                out[-1] = " "
            else:
                out.append(char)
        elif char == "\\" and quote != "'":
            escaped = True
            out.append(char)
        elif quote:
            quote = "" if char == quote else quote
            out.append(char)
        elif char == "#" and (not out or out[-1][-1:] in " \t;&|()"):
            comment = True
        else:
            quote = char if char in "'\"" else ""
            out.append(" ; " if char == "\n" else char)
    return "".join(out)


def _tokens(command: str) -> list[str]:
    """The command's words with shell operators as their own tokens; empty
    when the quoting is unbalanced, which is an uncertainty and so allows.
    Comments are gone already (_as_one_line), so a `#` inside a word stays."""
    lexer = shlex.shlex(_FD.sub("", command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return []


def _short_value(token: str) -> tuple[str, str] | None:
    """For a one-dash cluster that sets a value flag, as pflag reads it: the
    flag's letter and its glued value, empty when the value is the next word
    (`-R x` and `-sdR x`; `-Rx`, `-R=x` and `-sdRx` glue it). None for a
    cluster of booleans, and for `-s=true`, whose rest is the boolean's value."""
    if len(token) < 2 or token[0] != "-" or token[1] == "-":
        return None
    for at in range(1, len(token)):
        if token[at] == "=":
            return None
        if token[at] in _VALUE_SHORTS:
            rest = token[at + 1:]
            return token[at], rest[1:] if rest.startswith("=") else rest
    return None


def _takes_next(token: str) -> bool:
    """True when TOKEN is a flag whose value is the next word."""
    short = _short_value(token)
    return short[1] == "" if short else token in _VALUE_FLAGS


def _flags_from(tokens: list[str], index: int) -> tuple[int, list[str]]:
    """The flags gh takes before or between the subcommand words, with their
    values (`gh -R X pr merge`, `gh pr --repo=X merge`), and the index of the
    first word after them."""
    flags: list[str] = []
    while index < len(tokens) and tokens[index].startswith("-"):
        width = 2 if _takes_next(tokens[index]) and index + 1 < len(tokens) else 1
        flags.extend(tokens[index:index + width])
        index += width
    return index, flags


def _merges(command: str) -> list[tuple[list[str], list[str]]]:
    """Each real `gh pr merge` as (the words in front of gh in its command,
    its tokens up to the next shell operator), with any flag written before
    `merge` kept in front of the tail. Tokenising tells the command from the
    same words inside a string: the quoted form collapses into one token
    (hooks/guardrail-removal-merge-gate.py)."""
    command = _as_one_line(command)
    if not _GH_PR_MERGE.search(command):
        return []
    tokens = _tokens(command)
    merges = []
    start = 0
    for index, token in enumerate(tokens):
        if token in _OPERATORS:
            start = index + 1
        if not _is_gh(token):
            continue
        at, before = _flags_from(tokens, index + 1)
        if tokens[at:at + 1] != ["pr"]:
            continue
        at, between = _flags_from(tokens, at + 1)
        if tokens[at:at + 1] == ["merge"]:
            rest = itertools.takewhile(lambda t: t not in _OPERATORS, tokens[at + 1:])
            merges.append((tokens[start:index], [*before, *between, *rest]))
    return merges


def _positional(tail: list[str]) -> tuple[str | None, str | None]:
    """(target, repo) read the way gh reads `gh pr merge [<number> | <url> |
    <branch>] [flags]`: a flag's value and a redirection's file are never the
    target."""
    target = repo = None
    index = 0
    while index < len(tail):
        token = tail[index]
        short = _short_value(token)
        if short:
            value = short[1] or (tail[index + 1] if index + 1 < len(tail) else None)
            repo = value if short[0] == "R" else repo
            index += 1 if short[1] else 2
        elif token == "--repo" and index + 1 < len(tail):
            repo, index = tail[index + 1], index + 2
        elif token.startswith("--repo="):
            repo, index = token.split("=", 1)[1], index + 1
        elif token in _VALUE_FLAGS or _REDIRECT.fullmatch(token):
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            target, index = target or token, index + 1
    return target, repo


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def _gh_line(args: list[str], cwd: str, deadline: float) -> str | None:
    """One line from gh, or None on any doubt, within what is left of the budget."""
    timeout = min(GH_TIMEOUT_SECONDS, _remaining(deadline))
    if timeout <= 0:
        return None
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd)
    except Exception:  # noqa: BLE001 -- timeout, missing binary, anything
        return None
    line = (done.stdout or "").strip().split("\n")[0] if done.returncode == 0 else ""
    return line or None


def _host_prefix(host: str) -> str:
    """The `HOST/` to write in front of OWNER/REPO for a repository that a URL
    or the checkout places on HOST. A host other than github.com is always
    written: OWNER/REPO alone sent an enterprise checkout's reads to gh's default
    host (Codex on GRIP#8454). github.com is written only while this hook's
    environment exports GH_HOST for another host. gh applies GH_HOST only "where
    a hostname has not been provided, or cannot be inferred from the context of
    a local Git repository" (`gh help environment`), so gh merges a github.com
    URL or checkout on github.com, and every read must go there too (GRIP#9093)."""
    if host.lower() != "github.com":
        return host + "/"
    exported = os.environ.get("GH_HOST", "").strip().lower()
    return "github.com/" if exported not in ("", "github.com") else ""


def _current_repo(cwd: str, deadline: float) -> str | None:
    """The checkout's repository as gh sees it, with its host when the reads
    need one (_host_prefix)."""
    url = _REPO_URL.match(_gh_line(["repo", "view", "--json", "url", "--jq", ".url"], cwd, deadline) or "")
    if not url:
        return None
    return f"{_host_prefix(url['host'])}{url['owner']}/{url['repo']}"


def _shell_fills_in(target: str | None, repo: str | None) -> bool:
    """True when TARGET or REPO is a parameter (`"$PR"`, `$n`, `${n}`) or a
    command substitution (`$(...)`, backticks) the shell fills in only after
    this gate has already run, so it is not merely unresolved: it cannot be
    read at all. Denied outright (V>> 2026-11-26, GRIP#10064) rather than
    allowed with a note -- typing the literal pull request number is the
    only way to be checked, and it is never inside this denied set
    (rules/gate-resolution-escape-hatch.md, Invariant 1)."""
    return any(_EXPANDS.search(word) for word in (target, repo) if word)


def _resolve(target: str | None, repo: str | None, cwd: str, deadline: float) -> tuple[str, int] | None:
    """(repo, number) the merge is aimed at, or None when it cannot be told:
    the repository could not be read, or no pull request matches the target.
    A target or repository the shell fills in later is caught before this is
    called (_shell_fills_in) and denies instead of reaching here; gh is not
    asked about a branch literally named `$PR` (AGORA on GRIP#8454, round
    seven)."""
    url = _PR_URL.match(target or "")
    if url:
        return f"{_host_prefix(url['host'])}{url['owner']}/{url['repo']}", int(url["number"])
    repo = repo or _current_repo(cwd, deadline)
    if not repo:
        return None
    if target and target.isdigit():
        return repo, int(target)
    number = _gh_line(["pr", "view", *([target] if target else []), "-R", repo, "--json", "number",
                       "--jq", ".number"], cwd, deadline)
    return (repo, int(number)) if number and number.isdigit() else None


def _assigned(prefix: list[str], name: str) -> str:
    """The value NAME is given in front of the command (`NAME=value gh ...`), or ""."""
    values = [word.split("=", 1)[1] for word in prefix if word.startswith(name + "=")]
    return values[-1] if values else ""


def _with_env_target(repo: str | None, prefix: list[str]) -> str | None:
    """The repository gh merges in, given the words in front of it: `-R` wins,
    then GH_REPO, and GH_HOST qualifies either when it names no host (`gh help
    environment`; Codex on GRIP#8454, round seven)."""
    repo = repo or _assigned(prefix, "GH_REPO") or None
    host = _assigned(prefix, "GH_HOST")
    return f"{host}/{repo}" if repo and host and repo.count("/") == 1 else repo


def _on_github_com(repo: str) -> str | None:
    """OWNER/REPO for a repository on github.com, written with or without its
    host (`gh pr merge --help`: -R takes [HOST/]OWNER/REPO; GH_HOST=github.com
    names it too; Codex on GRIP#9091), or None for a repository on another host."""
    host, _, rest = repo.partition("/")
    if host.lower() == "github.com" and rest.count("/") == 1:
        return rest
    return repo if repo.count("/") == 1 else None


def _codex_hold(repo: str, number: int, deadline: float) -> str:
    """Why the pull request waits for the Codex reviewer, by the merge sweep's
    own judgement (`grip-pr-monitor --codex-hold`), or "" when it may merge. A
    repository written with a host other than github.com, a spent budget or a
    run that fails reads as unreadable. One written with github.com is read on
    github.com even when GH_HOST names another host, because gh merges it there."""
    timeout = min(GH_TIMEOUT_SECONDS, _remaining(deadline))
    owner_repo = _on_github_com(repo)
    if owner_repo is None or timeout <= 0:
        return _CODEX_UNREAD
    env = {**os.environ, "GRIP_PR_MONITOR_REPO": owner_repo}
    if owner_repo != repo:
        env["GH_HOST"] = "github.com"
    try:
        done = subprocess.run(["bash", str(MONITOR), "--log", "/dev/null", "--codex-hold", str(number)],
                              capture_output=True, text=True, timeout=timeout, check=False, env=env)
    except Exception:  # noqa: BLE001 -- timeout, missing bash, anything
        return _CODEX_UNREAD
    return done.stdout.strip() if done.returncode == 0 else _CODEX_UNREAD


def _judge_one(red_check, prefix: list[str], tail: list[str], cwd: str, deadline: float) -> tuple[str, str]:
    """(denial, note) for one merge segment; each is empty when it has nothing
    to say. A target or repository the shell fills in later denies outright,
    before the Codex hold or the red check, which this gate never applies to
    it (GRIP#10064). Otherwise the Codex hold comes first and the red check
    after it, as in the sweep, and a Codex denial ends the judgement as the
    sweep's first hold does."""
    target, repo = _positional(tail)
    repo = _with_env_target(repo, prefix)
    if _shell_fills_in(target, repo):
        _audit({"decision": "deny-unreadable", "tail": tail[:8]})
        return ("which pull request this merge is aimed at could not be told: a shell variable or command "
                "substitution is filled in only after this gate runs, so it cannot be read. Type the literal "
                "pull request number."), ""
    target = _resolve(target, repo, cwd, deadline)
    if target is None:
        why = ("the judgement ran out of its budget" if _remaining(deadline) <= 0
               else "which pull request this merge is aimed at could not be told")
        _audit({"decision": "allow-unresolved", "tail": tail[:8], "why": why})
        return "", f"{why}; the Codex and red-check rules were not applied."
    repo, number = target
    label = f"{repo.rsplit('/', 1)[-1]}#{number}"
    codex = _codex_hold(repo, number, deadline)
    if codex and codex != _CODEX_UNREAD:
        _audit({"decision": "deny", "pr": label, "codex": codex})
        return f"{label} waits for the Codex reviewer: {codex}.", ""
    codex_note = f"{label}: {codex}; the Codex rule was not applied to this merge." if codex else ""
    denial, note = _red_outcome(label, red_check.judge(repo, number, deadline=deadline), codex)
    return denial, " ".join(part for part in (codex_note, note) if part)


def _red_outcome(label: str, verdict, codex: str) -> tuple[str, str]:
    """(denial, note) from lib/red_check's verdict on the head's Actions runs.
    Its audit row carries the Codex state as well, so each merge keeps one row."""
    record = {"pr": label, "hold": verdict.hold, "own": list(verdict.own), "mains": list(verdict.mains),
              "unread": list(verdict.unread), "base": verdict.base, "cause": verdict.cause,
              "unfinished": list(verdict.unfinished), "codex": codex}
    if verdict.hold and verdict.unfinished:
        # Before the fail-open branch: a run the listing showed still going was
        # seen, whatever read failed after it (Codex on GRIP#8454).
        _audit({"decision": "deny", **record})
        return (f"{label} has runs on its head that have not finished: {', '.join(verdict.unfinished)}. Merge "
                "once they finish; a run that can never finish is released by cancelling it."), ""
    if verdict.unreadable:
        _audit({"decision": "allow-unreadable", **record})
        cause = f" ({verdict.cause})" if verdict.cause else ""
        return "", f"{label}: {verdict.hold}{cause}; the red-check rule was not applied to this merge."
    if verdict.hold:
        _audit({"decision": "deny", **record})
        unread = f" {', '.join(verdict.unread)} could not be compared." if verdict.unread else ""
        return (f"{label} has red check(s) that are not red on {verdict.base}: {', '.join(verdict.own)}.{unread}"
                " Fix or re-run the job and push, or wait for the base's next finished run of it to show the "
                "same red."), ""
    _audit({"decision": "allow", **record})
    if verdict.mains:
        return "", (f"{label}: {', '.join(verdict.mains)} red on {verdict.base} too (its newest finished run); "
                    "not held for it.")
    return "", ""


def _emit_or_bail(emit, response: dict) -> None:
    """Emitting is the last thing that can fail; a broken emitter must not block."""
    try:
        emit(response)
    except Exception as exc:  # noqa: BLE001
        _audit({"decision": "emit-failed", "error": repr(exc)[:400]})
        _bail()


def _payload() -> dict:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        _bail()
    return payload if isinstance(payload, dict) else {}


def _command(payload: dict) -> str:
    tool_input = payload.get("tool_input")
    return str(tool_input.get("command", "")) if isinstance(tool_input, dict) else ""


def _flag_set(tail: list[str], name: str) -> bool:
    """True when the boolean flag NAME is set in TAIL in any spelling gh reads
    as true (`--x`, `--x=true`, `--x=1`); gh reads 0, f and false as false,
    whatever their case."""
    given = name + "="
    return any(token == name or (token.startswith(given) and token[len(given):].lower() not in ("0", "f", "false"))
               for token in tail)


def _defers(tail: list[str]) -> bool:
    """True when the merge is handed to GitHub for later: `--auto` in any
    spelling gh reads as true (Codex on GRIP#8454)."""
    return _flag_set(tail, "--auto")


def _refuse_auto(emit, pretool_decision, command: str) -> None:
    _audit({"decision": "deny-auto", "command": command[:200]})
    _emit_or_bail(emit, pretool_decision("deny", (
        "--auto defers the merge to GitHub past the moment this gate reads the head, so a job still "
        "running could turn red after the reading. Drop --auto and merge once the checks have finished. "
        f"{_OVERRIDE} in front of the merge overrides, and is logged.")))
    sys.exit(0)


def _respond(outcomes: list[tuple[str, str]], emit, pretool_decision, pretool_context) -> None:
    denials = [denial for denial, _ in outcomes if denial]
    notes = [note for _, note in outcomes if note]
    if denials:
        _emit_or_bail(emit, pretool_decision("deny", " ".join(denials) + f" {_OVERRIDE} in front of the "
                                                                        "merge overrides, and is logged."))
    elif notes:
        _emit_or_bail(emit, pretool_context(" ".join(notes)))


def _judged(merges: list[tuple[list[str], list[str]]], command: str) -> list[tuple[list[str], list[str]]]:
    """The merges to judge. One with the override in front of it is logged and
    left alone, and only that one: a search of the whole command let an
    override inside a --body value or another command through every merge
    (Codex on GRIP#8454). `--disable-auto` cancels an armed auto-merge and
    merges nothing, so it is left alone too (Codex, round seven)."""
    judged = []
    for prefix, tail in merges:
        if _OVERRIDE in prefix:
            _audit({"decision": "override", "tail": tail[:8], "command": command[:200]})
        elif not _flag_set(tail, "--disable-auto"):
            judged.append((prefix, tail))
    return judged


def main() -> None:
    payload = _payload()
    if payload.get("tool_name") != "Bash":
        _bail()
    command = _command(payload)
    merges = _merges(command)
    if not merges:
        _bail()
    try:
        import red_check
        from hook_response import emit, pretool_context, pretool_decision
    except Exception:  # noqa: BLE001
        _bail()
    if os.environ.get(ENV_SWITCH) == "0":
        _audit({"decision": "override", "command": command[:200]})
        _bail()
    judged = _judged(merges, command)
    if not judged:
        _bail()
    if any(_defers(tail) for _, tail in judged):
        _refuse_auto(emit, pretool_decision, command)
    deadline = time.monotonic() + BUDGET_SECONDS
    cwd = str(payload.get("cwd") or os.getcwd())
    _respond([_judge_one(red_check, prefix, tail, cwd, deadline) for prefix, tail in judged],
             emit, pretool_decision, pretool_context)
    sys.exit(0)


if __name__ == "__main__":
    main()
