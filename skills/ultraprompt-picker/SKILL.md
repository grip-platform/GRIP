---
name: ultraprompt-picker
description: On a fresh SessionStart, surface on-disk continuation briefs (ultraprompts) that haven't been executed and let the operator pick which to run via AskUserQuestion.
command: /ultraprompt-picker
status: active
effort: medium
triggers:
  - /ultraprompt-picker
  - "which ultraprompt should I run"
  - "pending continuation briefs"
---

# Ultraprompt Picker

Surface every unexecuted continuation brief at session start and let the operator choose which one this session runs.

## What it is (plain English)

When you start a fresh session you have often left one or more **ultraprompts** on disk — self-contained "here is what the next session should do" briefs written by `/ultraprompt`. Today nothing reminds you they exist; you have to remember they are there and go find them.

The picker fixes that. A SessionStart hook (`hooks/ultraprompt-picker-sessionstart.py`) scans the repo for ultraprompt briefs, sorts them into **already-executed** vs **still-pending** against a small local ledger (`state/ultraprompt-ledger.json`), and injects a short note telling the assistant: "these pending briefs exist — offer them."

The assistant then runs **AskUserQuestion** (per `/asking-users`, `/creating-wizards`, `/hitl-decision-matrix`, and the `/hitl` dispatch contract that chooses between them) so the **operator** picks which brief to run — or picks none. That human choice is the whole point: absence from the ledger is only a *candidate* signal, not proof a brief still matters. The operator is the R0 verification gate ("verify first") — they confirm a brief is still worth running before anything executes. The picker never auto-runs a brief.

Discovery and classification are done by the registry (below), not re-implemented in the hook. A brief is keyed by the **sha256** of its content, so an *edited* brief correctly re-appears as a fresh candidate, while an *unchanged* executed one stays hidden.

## How it composes (DRY — reimplements nothing)

Four verbs, each owning one job; the picker only does selection:

| Verb | Owns |
|------|------|
| `/ultraprompt` | **Writes** the continuation brief. |
| `/ultraprompt-picker` (this) | **Selects + executes** a pending brief, operator-gated. |
| `/finish` | Drives the **chosen** task to a PR-shaped done. |
| `/next` | Runs the end-of-session handoff trio (`/ultraprompt` -> `/save` -> `/ultraclear`). |

Registry API + CLI: `lib/ultraprompt_registry.py`

- Python: `discover()`, `list_pending(limit=...)`, `is_executed(path)`, `mark_executed(path, session_id=...)`, `describe`, `unmark(...)`.
- CLI: `bin/grip-py lib/ultraprompt_registry.py {list | pending | describe | mark | is-executed | unmark}` — under the GRIP venv shim, never bare `python3` (`rules/never-system-python-for-grip.md`).
- Discovers ultraprompt-named `.md` briefs under each git root's `plans/` and `drafts/`; search roots overridable via `GRIP_ULTRAPROMPT_ROOTS`, ledger path via `GRIP_ULTRAPROMPT_LEDGER`.

The ledger is a tiny JSON file — a version marker plus one entry per executed brief, keyed by content hash:

```json
{ "version": 1, "executed": { "<sha256-of-brief>": { "path": "...", "session_id": "...", "at": "..." } } }
```

Check what is pending from a shell without the hook: `bin/grip-py lib/ultraprompt_registry.py pending`.

## "Still relevant?" ordering (Jev integration plan, Phase 5.3)

Pending briefs are shown **newest first by default**. When a fresh relevance
reading exists (`lib/ultraprompt_relevance.py`, cache at
`state/ultraprompt-relevance-cache.json`), the list is instead ordered
**most-likely-still-relevant first**, and each ranked brief carries a line
like `relevance: 83% still relevant (machine reading, precision
unmeasured)`. A brief with no reading — never scored, or refused by the
decision-model privacy floor — is **never dropped**: it is still listed,
after every ranked one.

The reading itself asks TypeSafe's Jev (through HAL `decide`) one yes/no
question per brief — title, tldr and the commit subjects since the brief's
own written date, never its full body — batched into as few calls as the
token budget allows. This hook **never calls Jev and never imports that
path**; it only reads the small cache file. Keeping the cache warm is a side
effect: when it is absent or older than 24h, the hook starts a **detached**
refresh (not waited on) so the next session benefits. Off with
`GRIP_ULTRAPROMPT_RELEVANCE=0` or the global `GRIP_CLASSIFY_NO_DECIDE=1` —
either falls back to today's newest-first order with no extra cost.

## SUPERSEDES

For the "what should the next session run?" niche, this replaces:

- **`/check-last-plan`** — surfaced a *single* cached last-plan (`cache/last-plan.json`) passively.
- **`/preplan`** — cached *one* prepared plan for the next session to inherit.

Both surfaced **one** cached plan with no choice step. The picker surfaces **all** unexecuted briefs and **gates on an operator choice**. Note: `check-last-plan`'s `cache/last-plan.json` advisory is **informational, never a blocking gate** — and neither is this. The injected note is a prompt to offer choices, not a wall in front of your tools. (Each superseded skill keeps its own residual job — see the one-line note at the top of each.)

## Activation

This PR wires the SessionStart hook into `settings.json` — one entry appended inside the existing `"SessionStart": [ ... ]` array, mirroring the other entries, with every existing hook preserved. Merging the PR is the operator gate: once merged, the picker fires automatically on fresh sessions. For any install whose `settings.json` differs (a HAPPIverse install, a fork), the entry to add is:

```json
{
  "matcher": "",
  "hooks": [
    {
      "type": "command",
      "command": "python3 $HOME/.claude/hooks/ultraprompt-picker-sessionstart.py",
      "timeout": 5000,
      "statusMessage": "Ultraprompt picker (#576)"
    }
  ]
}
```

Gating lives in the hook itself, not in the matcher:

- Fires only on SessionStart sources **startup** and **clear** (skips resume and compact).
- Disabled entirely when `GRIP_ULTRAPROMPT_PICKER=0`.
- **Fails open** — any error (no registry, no briefs, unreadable ledger) injects nothing and never blocks the session.

The picker is also invocable manually via `/ultraprompt-picker` at any time.

## Behaviour contract

When the assistant sees the injected "pending ultraprompts" context at session start:

1. **A real first-message instruction wins.** If the operator's opening message is itself a task, do that — the picker is a fallback, never a hijack of a clear request.
2. Otherwise **present the pending briefs via AskUserQuestion** — title + one-line tldr for each, plus a "none / not now" option.
3. **On pick** -> `Read` the chosen brief, execute it, then `mark_executed(path)` so it does not re-surface next time.
4. **On skip / defer** -> proceed normally; leave the ledger untouched so the briefs stay pending for a later session.

### Example (session start)

```
[ultraprompt-picker] 2 pending briefs found:
  1. drafts/opensource-agpl-ultraplan.md  — "Resume Gate-A sanitize convergence (W2-W6)"
  2. plans/der-wedge-harden.md            — "Harden DER receipt endpoint per council gaps"

-> assistant runs AskUserQuestion: "Run one of these now, or start fresh?"
-> operator picks (1) -> Read + execute -> mark_executed -> brief no longer surfaces.
```

## Falsification

Wrong / mis-tuned if, over roughly five fresh sessions it governs:

- it never once surfaces a brief the operator actually wanted (no signal -> demote to manual-only); OR
- it re-surfaces an **unchanged** already-executed brief (the ledger or `mark_executed` is broken — fix the hash/mark, never drop the gate); OR
- the choice step adds friction the operator routes around every time (the wizard is noise -> simplify or make it opt-in).

Track: correct-pick rate (target: rises over time as briefs are chosen and marked); false re-surfacing of unchanged executed briefs (target: zero).

---

Origin: task #576 (ultraprompt-picker). Authored with Claude Opus 4.8.
