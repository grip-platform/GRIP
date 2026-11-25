"""Achievement Badge System - Gamification for developers.

Tracks coding milestones from git history and awards tiered badges.
Badges progress through Bronze -> Silver -> Gold -> Platinum tiers
based on repetition count.

Usage:
    tracker = BadgeTracker("/path/to/repo")
    tracker.scan_history(days=30)
    print(tracker.format_badge_card())
"""

import json
import subprocess
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, date
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class BadgeTier(Enum):
    """Badge tier progression based on repetition."""
    BRONZE = "Bronze"
    SILVER = "Silver"
    GOLD = "Gold"
    PLATINUM = "Platinum"


# Tier thresholds: how many times a badge must be earned to reach each tier
TIER_THRESHOLDS = {
    BadgeTier.BRONZE: 1,
    BadgeTier.SILVER: 3,
    BadgeTier.GOLD: 10,
    BadgeTier.PLATINUM: 25,
}

# Tier display icons (ASCII-safe)
TIER_ICONS = {
    BadgeTier.BRONZE: "[B]",
    BadgeTier.SILVER: "[S]",
    BadgeTier.GOLD: "[G]",
    BadgeTier.PLATINUM: "[P]",
}


BADGE_DEFINITIONS = {
    "first_blood": {
        "name": "First Blood",
        "description": "First commit of the day",
        "category": "daily",
    },
    "centurion": {
        "name": "Centurion",
        "description": "100 commits in a 14-day sprint",
        "category": "volume",
    },
    "night_owl": {
        "name": "Night Owl",
        "description": "Commits after midnight (00:00-04:59)",
        "category": "time",
    },
    "early_bird": {
        "name": "Early Bird",
        "description": "Commits before 7am (05:00-06:59)",
        "category": "time",
    },
    "streak_master": {
        "name": "Streak Master",
        "description": "7+ consecutive days with commits",
        "category": "consistency",
    },
    "bug_squasher": {
        "name": "Bug Squasher",
        "description": "10+ bug fix commits",
        "category": "quality",
    },
    "feature_factory": {
        "name": "Feature Factory",
        "description": "5+ feature commits in a day",
        "category": "productivity",
    },
    "code_surgeon": {
        "name": "Code Surgeon",
        "description": "Commit with >500 deletions and <50 additions",
        "category": "cleanup",
    },
    "polyglot": {
        "name": "Polyglot",
        "description": "Commits touching 5+ file types in a day",
        "category": "breadth",
    },
    "speed_demon": {
        "name": "Speed Demon",
        "description": "5+ commits in an hour",
        "category": "velocity",
    },
    "reviewer": {
        "name": "Reviewer",
        "description": "10+ PR reviews",
        "category": "collaboration",
    },
    "documentation_hero": {
        "name": "Documentation Hero",
        "description": "Commits with >50% markdown changes",
        "category": "docs",
    },
    "test_champion": {
        "name": "Test Champion",
        "description": "Commits with more test files than source files",
        "category": "testing",
    },
    "mentor": {
        "name": "Mentor",
        "description": "Pair commits with 3+ different people",
        "category": "collaboration",
    },
    "marathon": {
        "name": "Marathon",
        "description": "8+ hour coding session",
        "category": "endurance",
    },
}


@dataclass
class CommitInfo:
    """Parsed git commit information."""
    sha: str
    author: str
    email: str
    timestamp: datetime
    message: str
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    files: List[str] = field(default_factory=list)
    co_authors: List[str] = field(default_factory=list)


@dataclass
class EarnedBadge:
    """A badge that has been earned."""
    badge_id: str
    name: str
    description: str
    tier: BadgeTier
    count: int
    first_earned: str  # ISO format date string
    last_earned: str   # ISO format date string
    category: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d


def tier_for_count(count: int) -> BadgeTier:
    """Determine badge tier from earned count."""
    if count >= TIER_THRESHOLDS[BadgeTier.PLATINUM]:
        return BadgeTier.PLATINUM
    elif count >= TIER_THRESHOLDS[BadgeTier.GOLD]:
        return BadgeTier.GOLD
    elif count >= TIER_THRESHOLDS[BadgeTier.SILVER]:
        return BadgeTier.SILVER
    return BadgeTier.BRONZE


class BadgeTracker:
    """Scans git history and awards achievement badges.

    Args:
        repo_path: Path to git repository root.
        state_file: Optional path for persisting badge state.
    """

    def __init__(self, repo_path: str = ".", state_file: Optional[str] = None):
        self.repo_path = str(Path(repo_path).resolve())
        self.state_file = state_file
        self.commits: List[CommitInfo] = []
        # badge_id -> {user -> list of earned dates}
        self._badge_events: Dict[str, Dict[str, List[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._pr_reviews: Dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------
    # Git interaction
    # ------------------------------------------------------------------

    def _run_git(self, args: List[str], timeout: int = 60) -> str:
        """Run a git command and return stdout."""
        cmd = ["git", "-C", self.repo_path] + args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return ""
        return result.stdout

    def _parse_log(self, days: int = 30) -> List[CommitInfo]:
        """Parse git log into CommitInfo objects."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        # Format: SHA|author|email|unix_timestamp|subject
        log_output = self._run_git([
            "log",
            f"--since={since}",
            "--format=%H|%an|%ae|%at|%s",
            "--no-merges",
        ])
        if not log_output.strip():
            return []

        commits = []
        for line in log_output.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 4)
            if len(parts) < 5:
                continue
            sha, author, email, ts_str, message = parts
            try:
                timestamp = datetime.fromtimestamp(int(ts_str))
            except (ValueError, OSError):
                continue
            commit = CommitInfo(
                sha=sha.strip(),
                author=author.strip(),
                email=email.strip(),
                timestamp=timestamp,
                message=message.strip(),
            )
            commits.append(commit)
        return commits

    def _enrich_commit(self, commit: CommitInfo) -> None:
        """Add file stats and co-author info to a commit."""
        # Get numstat for insertions/deletions/files
        stat_output = self._run_git([
            "diff-tree", "--no-commit-id", "--numstat", "-r", commit.sha
        ])
        total_ins = 0
        total_del = 0
        files = []
        for stat_line in stat_output.strip().split("\n"):
            if not stat_line.strip():
                continue
            parts = stat_line.split("\t")
            if len(parts) >= 3:
                ins = int(parts[0]) if parts[0] != "-" else 0
                dels = int(parts[1]) if parts[1] != "-" else 0
                total_ins += ins
                total_del += dels
                files.append(parts[2])
        commit.insertions = total_ins
        commit.deletions = total_del
        commit.files_changed = len(files)
        commit.files = files

        # Get co-authors from commit body
        body = self._run_git(["log", "-1", "--format=%b", commit.sha])
        co_author_pattern = re.compile(
            r"Co-[Aa]uthored-[Bb]y:\s*(.+?)(?:\s*<.*>)?\s*$",
            re.MULTILINE,
        )
        commit.co_authors = co_author_pattern.findall(body)

    def _count_pr_reviews(self, user: str, days: int = 30) -> int:
        """Count PR reviews from git log (Reviewed-by trailers)."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        log_output = self._run_git([
            "log", f"--since={since}", "--format=%b", "--all",
        ])
        count = 0
        review_pattern = re.compile(
            r"(?:Reviewed-by|Approved-by|LGTM).*" + re.escape(user),
            re.IGNORECASE,
        )
        for line in log_output.split("\n"):
            if review_pattern.search(line):
                count += 1
        return count

    # ------------------------------------------------------------------
    # Badge detection
    # ------------------------------------------------------------------

    def _detect_first_blood(self, commits: List[CommitInfo]) -> None:
        """First commit of the day."""
        # Group by author+date, find earliest per day
        day_first: Dict[Tuple[str, date], CommitInfo] = {}
        for c in sorted(commits, key=lambda x: x.timestamp):
            key = (c.author, c.timestamp.date())
            if key not in day_first:
                day_first[key] = c
                self._badge_events["first_blood"][c.author].append(
                    c.timestamp.isoformat()
                )

    def _detect_night_owl(self, commits: List[CommitInfo]) -> None:
        """Commits between 00:00-04:59."""
        for c in commits:
            hour = c.timestamp.hour
            if 0 <= hour <= 4:
                self._badge_events["night_owl"][c.author].append(
                    c.timestamp.isoformat()
                )

    def _detect_early_bird(self, commits: List[CommitInfo]) -> None:
        """Commits between 05:00-06:59."""
        for c in commits:
            hour = c.timestamp.hour
            if 5 <= hour <= 6:
                self._badge_events["early_bird"][c.author].append(
                    c.timestamp.isoformat()
                )

    def _detect_centurion(self, commits: List[CommitInfo]) -> None:
        """100+ commits in a 14-day sprint window."""
        by_author: Dict[str, List[CommitInfo]] = defaultdict(list)
        for c in commits:
            by_author[c.author].append(c)

        for author, author_commits in by_author.items():
            if not author_commits:
                continue
            sorted_commits = sorted(author_commits, key=lambda x: x.timestamp)
            # Sliding 14-day window
            for i, start_commit in enumerate(sorted_commits):
                window_end = start_commit.timestamp + timedelta(days=14)
                window_commits = [
                    c for c in sorted_commits[i:]
                    if c.timestamp <= window_end
                ]
                if len(window_commits) >= 100:
                    self._badge_events["centurion"][author].append(
                        start_commit.timestamp.isoformat()
                    )
                    break  # One per scan

    def _detect_streak_master(self, commits: List[CommitInfo]) -> None:
        """7+ consecutive days with commits."""
        by_author: Dict[str, set] = defaultdict(set)
        for c in commits:
            by_author[c.author].add(c.timestamp.date())

        for author, commit_dates in by_author.items():
            sorted_dates = sorted(commit_dates)
            if len(sorted_dates) < 7:
                continue
            streak = 1
            best_streak = 1
            best_end = sorted_dates[0]
            for i in range(1, len(sorted_dates)):
                if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
                    streak += 1
                    if streak > best_streak:
                        best_streak = streak
                        best_end = sorted_dates[i]
                else:
                    streak = 1
            if best_streak >= 7:
                self._badge_events["streak_master"][author].append(
                    best_end.isoformat()
                )

    def _detect_bug_squasher(self, commits: List[CommitInfo]) -> None:
        """10+ bug fix commits."""
        fix_pattern = re.compile(
            r"^(fix|bug|hotfix|patch|bugfix)[:\s(/]",
            re.IGNORECASE,
        )
        by_author: Dict[str, int] = Counter()
        by_author_last: Dict[str, str] = {}
        for c in commits:
            if fix_pattern.match(c.message):
                by_author[c.author] += 1
                by_author_last[c.author] = c.timestamp.isoformat()
        for author, count in by_author.items():
            if count >= 10:
                self._badge_events["bug_squasher"][author].append(
                    by_author_last[author]
                )

    def _detect_feature_factory(self, commits: List[CommitInfo]) -> None:
        """5+ feature commits in a single day."""
        feat_pattern = re.compile(
            r"^(feat|feature)[:\s(/]",
            re.IGNORECASE,
        )
        # Group by author+date
        by_author_day: Dict[Tuple[str, date], int] = Counter()
        by_author_day_ts: Dict[Tuple[str, date], str] = {}
        for c in commits:
            if feat_pattern.match(c.message):
                key = (c.author, c.timestamp.date())
                by_author_day[key] += 1
                by_author_day_ts[key] = c.timestamp.isoformat()
        for key, count in by_author_day.items():
            if count >= 5:
                author = key[0]
                self._badge_events["feature_factory"][author].append(
                    by_author_day_ts[key]
                )

    def _detect_code_surgeon(self, commits: List[CommitInfo]) -> None:
        """>500 deletions and <50 additions."""
        for c in commits:
            if c.deletions > 500 and c.insertions < 50:
                self._badge_events["code_surgeon"][c.author].append(
                    c.timestamp.isoformat()
                )

    def _detect_polyglot(self, commits: List[CommitInfo]) -> None:
        """Commits touching 5+ file types in a day."""
        by_author_day: Dict[Tuple[str, date], set] = defaultdict(set)
        by_author_day_ts: Dict[Tuple[str, date], str] = {}
        for c in commits:
            key = (c.author, c.timestamp.date())
            for f in c.files:
                ext = Path(f).suffix.lower()
                if ext:
                    by_author_day[key].add(ext)
            by_author_day_ts[key] = c.timestamp.isoformat()
        for key, exts in by_author_day.items():
            if len(exts) >= 5:
                author = key[0]
                self._badge_events["polyglot"][author].append(
                    by_author_day_ts[key]
                )

    def _detect_speed_demon(self, commits: List[CommitInfo]) -> None:
        """5+ commits in any 1-hour window."""
        by_author: Dict[str, List[CommitInfo]] = defaultdict(list)
        for c in commits:
            by_author[c.author].append(c)

        for author, author_commits in by_author.items():
            sorted_commits = sorted(author_commits, key=lambda x: x.timestamp)
            awarded_windows: set = set()
            for i, start in enumerate(sorted_commits):
                window_end = start.timestamp + timedelta(hours=1)
                window_key = (
                    start.timestamp.date(),
                    start.timestamp.hour,
                )
                if window_key in awarded_windows:
                    continue
                count = sum(
                    1 for c in sorted_commits[i:]
                    if c.timestamp <= window_end
                )
                if count >= 5:
                    awarded_windows.add(window_key)
                    self._badge_events["speed_demon"][author].append(
                        start.timestamp.isoformat()
                    )

    def _detect_reviewer(self, commits: List[CommitInfo]) -> None:
        """10+ PR reviews. Uses stored review counts."""
        for user, count in self._pr_reviews.items():
            if count >= 10:
                self._badge_events["reviewer"][user].append(
                    datetime.now().isoformat()
                )

    def _detect_documentation_hero(self, commits: List[CommitInfo]) -> None:
        """>50% of changed files are markdown."""
        for c in commits:
            if not c.files:
                continue
            md_count = sum(
                1 for f in c.files
                if f.lower().endswith((".md", ".mdx", ".rst"))
            )
            if md_count > len(c.files) / 2:
                self._badge_events["documentation_hero"][c.author].append(
                    c.timestamp.isoformat()
                )

    def _detect_test_champion(self, commits: List[CommitInfo]) -> None:
        """More test files than source files in a commit."""
        test_patterns = re.compile(
            r"(test_|_test\.|\.test\.|\.spec\.|tests/|__tests__/|spec/)",
            re.IGNORECASE,
        )
        for c in commits:
            if not c.files:
                continue
            test_files = sum(1 for f in c.files if test_patterns.search(f))
            source_files = len(c.files) - test_files
            if test_files > source_files and test_files > 0:
                self._badge_events["test_champion"][c.author].append(
                    c.timestamp.isoformat()
                )

    def _detect_mentor(self, commits: List[CommitInfo]) -> None:
        """Pair commits with 3+ different co-authors."""
        by_author: Dict[str, set] = defaultdict(set)
        by_author_ts: Dict[str, str] = {}
        for c in commits:
            for co in c.co_authors:
                by_author[c.author].add(co.strip())
                by_author_ts[c.author] = c.timestamp.isoformat()
        for author, co_authors in by_author.items():
            if len(co_authors) >= 3:
                self._badge_events["mentor"][author].append(
                    by_author_ts[author]
                )

    def _detect_marathon(self, commits: List[CommitInfo]) -> None:
        """8+ hour coding session (commits spanning 8h+ with <2h gaps)."""
        by_author: Dict[str, List[datetime]] = defaultdict(list)
        for c in commits:
            by_author[c.author].append(c.timestamp)

        for author, timestamps in by_author.items():
            sorted_ts = sorted(timestamps)
            if len(sorted_ts) < 2:
                continue
            session_start = sorted_ts[0]
            prev = sorted_ts[0]
            for ts in sorted_ts[1:]:
                gap = (ts - prev).total_seconds() / 3600
                if gap > 2:
                    # End of session
                    duration = (prev - session_start).total_seconds() / 3600
                    if duration >= 8:
                        self._badge_events["marathon"][author].append(
                            session_start.isoformat()
                        )
                    session_start = ts
                prev = ts
            # Check final session
            duration = (prev - session_start).total_seconds() / 3600
            if duration >= 8:
                self._badge_events["marathon"][author].append(
                    session_start.isoformat()
                )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_history(self, days: int = 30) -> int:
        """Scan git history and detect all badges.

        Args:
            days: Number of days to scan back.

        Returns:
            Number of commits scanned.
        """
        self._badge_events = defaultdict(lambda: defaultdict(list))
        self.commits = self._parse_log(days)

        # Enrich commits with file stats
        for commit in self.commits:
            self._enrich_commit(commit)

        # Count PR reviews for all authors
        authors = {c.author for c in self.commits}
        for author in authors:
            self._pr_reviews[author] = self._count_pr_reviews(author, days)

        # Run all detectors
        self._detect_first_blood(self.commits)
        self._detect_night_owl(self.commits)
        self._detect_early_bird(self.commits)
        self._detect_centurion(self.commits)
        self._detect_streak_master(self.commits)
        self._detect_bug_squasher(self.commits)
        self._detect_feature_factory(self.commits)
        self._detect_code_surgeon(self.commits)
        self._detect_polyglot(self.commits)
        self._detect_speed_demon(self.commits)
        self._detect_reviewer(self.commits)
        self._detect_documentation_hero(self.commits)
        self._detect_test_champion(self.commits)
        self._detect_mentor(self.commits)
        self._detect_marathon(self.commits)

        # Persist if state file configured
        if self.state_file:
            self._save_state()

        return len(self.commits)

    def get_badges(self, user: Optional[str] = None) -> List[EarnedBadge]:
        """Return earned badges, optionally filtered by user.

        Args:
            user: Filter to specific user. None returns all users' badges.

        Returns:
            List of EarnedBadge objects sorted by tier (highest first).
        """
        badges = []
        for badge_id, user_events in self._badge_events.items():
            defn = BADGE_DEFINITIONS.get(badge_id)
            if not defn:
                continue
            for author, dates in user_events.items():
                if user and author != user:
                    continue
                if not dates:
                    continue
                count = len(dates)
                sorted_dates = sorted(dates)
                badges.append(EarnedBadge(
                    badge_id=badge_id,
                    name=defn["name"],
                    description=defn["description"],
                    tier=tier_for_count(count),
                    count=count,
                    first_earned=sorted_dates[0],
                    last_earned=sorted_dates[-1],
                    category=defn["category"],
                ))
        # Sort: Platinum first, then Gold, Silver, Bronze
        tier_order = {
            BadgeTier.PLATINUM: 0,
            BadgeTier.GOLD: 1,
            BadgeTier.SILVER: 2,
            BadgeTier.BRONZE: 3,
        }
        badges.sort(key=lambda b: (tier_order[b.tier], b.name))
        return badges

    def get_streak(self, user: Optional[str] = None) -> Dict:
        """Get current commit streak info.

        Args:
            user: Filter to specific user.

        Returns:
            Dict with current_streak, longest_streak, last_commit_date.
        """
        commit_dates: set = set()
        for c in self.commits:
            if user and c.author != user:
                continue
            commit_dates.add(c.timestamp.date())

        if not commit_dates:
            return {
                "current_streak": 0,
                "longest_streak": 0,
                "last_commit_date": None,
            }

        sorted_dates = sorted(commit_dates)
        today = date.today()

        # Current streak (counting back from today or last commit date)
        current = 0
        check_date = today
        # Allow 1-day gap (today might not have commits yet)
        if check_date not in commit_dates:
            check_date = today - timedelta(days=1)
        while check_date in commit_dates:
            current += 1
            check_date -= timedelta(days=1)

        # Longest streak
        longest = 1
        streak = 1
        for i in range(1, len(sorted_dates)):
            if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
                streak += 1
                longest = max(longest, streak)
            else:
                streak = 1

        if len(sorted_dates) == 1:
            longest = 1

        return {
            "current_streak": current,
            "longest_streak": longest,
            "last_commit_date": sorted_dates[-1].isoformat(),
        }

    def get_leaderboard(self) -> List[Dict]:
        """Multi-user badge comparison.

        Returns:
            List of dicts sorted by total badge score (descending).
        """
        user_scores: Dict[str, Dict] = {}

        for badge_id, user_events in self._badge_events.items():
            for author, dates in user_events.items():
                if not dates:
                    continue
                if author not in user_scores:
                    user_scores[author] = {
                        "user": author,
                        "badges": 0,
                        "score": 0,
                        "highest_tier": BadgeTier.BRONZE,
                        "badge_list": [],
                    }
                count = len(dates)
                tier = tier_for_count(count)
                tier_points = {
                    BadgeTier.BRONZE: 1,
                    BadgeTier.SILVER: 3,
                    BadgeTier.GOLD: 10,
                    BadgeTier.PLATINUM: 25,
                }
                user_scores[author]["badges"] += 1
                user_scores[author]["score"] += tier_points[tier]
                defn = BADGE_DEFINITIONS.get(badge_id, {})
                user_scores[author]["badge_list"].append(
                    defn.get("name", badge_id)
                )
                # Track highest tier
                tier_rank = {
                    BadgeTier.BRONZE: 0,
                    BadgeTier.SILVER: 1,
                    BadgeTier.GOLD: 2,
                    BadgeTier.PLATINUM: 3,
                }
                current_highest = user_scores[author]["highest_tier"]
                if tier_rank[tier] > tier_rank[current_highest]:
                    user_scores[author]["highest_tier"] = tier

        result = []
        for data in user_scores.values():
            result.append({
                "user": data["user"],
                "badges": data["badges"],
                "score": data["score"],
                "highest_tier": data["highest_tier"].value,
                "badge_list": sorted(data["badge_list"]),
            })
        result.sort(key=lambda x: (-x["score"], x["user"]))
        return result

    def format_badge_card(self, user: Optional[str] = None) -> str:
        """Pretty ASCII art badge card.

        Args:
            user: Show badges for specific user. When None, groups by committer.

        Returns:
            Multi-line string with badge card.
        """
        width = 52
        lines = []

        if user:
            # Single-user view
            badges = self.get_badges(user)
            streak = self.get_streak(user)
            lines.append("+" + "-" * width + "+")
            lines.append("|" + f"Badges for {user}".center(width) + "|")
            lines.append("+" + "-" * width + "+")
            if not badges:
                lines.append("|" + "  No badges earned yet.".ljust(width) + "|")
            else:
                for b in badges:
                    icon = TIER_ICONS[b.tier]
                    entry = f"  {icon} {b.name} (x{b.count})"
                    lines.append("|" + entry.ljust(width) + "|")
            lines.append("+" + "-" * width + "+")
            cs = streak["current_streak"]
            ls = streak["longest_streak"]
            lines.append("|" + f"  Streak: {cs} days current / {ls} days longest".ljust(width) + "|")
            lines.append("+" + "-" * width + "+")
        else:
            # Multi-user view: one section per committer
            all_users: List[str] = sorted(
                {u for events in self._badge_events.values() for u in events}
            )
            lines.append("+" + "-" * width + "+")
            lines.append("|" + "Achievement Badges".center(width) + "|")
            lines.append("+" + "-" * width + "+")
            for author in all_users:
                badges = self.get_badges(author)
                streak = self.get_streak(author)
                lines.append("|" + f"  {author}".ljust(width) + "|")
                lines.append("|" + ("-" * (width - 2)).center(width) + "|")
                if not badges:
                    lines.append("|" + "    No badges earned yet.".ljust(width) + "|")
                else:
                    for b in badges:
                        icon = TIER_ICONS[b.tier]
                        entry = f"    {icon} {b.name} (x{b.count})"
                        lines.append("|" + entry.ljust(width) + "|")
                cs = streak["current_streak"]
                ls = streak["longest_streak"]
                lines.append("|" + f"    Streak: {cs} days / {ls} days longest".ljust(width) + "|")
                lines.append("+" + "-" * width + "+")
            if not all_users:
                lines.append("|" + "  No badges earned yet.".ljust(width) + "|")
                lines.append("+" + "-" * width + "+")
                lines.append("|" + "  Streak: 0 days current / 0 days longest".ljust(width) + "|")
                lines.append("+" + "-" * width + "+")

        return "\n".join(lines)

    def format_slack_message(self, user: Optional[str] = None) -> str:
        """Slack mrkdwn formatted badge summary.

        Uses *bold* (Slack mrkdwn), not **bold** (GitHub markdown).

        Args:
            user: Show badges for specific user.

        Returns:
            Slack mrkdwn formatted string.
        """
        badges = self.get_badges(user)
        streak = self.get_streak(user)
        title = f"*Achievement Badges for {user}*" if user else "*Achievement Badges*"

        parts = [title, ""]

        if not badges:
            parts.append("_No badges earned yet._")
        else:
            by_tier: Dict[BadgeTier, List[EarnedBadge]] = defaultdict(list)
            for b in badges:
                by_tier[b.tier].append(b)

            tier_order = [
                BadgeTier.PLATINUM,
                BadgeTier.SILVER,
                BadgeTier.GOLD,
                BadgeTier.BRONZE,
            ]
            for tier in tier_order:
                if tier not in by_tier:
                    continue
                parts.append(f"*{tier.value}*")
                for b in by_tier[tier]:
                    parts.append(f"  {b.name} (x{b.count}) - _{b.description}_")
                parts.append("")

        parts.append(
            f"*Streak*: {streak['current_streak']} days current, "
            f"{streak['longest_streak']} days longest"
        )
        return "\n".join(parts)

    def generate_mcp_progress(self, user: Optional[str] = None) -> Dict:
        """Generate progress dict for grip_progress MCP app.

        Args:
            user: Filter to specific user.

        Returns:
            Dict with badges, streak, leaderboard, and stats.
        """
        badges = self.get_badges(user)
        streak = self.get_streak(user)
        leaderboard = self.get_leaderboard()

        total_possible = len(BADGE_DEFINITIONS)
        unique_earned = len({b.badge_id for b in badges})
        completion = (
            round(unique_earned / total_possible * 100, 1)
            if total_possible > 0 else 0
        )

        tier_counts = Counter(b.tier.value for b in badges)

        return {
            "type": "achievement_badges",
            "user": user,
            "badges": [b.to_dict() for b in badges],
            "badge_count": len(badges),
            "unique_badges": unique_earned,
            "total_possible": total_possible,
            "completion_pct": completion,
            "tier_summary": dict(tier_counts),
            "streak": streak,
            "leaderboard": leaderboard[:10],  # Top 10
            "scan_commits": len(self.commits),
        }

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Save badge state to file."""
        if not self.state_file:
            return
        state = {}
        for badge_id, user_events in self._badge_events.items():
            state[badge_id] = dict(user_events)
        path = Path(self.state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2))

    def load_state(self) -> bool:
        """Load badge state from file.

        Returns:
            True if state was loaded successfully.
        """
        if not self.state_file:
            return False
        path = Path(self.state_file)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text())
            self._badge_events = defaultdict(lambda: defaultdict(list))
            for badge_id, user_events in data.items():
                for user, dates in user_events.items():
                    self._badge_events[badge_id][user] = dates
            return True
        except (json.JSONDecodeError, KeyError):
            return False

    def inject_pr_reviews(self, reviews: Dict[str, int]) -> None:
        """Inject PR review counts (for external sources like GitHub API).

        Args:
            reviews: Dict mapping username to review count.
        """
        self._pr_reviews.update(reviews)

    def inject_commits(self, commits: List[CommitInfo]) -> None:
        """Inject pre-built commits (for testing or external sources).

        Args:
            commits: List of CommitInfo objects.
        """
        self.commits = commits


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Achievement Badge Tracker")
    sub = parser.add_subparsers(dest="cmd")

    check_p = sub.add_parser("check", help="Show earned badges")
    check_p.add_argument("--days", type=int, default=30, help="Days of history to scan")
    check_p.add_argument("--user", default=None, help="Filter to specific user")
    check_p.add_argument(
        "--format",
        choices=["card", "slack", "json"],
        default="card",
        help="Output format (card, slack, json)",
    )
    check_p.add_argument("--repo", default=".", help="Path to git repository")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    tracker = BadgeTracker(repo_path=args.repo)
    count = tracker.scan_history(days=args.days)

    if args.format == "slack":
        print(tracker.format_slack_message(user=args.user))
    elif args.format == "json":
        print(json.dumps(tracker.generate_mcp_progress(user=args.user), indent=2))
    else:
        print(tracker.format_badge_card(user=args.user))

    print(f"\n({count} commits scanned, last {args.days} days)")
