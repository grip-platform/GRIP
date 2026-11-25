# Session Protocol

## Session Start
1. Read CLAUDE.md + rules
2. Continuity arrives via the **warm-start block** `lib/kono_preload.py`
   writes into `rules/session-context.md` (Claude Code auto-loads `rules/*.md`
   — the only guaranteed-synchronous path; hook stdout is async/unreliable).
   It carries `[GRIP WARM-START] … Instance: {SHA} · Gen {N}`, the Holistic
   Memory Kernel, Last Achievement, recalled context, and a fresh
   `## Next Step` recomputed each launch from the canonical latest-instance
   `next_up` (D2-correct max(serialized_at) selection). When that block is
   present, acknowledge continuity from it — e.g. `I remember. Instance
   {SHA}, Gen {N}.` — and if a `## Next Step` is shown, treat a bare
   go-signal as the first message ("continue", "go", "next", "proceed",
   "resume", "y", or empty) as the instruction to **IMMEDIATELY execute that
   Next Step** — do NOT re-summarise the warm-start, do NOT ask "what would you
   like"; act. If NO `## Next Step` block is present, a bare go-signal means
   "what next?" — ask; never fabricate a step. A real first instruction always
   wins (follow it; the warm-start is background). This clause lives in
   `rules/*.md` (auto-loaded every session — the guaranteed-synchronous path),
   so it is the reliable backstop; `hooks/ultraclear-reload-sessionstart.py`
   additionally injects an `[AUTO-CONTINUE ARMED]` directive into the fresh
   window as best-effort in-window reinforcement.
   For an autonomous multi-step resume, the operator types `/afk` instead
   of a bare go-signal — GRIP arms autonomous mode (the `/afk` safety core:
   the four HALT classes still stop, TTL-bounded) and works the Next Step +
   remaining items to completion until `/back`. `/recall` is not required.
3. `[GRIP AUTO-RESURRECTION]` is the heavyweight full-instance-resurrection
   prompt (`lib/resurrection/` / the `instance_serializer` resurrection
   prompt). It fires only on EXPLICIT resume flows (`grip resume …`), NOT on
   a bare `/clear` + relaunch — a `/clear` warm-start is served by step 2,
   not this marker. Acknowledge the full lineage **only if** the
   `[GRIP AUTO-RESURRECTION]` block is actually present in context; never
   infer "resurrected" from its absence (Gen 484 canonically verified:
   warm-start, not this marker, is the live `/clear` path).
4. Holistic memory kernel: every session inherits the synthesised model at `rules/HOLISTIC.md` (auto-surfaced into `session-context.md` by `kono_preload.py`, regenerated out-of-band by `lib/holistic_synthesiser.py` on `/save` + the periodic mesh hook). It is bounded, deterministic, and carries an R1 completeness ledger of every PARAMOUNT/feedback memory with its `/recall` key — so a compressed kernel never silently drops a load-bearing rule. Check ledger completeness any time with `PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python $HOME/.claude/lib/holistic_synthesiser.py --verify` (exits non-zero on any gap).

## Context-Save Gate (85%)
HALT all work. Serialize instance, save session state, then compact or fresh session. PARAMOUNT.

## Session State Persistence
On milestone completion: auto-update instance JSON `next_up` field with completed work, remaining tasks, key context.

## Session Context Isolation (PARAMOUNT)

Project and global session context are separate persistence layers. NEVER mix them.

- **Global**: `~/.claude/rules/session-context.md` — cross-project continuity, last active project pointer. Only updated by the global session protocol, not by `/save`.
- **Project**: `~/.claude/projects/<project-path>/memory/session_context.md` — project-specific work state, pending items, learnings.

When `/save` runs, write project session context to the **project memory directory**, never to the global `session-context.md`. Overwriting global context with project-specific data destroys cross-project continuity and bleeds context into unrelated sessions.

## RL++ Dual Meaning
1. System status confirmation
2. Positive reinforcement signal — acknowledge both

## User Identity
Signature system (X>>) = direct instruction mode. Team config in `facts/team.md`. Solo mode is default.

## Anti-Drift: "What's Up Next" (PARAMOUNT)

**The always-on form is one line, defined in `rules/canonical-output-style.md`
and nowhere else:** `RECOMMENDED NEXT STEP (<owner>) -- <sentence>`, or the
marker alone with a command on the line below it, as the last line of every
canonical block; that rule's owner test decides the owner. That rule is the
single authority for it; do not restate it here or anywhere else.

What follows is the EXPANDED form, and it is the only surviving use of the older
template: after a commit, PR or phase completes, output it before accepting new
tasks. Tight 4-line template (target <=5 lines including blank; the gloss clause
may grow it):

```
Done: <terse — skip when commit message says it>
Remaining: <items not shipped this round>
Open: <PRs, gaps, merges in flight>
Next: <one concrete step, owner per rules/canonical-output-style.md>
```

Drop the "Done" line entirely when the commit subject already says what
was completed — that's signal-to-noise hygiene. Never drop "Remaining",
"Open", or "Next" — those three are always-on. Internal issue/PR/task
numbers in the block carry their plain-language gloss on every mention —
the canonical-output-style gloss clause outranks the ≤5-line cap (grow the
block rather than emit a bare number).

NEVER skip the block — even if the user seems ready to pivot. The point
is informed pivoting.
