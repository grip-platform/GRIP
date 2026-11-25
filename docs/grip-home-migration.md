# GRIP Home Migration Plan: `~/.claude` to `~/.grip`

**Status**: DRAFT — pending V>> review. DO NOT execute.
**Author**: planning subagent, Gen 482, for V>>
**Date**: 2026-04-18

---

## 1. Executive Summary

GRIP and Claude Code (Anthropic's harness) currently share `~/.claude`,
conflating Layer 0 (the CC harness `settings.json`/`.mcp.json`) with Layer 1
(the public GRIP repo — lib, skills, hooks, agents, commands, rules, docs,
bin). This plan separates them by moving the GRIP *repository content* to
`~/.grip` while keeping CC's required settings in `~/.claude` via a thin
compatibility layer. The migration uses **Option A (symlink + env var
overlay)** because it provides functional parity with the least code change
and the cleanest rollback.

---

## 2. Audit Findings

### 2.1 Reference counts by bucket (`rg` with `!venv !node_modules !.next !dist !__pycache__`)

| Bucket | Hits | Files | STAYS (CC harness) | MOVES (GRIP) |
|---|---|---|---|---|
| `settings.json` | 54 | 1 | 54 (all hook commands) | 0 direct — path rewrite only |
| `.mcp.json` | 0 | 1 | n/a (uses relative `mcp-servers/...`) | 0 — already portable |
| `lib/` | 282 | 131 | 0 | 282 |
| `bin/` | 42 | 8 | 0 | 42 |
| `hooks/` | 53 | 23 | 0 | 53 (hooks are GRIP-authored) |
| `skills/` | 701 | 160 | 0 | 701 |
| `agents/` | 35 | 16 | 0 | 35 |
| `rules/` | 18 | 6 | 0 | 18 |
| `docs/` | 70 | 9 | 0 | 70 |
| `commands/` | 139 | 50 | 0 | 139 |
| **Total text refs** | **1,394** | **405** | **54 (path)** | **1,340** |

Additional audit:

- `CLAUDE_DIR` / `GRIP_DIR` already-used env vars: **147 occurrences in lib/, 1,318 across repo**. The codebase has *already converged* on `CLAUDE_DIR` as the canonical env var pattern. Example (`hooks/auto-memory-hook.py:31`):
  ```python
  CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR") or Path.home() / ".claude")
  ```
- Hardcoded absolute `~/.claude`: **90 hits across 14 files** — mostly data/ JSONL logs, launchd plists, and one drafts file. These are state/artefact files (gitignored in most cases) that will be rewritten in-place.
- `.mcp.json` MCP servers use **relative paths** (`mcp-servers/code-index/start.sh`). Spawned from cwd `~/.claude`. Migration target cwd `~/.grip` means they keep working without edits.

### 2.2 Classification rationale

- **STAYS in `~/.claude`**: only `settings.json` and `settings.local.json` paths — because the CC harness hard-requires `~/.claude/settings.json`. Their *content* still references `$HOME/.claude/...` which will be rewritten to `$HOME/.grip/...`.
- **MOVES to `~/.grip`**: every `~/.claude/<path>` reference that points to GRIP repo content (lib, skills, hooks, agents, commands, rules, docs, bin, scripts, mcp-servers, tests, config). 1,340 literal references.
- **DUAL NATURE**: The `~/.claude` directory becomes a thin compatibility shim containing only: `settings.json`, `settings.local.json`, `.mcp.json`, and the CC-internal `projects/` + `todos/` + `shell-snapshots/` + `statsig/` + `ide/` sub-dirs the harness writes to.

### 2.3 Sample read confirmations

Three hooks/lib files inspected to confirm the environment-variable pattern is
already canonical:

1. `hooks/auto-memory-hook.py:31` — `os.environ.get("CLAUDE_DIR") or Path.home() / ".claude"`
2. `hooks/startup/grip.sh:8` — `source "${CLAUDE_DIR:-$HOME/.claude}/lib/portable-sed.sh"`
3. `lib/path-resolver.sh:32` — `readonly CLAUDE_PROJECTS_DIR="$HOME/.claude/projects"`
4. `bin/grip:202` — `[[ -z "${CLAUDE_DIR:-}" ]] && readonly CLAUDE_DIR="$HOME/.claude"`

Hardcoded `$HOME/.claude` already has an env-var override in the idiomatic cases. The ones that *don't* currently support override (like `lib/path-resolver.sh`) need one line changed.

---

## 3. Backward Compatibility Strategy (the decision)

### 3.1 Options evaluated

| Option | Description | Functional parity? | Rollback cost | Complexity |
|---|---|---|---|---|
| **(a) Full symlink** `~/.claude -> ~/.grip` | Move content to `~/.grip`, leave symlink so CC still finds settings | YES (ln -s is transparent to CC) | `rm symlink && mv back` | Lowest |
| **(b) Env var `GRIP_DIR` + fallback** | Every file checks `${GRIP_DIR:-$HOME/.claude}` | YES (if all 405 files rewritten) | Revert env var | Highest — 1,340 edits |
| **(c) Dual-write period** | Keep both, write to both | FRAGILE — divergence risk within hours | Complex — pick a winner | High |
| **(d) Hard cutover** | Move everything, break CC until paths rewritten | NO during the window | Restore from snapshot | Medium |

### 3.2 Chosen: **Option A — full symlink `~/.claude -> ~/.grip`**

**Justification:**

1. **Functional parity proof:** A POSIX symlink is resolved transparently by CC, the shell, Python `Path.home()/".claude"`, and every hook command in `settings.json`. The process `$HOME/.claude/hooks/...` resolves to the identical inode as `$HOME/.grip/hooks/...`. Hash the files on both sides: identical. Run any CC session: identical. The 1,340 literal references keep working because they resolve through the symlink.
2. **KISS:** one `mv` + one `ln -s`. Zero code edits in the first phase. 0 files touched in lib/bin/skills/hooks/agents/commands/rules/docs to achieve Day-0 parity.
3. **Staged path rewriting can follow as a second, non-blocking phase:** once the symlink works, we rewrite the 1,340 literal references to `~/.grip/...` or `${GRIP_DIR:-$HOME/.grip}/...` at our leisure, each batch verified against the symlinked path (which is still valid). The symlink is the safety net.
4. **Rollback is O(seconds):** `rm ~/.claude && mv ~/.grip ~/.claude`.
5. **git-crypt survives:** `.git/` moves with the tree; git-crypt's smudge filter operates via the `.git/config` and is location-agnostic. The `grip-worktree.sh` script only cares about the git common-dir, which follows the move.

**Why not (b) the env-var approach alone**: 1,340 literal references means 1,340 potential regressions. Even at 99.9% accuracy that's ~1 broken reference per subsystem. The symlink eliminates all of them in one atomic operation and lets us do the rewrite *defensively* afterwards.

**Why not (c) dual-write**: two sources of truth for state/session, learning/, cache/, KONO — any hook that writes to one while another reads from the other produces lost updates within the first session. Ruled out on correctness grounds.

**Why not (d) hard cutover**: guaranteed window of broken hooks until every reference is rewritten. Violates the "zero regressions" constraint.

### 3.3 Layered design (aligns with 140.85 SKILL.md)

```
Layer 0 (CC harness)      ~/.claude/settings.json     (actual file, not symlink)
                          ~/.claude/settings.local.json
                          ~/.claude/.mcp.json
                          ~/.claude/projects/         (CC-written state)
                          ~/.claude/todos/            (CC-written state)
                          ~/.claude/statsig/          (CC-written state)
                          ~/.claude/shell-snapshots/  (CC-written state)
                          ~/.claude/ide/              (CC-written state)

Layer 1+ (GRIP)           ~/.grip/                    (the git repo)
                          ~/.claude/lib -> ~/.grip/lib
                          ~/.claude/skills -> ~/.grip/skills
                          ~/.claude/hooks -> ~/.grip/hooks
                          ~/.claude/agents -> ~/.grip/agents
                          ~/.claude/bin -> ~/.grip/bin
                          ~/.claude/commands -> ~/.grip/commands
                          ~/.claude/rules -> ~/.grip/rules
                          ~/.claude/docs -> ~/.grip/docs
                          ~/.claude/scripts -> ~/.grip/scripts
                          ~/.claude/mcp-servers -> ~/.grip/mcp-servers
                          ~/.claude/tests -> ~/.grip/tests
                          ~/.claude/config -> ~/.grip/config
                          ~/.claude/data -> ~/.grip/data
                          ~/.claude/state -> ~/.grip/state
                          ~/.claude/learning -> ~/.grip/learning
                          ~/.claude/cache -> ~/.grip/cache
                          ~/.claude/logs -> ~/.grip/logs
                          ~/.claude/facts -> ~/.grip/facts
                          ~/.claude/private -> ~/.grip/private
                          ~/.claude/CLAUDE.md -> ~/.grip/CLAUDE.md
                          ~/.claude/venv -> ~/.grip/venv
                          ~/.claude/.git -> ~/.grip/.git  (for git ops in ~/.claude)
```

**This is per-directory symlinks, NOT a top-level symlink.** A top-level
symlink would capture CC harness state into the GRIP git tree, which defeats
the purpose. Per-directory symlinks keep Layer 0 files physically in
`~/.claude/` (where CC writes them) and Layer 1 content physically in
`~/.grip/` (where it logically belongs and where git operates).

---

## 4. Migration Script Spec (`bin/migrate-to-grip-dir`)

Not yet implemented — spec only. Pseudo-code:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Pre-flight
require_fresh_session    # assert no other CC sessions running
require_git_clean        # abort if ~/.claude has uncommitted changes
require_no_active_rsi    # abort if RSI/ultrado markers present
snapshot_backup          # tar -czf ~/.grip-backup-<ts>.tar.gz --exclude venv --exclude node_modules ~/.claude

# Phase 1: create target
mkdir -p ~/.grip
chmod 700 ~/.grip

# Phase 2: move GRIP content (git-tracked directories and their state)
GRIP_DIRS=(
  lib skills hooks agents bin commands rules docs scripts
  mcp-servers tests config data state learning cache logs
  facts private apps articles audit backlog contracts
  contexts debug drafts embeddings plans prep web
  .git                # git repo moves with content
  venv                # venv rebuilt at new location (see phase 5)
)
for d in "${GRIP_DIRS[@]}"; do
  [[ -e "$HOME/.claude/$d" ]] && mv "$HOME/.claude/$d" "$HOME/.grip/$d"
done

# Phase 3: move tracked root files
GRIP_FILES=(
  CLAUDE.md CHANGELOG.md CONTRIBUTING.md README.md AGENTS.md
  activate-venv.sh .gitignore .evo-last-run .mcp.json.example
  BOOTSTRAP_COMPLETE.md
)
for f in "${GRIP_FILES[@]}"; do
  [[ -e "$HOME/.claude/$f" ]] && mv "$HOME/.claude/$f" "$HOME/.grip/$f"
done

# Phase 4: preserve Layer 0 (CC harness) files IN PLACE — DO NOT MOVE
# These physically remain in ~/.claude/:
#   settings.json, settings.local.json, .mcp.json,
#   projects/, todos/, statsig/, shell-snapshots/, ide/, .credentials.json,
#   any other *.json that CC wrote

# Phase 5: create per-directory compatibility symlinks
for d in "${GRIP_DIRS[@]}" "${GRIP_FILES[@]}"; do
  ln -sfn "$HOME/.grip/$d" "$HOME/.claude/$d"
done

# Phase 6: rewrite settings.json hook commands
# Option: leave as $HOME/.claude (symlinks handle it) — PREFERRED for Day-0 parity
# Option: rewrite to $HOME/.grip (done in Phase 2 of path cleanup, post-stabilisation)

# Phase 7: rebuild venv at new location (site-packages has absolute paths)
rm -rf "$HOME/.grip/venv"
python3 -m venv "$HOME/.grip/venv"
"$HOME/.grip/venv/bin/pip" install -r "$HOME/.grip/requirements.txt"
ln -sfn "$HOME/.grip/venv" "$HOME/.claude/venv"

# Phase 8: re-register MCP servers (in case CC caches absolute paths)
claude mcp remove grip-channel --scope project 2>/dev/null || true
claude mcp remove grip-apps --scope project 2>/dev/null || true
claude mcp remove code-index --scope project 2>/dev/null || true
claude mcp remove linkedin-dma --scope project 2>/dev/null || true
# .mcp.json is already symlinked, CC will re-register on next launch

# Phase 9: export GRIP_DIR for all future shells
echo 'export GRIP_DIR="$HOME/.grip"' >> ~/.zshrc  # or appropriate shell rc
echo 'export CLAUDE_DIR="$HOME/.claude"' >> ~/.zshrc

# Phase 10: verify
bash "$HOME/.grip/scripts/verify-grip-install.sh"
python3 "$HOME/.grip/lib/grip_doctor.py"

# Phase 11: commit marker
cd "$HOME/.grip"
git add -A
git commit -m "chore(migration): relocate GRIP repo to ~/.grip (symlinks in ~/.claude)"
```

**Preservation guarantees:**

- `.git` history moves with the tree, so `git log` is intact
- `settings.json` / `settings.local.json` / `.mcp.json` stay physically in `~/.claude/` (CC protocol)
- `state/session/*` moves as part of `state/` directory (symlink keeps reads working)
- `cache/*`, `learning/*`, `memory/*` move as directories, symlinks preserve all references
- `venv` is rebuilt because pip-installed packages hardcode the prefix path in `bin/pip`, `bin/python3`, and site-packages `.pth` files. Symlinking the old venv would break non-trivially on first pip operation.
- `projects/` (CC's per-project session storage) stays in `~/.claude/` — it's a CC artefact, not GRIP content
- Hardcoded-path rewrites **deferred** to a Phase-2 PR. Symlinks cover Day 0.

---

## 5. Claude Code Integration Decision

**Chosen: (a) per-directory symlinks in `~/.claude` pointing into `~/.grip`**, NOT a top-level symlink, NOT config-split.

**Rationale:**

- CC's protocol requires `~/.claude/settings.json` to exist as a real file. It will.
- CC reads `~/.claude/.mcp.json` from cwd at launch. It will (via symlink).
- CC writes transient state to `~/.claude/projects/<project>/` — this stays in `~/.claude/` as a real directory (CC-owned, not GRIP-owned).
- All GRIP content (lib, skills, hooks, etc.) resolves through symlinks and looks byte-identical to CC.

**Why not top-level symlink (`~/.claude -> ~/.grip`)**:
It would pull CC's transient state (`projects/`, `todos/`, `shell-snapshots/`) into the GRIP git tree — either git-ignored-but-present (untidy) or accidentally committed (pollutes history). The per-directory split is cleaner.

**Why not config-split (GRIP reads `~/.grip/grip-settings.json`)**:
GRIP doesn't have its own settings.json — all its config is in settings.json's hook blocks (which CC owns) and in `config/grip.json` (which already lives inside the repo). A config-split requires new code to merge configs at read time, which is exactly the complexity KISS tells us to avoid.

**Functional-parity impact**: zero. Every file access path that works today resolves identically after migration. `stat -L` on every symlinked path returns the same inode as the source.

---

## 6. MCP Server Path Rewrites

`.mcp.json` uses **relative paths** (verified):

```json
"args": ["mcp-servers/code-index/start.sh"]
"args": ["mcp-servers/grip-apps/dist/index.js"]
"args": ["mcp-servers/grip-channel/dist/index.js"]
"args": ["mcp-servers/linkedin-dma/server.py"]
```

These resolve against **cwd** at CC launch. Since users launch CC from `~/.claude/` today, cwd is `~/.claude/` and the symlink `~/.claude/mcp-servers -> ~/.grip/mcp-servers` makes these paths resolve correctly after migration. **No rewrite needed.**

**However:** users who launch CC from `~/.grip/` directly will also want these to work. Since `~/.grip/mcp-servers/` is the real directory, launching from `~/.grip/` works natively with the same relative paths.

**Atomic rewrite (if user prefers absolute paths for clarity):**

```python
# scripts/rewrite-mcp-paths.py
import json, os, pathlib, tempfile, shutil
GRIP = os.environ.get("GRIP_DIR", f"{os.environ['HOME']}/.grip")
mcp_path = pathlib.Path(f"{os.environ['HOME']}/.claude/.mcp.json").resolve()
data = json.loads(mcp_path.read_text())
for server in data.get("mcpServers", {}).values():
    args = server.get("args", [])
    server["args"] = [
        f"{GRIP}/{a}" if a.startswith("mcp-servers/") else a
        for a in args
    ]
# Atomic write
tmp = tempfile.NamedTemporaryFile("w", dir=mcp_path.parent, delete=False)
json.dump(data, tmp, indent=2)
tmp.close()
os.replace(tmp.name, mcp_path)
```

Recommendation: **leave relative**. Simpler, more portable, already works.

---

## 7. Hook Path Updates (`settings.json`)

All 54 hook entries use `$HOME/.claude/...`. With the symlink strategy, these
resolve correctly on Day 0 with zero edits. A Phase-2 cleanup (after 14 days
of stability) rewrites them to `$HOME/.grip/...` for clarity.

**Phase-2 rewrite script spec** (`bin/rewrite-settings-paths`):

```python
# Pseudo-code
import json, re, pathlib, tempfile, os

settings = pathlib.Path(os.environ["HOME"]) / ".claude" / "settings.json"
data = json.loads(settings.read_text())

def rewrite(cmd: str) -> str:
    # Replace $HOME/.claude/hooks -> $HOME/.grip/hooks
    # Replace $HOME/.claude/lib -> $HOME/.grip/lib
    return (cmd
      .replace("$HOME/.claude/hooks/", "$HOME/.grip/hooks/")
      .replace("$HOME/.claude/lib/",   "$HOME/.grip/lib/")
      .replace("$HOME/.claude/statusline-command.sh",
               "$HOME/.grip/statusline-command.sh"))

for event_name, event_list in data.get("hooks", {}).items():
    for entry in event_list:
        for h in entry.get("hooks", []):
            if "command" in h:
                h["command"] = rewrite(h["command"])
if "command" in data.get("statusLine", {}):
    data["statusLine"]["command"] = rewrite(data["statusLine"]["command"])

# Atomic write with backup
backup = settings.with_suffix(".json.pre-grip-dir.bak")
shutil.copy2(settings, backup)
tmp = tempfile.NamedTemporaryFile("w", dir=settings.parent, delete=False)
json.dump(data, tmp, indent=2); tmp.close()
os.replace(tmp.name, settings)
```

Validation after rewrite:

```bash
jq '.hooks | length' ~/.claude/settings.json   # must equal pre-rewrite count
jq '[.hooks[][].hooks[].command] | length' ~/.claude/settings.json
```

---

## 8. Rollback Procedure

**Pre-migration snapshot:**
```bash
tar --exclude="**/venv" --exclude="**/node_modules" --exclude="**/__pycache__" \
    -czf "$HOME/.grip-premigration-$(date +%Y%m%d-%H%M%S).tar.gz" \
    -C "$HOME" .claude
```

**Rollback steps (if the migration misbehaves within the 48-hour observation window):**
```bash
# 1. Stop all Claude Code sessions
pkill -x claude

# 2. Stop any running MCP servers
pm2 stop grip-channel 2>/dev/null || true
pkill -f "mcp-servers/grip-apps" || true
pkill -f "mcp-servers/code-index" || true

# 3. Remove the new location + symlinks
rm -rf "$HOME/.grip"
# Remove per-directory symlinks in ~/.claude (they now point to deleted paths)
for link in "$HOME/.claude"/*; do
  [[ -L "$link" ]] && rm -- "$link"
done

# 4. Restore from snapshot
cd "$HOME"
tar -xzf "$HOME/.grip-premigration-<timestamp>.tar.gz"

# 5. Unset env vars in shell rc (if added)
sed -i.pre-grip-dir.bak '/export GRIP_DIR=/d' ~/.zshrc
sed -i.pre-grip-dir.bak '/export CLAUDE_DIR=/d' ~/.zshrc

# 6. Verify
bash "$HOME/.claude/scripts/verify-grip-install.sh"
```

Rollback is tested as the final step of the dry-run phase (see section 11).

---

## 9. Functional-Parity Test Checklist

Run all 25 checks in a **fresh CC session** immediately after migration.
Every check must pass before marking migration successful.

### Harness

1. [ ] CC launches cleanly from `~/.claude/` — `claude --help` exits 0 within 5s
2. [ ] CC launches cleanly from `~/.grip/` — same
3. [ ] `~/.claude/settings.json` loaded (verify via `jq` read of stored settings)
4. [ ] `~/.claude/.mcp.json` loaded — `claude mcp list` shows 4 servers
5. [ ] Status line renders — `bash $HOME/.claude/statusline-command.sh` exits 0

### Hooks (all 54 entries in settings.json)

6. [ ] PreToolUse hooks fire — touch a file, verify `hooks/context-gate-hook.py` log line appears in `logs/sentinel.jsonl`
7. [ ] PostToolUse hooks fire — same check with `quality-posttool-hook.py`
8. [ ] UserPromptSubmit hook fires — submit a prompt, check `hooks/user-prompt-secrets-hook.py` invocation log
9. [ ] Stop hook writes MEMORY — end a session, verify `auto-memory-hook.py` appends to `projects/<enc>/memory/MEMORY.md`
10. [ ] Sentinel hook writes to `logs/sentinel.jsonl` on every tool call
11. [ ] Elicitation hook loads (MCP elicitation attempt does not crash)

### Skills / Agents / Commands

12. [ ] `~210` skills load — `python3 $HOME/.grip/scripts/generate-skills-index.py --count` matches pre-migration count
13. [ ] `~38` agents load — list agents via CC, count matches
14. [ ] All 56 commands discoverable — `ls $HOME/.grip/commands/*.md | wc -l` matches pre-migration

### GRIP-specific commands

15. [ ] `/recall` works — returns current instance, generation, session context
16. [ ] `/save` works — writes to `state/session/`, `learning/insights.jsonl`, KONO
17. [ ] `/140.85` works — codec panel renders, `state/missions/counter.json` readable
18. [ ] `/140.85 --save` works — Fibonacci counter advances, mission log appended
19. [ ] `/mode status` works — returns active mode(s)
20. [ ] `/converge` dry-run works — `python3 $HOME/.grip/lib/converge_cookbook.py --dry-run`

### Infrastructure

21. [ ] Git operations work — `git -C $HOME/.grip status` returns clean; a no-op commit+rollback works
22. [ ] git-crypt works — `git-crypt status -e` returns non-empty; decrypted files readable
23. [ ] venv imports work — `$HOME/.grip/venv/bin/python3 -c "import apsw, sentence_transformers"`
24. [ ] Semantic RL loads — `$HOME/.grip/venv/bin/python3 -c "from lib.embeddings.engine import load_engine; load_engine()"`
25. [ ] KONO loads — `$HOME/.grip/venv/bin/python3 $HOME/.grip/lib/kono_preload.py --smoke` exits 0

### Hypothesis / RSI

26. [ ] Hypothesis engine loads — `$HOME/.grip/venv/bin/python3 $HOME/.grip/lib/hypothesis_engine.py status` lists H021+H045+H046+H089+H090
27. [ ] RSI state readable — `ls $HOME/.grip/state/rsi/` returns existing checkpoints
28. [ ] Learning pipeline loads — `$HOME/.grip/venv/bin/python3 $HOME/.grip/lib/insights_learner.py --smoke`

### MCP servers

29. [ ] `grip-channel` MCP reachable — if pm2 service is started, `channel_status` tool returns
30. [ ] `grip-apps` MCP tool returns — invoke `grip_bootstrap` in a test session
31. [ ] `code-index` MCP tool returns — invoke `mcp__code-index__overview`

### Regression sentinels

32. [ ] Fresh session shows no `[GRIP]` error lines in `logs/*.log`
33. [ ] No hook timeouts in first 10 tool calls
34. [ ] No "file not found" errors when any GRIP file is read via symlink path

If any of 1–34 fails: **halt and rollback** (section 8).

---

## 10. Hypothesis Registration Block

```bash
PYTHONPATH=$HOME/.grip python3 $HOME/.grip/lib/hypothesis_engine.py register \
  --pr "grip-home-migration" \
  --claim "Migrating ~/.claude to ~/.grip via per-directory symlinks produces zero session-start failures, zero hook misfires, zero MCP server reconnect loops, and zero skill-not-found errors over 14 days" \
  --metric "incident_count" \
  --prediction "0" \
  --deadline "2026-05-02"
```

**Falsification sources** (each checked daily via `lib/hypothesis_engine.py verify`):

- `state/session/*.log` — count lines matching `ERROR|FATAL|session-start-failed`
- `logs/sentinel.jsonl` — count `hook_timeout` or `hook_error` entries
- `logs/mcp/*.log` — count `reconnect` / `disconnected` spikes above baseline
- `logs/skill-usage.jsonl` — count `skill_not_found` warnings
- User-reported incidents (manual tally in `learning/hypothesis_verifications.jsonl`)

Falsified if any of the four counts > 0 at the 14-day deadline. Confirmed only if all four remain at zero.

---

## 11. Sequenced Rollout Steps

### Phase P: preparation (branch, 1h)
1. Create feature branch `feat/grip-home-migration` off `main`
2. Add `bin/migrate-to-grip-dir` (the script from section 4)
3. Add `bin/rewrite-settings-paths` (the Phase-2 script from section 7)
4. Add `scripts/rewrite-mcp-paths.py` (from section 6)
5. Add unit tests that *simulate* the migration on a temp dir
6. Open PR, review, merge to main (no execution yet)

### Phase D: dry-run on `~/.grip-test` (2h)
1. Snapshot `~/.claude` (tar.gz)
2. Copy `~/.claude` to `~/.grip-test` (full copy, not move)
3. Run migration script with `TARGET_DIR=$HOME/.grip-test` (pseudo-merge: symlinks from a parallel `~/.claude-test`)
4. Launch a fresh CC instance with `CLAUDE_DIR=$HOME/.claude-test`, `GRIP_DIR=$HOME/.grip-test`
5. Run parity checklist (section 9) — all 34 must pass
6. Run rollback procedure against the test location; verify restoration
7. If any failure: fix script, goto step 3

### Phase M: real migration (1h, FRESH CC session)
1. Snapshot `~/.claude` (tar.gz) — keep for 30 days
2. Exit all running CC sessions (`pkill -x claude`)
3. Stop pm2-managed MCP servers if any
4. Run `bin/migrate-to-grip-dir`
5. Launch fresh CC session (not `grip resume`)
6. Immediately run parity checklist (section 9)
7. If all pass: record migration commit SHA, move to Phase O
8. If any fail: rollback (section 8), file an issue, regroup

### Phase O: 48h observation (no other changes)
1. `bin/yolo-push`, `/save`, `/recall`, `/mode`, `/140.85`, `/converge`, `/broly` — use organically, no forced tests
2. Monitor `logs/sentinel.jsonl` and `logs/*.log` daily
3. Track incident_count against H-<new> hypothesis (section 10)
4. No path rewrites, no hook edits — observe baseline behaviour

### Phase C: commit / rollback decision
1. Day 2: if incident_count == 0, commit Phase-2 rewrites (settings.json and mcp.json literal-path updates) in separate PR
2. Day 14: verify hypothesis via `hypothesis_engine.py verify --pr grip-home-migration`
3. If CONFIRMED: remove snapshot tar.gz, finalise
4. If REFUTED: rollback, document failure modes in MEMORY

---

## 12. Open Questions for V>>

1. **`~/.claude` cleanup**: after migration, `~/.claude/` holds only CC-owned files + symlinks. Do you want a final step to `rmdir` any empty subdirs, or leave as-is for CC to populate?
2. **Top-level CLAUDE.md**: should it move to `~/.grip/CLAUDE.md` (with a symlink back to `~/.claude/CLAUDE.md`)? CC reads global-user CLAUDE.md from `~/.claude/CLAUDE.md` — the symlink covers it, but do you want a second copy?
3. **git remote URL**: after the move, `git -C ~/.grip remote -v` still points to `grip-platform/GRIP`. No change needed, confirming.
4. **Shell rc file**: which shell? The script appends to `~/.zshrc` by default. If you use bash/fish, specify so the script can detect `SHELL` and append accordingly.
5. **Should we also pull in the CHANGELOG narrative** (Layer-0 vs Layer-1 split) as a one-liner comment in the migrated README / CLAUDE.md? Or defer to the 140.85 SKILL.md where it already lives?
6. **`.mcp.json` tracked-status**: it's currently in `.gitignore` (line 20). Post-migration it physically lives at `~/.grip/.mcp.json` (symlinked from `~/.claude/.mcp.json`). Confirm we keep it gitignored (Layer-0 operator-specific) — yes?
7. **Windows operators** (Andre, Toopie on Git Bash, Fabio): symlinks work on Git Bash if Developer Mode is on. If not, we need directory junctions via `cmd //c mklink /J`. Do we want the migration script to handle Windows on day one, or ship Mac-only and follow up?
8. **Phase-2 path rewrites timing**: the plan defers literal-path rewrites to a second PR. Do you want that PR queued immediately after the 48h window, or should it wait until the 14-day hypothesis verdict?
9. **Backup retention**: snapshot tar.gz is kept for 30 days by default. Longer? Shorter?
10. **Hypothesis deadline**: 14 days vs 30 days. Longer deadline = more falsification chances = stronger claim. Does 14 days give enough signal?

---

## Appendix A: Directory inventory (pre-migration)

From `ls ~/.claude/ | wc -l` = 121 top-level entries at time of audit.

Confirmed in-plan directories (to be moved): lib, skills, hooks, agents, bin,
commands, rules, docs, scripts, mcp-servers, tests, config, data, state,
learning, cache, logs, facts, private, apps, articles, audit, backlog,
contracts, contexts, debug, drafts, embeddings, plans, prep, web.

Confirmed in-place (CC harness, stays in `~/.claude/`): settings.json,
settings.local.json, .mcp.json, projects/, todos/, shell-snapshots/, statsig/,
ide/, .credentials.json.

Any directory not listed above needs explicit classification in a follow-up
before execution. Current best-effort inventory: 121 entries reviewed in
batch; any drift between plan-draft-time and execution-time should be handled
by a pre-flight `diff` in the migration script.

---

*End of plan. V>>--<<V review required before execution.*
