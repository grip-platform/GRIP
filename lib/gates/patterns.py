"""Gate patterns and constants -- extracted from context-gate-hook.py (#933).

Pure data: regex patterns, string constants, and sets used by gate functions.
No side effects, no I/O, no state mutation. Safe to import everywhere.
"""

import os
import re


class _LazyPattern:
    """Defers regex compilation to first use. Drop-in for re.compile() objects.

    74 patterns compiled at import cost ~30-50ms. Most gates only fire for
    specific tool types, so deferring compilation saves startup time.
    """

    __slots__ = ("_src", "_flags", "_compiled")

    def __init__(self, pattern: str, flags: int = 0):
        self._src = pattern
        self._flags = flags
        self._compiled = None

    def _ensure(self):
        if self._compiled is None:
            self._compiled = re.compile(self._src, self._flags)
        return self._compiled

    def search(self, string, *a, **kw):
        return self._ensure().search(string, *a, **kw)

    def match(self, string, *a, **kw):
        return self._ensure().match(string, *a, **kw)

    def findall(self, string, *a, **kw):
        return self._ensure().findall(string, *a, **kw)

    def sub(self, repl, string, *a, **kw):
        return self._ensure().sub(repl, string, *a, **kw)

    @property
    def pattern(self):
        return self._src


def _lc(pattern: str, flags: int = 0) -> _LazyPattern:
    """Shorthand: lazy-compile a regex pattern."""
    return _LazyPattern(pattern, flags)


# A "command-position" prefix: a token only counts as the program being
# *invoked* when it is at the start of the line, right after a shell separator
# or subshell (``;`` ``&&`` ``||`` ``|`` ``(`` `` ` ``), or right after a JS
# package runner (``npx`` / ``pnpm dlx`` / ``yarn dlx`` / ``bunx``) -- optionally
# preceded by inline env-var assignments (``FOO=bar vercel deploy``). This is the
# structural difference between the CLI being *run* (`vercel deploy`, `npx vercel`)
# and the same word appearing inside a path (`.vercel/project.json`), a domain
# (`foo.vercel.app`), or as an argument to a read tool (`rg vercel file`,
# `cat .vercel/x`). Origin: 2026-05-30 deploy-gate false-positive on read-only
# Bash (`cat .vercel/...`, `curl https://x.vercel.app/`, `rg vercel <file>`).
_CMD_POSITION = (
    r"(?:^|&&|\|\||[\n;&|(`]|\bnpx\s+|\bpnpm\s+dlx\s+|\byarn\s+dlx\s+|\bbunx\s+)"
    r"\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
)


def _cmd_invocation(tool: str) -> str:
    """Regex source matching ``tool`` in shell *command position* (i.e. being
    invoked), never as a substring of a path, URL, or argument to another
    command. ``tool`` must be a bare program name (regex-safe)."""
    return _CMD_POSITION + tool + r"\b"

# =========================================================================
# PERFORMANCE: Fast-path for known-safe Bash commands (#165)
# =========================================================================

SAFE_BASH_PREFIXES = (
    "git status", "git log", "git diff", "git branch", "git remote",
    "git stash list", "git show", "git rev-parse", "git describe",
    "git tag -l", "git config --get", "git config --list",
    "ls ", "pwd", "date", "whoami", "hostname",
    "wc -l", "wc -c", "wc -w",
    "which ", "type ", "file ",
    "python3 --version", "python --version", "node --version",
    "gh issue view", "gh issue list", "gh pr view", "gh pr list",
    "gh pr checks", "gh api",
)

# =========================================================================
# Approval system constants
# =========================================================================

APPROVAL_EXPIRY_SECONDS = 600  # 10 minutes — default for any action without a specific override

# Canonical action types
ACTION_FORCE_PUSH = "force_push"
ACTION_GIT_PUSH = "git_push"
ACTION_CODE_WRITE = "code_write"
ACTION_DEPLOY = "deploy"
ACTION_TAILSCALE_SSH = "tailscale_ssh"

# Asymmetric TTL per action — higher-risk actions keep the strict 10-min default;
# operationally-long workflows (deploy, ssh-to-own-vps) get the headroom realistic
# flows need. Origin: 2026-05-25 CryptoMondays demo prep — V>>'s SSH approval
# expired mid-event between successive scp deploys, blocking a critical UI fix.
# `feedback_no_self_imposed_blockers.md` PARAMOUNT says: don't fabricate gates
# that block legitimate work; tune the floor where the friction outweighs the
# safety. SSH/deploy already require operator-private SSH key + tailscale auth
# + AskUserQuestion tap — the gate is the third factor, not the first; longer
# TTL on it is acceptable trade.
ACTION_EXPIRY_OVERRIDES = {
    ACTION_FORCE_PUSH: 600,         # 10 min — destructive, keep strict
    ACTION_GIT_PUSH: 600,           # 10 min — destructive, keep strict
    ACTION_CODE_WRITE: 600,         # 10 min — every edit is fresh consent
    ACTION_DEPLOY: 3600,            # 1 hour  — deploy workflows can span an hour
    ACTION_TAILSCALE_SSH: 14400,    # 4 hours — own-VPS ssh, multi-factor protected, long sessions
}

# Superset map: force_push approval also satisfies git_push checks
ACTION_SUPERSETS = {
    ACTION_FORCE_PUSH: {ACTION_GIT_PUSH},
}

# Map free-form strings to canonical action keys
ACTION_ALIASES = {
    "force push": ACTION_FORCE_PUSH,
    "force-push": ACTION_FORCE_PUSH,
    "force_push": ACTION_FORCE_PUSH,
    "git push": ACTION_GIT_PUSH,
    "git-push": ACTION_GIT_PUSH,
    "git_push": ACTION_GIT_PUSH,
    "code write": ACTION_CODE_WRITE,
    "code-write": ACTION_CODE_WRITE,
    "code_write": ACTION_CODE_WRITE,
    "deploy": ACTION_DEPLOY,
    "tailscale ssh": ACTION_TAILSCALE_SSH,
    "tailscale-ssh": ACTION_TAILSCALE_SSH,
    "tailscale_ssh": ACTION_TAILSCALE_SSH,
    "ssh tailscale": ACTION_TAILSCALE_SSH,
}

# Keywords in AskUserQuestion text that grant code_write approval (Gate 7a)
CODE_WRITE_APPROVAL_KEYWORDS = [
    "code write", "code-write", "code_write",
    "writing code", "write code", "code files",
    "write files", "writing files", "edit files",
    "modify files", "modify code",
]

# Keywords that indicate batch code-write (longer expiry)
CODE_WRITE_BATCH_KEYWORDS = [
    "batch", "all files", "multiple files", "bulk",
]

# =========================================================================
# File type detection
# =========================================================================

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".cpp", ".c", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".kt", ".scala", ".sh", ".bash", ".zsh", ".pl", ".lua",
    ".r", ".m", ".mm", ".zig", ".nim", ".v", ".dart", ".ex",
    ".exs", ".erl", ".hs", ".ml", ".fs", ".clj", ".lisp",
}

MAX_READS_PER_FILE = 2

# =========================================================================
# Security patterns
# =========================================================================

# Gate A: Environment Variable Protection
# NOTE: Strings split to avoid triggering our own env-var gate on Write
_AK = "API" + "_KEY"
_SC = "SEC" + "RET"
_TK = "TOK" + "EN"
_PW = "PASS" + "WORD"
_CR = "CRED" + "ENTIALS"
_PK = "PRIV" + "ATE_KEY"

#: An optional directory before a command name, as in `/usr/bin/grep`
#: (GRIP#10120).
_PATH = r"(?:[^\s;&|]*/)?"
#: The search commands a listing pipes into. `env_view._PATTERN_COMMANDS`
#: holds the same names, so change both together (GRIP#10092).
_SEARCH = r"(?:e?grep|fgrep|rg)\b"
#: Commands that run another command after their own options, as in
#: `sudo -u root grep` (GRIP#9993).
LISTING_WRAPPERS = "sudo|doas|command|exec|nice|nohup|time|timeout|stdbuf|env"
#: A wrapper, then any words that are not a search command and hold no
#: separator (its options and their values), then a blank. Either name may
#: follow a directory. Shared with `env_view._COMMAND_WORD`, so the two cannot
#: drift apart.
WRAPPER_PREFIX = rf"(?:{_PATH}(?:{LISTING_WRAPPERS})(?:[ \t]+(?!{_PATH}{_SEARCH})[^\s;&|]+)*[ \t]+)?"
#: Where an environment listing starts: `env` (or the end of `printenv`), a
#: pipe, then a search command, which may open a subshell or a brace group,
#: follow a wrapper and its options, or follow a directory (GRIP#9991,
#: GRIP#9993, GRIP#10120). A brace group's `{` needs a blank after it, as in
#: the shell.
_LISTING = rf"env\s*\|\s*(?:\(\s*|\{{\s+)?{WRAPPER_PREFIX}{_PATH}{_SEARCH}"

SENSITIVE_ENV_PATTERNS = [
    rf"\$[A-Z_]+(?:{_AK}|{_SC}|{_TK}|{_PW}|{_CR}|{_PK})",
    rf"\$\{{[A-Z_]+(?:{_AK}|{_SC}|{_TK}|{_PW}|{_CR}|{_PK})",
    r"printenv\s*\|?\s*(?:grep|rg)?\s*(?:" + _SC + "|KEY|" + _TK + "|PASS|CRED)",
    # `env` also matches the end of `printenv`. The second alternative lets
    # options, a quote or a word prefix sit between the search command and the
    # secret word, which the first does not allow (GRIP#9958). It crosses later
    # pipes, as in a chain of filters, and stops at `;`, `&`, a newline or the
    # start of the next listing, where a match is tried in turn: a chain is
    # read in full and each part once (GRIP#9984). On the Bash path the view
    # removes quotes, and turns a separator or blank inside a search pattern
    # into `_` (GRIP#9983, GRIP#10120).
    rf"env\s*\|?\s*(?:grep|rg)?\s*(?:{_SC}|KEY|{_TK}|PASS|CRED)"
    rf"|{_LISTING}(?:(?!{_LISTING})[^;&\n])*?(?:{_SC}|KEY|{_TK}|PASS|CRED)",
    r"echo\s+\$[A-Z_]*(?:" + _AK + "|" + _SC + "|" + _TK + "|" + _PW + "|AWS_|ANTHRO" + "PIC_|OPEN" + "AI_)",
    # Narrowed 2026-07-08 (V>>--<<V ratified): a source-code env-secret READ is
    # only flagged when its value is ALSO passed to an output/exposure sink
    # (console.log / print / logger / stdout.write). A bare read into a variable
    # is legitimate application code, not command-output exposure (this gate's
    # stated concern), and was false-positiving on real source. Shell-level
    # exposure stays fully covered by the patterns above.
    r'\b(?:console\.(?:log|error|warn|info|debug)|logger\.\w+|print|process\.stdout\.write)[^\n]{0,40}os\.environ(?:\.get)?\s*\(\s*["\'][^"\']*(?:' + _AK + "|" + _SC + "|" + _TK + "|" + _PW + ")",
    r'\b(?:console\.(?:log|error|warn|info|debug)|logger\.\w+|print|process\.stdout\.write)[^\n]{0,40}process\.env\.[A-Z_]*(?:' + _AK + "|" + _SC + "|" + _TK + "|" + _PW + ")",
    r"\$AWS_(?:ACCESS_KEY|" + _SC[:3] + _SC[3:] + "_KEY|SESSION_" + _TK + ")",
    r"\$ANTHRO" + r"PIC_" + _AK,
    r"\$OPEN" + r"AI_" + _AK,
    r"\$GIT" + r"HUB_" + _TK,
]
COMPILED_ENV_PATTERNS = [_lc(p, re.IGNORECASE) for p in SENSITIVE_ENV_PATTERNS]

# Gate B: Database Exposure Prevention
DATABASE_DUMP_PATTERNS = [
    r"\b(?:pg_dump|mysqldump|mongodump|sqlite3\s+\S+\s+\.dump)\b",
    r"\bSELECT\s+\*\s+FROM\s+(?:users|" + _CR.lower() + r"|tokens|" + _PW.lower() + r"s|" + _SC.lower() + r"s|accounts)\b",
    r"postgres://[^:]+:[^@]+@",
    r"mysql://[^:]+:[^@]+@",
    r"mongodb(?:\+srv)?://[^:]+:[^@]+@",
]
COMPILED_DB_PATTERNS = [_lc(p, re.IGNORECASE) for p in DATABASE_DUMP_PATTERNS]

# Gate C: Remote Execution Verification
REMOTE_MODE_ENABLED = os.environ.get("GRIP_REMOTE_MODE", "").lower() == "true"
DEPLOYMENT_TOKEN_VALUE = os.environ.get("GRIP_DEPLOYMENT_" + _TK, "")

# Gate D: Network Binding Protection
UNSAFE_BINDING_PATTERNS = [
    r'HTTPServer\s*\(\s*\(\s*["\']0\.0\.0\.0["\']\s*,',
    r'host\s*=\s*["\']0\.0\.0\.0["\']',
    r'\.bind\s*\(\s*\(\s*["\']["\']',
    r'\.bind\s*\(\s*\(\s*["\']0\.0\.0\.0["\']',
    r'\.listen\s*\(\s*[^)]*0\.0\.0\.0',
    r'INADDR_ANY',
    r'socket\.INADDR_ANY',
]
COMPILED_BINDING_PATTERNS = [_lc(p) for p in UNSAFE_BINDING_PATTERNS]

AUTH_PATTERNS = [
    r'@require_auth',
    r'@authenticate',
    r'verify_' + _TK.lower(),
    r'check_auth',
    r'Authorization',
    r'Bearer\s+',
    r'api_key\s*=',
    r'authenticate\(',
]
COMPILED_AUTH_PATTERNS = [_lc(p, re.IGNORECASE) for p in AUTH_PATTERNS]

# Gate G: Steering Protection
STEERING_SOURCE = os.environ.get("GRIP_STEERING_SOURCE", "")
STEERING_DANGEROUS_PATTERNS = [
    _lc(r"git\s+push\s+.*--force", re.IGNORECASE),
    _lc(r"git\s+reset\s+--hard", re.IGNORECASE),
    _lc(r"\brm\s+-rf\s+", re.IGNORECASE),
    _lc(r"\bdeploy\b", re.IGNORECASE),
    _lc(r"\bnpm\s+publish\b", re.IGNORECASE),
    _lc(_cmd_invocation("vercel"), re.IGNORECASE),
    _lc(r"\bfirebase\s+deploy\b", re.IGNORECASE),
    _lc(r"docker\s+push\b", re.IGNORECASE),
]

# =========================================================================
# Dependency Guardian
# =========================================================================

BLOCKED_PATHS = [
    "node_modules",
    ".next",
    "dist",
    "build",
    "__pycache__",
    "venv",
    ".venv",
    "target",
    "vendor",
    "Pods",
    ".git/objects",
    ".nuxt",
    ".output",
    "coverage",
    ".cache",
]

# =========================================================================
# Git patterns
# =========================================================================

DESTRUCTIVE_GIT_PATTERNS = [
    # [^;&|]* keeps the match inside one shell segment — `.*` reached across
    # `&&` into unrelated text (`git push origin x && rm --force y`), the same
    # false-match class as issue #3804's is_force_push bug.
    r"git\s+push\s+[^;&|]*--force",
    r"git\s+push\s+-f\b",
    r"git\s+reset\s+--hard",
    r"git\s+clean\s+-fd",
    r"git\s+checkout\s+--\s+\S",
    r"git\s+checkout\s+[a-f0-9]+\s+--",
    r"git\s+restore\s+(?!--staged)\S",
    r"git\s+stash\s+drop",
    r"git\s+branch\s+-D\b",
]

SENSITIVE_FILE_PATTERNS = [
    '.env',
    '.npmrc',
    'cred' + 'entials.json',
    _SC.lower() + 's.json',
    _SC.lower() + '.json',
    '.pem',
    '.key',
    'id_rsa',
    'id_ed25519',
    _PK.lower(),
    'api_key',
    '.p12',
    '.pfx',
    _TK.lower() + '.json',
    'serviceaccount',
    'service-account',
    'auth.json',
    '.netrc',
    'htpasswd',
    '.metrics-auth',
    '.audit-signing-key',
    'signing-key',
]

# Root-level dotfile state patterns — runtime files that must never be committed.
# Used by Gate 3 to block staging of state dotfiles.
STATE_DOTFILE_PATTERNS = [
    _lc(r'^\.[\w-]+-state\.json$'),
    _lc(r'^\.[\w-]+-cache\.json$'),
    _lc(r'^\.[\w-]+-counters\.json'),
    _lc(r'^\.metrics-auth$'),
    _lc(r'^\.audit-signing-key$'),
    _lc(r'^\.active-mode[s]?$'),
    _lc(r'^\.drift-state\.json$'),
    _lc(r'^\.insight-(?:stack|verifier-state)\.json$'),
    _lc(r'^\.(?:last-assistant-msg|changelog-seen|wave-position|extended-thinking)'),
    _lc(r'^\.(?:pending-approval|plan-reviewed-session|semantic-context-active)'),
    _lc(r'^\.(?:grip-symlinked|quality-debounce|rsi-budget-state|rsi-last-collect)'),
]

ENV_SAFE_SUFFIXES = (
    '.env.example',
    '.env.sample',
    '.env.template',
    '.env.dist',
    '.env.defaults',
    '.env.schema',
)

GIT_PUSH_PATTERN = _lc(r"git\s+push\b", re.IGNORECASE)
PROTECTED_BRANCHES = {"main", "master", "develop", "release", "production"}

# Git write operations that require the index lock
GIT_WRITE_OPS = _lc(
    r"git\s+(?:-C\s+\S+\s+)?"
    r"(add|commit|merge|rebase|pull|checkout|switch|stash|reset|cherry-pick|revert)\b"
)

GIT_LOCK_STALE_THRESHOLD_SECONDS = int(
    os.environ.get("GIT_LOCK_STALE_THRESHOLD", "300")
)

# =========================================================================
# Bash gate patterns
# =========================================================================

# Tool substitution rules (pattern -> (replacement, reason))
TOOL_SUBSTITUTIONS = {
    r"(?:^|(?:&&|\|\||;)\s*)grep\s+": ("rg", "Use rg (ripgrep) instead of grep for better performance"),
    r"(?:^|(?:&&|\|\||;)\s*)find\s+": ("fd", "Use fd instead of find for better performance"),
    # The negated class excludes command SEPARATORS (newline, ; &), not just the
    # pipe. Without them `[^|]*` runs past the end of the cat command and matches
    # a pipe belonging to some LATER command, so a `cat > file` WRITE is denied
    # because something downstream happened to pipe. Observed (#4409):
    #   cat > /tmp/x.py <<'PY' … PY
    #   pytest -q | tail -8          -> denied, "use Read instead of cat piping"
    # Heredoc bodies are already stripped upstream by check_bash_tools, so the
    # table-pipes theory in the issue is NOT the mechanism — crossing the command
    # boundary is. `cat "a>b.txt" | head` stays denied, so `>` needs no special
    # handling here.
    #
    # `\bcat\b` on its own matches the WORD anywhere, not the COMMAND — the same
    # class of bug _CMD_POSITION exists to fix (see its docstring above). Observed
    # 2026-11-24: `cat` as the VALUE of a git config assignment
    # (`git -c filter.git-crypt.smudge=cat archive … | tar -x`, reading `config`
    # through `tar`, not through `cat`) and `cat` inside a quoted search string
    # (`rg -n "cat piping" lib/gates | head -3`) were both denied, although
    # neither command reads a file through cat. Anchoring on _CMD_POSITION makes
    # `cat` count only where a command can actually start: line-start, after
    # `| ; && || (` / a newline / `$(` (the bare `(` covers `$(` too, since
    # nothing requires a `$` immediately before it), or one of _CMD_POSITION's
    # existing env-var / npx-style prefixes — never mid-word, never after `=`.
    # The match is still purely textual, with no concept of quoting: a quoted
    # string that itself CONTAINS one of those separator characters (e.g.
    # `echo "run; cat x | head"`) still matches, exactly as it did before this
    # change. That residual case is shared by every _CMD_POSITION consumer in
    # this file and is unaffected either way -- not claimed fixed here.
    _CMD_POSITION + r"cat\s+[^|\n;&]*\|(?!\|)": ("Read tool", "Use Read tool instead of cat piping"),
    r"(?:^|&&|\|\||;|\|)\s*rm\s": ("trash", "Use 'trash' (macOS), 'gio trash' (Linux), or 'recycle' (Windows) for recoverable deletion"),
}

BASH_LINT_PATTERNS = {
    r"\$\([^)]+\)\s*;": "Semicolon after command substitution causes parse error in eval contexts. Use && instead.",
    r"sed\s+'[^']*;[^']*'": "Semicolon inside sed pattern causes zsh parse error. Use pipes for multiple sed.",
    r"\[\[\s*!": "Negation in [[ ! ... ]] can cause zsh eval parse errors. Use positive test with inverted logic, or Python for complex conditionals.",
    # zsh applies its :r/:e/:h/:t history-style modifier to a BARE $VAR before a literal
    # ':' -- `$COMMIT:refs/heads/main` becomes `$COMMIT` + `efs/heads/main` (the `:r` is
    # eaten), mangling git refspecs. Brace it (`${COMMIT}:refs/...`) or use a literal SHA.
    # The (?:refs|heads|tags|remotes)/ tail keeps this ZERO-FP: `${VAR}:` and literal SHAs
    # lack the `$<name>:` shape; intentional `$file:r` modifier use is never followed by a
    # refspec component; `$src:$dst` / `$msg: text` don't match. Origin: 2026-06-24 push
    # failed `src refspec ...efs/heads/main does not match`.
    r"\$[A-Za-z_][A-Za-z0-9_]*:(?:refs|heads|tags|remotes)/": "zsh parses $VAR:refs/... as the :r (root) modifier on $VAR, eating ':r' and mangling the git refspec. Use ${VAR}:refs/... (braces) or a literal SHA.",
    # The GENERAL case of the same footgun. The refspec pattern above is narrow, so
    # `git show "$ref:lib/mcp_health.py"` sailed straight through it and returned FOUR
    # false zeros in a row on 2026-08-14: zsh ate `:l` (lowercase) and the path was
    # mangled, which reads as a confident "the file has no such content" rather than as
    # an error. Ordered AFTER the refspec entry deliberately -- check_bash_lint returns
    # on first match, so refspecs keep their more specific message.
    #
    # The character class is MEASURED, not guessed (zsh 5.9 via `zsh -f`, each char
    # tested for whether `$V:<c>suffix` still expands verbatim). Exactly these are
    # consumed as history-style modifiers:  a A c e f F g h l P Q q r s t u w
    # Everything else expands untouched -- digits, `/`, `.`, `:`, `,`, `=`, `?`, `@`,
    # `_`, `+`, `#`, `%`, and the letters b d E i j k m n o p S U v W x X y z. So
    # `$HOST:8080`, `$src:$dst` and `$msg: text` do not match, and neither does the
    # very common `$dir:/abs/path`.
    #
    # The trailing [A-Za-z0-9_./-] is what separates the ACCIDENT from deliberate use:
    # `${f:r}` is braced and can never match, and a bare `$f:r` at a word boundary is
    # someone genuinely asking for the modifier. Only `$VAR:` + modifier + MORE word
    # characters is the mistake this catches.
    r"\$[A-Za-z_][A-Za-z0-9_]*:[aAcefFghlPQqrstuw][A-Za-z0-9_./-]": "zsh consumes the character after a bare $VAR: as a history-style modifier (measured set: a A c e f F g h l P Q q r s t u w), silently mangling the value -- this is how a `git show` of VAR-colon-path returns empty instead of the file, which reads as absence rather than as an error. Brace it: ${VAR}:path",
}

# The lint patterns about a BARE $NAME followed by a colon describe a zsh EXPANSION,
# so they must see the text zsh expands -- and zsh expands parameters inside double
# quotes, modifiers included. The other patterns are matched with long quoted strings
# stripped so prose cannot trip them; that strip turned `git show "$ref:lib/x.py"`
# into `git show ""` and hid both of these from the live hook, which is how a mangled
# `git show` loop ran unchallenged on 2026-10-03. check_bash_lint matches these
# against an expansion view instead (lib/gates/bash_gates.py).
ZSH_EXPANSION_LINT_PATTERNS = frozenset(
    pattern for pattern in BASH_LINT_PATTERNS
    if pattern.startswith(r"\$[A-Za-z_][A-Za-z0-9_]*:")
)

TEMP_SCRIPT_PATTERNS = [
    "temp",
    "tmp",
    "/var/folders/",
    "script",
]
TEMP_SCRIPT_EXTENSIONS = [".sh", ".py", ".js", ".ts"]

# Strings longer than this are treated as natural language and stripped
# before tool substitution checks
LONG_STRING_THRESHOLD = 20

# =========================================================================
# Deploy patterns
# =========================================================================

# Each pattern fires only on an actual deploy *invocation*. `vercel` (and the
# `heroku` CLI) are matched in command position only (see `_cmd_invocation`) so a
# `.vercel/` path, a `*.vercel.app` URL, or `rg vercel <file>` no longer
# false-positives. The `heroku` CLI's most common deploy is a git remote push, so
# `git push [-flags] heroku` is matched as a second branch. Still deny-by-default
# for real deploys -- this narrows *what counts as* a deploy, it does not weaken
# the gate's response to one.
DEPLOY_COMMAND_PATTERNS = [
    (_lc(_cmd_invocation("vercel"), re.IGNORECASE), "Vercel deployment"),
    (_lc(r"\bfirebase\s+deploy\b", re.IGNORECASE), "Firebase deployment"),
    (_lc(r"\bnpm\s+publish\b", re.IGNORECASE), "npm publish to registry"),
    (_lc(r"\bnetlify\s+deploy\b", re.IGNORECASE), "Netlify deployment"),
    (_lc(r"\bwrangler\s+deploy\b", re.IGNORECASE), "Cloudflare Wrangler deployment"),
    (_lc(r"\bfly\s+deploy\b", re.IGNORECASE), "Fly.io deployment"),
    (_lc(r"\brailway\s+up\b", re.IGNORECASE), "Railway deployment"),
    (_lc(r"(?:" + _cmd_invocation("heroku") + r"|\bgit\s+push(?:\s+-\S+)*\s+heroku\b)", re.IGNORECASE), "Heroku operation"),
    (_lc(r"\bkubectl\s+apply\b", re.IGNORECASE), "Kubernetes apply"),
    (_lc(r"\bdocker\s+push\b", re.IGNORECASE), "Docker push to registry"),
    (_lc(r"\baws\s+s3\s+sync\b", re.IGNORECASE), "AWS S3 sync"),
    (_lc(r"\bgcloud\s+app\s+deploy\b", re.IGNORECASE), "Google Cloud deployment"),
    (_lc(r"\byarn\s+deploy\b", re.IGNORECASE), "yarn deploy script"),
    (_lc(r"\bnpm\s+run\s+deploy\b", re.IGNORECASE), "npm deploy script"),
]

# =========================================================================
# SSH patterns (Gate F)
# =========================================================================

SSH_BLOCKED_PATTERNS = [
    (_lc(r"^\s*ssh\s+(?!-)", re.IGNORECASE), "Direct SSH command"),
    (_lc(r"\bssh\s+\S+@", re.IGNORECASE), "SSH to remote host"),
    (_lc(r"\bssh\s+-[A-Za-z]*[^-]\s+\S+", re.IGNORECASE), "SSH with flags to host"),
    (_lc(r"\bansible\s+(?!-playbook)\S+\s+-m\s+(?:shell|command|raw)", re.IGNORECASE), "Ansible ad-hoc shell"),
    (_lc(r"\bansible\s+(?!-playbook)\S+\s+-a\s+", re.IGNORECASE), "Ansible ad-hoc command"),
    (_lc(r"\bscp\s+", re.IGNORECASE), "SCP file transfer"),
    (_lc(r"\brsync\s+[^:]*\S+:", re.IGNORECASE), "Rsync to remote host"),
    (_lc(r"\bsftp\s+", re.IGNORECASE), "SFTP connection"),
]

SSH_ALLOWED_PATTERNS = [
    _lc(r"\bansible-playbook\b", re.IGNORECASE),
    _lc(r"\bssh-keygen\b", re.IGNORECASE),
    _lc(r"\bssh-add\b", re.IGNORECASE),
    _lc(r"\bssh-agent\b", re.IGNORECASE),
    _lc(r"\blocalhost\b", re.IGNORECASE),
    _lc(r"\b127\.0\.0\.1\b", re.IGNORECASE),
    _lc(r"\btailscale\s+ssh\s+\S*@?grip-remote\b", re.IGNORECASE),
    _lc(r"\bssh-keyscan\b", re.IGNORECASE),
    _lc(r"\bssh\b.*\bgrip-remote\b", re.IGNORECASE),
    _lc(r"\bssh\b.*\b100\.104\.253\.41\b", re.IGNORECASE),
    _lc(r"\brsync\b.*\bgrip-remote\b", re.IGNORECASE),
    _lc(r"\bscp\b.*\bgrip-remote\b", re.IGNORECASE),
    # grip-remote-v2 (new VPS): V>> migration access
    _lc(r"\bssh\b.*\b46\.224\.175\.154\b", re.IGNORECASE),
    _lc(r"\bscp\b.*\b46\.224\.175\.154\b", re.IGNORECASE),
    _lc(r"\brsync\b.*\b46\.224\.175\.154\b", re.IGNORECASE),
    _lc(r"\bssh\b.*\bgrip-remote-v2\b", re.IGNORECASE),
]

# =========================================================================
# MCP/UI patterns
# =========================================================================

GRIP_UI_TOOLS = {
    "grip_kanban", "grip_checklist", "grip_decision_matrix", "grip_wizard",
    "grip_inbox", "grip_data_table", "grip_digest", "grip_progress",
    "grip_journal", "grip_concept_map", "grip_timeline", "grip_retrospective",
    "grip_threat_matrix", "grip_phase_stepper", "grip_scoring_rubric",
    "grip_idea_canvas", "grip_circuit_dashboard", "grip_review_annotator",
    "grip_gap_visualiser", "grip_options_picker", "grip_confirmation",
}

MAX_PLAN_ITERATIONS = 2
