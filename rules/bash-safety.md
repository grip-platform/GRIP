# Bash Safety

## Tool Selection
Use `rg` (not grep), `fd` (not find), `jq` for JSON, `bat` for display. Always exclude `node_modules`, `venv`, etc.

## ripgrep flag footguns (PARAMOUNT — verify-canonical corruption source)
- **`rg -r` is `--replace`, NOT "recursive".** ripgrep recurses BY DEFAULT — there is no recursion flag. So `rg -rn 'PATTERN'` parses as `--replace=n` and **silently rewrites every match to the literal `n`** in the output (and to disk on `> file`). This is invisible data corruption of verify-canonical reads: `claude-sonnet-4-6` → `nsonnet-4-6`, `_DEFAULT_MODEL` → `n`. For "recursive + line numbers" use **`rg -n`** (recursion is implicit). Only use `-r`/`--replace` when you genuinely want substitution.
- If a read shows suspicious single-letter tokens where identifiers should be, suspect a stray `-r` in your OWN command before blaming any tool (self-inflicted-corruption-before-external-blame). Confirm via the native Read tool (mangle-proof) + a numeric check (`grep -c 'literal' file` — counts can't be substituted).
- **`rg -E` is `--encoding`, NOT "extended regex".** ripgrep is ERE by default, so there is no `-E` to reach for — it consumes the next token as a character-encoding name and dies with `error parsing flag -E: grep config error: unknown encoding: <your pattern>`. Loud, not silent (unlike `-r`), so it costs a round-trip rather than corrupting data. Just drop it: `rg -n 'a|b|^c'` already works. Verified 2026-08-01 on ripgrep 15.2.0 — the quoted wording is that build's verbatim output (`grep config error` is ripgrep's own phrasing, not GNU grep's); expect the same shape, not necessarily the same string, on other versions.
- **`rg -h` is `--help`, NOT `--no-filename`.** GNU grep's `-h` suppresses the filename prefix; ripgrep spells that `-I`, and gives `-h` to help. So `rg -oh PATTERN file` prints ripgrep's entire help page — roughly 120 lines of flag documentation — instead of the matches, with exit 0. It is not silent like `-r` and not loud like `-E`: it *succeeds* and floods, which is the worst of the three in a context-bounded agent, because the failure arrives as thousands of tokens of plausible-looking output. Use `rg -oI` when you want match-only with no filename.
- **The class, not the three instances:** grep's single-letter flags do NOT reliably carry over to rg — and crucially they carry over *inconsistently*, which is what makes the reflex dangerous. `-P` DOES mean the same thing in both (PCRE; `rg -P '\d+'` works on a PCRE2-enabled build), while `-r`, `-E` and `-h` each mean something else entirely. A flag working last time is therefore no evidence the next one will. When an rg flag "does nothing" or errors oddly, check `rg --help` for THAT letter before assuming the tool is broken.

## Looser Search — reach gitignored content without token waste (canonical)

Plain `rg` respects `.gitignore`, so it silently SKIPS useful gitignored content
(`drafts/`, non-hidden `state/`, local notes). The retrieval idiom that reaches it:

```bash
rg --no-ignore-vcs <pat>     # searches gitignored content too; keeps hidden-file skip
rg -uu <pat>                 # also reaches hidden dotfiles (rarely needed)
```

Both stay token-cheap because `config/ripgreprc` (wired via `RIPGREP_CONFIG_PATH`
in `~/.zshrc`) carries always-on `--glob=!**/<dir>/**` excludes for every
dep/build/cache dir plus secret patterns — and those globs survive BOTH
`--no-ignore-vcs` AND `-uu` (manual `--glob` outranks ignore flags). So you never
search `node_modules`, `dist`, `.env`, `*.pem`, etc., regardless of the flag.

- Dir-exclude glob MUST be `!**/name/**` — `!name/*` does NOT exclude.
- `--no-ignore-vcs` keeps ripgrep's hidden-file skip, so dot-secrets (`.env`, `.credentials.json`, `.git-crypt/`) stay out for free; `-uu` bypasses that, which is why the secret globs above also exist.
- `tmp/`, `temp/`, `.cache/` are intentionally NOT excluded (operator carve-out — useful scratch).
- Current shell didn't inherit it? `export RIPGREP_CONFIG_PATH=$HOME/.claude/config/ripgreprc`.
- Regression test: `tests/test_ripgrep_config.py`.

## RTK Token Compression (auto-active via hook)
RTK is installed (0.42.4 when checked on 2026-11-11) and wired as a PreToolUse Bash hook (position 11, last in chain).
It transparently rewrites large-output commands to compressed equivalents — no manual prefixing needed.
GRIP-specific TOML filters active: `~/.claude/.rtk/filters.toml` (9 custom filters, trusted).

### Universal Routing Mandates (always prefix with `rtk`)

| Raw | RTK equivalent | Saving |
|-----|----------------|--------|
| `git <any>` | `rtk git <any>` | -59–80% |
| `git log` with no `-n` | `git log -n 10000 ...` (any explicit `-n` above the expected count) -- `rtk git log` stops at 50 commits when no `-n` is given and says nothing (GRIP#9179; on rtk 0.42.4 a week of 208 commits printed 50). An explicit `-n` is kept byte for byte (214 lines both ways on 2026-11-25) and works without rtk; `rtk proxy git log` works too, but only where rtk is installed | n/a |
| `gh <any>` | `rtk gh <any>` -- except `gh pr create` / `gh pr edit` bodies, which must come from a file path, never `--body-file -` (stdin is lost; see the RTK footgun below) | -80% |
| `ls -la` / `ls` | `rtk ls -la` | -70% |
| `rg <any>` | `rg <any>` (passthrough — preserves ripgrep flags) | n/a |
| `pytest` / `venv/bin/pytest` | `rtk pytest` | -90% |
| `pnpm test` / `vitest` | `rtk vitest` | -90% |
| `tsc --noEmit` | `rtk tsc` | -80% |
| `next build` | `rtk proxy ./node_modules/.bin/next build`, run in the app folder -- not `rtk next build`, which printed `Errors: 0` without building on 2026-11-11 (GRIP#9166), and not `npx next build`, which can run a `next` from outside the project. Check that the build output changed: `out/` for a static export, else `BUILD_ID` in `.next/` or in the `distDir` that `next.config` sets | n/a |

### GRIP-Specific TOML Filters (auto-applied, no prefix needed)

| Command pattern | Filter | Saving |
|----------------|--------|--------|
| `python3 .claude/lib/*.py` | grip-python-lib | ~75% |
| `pm2 logs` | pm2-logs | ~92% |
| `pm2 restart/stop/start` | pm2-manage | ~85% |
| `ssh root@...` / `ssh grip@...` | ssh-remote | ~70% |
| `tailscale ssh ...` | tailscale-ssh | ~70% |
| `git checkout` / `git switch` | git-checkout | ~85% |
| `fd ...` | fd-discover | ~50% |
| `git push` / `git fetch` | git-push-sink | ~80% |
| `npm ci` / `npm install` | npm-install-sink | ~70% |

Full unfiltered output: `rtk proxy <cmd>`

Does NOT affect Read/Grep/Glob (Claude Code native tools — Bash calls only).
E11 JSONL watermarking hook handles Read-tool compression for `.jsonl` files.
Telemetry: `RTK_TELEMETRY_DISABLED=1` in `~/.zshrc`.

### RTK footgun -- `rtk gh pr create` / `rtk gh pr edit` get an EMPTY stdin

Never `--body-file -` under rtk; write the body to a file and pass its path.

- **Symptom.** The PR is published with no description, and nothing warns you: `gh pr create`
  prints the URL and exits 0 either way. GRIP#7367 and GRIP#7373 shipped that way on
  2026-10-05 -- a silent breach of the plain-language PR-body mandate
  (`rules/commit-standards.md`).
- The loss is in rtk's `gh pr` handling,
  not in rtk generally.
- **Check a fresh PR once:** `gh pr view N --json body --jq '.body' | wc -c` -- 1 means empty.
- **Restore:** `gh pr edit N --body-file <path>`.

## Syntax Safety (zsh eval)
- No semicolons after `$()` — use `&&` instead (mechanically gated: `lib/gates/patterns.py` `BASH_LINT_PATTERNS`)
- Use pipes for multiple sed: `sed 's|a||' | sed 's|b||'`
- Avoid `!` in `[[ ]]` — use positive tests or Python
- **Unquoted `$VAR` does NOT word-split in zsh** (unlike bash/sh). `for f in $LIST` and `cmd -- $PATHS` pass the *entire* string as ONE argument. Use a zsh array (`arr=(a b c); cmd -- "${arr[@]}"`), a heredoc `while IFS= read -r f; do …; done <<'EOF'`, or literal multi-arg commands.
- **A bare `$VAR` followed by `:` and a letter is often a zsh modifier, NOT literal text -- brace every variable a colon follows.** zsh applies its history-style modifiers to an unbraced parameter inside double quotes as well as outside them. Measured on zsh 5.9, these letters are consumed when more word characters follow: `a A c e f F g h l P Q q r s t u w` (`:l` lowercases, `:u` uppercases, `:r` `:e` `:h` `:t` cut a path, `:s` substitutes). For a literal colon after a variable (refspecs, `<rev>:<path>`, `host:port`), **brace it**: `${VAR}:lib/…`. Digits, `/`, `.` and a second `$` are not modifiers, so `$HOST:8080` and `$src:$dst` are safe. Mechanically gated in `lib/gates/patterns.py` `BASH_LINT_PATTERNS`, matched against the text zsh expands, double-quoted strings included (`lib/gates/bash_gates.py` `_expansion_view`).
- **`status` is a READ-ONLY special variable in zsh** — it mirrors `$?`, so a plain `status=$(cmd)` aborts the script with `(eval):N: read-only variable: status`. bash has no such reservation, so a poller/loop that runs fine under bash dies under zsh (the harness shell). Name loop/poll vars `rc` / `run_status` / `st` — never `status`. (`pipestatus`/`signals` are NOT read-only — but are still unsafe variable names; see the silent-clobber bullet below.)
- **Full read-only-special set — none may be used as an ordinary variable name (assignment aborts with `read-only variable: NAME`).** The set is version-specific; regenerate the authoritative list for the running zsh with `zsh -fc 'zmodload zsh/parameter; for k in ${(ok)parameters}; do [[ ${parameters[$k]} == *readonly* ]] && print -- $k; done'`. zsh 5.9 snapshot — word-like names that realistically collide with an ordinary variable: `status` `funcstack` `functrace` `funcfiletrace` `funcsourcetrace` `functions_source` `history` `historywords` `builtins` `reswords` `patchars` `keymaps` `widgets` `modules` `parameters` `jobstates` `jobtexts` `jobdirs` `userdirs` `usergroups` `termcap` `terminfo` (plus the `dis_*` variants `dis_builtins` `dis_functions_source` `dis_patchars` `dis_reswords`); also read-only but uppercase/punctuation and unlikely to collide: `ARGC` `HISTCMD` `LINENO` `PPID` `TTYIDLE` `ZSH_SUBSHELL` `ZSH_EVAL_CONTEXT` `zsh_eval_context` `zsh_scheduled_events` `-` `!` `?` `@` `*` `#` `$`. Method: the list IS zsh's `$parameters` read-only-flagged set; spot-checked by direct assignment (`status`/`LINENO`/`HISTCMD`/`PPID`/`funcstack`/`ARGC`/`reswords` abort; the non-flagged `pipestatus`/`signals` assign without aborting — but see the next bullet).
- **`pipestatus` and `signals` are the settable trap — a SILENT bug, not a loud abort.** Neither is read-only, so assigning them does not abort; but each shadows live shell state, so using either as your own variable silently corrupts it. `pipestatus` is the running pipe-exit-status array — the shell overwrites your value after the very next command (verified zsh 5.9: `pipestatus=(5 5)` then `true | false` yields `0 1`, not `5 5`), so you both lose your value AND clobber the status any later `$pipestatus` read depends on. `signals` is the signal-name array (34 entries, `EXIT HUP …`); the assignment sticks and wipes it for the rest of the shell. Use distinct names here too.

## Path Handling
- Always `--` before variable paths to prevent flag interpretation
- Project dir encoding: `path.replace('/', '-').replace('.', '-')` (keeps leading dash)

## JSONL Processing
Always use `jq -s` (slurp mode) for JSONL files.

## Cross-Platform
Scripts must work on macOS (zsh), Linux (bash), Windows Git Bash. Use `lib/file-mtime-cache.sh` for cross-platform mtime. Platform-specific `stat`: macOS `-f %m`, Linux `-c %Y`.

## macOS: No `timeout` Command (PERMANENT)
macOS does not ship GNU `timeout`. Never use `timeout` in Bash on macOS. Alternatives:
- **Python**: `subprocess.run(..., timeout=N)` — preferred, cross-platform
- **Bash builtin**: `CLAUDE_DIR/lib/` helpers or background + kill pattern
- **brew**: `brew install coreutils` provides `gtimeout`, but don't assume it's installed

## Timeouts
- `optim.sh audit`: 300s, `optim.sh cycle`: 600s, `optim.sh detect`: 180s
- RSI minimum scan window: 24 hours (shorter produces insufficient data)

Narrative (origin, incidents, measurements): `docs/doctrine/bash-safety.md`.
