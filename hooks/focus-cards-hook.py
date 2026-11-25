#!/usr/bin/env python3
"""
Focus Cards — Session Lifecycle Hook.

Wires Focus Cards into Claude Code's hook system for automatic
cross-session tracking. Three modes:

  start  — SessionStart: create/resume a card for the current session
  post   — PostToolUse: infer checklist items from tool output (commits)
  end    — SessionEnd: pause the active card

Session state persisted via /tmp/grip-focus-active.json so all three
hook phases share the active card ID without needing GRIP_SESSION_ID.

Registration: settings.json (SessionStart, PostToolUse, SessionEnd)
"""

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
# Add both CLAUDE_DIR (for `from lib.X` imports) and CLAUDE_DIR/lib (for direct imports)
sys.path.insert(0, str(CLAUDE_DIR))
sys.path.insert(0, str(CLAUDE_DIR / "lib"))

# Session state file — ephemeral, one per machine
SESSION_STATE = Path("/tmp/grip-focus-active.json")

# Commit hash pattern in Bash tool output
COMMIT_PATTERN = re.compile(r"\[[\w/.-]+\s+([0-9a-f]{7,12})\]\s+(.+)")

# Long-lived branches are not a "focus" — their cards keep the honest
# Session: placeholder rather than a frozen single-commit title (#2885).
DEFAULT_BRANCHES = {"main", "master", "develop", "HEAD", ""}


def _get_branch() -> str:
    """Detect current git branch."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
            cwd=str(CLAUDE_DIR),
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _load_session_state() -> dict:
    """Load active session state."""
    try:
        if SESSION_STATE.exists():
            return json.loads(SESSION_STATE.read_text())
    except Exception:
        pass
    return {}


def _save_session_state(state: dict) -> None:
    """Persist active session state atomically."""
    try:
        tmp = SESSION_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        # replace, not rename: on Windows, rename refuses a target that exists (GRIP#8778)
        tmp.replace(SESSION_STATE)
    except Exception:
        pass


def _get_manager():
    """Lazy-load FocusCardsManager."""
    from focus_cards import FocusCardsManager
    return FocusCardsManager()


def _reconcile_stale_cards(mgr):
    """Pause cards for dead sessions. Same PID-liveness pattern as worktree_health_monitor."""
    from focus_cards import CardStatus
    try:
        from session_lister import scan_all_sessions
        live_sids = {s["session_id"] for s in scan_all_sessions()}
    except Exception:
        return  # Graceful no-op if lister unavailable
    for card in mgr.list_cards(status=CardStatus.ACTIVE):
        if card.session_id not in live_sids:
            mgr.pause_card(card.id)


# --------------------------------------------------------------------------
# SessionStart
# --------------------------------------------------------------------------

def _emit_session_start(context: str = "") -> None:
    """Emit a SessionStart hook result WITHOUT the noisy startup echo.

    Claude Code prints a hook's ``systemMessage`` verbatim as
    ``⎿  SessionStart:startup says: <message>`` in the startup box. The
    focus-card resume/create state is useful to the model but that terminal
    echo is startup clutter (operator directive, 2026-05-15: "this output
    needs to be disabled"). So the focus context is routed through
    ``hookSpecificOutput.additionalContext`` with ``suppressOutput`` instead
    — the model still receives the context, the operator gets a clean
    startup. Mirrors the established project-cwd-hint.sh SessionStart pattern.
    """
    payload = {"continue": True, "suppressOutput": True}
    if context:
        payload["hookSpecificOutput"] = {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    print(json.dumps(payload))


# >= this many items with 0 ever closed is the #2885 silent-failure
# shape — pre-fix this read as a serene "(0/47 done)" for two months.
STALL_WARN_THRESHOLD = 10


def _progress_suffix(done: int, total: int) -> str:
    """Progress tail, self-announcing the #2885 regression shape.

    A card with many items and none ever closed is no longer rendered
    as valid state — it emits a health warning pointing at the repair
    tool. This is the #2885 lesson made mechanical (the original
    append-only bug hid precisely because 0/N looked serene).
    """
    if total >= STALL_WARN_THRESHOLD and done == 0:
        return (
            f"({done}/{total} done) — WARNING: 0 of {total} ever closed; "
            f"possible Focus Cards tracking regression "
            f"(run: python3 lib/focus_cards.py repair)"
        )
    return f"({done}/{total} done)"


def _try_resume_existing(mgr, session_id: str) -> bool:
    """Resume the card bound to this exact session, if any."""
    from focus_cards import CardStatus
    existing = mgr.get_card_by_session(session_id)
    if not existing:
        return False
    if existing.status == CardStatus.PAUSED:
        existing.status = CardStatus.ACTIVE
        existing.touch()
        mgr.save()
    _save_session_state({"card_id": existing.id, "session_id": session_id})
    done, total = existing.progress()
    _emit_session_start(
        f"Focus Card resumed: {existing.title} {_progress_suffix(done, total)}"
    )
    return True


def _try_resume_paused_branch(mgr, session_id: str, branch: str) -> bool:
    """Cross-session resume: adopt a paused card on the current branch."""
    from focus_cards import CardStatus
    for card in mgr.list_cards(status=CardStatus.PAUSED):
        if card.branch == branch and branch:
            card.status = CardStatus.ACTIVE
            card.session_id = session_id
            card.touch()
            mgr.save()
            _save_session_state({"card_id": card.id, "session_id": session_id})
            done, total = card.progress()
            _emit_session_start(
                f"Focus Card resumed from branch: {card.title} "
                f"{_progress_suffix(done, total)}"
            )
            return True
    return False


def _try_resume_pinned_branch(mgr, session_id: str, branch: str) -> bool:
    """Adopt a pinned card on the current branch (status unchanged)."""
    from focus_cards import CardStatus
    for card in mgr.list_cards(status=CardStatus.PINNED):
        if card.branch == branch and branch:
            _save_session_state({"card_id": card.id, "session_id": session_id})
            done, total = card.progress()
            _emit_session_start(
                f"Focus Card pinned: {card.title} {_progress_suffix(done, total)}"
            )
            return True
    return False


def _create_card(mgr, session_id: str, branch: str) -> None:
    """No resumable card found — create a fresh one for this session."""
    from focus_inference import FocusInference
    inference = FocusInference()
    title = inference.infer_title({
        "branch": branch,
        "first_message": "",
        "project_name": Path(os.getcwd()).name,
    })
    if title.startswith("Session:"):
        title = f"{title} [{session_id[:8]}]"
    card = mgr.create_card(
        session_id=session_id,
        title=title,
        branch=branch,
        project_path=str(CLAUDE_DIR),
    )
    _save_session_state({"card_id": card.id, "session_id": session_id})
    _emit_session_start(f"Focus Card created: {card.title} (id: {card.id})")


def on_start():
    """Create or resume a Focus Card for this session.

    Coordinator: try the three resume strategies in priority order, else
    create. Behaviour identical to the prior monolithic implementation —
    same order, same side-effects, same emissions, same crash-safe except.
    """
    try:
        mgr = _get_manager()
        _reconcile_stale_cards(mgr)
        branch = _get_branch()
        session_id = os.environ.get("CLAUDE_SESSION_ID", str(uuid.uuid4())[:12])
        if (_try_resume_existing(mgr, session_id)
                or _try_resume_paused_branch(mgr, session_id, branch)
                or _try_resume_pinned_branch(mgr, session_id, branch)):
            return
        _create_card(mgr, session_id, branch)
    except Exception:
        # Hooks must never crash the session
        print(json.dumps({}))


# --------------------------------------------------------------------------
# PostToolUse
# --------------------------------------------------------------------------

def _iter_commit_items(inference, matches):
    """Flatten the (commit -> inferred items) 2D walk into one generator.

    Keeps _add_items_from_commits at a single loop (no function nests
    for-loops). ``yield from`` is not a loop statement, so the per-commit
    truncation + inference reads linearly here.
    """
    for _hash, commit_msg in matches:
        # Truncate at first newline, cap at 80 chars (prevents git stat pollution)
        commit_msg = commit_msg.split("\\n")[0].split("\n")[0][:80]
        yield from inference.from_commit_message(commit_msg)


def _refresh_title(card) -> None:
    """Keep the title branch-derived; never freeze it to one commit.

    Pre-#2885 the hook froze a card's title to its first commit subject
    forever (title drift). A card's title should reflect its focus — the
    branch: a Session:-placeholder card on a descriptive branch adopts
    the branch-derived title; default-branch cards keep the honest
    placeholder rather than a misleading single-commit title.
    """
    if not card.title.startswith("Session:") or card.branch in DEFAULT_BRANCHES:
        return
    from focus_inference import FocusInference
    derived = FocusInference().branch_to_title(card.branch)
    if derived:
        card.title = derived


def _add_items_from_commits(card, matches) -> list:
    """Reconcile a card's checklist against detected commits (#2885).

    A commit is *completed work*, not a pending intent. Each inferred
    commit item either CLOSES a matching open (planned/manual) item or
    is recorded as a new DONE item — never an ever-pending append, which
    is what grew the permanently-0/N runaway. Dedup is hash-based, first
    occurrence wins. Returns the texts of changed items.
    """
    from focus_inference import FocusInference
    from focus_cards import ChecklistStatus
    inference = FocusInference()
    existing_texts = {item.text.lower().strip() for item in card.checklist}
    pending = [c for c in card.checklist if c.status == ChecklistStatus.PENDING]
    changed = []
    for item in _iter_commit_items(inference, matches):
        match = inference.completion_match(item.text, pending)
        if match is not None:
            match.complete()
            pending.remove(match)
            card.touch()
            changed.append(item.text)
            continue
        key = item.text.lower().strip()
        if key not in existing_texts:
            card.add_checklist_item(item.text, source=item.source,
                                    status=ChecklistStatus.DONE)
            existing_texts.add(key)
            changed.append(item.text)
    return changed


def on_post():
    """Infer checklist items from tool output (commit detection).

    Coordinator: guard clauses bail early with an empty result; the
    commit -> items work is delegated. Behaviour identical to the prior
    monolithic implementation.
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print(json.dumps({}))
            return
        data = json.loads(raw)
        if data.get("tool_name", "") != "Bash":
            print(json.dumps({}))
            return
        matches = COMMIT_PATTERN.findall(str(data.get("tool_response", "")))
        if not matches:
            print(json.dumps({}))
            return
        card_id = _load_session_state().get("card_id")
        if not card_id:
            print(json.dumps({}))
            return
        mgr = _get_manager()
        card = mgr.get_card(card_id)
        if not card:
            print(json.dumps({}))
            return

        changed = _add_items_from_commits(card, matches)
        if changed:
            _refresh_title(card)
            mgr.save()
            print(json.dumps({
                "systemMessage": f"[Focus Card] {len(changed)} update(s): "
                                 f"{', '.join(changed[:3])}"
            }))
        else:
            print(json.dumps({}))

    except Exception:
        print(json.dumps({}))


# --------------------------------------------------------------------------
# SessionEnd
# --------------------------------------------------------------------------

def on_end():
    """Pause the active card at session end."""
    try:
        state = _load_session_state()
        card_id = state.get("card_id")
        if not card_id:
            print(json.dumps({}))
            return

        mgr = _get_manager()
        card = mgr.get_card(card_id)
        if not card:
            print(json.dumps({}))
            return

        from focus_cards import CardStatus
        done, total = card.progress()

        # Auto-complete if all items done
        if card.is_complete() and card.status in (CardStatus.ACTIVE, CardStatus.PAUSED):
            card.complete()
        elif card.status == CardStatus.ACTIVE:
            mgr.pause_card(card.id)
        # Pinned cards stay pinned

        mgr.save()

        # Store in KONO for cross-session recall
        mgr.kono.store_card(card)

        # Clean up session state
        try:
            SESSION_STATE.unlink(missing_ok=True)
        except Exception:
            pass

        print(json.dumps({}))

    except Exception:
        print(json.dumps({}))


# --------------------------------------------------------------------------
# CLI dispatch
# --------------------------------------------------------------------------

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"

    if mode == "start":
        on_start()
    elif mode == "post":
        on_post()
    elif mode == "end":
        on_end()
    else:
        print(json.dumps({}))
