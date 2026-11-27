"""Withdraw this session's own settled pending question, or list its open ones (GRIP#10171).

    grip-pending-withdraw --list
    grip-pending-withdraw REF --reason "why it is settled"

A session runs this when a question it filed with a `(yours)` closer was settled
another way: answered inside the session, or done once the operator allowed it.
REF is the four-character ref an alert shows, or the full question id. The
session comes from --session, else CLAUDE_CODE_SESSION_ID or CLAUDE_SESSION_ID.
A withdrawal is never an answer; see lib/pending_question.withdraw.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from lib.claude_dir import get_claude_dir  # noqa: E402
from lib.pending_question import Pending, PendingStore, bare_session, withdraw, withdraw_refusal  # noqa: E402


def _session(arg: str) -> str:
    return arg or os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""


def _mine(store: PendingStore, session: str) -> list[Pending]:
    me = bare_session(session)
    return [p for p in store.open_questions() if bare_session(p.session_id) == me]


def _list(mine: list[Pending]) -> int:
    for p in sorted(mine, key=lambda q: q.asked_at):
        tag = "  (expired)" if p.is_expired() else ""  # reported apart from live ones (GRIP#10186)
        print(f"{p.ref}  {p.question_id}  {' '.join(p.step_text.split())[:110]}{tag}")
    if not mine:
        print("no open questions for this session")
    return 0


def _withdraw_one(store: PendingStore, mine: list[Pending], ref: str, reason: str, session: str) -> int:
    # Each refusal names its own cause (GRIP#10186). The reason and the match are
    # checked here; after a refusal from withdraw() itself, withdraw_refusal names the
    # cause, by the same rule withdraw() applied under its lock.
    if not reason.strip():
        print(f"{ref} was not withdrawn: give a --reason saying what settled it", file=sys.stderr)
        return 1
    matches = [p for p in mine if ref in (p.question_id, p.ref)]
    if not matches:
        print(f"{ref} is not an open question of this session", file=sys.stderr)
        return 1
    if len(matches) > 1:
        ids = ", ".join(p.question_id for p in matches)
        print(f"{ref} matches more than one open question ({ids}); give the full id", file=sys.stderr)
        return 1
    done = withdraw(store, matches[0].question_id, reason, session)
    if done is None:
        why = withdraw_refusal(store.question(matches[0].question_id), reason, session)
        print(f"{ref} was not withdrawn: {why or 'it changed while it was being withdrawn'}",
              file=sys.stderr)
        return 1
    print(f"withdrawn {done.ref}: {done.withdrawn_reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grip-pending-withdraw",
                                     description="Withdraw this session's own settled pending question.")
    parser.add_argument("ref", nargs="?", help="the four-character ref, or the full question id")
    parser.add_argument("--reason", default="", help="why the question is settled (required to withdraw)")
    parser.add_argument("--list", action="store_true", help="list this session's open questions")
    parser.add_argument("--session", default="", help="the asking session's id (default: from the environment)")
    parser.add_argument("--store", type=Path, default=None,
                        help="the store file (default: <CLAUDE_DIR>/state/pending-questions.json)")
    args = parser.parse_args(argv)
    if args.list and args.ref:
        parser.error("give either --list or a REF, not both")  # one silent half would hide the other
    session = _session(args.session)
    if not session:
        print("no session id: pass --session, or run this inside a Claude Code session", file=sys.stderr)
        return 2
    store = PendingStore(args.store or get_claude_dir() / "state" / "pending-questions.json")
    mine = _mine(store, session)
    if args.list:
        return _list(mine)
    if not args.ref:
        parser.error("give a REF to withdraw, or --list")
    return _withdraw_one(store, mine, args.ref, args.reason, session)


if __name__ == "__main__":
    sys.exit(main())
