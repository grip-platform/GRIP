"""GG — the session Victory Lap card.

A deterministic, gamified end-of-session scorecard built from real git data.
Zero LLM (rate-limit-immune), pure stdlib, Goodhart-testable.

Delight without lying (Rule 17 / output-tier precision invariant): every number
on the card is a real, verifiable count from `git log --numstat` for the window.
The "play of the session" is the commit with the largest net line change. The
streak is the count of consecutive UTC days with >=1 commit, ending today.

Public surface (pure, easily testable — the I/O is isolated in ``main``):
  parse_numstat_log(raw) -> list[Commit]
  compute_stats(commits)  -> VictoryStats
  compute_streak(dates, today) -> int
  render_card(stats)      -> str
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# Subjects that mark a merge/PR-landing commit (squash-merge subjects keep the
# "(#NNNN)" suffix; explicit merges start with "Merge ").
_MERGE_MARKERS = ("merge ", "merge pull request")
_CARD_WIDTH = 39  # inner width of the dotted-leader rows
_ONE_DAY = timedelta(days=1)  # stable 1-day delta for streak arithmetic


@dataclass(frozen=True)
class Commit:
    """One commit's numstat-derived facts."""

    sha: str
    subject: str
    day: date          # author date (UTC), date only
    insertions: int
    deletions: int
    touches_tests: bool = False

    @property
    def net(self) -> int:
        return self.insertions - self.deletions

    @property
    def is_merge(self) -> bool:
        low = self.subject.lower()
        return low.startswith("merge ") or "(#" in self.subject


@dataclass(frozen=True)
class VictoryStats:
    commits: int
    merges: int
    test_commits: int
    insertions: int
    deletions: int
    streak_days: int
    xp: int
    play_of_session: str
    window_label: str


def parse_numstat_log(raw: str) -> list[Commit]:
    """Parse ``git log --numstat --format=__C__%H%x09%cI%x09%s`` output.

    Each commit starts with a ``__C__`` header line carrying sha, committer ISO
    date and subject (tab-separated), followed by zero+ numstat rows
    ``<ins>\\t<del>\\t<path>``. Binary files show ``-`` for ins/del → counted 0.
    Unparseable/garbage lines are skipped (fail-open: a malformed log yields the
    commits it can read, never a crash).
    """
    commits: list[Commit] = []
    cur_header: tuple[str, date, str] | None = None
    cur_ins = cur_del = 0
    cur_test = False

    def _flush() -> None:
        nonlocal cur_header, cur_ins, cur_del, cur_test
        if cur_header is not None:
            sha, day, subject = cur_header
            commits.append(Commit(sha, subject, day, cur_ins, cur_del, cur_test))
        cur_header, cur_ins, cur_del, cur_test = None, 0, 0, False

    for line in raw.splitlines():
        if line.startswith("__C__"):
            _flush()
            parts = line[len("__C__"):].split("\t", 2)
            if len(parts) != 3:
                continue
            sha, iso, subject = parts
            try:
                day = datetime.fromisoformat(iso).astimezone(timezone.utc).date()
            except ValueError:
                continue
            cur_header = (sha, day, subject)
            continue
        if cur_header is None or "\t" not in line:
            continue
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        ins = 0 if cols[0] == "-" else _safe_int(cols[0])
        dele = 0 if cols[1] == "-" else _safe_int(cols[1])
        cur_ins += ins
        cur_del += dele
        if _is_test_path(cols[2]):
            cur_test = True
    _flush()
    return commits


def _safe_int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


def compute_streak(commit_days: set[date], today: date) -> int:
    """Consecutive days with >=1 commit, ending today.

    If there is no commit today, the streak is 0 (the run has been broken). One
    day per step backwards while the day is present.
    """
    if today not in commit_days:
        return 0
    streak = 0
    cursor = today
    one_day = _ONE_DAY
    while cursor in commit_days:
        streak += 1
        cursor = cursor - one_day
    return streak


def compute_stats(commits: list[Commit], today: date, window_label: str = "") -> VictoryStats:
    """Aggregate commits into the scorecard stats. Pure.

    XP is a deterministic, transparent formula (gamified flavour, not a metric
    anyone optimises): commits*10 + merges*50 + test_commits*25 + added//10.
    """
    merges = sum(1 for c in commits if c.is_merge)
    test_commits = sum(1 for c in commits if c.touches_tests)
    insertions = sum(c.insertions for c in commits)
    deletions = sum(c.deletions for c in commits)
    streak = compute_streak({c.day for c in commits}, today)
    xp = len(commits) * 10 + merges * 50 + test_commits * 25 + insertions // 10
    play = max(commits, key=lambda c: c.net).subject if commits else "(a quiet session)"
    return VictoryStats(
        commits=len(commits),
        merges=merges,
        test_commits=test_commits,
        insertions=insertions,
        deletions=deletions,
        streak_days=streak,
        xp=xp,
        play_of_session=play,
        window_label=window_label or "this session",
    )


def _is_test_path(path: str) -> bool:
    """A changed path on a test surface: a ``tests`` directory component, or a
    basename that starts ``test_`` or ends ``_test.py``.

    Component-based, NOT a loose substring — so ``latest_results.py``,
    ``fastest.py`` or ``contest.py`` are correctly NOT counted as tests
    (AGORA #3378 C2).
    """
    parts = path.replace("\\", "/").lower().split("/")
    if "tests" in parts:
        return True
    base = parts[-1] if parts else ""
    return base.startswith("test_") or base.endswith("_test.py")


def _row(label: str, value: str) -> str:
    """Dotted-leader row: 'Label ...... value' padded to the card width."""
    lead = f"{label} "
    pad = _CARD_WIDTH - len(lead) - len(value) - 1
    dots = "." * max(pad, 1)
    return f"     {lead}{dots} {value}"


def render_card(stats: VictoryStats) -> str:
    """Render the Victory Lap card. Deterministic; ASCII-safe (no emoji-width
    drift in the aligned columns — flair lives on its own lines)."""
    fire = "  (on fire)" if stats.streak_days >= 3 else ""
    streak_word = "day" if stats.streak_days == 1 else "days"
    lines = [
        "  +-----------------------------------+",
        "  |  GG . GRIP SESSION VICTORY LAP    |",
        "  +-----------------------------------+",
        "",
        _row("Commits", str(stats.commits)),
        _row("Merges", str(stats.merges)),
        _row("Test commits", str(stats.test_commits)),
        _row("Lines +", f"+{stats.insertions}"),
        _row("Lines -", f"-{stats.deletions}"),
        _row("Streak", f"{stats.streak_days} {streak_word}{fire}"),
        _row("XP gained", str(stats.xp)),
        "",
        "     * Play of the session:",
        f"       {stats.play_of_session}",
        "",
        f"     gg wp -- well played, GRIP.  ({stats.window_label})",
    ]
    return "\n".join(lines)


def _git_log_numstat(since: str, ref: str = "HEAD") -> tuple[int, str, str]:
    """Impure: read the real git log for the window on ``ref``. ``since`` is any
    git approxidate (e.g. '8 hours ago', '2026-06-02 00:00').

    Returns ``(returncode, stdout, stderr)`` so the caller can distinguish a
    genuine quiet session (rc 0, empty stdout) from a git failure such as a bad
    ``ref`` or not-a-repo (rc != 0, diagnostic on stderr) — AGORA #3378 C1.
    """
    out = subprocess.run(
        ["git", "log", ref, f"--since={since}",
         "--numstat", "--format=__C__%H%x09%cI%x09%s"],
        capture_output=True, text=True, check=False,
    )
    return out.returncode, out.stdout, out.stderr


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="gg", description="GRIP session Victory Lap card")
    ap.add_argument("--since", default="8 hours ago",
                    help="git approxidate window start (default: '8 hours ago')")
    ap.add_argument("--label", default="", help="window label shown on the card")
    ap.add_argument("--ref", default="HEAD",
                    help="git ref to read (default HEAD; e.g. origin/main)")
    args = ap.parse_args(argv)

    rc, raw, err = _git_log_numstat(args.since, ref=args.ref)
    if rc != 0:
        print(err.strip() or f"git log failed (exit {rc})", file=sys.stderr)
        return rc or 1
    commits = parse_numstat_log(raw)
    today = datetime.now(timezone.utc).date()
    stats = compute_stats(commits, today, window_label=args.label or f"since {args.since}")
    print(render_card(stats))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
