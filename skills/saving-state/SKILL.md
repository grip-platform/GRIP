---
name: saving-state
description: Unified one-shot save of all GRIP memory subsystems. Persists insights, genome DNA, instance, KONO, and session context in one command. Mid-session safe.
invocable: true
invocable_name: save
tokens: 500
---

# /save — Unified State Persistence

One command to save all GRIP memory infrastructure mid-session.

## Live Session Context (DCI)

**Git state:** !`cd ~/.claude && git log --oneline -3 2>/dev/null`
**Working tree:** !`cd ~/.claude && git status --short 2>/dev/null | head -5`
**Open PRs:** !`cd ~/.claude && gh pr list --limit 3 --json number,title --jq '.[] | "#\(.number): \(.title)"' 2>/dev/null`
**RSI lock:** !`test -f ~/.claude/state/rsi/rsi-active.lock && echo "ACTIVE" || echo "inactive"`

## Usage

```bash
/save                              # Auto-detect achievement from git log
/save "completed the auth refactor" # Explicit achievement message
```

## What It Saves

| # | Subsystem | What | File |
|---|-----------|------|------|
| 1a | Insights | Flush accumulated insight stack | `learning/converge-metrics.jsonl` |
| 1b | Insights | Detect recurring patterns (YSH 2x) | `auto_skill_generator.py` |
| 2 | DNA | Genome sync, time-decay, KONO attestation | `cache/genome.json` |
| 3 | Instance | Session serialization with achievement | `projects/*/grip/index.json` |
| 4 | Memory | Inject the compact brief into the memory index | `projects/*/memory/MEMORY.md` |

**`/save` does NOT write `rules/session-context.md`** (corrected 2026-09-11; this row
claimed it did). `grip_save.py:612` runs `unified_memory_brief.py --inject-memory
--skip-if-no-instance`, and only the separate `--write-rules` flag reaches
`write_rules_file`. That file is written at session boundaries instead —
`hooks/startup/grip.sh:545`, `hooks/session-end.sh:578`, and
`lib/grip_recall.py:807` behind a 5-minute freshness guard. Verified by measurement:
the file's mtime was unchanged across a save that reported all 12 steps passed.
| 5 | KONO | Episodic session summary to embeddings DB | `embeddings.db` |

## Execution

Run the orchestrator directly:

```bash
grip-py ~/.claude/lib/grip_save.py
grip-py ~/.claude/lib/grip_save.py "achievement message"
grip-py ~/.claude/lib/grip_save.py --quiet  # Minimal output
```

Each subsystem is error-isolated: one failure does not block others.

**Use `grip-py`, not bare `python3`.** The dual-key IDR seal (ML-DSA-65) needs
`cryptography>=48`, which is installed only in `~/.claude/venv`. The floor is 48
because 48.0.0 is the release that brought ML-DSA to the published wheels that
`pip install` actually delivers; 47.0.0 added the module but only for AWS-LC and
BoringSSL builds, so a `>=46` or `>=47` floor is satisfiable by a version that
still cannot sign. A
bare `python3` (the Homebrew interpreter, here) lacks it, so `/save` silently
downgrades to the shared-key DEMO seal — records save, but are forgeable rather
than independently verifiable. `bin/grip-py` resolves the venv through
`resolve_venv_python` and, unlike that helper's own lenient fallback, REFUSES
rather than accepting a system interpreter: silently accepting it is the bug.

Belt and braces: `grip_save.py` is also wired to `lib/pq_reexec.py`, so even
invoked under a bare `python3` it re-execs itself into a capable interpreter.
`grip-py` is the habit; the re-exec is the net under it.

**Scope.** `grip_save.py` is not the only module that seals records — measured
2026-09-14, six `lib/` modules and seven `bin/` tools reach `append_idr`
(`tests/test_pq_reexec.py` derives the list from the import graph, so it cannot
go stale the way this sentence did: it previously read "Only `grip_save.py`
seals IDR records", which was false). Modules that do no crypto —
`next_session_prompt.py`, the clipboard helper in the Final step below — are
genuinely fine under bare `python3`.

### Execution Channel — mid-loop vs interactive (dispatch-economics)

`grip_save.py` is a **small-output script** (a short summary, not 80+
lines like `/evo`). Per `rules/dispatch-economics.md` — *the most
token-efficient agent is no agent* — pick the channel by intent:

| Intent | Channel | Cost | Correct? |
|--------|---------|------|----------|
| **Mid-loop / "in background"** (RSI breathing pause, checkpoint while work continues) | `Bash` with `run_in_background=true` of `~/.claude/venv/bin/python ~/.claude/lib/grip_save.py "<msg>"` | ~0 marginal; harness notifies on exit | **Yes — canonical** |
| Explicit interactive `/save` (operator wants the digest now) | Foreground `Bash` of `~/.claude/venv/bin/python ~/.claude/lib/grip_save.py` | ~0 tokens, stalls only that turn | Yes — the default |
| Any `/save` | An `Agent` (even `run_in_background`) | **~84k ambient floor per spawn** | **No** — `Agent` is for output-polluting heavy pipelines (`/evo`, `/learn`) per `feedback_rsi_background_pipelines`, never a small-output script |

So a mid-loop or "save in the background" `/save` MUST be a backgrounded
`Bash` of `grip_save.py` — **never** an `Agent`, **never** inline-blocking
the orchestrator. Foreground stays the default for an explicit interactive
`/save`. (YSH/YAGNI: `/save-exit` is already folded into this one
canonical `/save` — one command, one skill; do not re-split.)

### Output

After saving, a structured checkmark report shows subsystem statuses followed by
a **Memory Digest** — a creative summary of WHAT was saved (not just whether):

```
Memory Systems Saved:
  [x] Insights flush
  [x] DNA sync
  ...
  [x] KONO substrate (active)

── Memory Digest ─────────────────────────────────────────
  Achievements
  · Gen 377: Mid-session save
  · Gen 371: RSI memory systems complete
  · Gen 369: RSI test checkpoint

  Active State
  · "The Feedback Loop" Article (Gen 367)
  · 130 items tracked · 19 pending · 1 in progress

  Genome  gen 25 · 164 genes · 0.661 ▲
  ▸ design-principles  ▸ mode  ▸ converge
─────────────────────────────────────────────────────────

/save complete: 8 passed, 0 failed (4.7s)
Achievement: Mid-session save (last: feat: unified install script)
```

The digest pulls from three data sources:

- **Achievements**: Recent session achievements from `session-context.md`
- **Active State**: Current work focus + TODO pulse from `MEMORY.md`
- **Genome**: Evolutionary DNA generation, gene count, fitness trend (▲/▼/▬)

## When to Use

- Before context compaction (`/compact`)
- After completing a significant milestone
- Before switching to a different project
- As part of RSI breathing pauses
- Anytime you want a checkpoint

## What It Does NOT Do (session-end only)

- Clear wave state (`.wave-position`)
- Deregister from session registry
- Stop caffeinate
- Clean up pair-mode sockets

These are handled by `session-end.sh` when the session actually ends.

## Final step — minimal next-session prompt (clipboard)

On an **interactive session-end** `/save`, the final output is the **MINIMAL
prompt to paste into the next session**, auto-copied to the clipboard. It is
produced by the shared lib (DRY — never inline the logic; the same lib backs
`/next` and `/handoff-prompt`):

```bash
# Explicit next action (interactive session-end — copies to clipboard):
# (no venv needed — next_session_prompt.py does no IDR crypto)
python3 ~/.claude/lib/next_session_prompt.py --next "<one-line next action>"
# Or bare — auto-gathers the next action from rules/session-context.md:
python3 ~/.claude/lib/next_session_prompt.py
```

**G1 — background /save never clips.** A mid-loop / RSI-pause / `run_in_background`
`/save` checkpoint runs many times per session; it MUST NOT touch the clipboard
(that would clobber the operator's copy dozens of times). Those runs skip this
step, or pass `--no-clip`. Only the interactive session-end handoff copies —
keep the clipboard the operator's, not GRIP's.

It prints the prompt and copies it cross-platform (pbcopy / xclip / wl-copy /
clip.exe; graceful no-op + stderr note if none). Then tell the operator:
**"copied to clipboard — paste into the new session."** This composes with
warm-start (which reloads CLAUDE.md + the holistic memory kernel +
session-context), so the prompt stays a short pointer, not a full brief — it
never duplicates the `/ultraprompt` continuation brief. See
`skills/handoff-prompt/SKILL.md`.
