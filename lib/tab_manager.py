#!/usr/bin/env python3
"""GRIP Tab Manager — cross-platform browser tab management from the CLI.

Captures, snapshots, restores, searches, and garbage-collects browser tabs
across all running browsers. Saves memory by closing tabs while preserving
their information for instant recall.

Usage:
    python3 tab_manager.py list                  # List all open tabs
    python3 tab_manager.py snapshot [name]       # Save current tabs to snapshot
    python3 tab_manager.py restore <name>        # Restore tabs from snapshot
    python3 tab_manager.py close <pattern>       # Close tabs matching pattern
    python3 tab_manager.py gc                    # Close duplicates + stale tabs
    python3 tab_manager.py search <query>        # Search across all snapshots
    python3 tab_manager.py snapshots             # List all saved snapshots
    python3 tab_manager.py memory                # Show browser memory usage
    python3 tab_manager.py nuke [browser]        # Snapshot ALL then close ALL
    python3 tab_manager.py archaeology           # Show tab age from browser history
    python3 tab_manager.py categorise            # Group tabs by category
    python3 tab_manager.py bankrupt [--force]    # Archive all tabs to markdown + close

Cross-platform: macOS (AppleScript), Windows (PowerShell), Linux (basic).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# ── Config ──────────────────────────────────────────────────────────────────

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
SNAPSHOTS_DIR = CLAUDE_DIR / "state" / "tab-snapshots"
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

SYSTEM = platform.system()  # Darwin, Windows, Linux

# Chromium-based browsers share identical AppleScript interfaces
CHROMIUM_BROWSERS = {
    "Google Chrome": "Google Chrome",
    "Brave Browser": "Brave Browser",
    "Microsoft Edge": "Microsoft Edge",
    "Vivaldi": "Vivaldi",
    "Arc": "Arc",
    "Chromium": "Chromium",
}

# Non-Chromium
OTHER_BROWSERS = {
    "Safari": "Safari",
    "Firefox": "Firefox",
}

ALL_BROWSERS = {**CHROMIUM_BROWSERS, **OTHER_BROWSERS}


# ── Platform: macOS ─────────────────────────────────────────────────────────

def _running_browsers_macos() -> list[str]:
    """Detect which browsers are currently running on macOS using pgrep."""
    # pgrep is instant vs System Events which iterates all processes
    process_map = {
        "Google Chrome": "Google Chrome",
        "Brave Browser": "Brave Browser",
        "Microsoft Edge": "Microsoft Edge",
        "Safari": "Safari",
        "firefox": "Firefox",
        "Vivaldi": "Vivaldi",
        "Arc": "Arc",
    }
    running = []
    for pname, browser in process_map.items():
        result = subprocess.run(
            ["pgrep", "-x", pname],
            capture_output=True, timeout=2,
        )
        if result.returncode == 0:
            running.append(browser)
    return running


def _run_osascript_capture(script: str, timeout: int = 15) -> str:
    """Run ``osascript -e <script>`` and return stripped stdout.

    Extracted helper so the three list-tabs functions don't repeat the
    subprocess invocation. Callers split on tabs and interpret fields
    themselves — this helper has zero per-browser knowledge.
    """
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip()


def _list_tabs_template(tell_app: str, title_attr: str) -> str:
    """Build the non-Arc list-tabs AppleScript (Chromium + Safari variant).

    ``title_attr`` is either ``title`` (Chromium family) or ``name`` (Safari).
    Arc uses its own template in :func:`_get_tabs_arc_macos` because it
    iterates ``every space of w`` and emits a 5th field (space name).
    """
    return f"""
tell application "{tell_app}"
    set tabData to ""
    set winIdx to 0
    repeat with w in windows
        set winIdx to winIdx + 1
        set tabIdx to 0
        repeat with t in tabs of w
            set tabIdx to tabIdx + 1
            set tabURL to URL of t
            set tabTitle to {title_attr} of t
            set tabData to tabData & winIdx & "\\t" & tabIdx & "\\t" & tabURL & "\\t" & tabTitle & linefeed
        end repeat
    end repeat
    return tabData
end tell
"""


def _get_tabs_chromium_macos(browser: str) -> list[dict]:
    """Get all tabs from a Chromium-based browser on macOS via AppleScript."""
    script = _list_tabs_template(browser, title_attr="title")
    tabs = []
    for line in _run_osascript_capture(script).splitlines():
        parts = line.split("\t", 3)
        if len(parts) >= 4:
            tabs.append({
                "browser": browser,
                "window": int(parts[0]),
                "tab_index": int(parts[1]),
                "url": parts[2],
                "title": parts[3],
            })
    return tabs


def _get_tabs_arc_macos() -> list[dict]:
    """Get all tabs from Arc on macOS, including Space names.

    Arc's AppleScript exposes ``space`` as an extra layer between
    window and tab, so this function uses a distinct template (vs. the
    shared :func:`_list_tabs_template` used by Chromium + Safari).
    """
    script = """
tell application "Arc"
    set tabData to ""
    repeat with w in every window
        set winIdx to id of w
        repeat with s in every space of w
            set spaceName to title of s
            set tabIdx to 0
            repeat with t in every tab of s
                set tabIdx to tabIdx + 1
                set tabURL to URL of t
                set tabTitle to title of t
                set tabData to tabData & winIdx & "\\t" & tabIdx & "\\t" & spaceName & "\\t" & tabURL & "\\t" & tabTitle & linefeed
            end repeat
        end repeat
    end repeat
    return tabData
end tell
"""
    tabs = []
    for line in _run_osascript_capture(script).splitlines():
        parts = line.split("\t", 4)
        if len(parts) >= 5:
            tabs.append({
                "browser": "Arc",
                "window": int(parts[0]),
                "tab_index": int(parts[1]),
                "space": parts[2],
                "url": parts[3],
                "title": parts[4],
            })
    return tabs


def _get_tabs_safari_macos() -> list[dict]:
    """Get all tabs from Safari on macOS."""
    script = _list_tabs_template("Safari", title_attr="name")
    tabs = []
    for line in _run_osascript_capture(script).splitlines():
        parts = line.split("\t", 3)
        if len(parts) >= 4:
            tabs.append({
                "browser": "Safari",
                "window": int(parts[0]),
                "tab_index": int(parts[1]),
                "url": parts[2],
                "title": parts[3],
            })
    return tabs


def _get_tabs_firefox_macos() -> list[dict]:
    """Get Firefox tabs on macOS — limited: Firefox AppleScript is minimal."""
    # Firefox on macOS has very limited AppleScript support.
    # We can only get the frontmost tab's URL via GUI scripting.
    # For full tab access, users would need the brotab extension.
    # Return empty with a note rather than incomplete data.
    return []


def _close_tab_chromium_macos(browser: str, url_pattern: str) -> int:
    """Close tabs matching a URL pattern in a Chromium browser. Returns count closed.

    β-4b migration: prefer the HAL-hardened dispatcher (registry pattern
    rejects quote/backslash injection in ``url_pattern`` before it ever
    reaches AppleScript). Falls back to the original f-string path when
    HAL is unavailable or rejects the input — the caller still gets a
    number, just without the hardened layer.
    """
    try:
        import hal_osa
        hal_count = hal_osa.close_chromium_tabs_by_url(browser, url_pattern)
        if hal_count is not None:
            return hal_count
    except ImportError:
        pass

    script = f"""
tell application "{browser}"
    set closedCount to 0
    repeat with w in windows
        set tabList to tabs of w
        repeat with i from (count of tabList) to 1 by -1
            set t to item i of tabList
            if URL of t contains "{url_pattern}" then
                close t
                set closedCount to closedCount + 1
            end if
        end repeat
    end repeat
    return closedCount
end tell
"""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def _close_tab_safari_macos(url_pattern: str) -> int:
    """Close tabs matching a URL pattern in Safari."""
    script = f"""
tell application "Safari"
    set closedCount to 0
    repeat with w in windows
        set tabList to tabs of w
        repeat with i from (count of tabList) to 1 by -1
            set t to item i of tabList
            if URL of t contains "{url_pattern}" then
                close t
                set closedCount to closedCount + 1
            end if
        end repeat
    end repeat
    return closedCount
end tell
"""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def _open_url_macos(browser: str, url: str) -> None:
    """Open a URL in a specific browser on macOS."""
    if browser in CHROMIUM_BROWSERS:
        script = f'tell application "{browser}" to open location "{url}"'
    elif browser == "Safari":
        script = f"""
tell application "Safari"
    tell window 1
        set current tab to (make new tab with properties {{URL:"{url}"}})
    end tell
end tell
"""
    else:
        subprocess.run(["open", "-a", browser, url], timeout=5)
        return
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)


def _get_memory_macos() -> list[dict]:
    """Get memory usage per browser process on macOS.

    Uses a single compiled regex to match all browser names per line,
    reducing from O(lines * browsers) to O(lines) with regex alternation.
    """
    result = subprocess.run(
        ["ps", "-eo", "rss,comm"],
        capture_output=True, text=True, timeout=5,
    )
    # Build a single regex matching any browser name (case-insensitive)
    browser_patterns = {
        name.lower().replace(" ", ""): name for name in ALL_BROWSERS
    }
    combined = "|".join(re.escape(p) for p in browser_patterns)
    browser_re = re.compile(combined, re.IGNORECASE)

    browser_mem: dict[str, int] = {}
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        rss_kb = int(parts[0])
        comm = parts[1].lower().replace(" ", "").replace("/", "")
        match = browser_re.search(comm)
        if match:
            browser_name = browser_patterns[match.group().lower()]
            browser_mem[browser_name] = browser_mem.get(browser_name, 0) + rss_kb
    return [
        {"browser": name, "memory_mb": round(kb / 1024, 1)}
        for name, kb in sorted(browser_mem.items(), key=lambda x: -x[1])
        if kb > 0
    ]


# ── Platform: Windows ───────────────────────────────────────────────────────

def _running_browsers_windows() -> list[str]:
    """Detect running browsers on Windows via tasklist.

    Parses each CSV line's first field (the image name) and checks
    dict membership — O(n) over tasklist rows, O(1) per row. No nested
    loop over the process map.
    """
    process_map = {
        "chrome.exe": "Google Chrome",
        "brave.exe": "Brave Browser",
        "msedge.exe": "Microsoft Edge",
        "firefox.exe": "Firefox",
        "vivaldi.exe": "Vivaldi",
    }
    result = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, timeout=10,
    )
    running: set[str] = set()
    for line in result.stdout.splitlines():
        first_field = line.lstrip().split(",", 1)[0].strip().strip('"').lower()
        if first_field in process_map:
            running.add(process_map[first_field])
    return list(running)


# ── Cross-Platform API ──────────────────────────────────────────────────────

def get_running_browsers() -> list[str]:
    """Detect which browsers are currently running."""
    if SYSTEM == "Darwin":
        return _running_browsers_macos()
    elif SYSTEM == "Windows":
        return _running_browsers_windows()
    return []


def get_all_tabs() -> list[dict]:
    """Get all open tabs across all running browsers."""
    browsers = get_running_browsers()
    all_tabs = []
    for browser in browsers:
        try:
            if SYSTEM == "Darwin":
                if browser == "Arc":
                    all_tabs.extend(_get_tabs_arc_macos())
                elif browser in CHROMIUM_BROWSERS:
                    all_tabs.extend(_get_tabs_chromium_macos(browser))
                elif browser == "Safari":
                    all_tabs.extend(_get_tabs_safari_macos())
                elif browser == "Firefox":
                    all_tabs.extend(_get_tabs_firefox_macos())
        except (subprocess.TimeoutExpired, Exception) as e:
            print(f"  Warning: Could not read tabs from {browser}: {e}", file=sys.stderr)
    return all_tabs


def close_tabs(pattern: str) -> dict:
    """Close all tabs matching a URL/title pattern. Returns summary."""
    browsers = get_running_browsers()
    total_closed = 0
    details = {}
    for browser in browsers:
        try:
            if SYSTEM == "Darwin":
                if browser in CHROMIUM_BROWSERS:
                    closed = _close_tab_chromium_macos(browser, pattern)
                elif browser == "Safari":
                    closed = _close_tab_safari_macos(pattern)
                else:
                    closed = 0
                if closed > 0:
                    details[browser] = closed
                    total_closed += closed
        except Exception:
            pass
    return {"total_closed": total_closed, "per_browser": details}


def find_duplicates(tabs: list[dict]) -> list[dict]:
    """Find tabs with duplicate URLs."""
    seen: dict[str, list[dict]] = {}
    for tab in tabs:
        url = tab["url"].rstrip("/").split("#")[0].split("?")[0]  # Normalise
        seen.setdefault(url, []).append(tab)
    return [
        {"url": url, "count": len(dupes), "browsers": list({d["browser"] for d in dupes})}
        for url, dupes in seen.items()
        if len(dupes) > 1
    ]


# ── Snapshots ───────────────────────────────────────────────────────────────

def save_snapshot(name: Optional[str] = None, tabs: Optional[list[dict]] = None) -> Path:
    """Save current tabs as a named snapshot."""
    if tabs is None:
        tabs = get_all_tabs()
    if name is None:
        name = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Sanitise name
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", name)
    snapshot = {
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tab_count": len(tabs),
        "browsers": list({t["browser"] for t in tabs}),
        "tabs": tabs,
    }
    path = SNAPSHOTS_DIR / f"{safe_name}.json"
    path.write_text(json.dumps(snapshot, indent=2) + "\n")
    return path


def load_snapshot(name: str) -> Optional[dict]:
    """Load a snapshot by name (with fuzzy matching)."""
    # Exact match first
    exact = SNAPSHOTS_DIR / f"{name}.json"
    if exact.exists():
        return json.loads(exact.read_text())
    # Fuzzy: find snapshots containing the name
    for path in sorted(SNAPSHOTS_DIR.glob("*.json"), reverse=True):
        if name.lower() in path.stem.lower():
            return json.loads(path.read_text())
    return None


def list_snapshots() -> list[dict]:
    """List all saved snapshots with metadata."""
    snapshots = []
    for path in sorted(SNAPSHOTS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text())
            snapshots.append({
                "name": data.get("name", path.stem),
                "file": path.name,
                "created_at": data.get("created_at", "unknown"),
                "tab_count": data.get("tab_count", len(data.get("tabs", []))),
                "browsers": data.get("browsers", []),
                "size_kb": round(path.stat().st_size / 1024, 1),
            })
        except (json.JSONDecodeError, KeyError):
            pass
    return snapshots


def restore_snapshot(name: str, browser: Optional[str] = None) -> dict:
    """Restore tabs from a snapshot. Optionally force a specific browser."""
    snapshot = load_snapshot(name)
    if snapshot is None:
        return {"error": f"Snapshot '{name}' not found"}

    opened = 0
    target_browser = browser
    for tab in snapshot.get("tabs", []):
        url = tab.get("url", "")
        if not url or url in ("chrome://newtab/", "brave://newtab/", "about:blank"):
            continue
        b = target_browser or tab.get("browser", "Google Chrome")
        # Fall back to first running browser if original isn't running
        running = get_running_browsers()
        if b not in running:
            b = running[0] if running else "Google Chrome"
        try:
            if SYSTEM == "Darwin":
                _open_url_macos(b, url)
            opened += 1
        except Exception:
            pass

    return {
        "snapshot": snapshot.get("name"),
        "tabs_restored": opened,
        "total_in_snapshot": len(snapshot.get("tabs", [])),
    }


def _search_one_snapshot(path: Path, query_lower: str) -> list[dict]:
    """Return matches from a single snapshot file. Extracted so the
    caller stays single-level and each snapshot load is its own
    try/except scope."""
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, KeyError, OSError):
        return []
    snap_name = data.get("name", path.stem)
    hits: list[dict] = []
    for tab in data.get("tabs", []):
        title = tab.get("title", "").lower()
        url = tab.get("url", "").lower()
        if query_lower in title or query_lower in url:
            hits.append({
                "snapshot": snap_name,
                "browser": tab.get("browser", "unknown"),
                "title": tab.get("title", ""),
                "url": tab.get("url", ""),
            })
    return hits


def search_snapshots(query: str) -> list[dict]:
    """Search across all snapshots for tabs matching a query.

    Complexity is O(snapshots * tabs_per_snapshot) which is inherently
    required — every tab must be checked. The data volume is bounded
    by local snapshot count (typically <100 files, <10k tabs total).
    """
    query_lower = query.lower()
    results: list[dict] = []
    for path in SNAPSHOTS_DIR.glob("*.json"):
        results.extend(_search_one_snapshot(path, query_lower))
    return results


# ── Garbage Collection ──────────────────────────────────────────────────────

def garbage_collect(dry_run: bool = True) -> dict:
    """Find and optionally close duplicate and low-value tabs."""
    tabs = get_all_tabs()
    dupes = find_duplicates(tabs)

    # Identify low-value tabs (new tab pages, blank pages)
    low_value_patterns = [
        "chrome://newtab", "brave://newtab", "edge://newtab",
        "about:blank", "about:newtab", "chrome://extensions",
    ]
    low_value = [
        t for t in tabs
        if any(t["url"].startswith(p) for p in low_value_patterns)
    ]

    report = {
        "total_tabs": len(tabs),
        "duplicates": len(dupes),
        "duplicate_urls": [d["url"] for d in dupes[:10]],
        "low_value": len(low_value),
        "dry_run": dry_run,
        "would_close": sum(d["count"] - 1 for d in dupes) + len(low_value),
    }

    if not dry_run:
        closed = 0
        # Close duplicate tabs (keep first, close rest)
        for dupe in dupes:
            closed += close_tabs(dupe["url"])["total_closed"] - 1
        # Close low-value tabs
        for pattern in low_value_patterns:
            closed += close_tabs(pattern)["total_closed"]
        report["actually_closed"] = closed

    return report


# ── Nuke (snapshot + close all) ─────────────────────────────────────────────

def nuke(browser: Optional[str] = None) -> dict:
    """Snapshot ALL tabs then close ALL. The nuclear option for RAM recovery."""
    tabs = get_all_tabs()
    if browser:
        tabs = [t for t in tabs if t["browser"] == browser]

    if not tabs:
        return {"error": "No tabs to nuke"}

    # Measure RAM before
    import time
    ram_before = _get_memory_macos() if SYSTEM == "Darwin" else []
    ram_before_total = sum(m["memory_mb"] for m in ram_before)

    # Save snapshot first
    name = f"nuke-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path = save_snapshot(name, tabs)

    # Close all tabs (except one per browser to keep browser open)
    closed = 0
    browsers_seen = set()
    for tab in reversed(tabs):
        if tab["browser"] not in browsers_seen:
            browsers_seen.add(tab["browser"])
            continue  # Keep one tab per browser
        try:
            result = close_tabs(tab["url"])
            closed += result["total_closed"]
        except Exception:
            pass

    # Measure RAM after (give browsers a moment to release memory)
    time.sleep(2)
    ram_after = _get_memory_macos() if SYSTEM == "Darwin" else []
    ram_after_total = sum(m["memory_mb"] for m in ram_after)
    ram_saved = ram_before_total - ram_after_total

    return {
        "snapshot_saved": str(path),
        "snapshot_name": name,
        "tabs_captured": len(tabs),
        "tabs_closed": closed,
        "ram_before_mb": round(ram_before_total, 1),
        "ram_after_mb": round(ram_after_total, 1),
        "ram_saved_mb": round(ram_saved, 1),
    }


# ── Tab Statistics ──────────────────────────────────────────────────────────

def tab_stats(tabs: Optional[list[dict]] = None) -> dict:
    """Generate tab statistics: per-browser counts, domain frequency, estimated RAM."""
    if tabs is None:
        tabs = get_all_tabs()

    # Per-browser counts
    browser_counts: dict[str, int] = {}
    for t in tabs:
        browser_counts[t["browser"]] = browser_counts.get(t["browser"], 0) + 1

    # Domain frequency
    domain_counts: dict[str, int] = {}
    for t in tabs:
        try:
            domain = urlparse(t["url"]).netloc
            if domain:
                # Simplify: strip www and subdomains for grouping
                parts = domain.split(".")
                if len(parts) > 2:
                    domain = ".".join(parts[-2:])
                domain_counts[domain] = domain_counts.get(domain, 0) + 1
        except Exception:
            pass

    # Top domains sorted by count
    top_domains = sorted(domain_counts.items(), key=lambda x: -x[1])[:10]

    # Estimated RAM (75 MB per tab average from benchmarks)
    est_ram_mb = len(tabs) * 75

    return {
        "total_tabs": len(tabs),
        "per_browser": browser_counts,
        "top_domains": top_domains,
        "estimated_ram_mb": est_ram_mb,
    }


# ── Domain Categorisation ──────────────────────────────────────────────

# Ordered list — first match wins
CATEGORY_RULES = [
    (r"github\.com/.+/pull/", "GitHub PR"),
    (r"github\.com/.+/issues/", "GitHub Issue"),
    (r"github\.com", "GitHub"),
    (r"stackoverflow\.com/questions/", "Stack Overflow"),
    (r"(^|\.)google\.[a-z.]+/search", "Google Search"),
    (r"(^docs\.|/docs/)", "Documentation"),
    (r"youtube\.com", "Video"),
    (r"linkedin\.com", "Social"),
]

# Pre-compile for performance
_COMPILED_RULES = [(re.compile(pattern), cat) for pattern, cat in CATEGORY_RULES]


def categorise_url(url: str) -> str:
    """Categorise a single URL. Returns category name or domain."""
    try:
        parsed = urlparse(url)
        host = parsed.netloc or ""
        path = parsed.path or ""
        full = host + path
    except Exception:
        return "Other"

    for regex, category in _COMPILED_RULES:
        if regex.search(full):
            return category

    # Fall back to simplified domain
    parts = host.split(".")
    if len(parts) > 2:
        return ".".join(parts[-2:])
    return host or "Other"


def categorise_tabs(tabs: Optional[list[dict]] = None) -> dict[str, list[dict]]:
    """Group tabs by automatically detected category."""
    if tabs is None:
        tabs = get_all_tabs()
    groups: dict[str, list[dict]] = {}
    for tab in tabs:
        cat = categorise_url(tab.get("url", ""))
        groups.setdefault(cat, []).append(tab)
    return groups


# ── Tab Archaeology (tab aging) ────────────────────────────────────────

# Chrome epoch: microseconds since 1601-01-01 00:00:00 UTC
_CHROME_EPOCH_OFFSET = 11644473600  # seconds between 1601 and 1970

# Browser history database locations (macOS)
_HISTORY_DB_PATHS = {
    "Google Chrome": Path.home() / "Library/Application Support/Google/Chrome/Default/History",
    "Brave Browser": Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser/Default/History",
}

# Age bands with ANSI colours
AGE_BANDS = [
    (1, "fresh", "\033[92m"),       # green
    (7, "recent", "\033[96m"),      # cyan
    (30, "aging", "\033[93m"),      # yellow
    (90, "stale", "\033[91m"),      # red
    (float("inf"), "fossil", "\033[95m"),  # magenta
]
_RESET = "\033[0m"


def _age_band(days: float) -> tuple[str, str]:
    """Return (band_name, ansi_colour) for a given age in days."""
    for threshold, name, colour in AGE_BANDS:
        if days < threshold:
            return name, colour
    return "fossil", "\033[95m"


def _query_history_db(db_path: Path, urls: list[str]) -> dict[str, float]:
    """Query a Chrome/Brave History SQLite DB for last_visit_time of given URLs.

    Returns {url: age_in_days}. Copies the DB first to avoid lock conflicts.
    """
    if not db_path.exists():
        return {}

    url_ages: dict[str, float] = {}
    tmp_db = None
    try:
        # Copy to temp to avoid browser lock
        tmp_fd, tmp_db = tempfile.mkstemp(suffix=".sqlite")
        os.close(tmp_fd)
        shutil.copy2(str(db_path), tmp_db)

        conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)

        for url in urls:
            cursor.execute(
                "SELECT last_visit_time FROM urls WHERE url = ? ORDER BY last_visit_time DESC LIMIT 1",
                (url,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                # Convert Chrome timestamp to datetime
                chrome_ts = row[0]
                unix_ts = (chrome_ts / 1_000_000) - _CHROME_EPOCH_OFFSET
                visit_dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
                age_days = (now - visit_dt).total_seconds() / 86400
                url_ages[url] = max(0, age_days)

        conn.close()
    except (sqlite3.Error, OSError):
        pass
    finally:
        if tmp_db and os.path.exists(tmp_db):
            os.unlink(tmp_db)

    return url_ages


def _merge_history_ages(urls: list[str]) -> dict[str, float]:
    """Query every configured history DB and keep the minimum age per URL.

    Each DB is queried independently and its per-URL ages are folded into
    the accumulator via :func:`dict.update` with a min-guard — flattens
    what was previously a doubly-nested loop into a single-level one.
    """
    all_ages: dict[str, float] = {}
    for _browser, db_path in _HISTORY_DB_PATHS.items():
        ages = _query_history_db(db_path, urls)
        _fold_min_ages(all_ages, ages)
    return all_ages


def _fold_min_ages(accumulator: dict[str, float], incoming: dict[str, float]) -> None:
    """Merge ``incoming`` into ``accumulator`` keeping the smaller age per URL.

    Single-level loop — the outer caller iterates DBs, this helper does
    one pass over the per-DB ages, so the two dimensions never appear in
    the same function body.
    """
    for url, age in incoming.items():
        if url not in accumulator or age < accumulator[url]:
            accumulator[url] = age


def tab_archaeology(tabs: Optional[list[dict]] = None) -> dict:
    """Cross-reference open tabs with browser history to estimate tab age.

    Returns a report with per-tab age data and summary counts per band.
    """
    if tabs is None:
        tabs = get_all_tabs()

    urls = [t.get("url", "") for t in tabs]

    all_ages = _merge_history_ages(urls)

    # Build per-tab report
    tab_report = []
    band_counts: dict[str, int] = {}
    for tab in tabs:
        url = tab.get("url", "")
        age_days = all_ages.get(url)
        if age_days is not None:
            band_name, colour = _age_band(age_days)
        else:
            age_days = -1
            band_name = "unknown"
            colour = "\033[90m"  # grey

        band_counts[band_name] = band_counts.get(band_name, 0) + 1
        tab_report.append({
            "browser": tab.get("browser", "unknown"),
            "title": tab.get("title", ""),
            "url": url,
            "age_days": round(age_days, 1) if age_days >= 0 else None,
            "band": band_name,
            "colour": colour,
        })

    return {
        "total": len(tabs),
        "tabs": tab_report,
        "summary": band_counts,
    }


# ── Tab Bankruptcy ─────────────────────────────────────────────────────

def _format_tab_md_line(tab: dict) -> str:
    """Format a single tab as a markdown bullet. Extracted so the
    bankruptcy generator stays a single-level loop."""
    title = tab.get("title", "Untitled")
    url = tab.get("url", "")
    space = tab.get("space", "")
    space_note = f" [{space}]" if space else ""
    return f"- [{title}]({url}){space_note}"


def _format_browser_md_section(browser: str, btabs: list[dict]) -> list[str]:
    """Produce the markdown section for one browser (header + tab bullets + blank)."""
    header = f"## {browser} ({len(btabs)} tabs)\n"
    return [header, *map(_format_tab_md_line, btabs), ""]


def _generate_bankruptcy_markdown(
    tabs: list[dict],
    by_browser: dict[str, list[dict]],
) -> str:
    """Generate a markdown archive of all tabs grouped by browser."""
    md_lines = [
        f"# Tab Bankruptcy — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"\nTotal tabs archived: {len(tabs)}\n",
    ]
    for browser, btabs in sorted(by_browser.items()):
        md_lines.extend(_format_browser_md_section(browser, btabs))
    return "\n".join(md_lines)


def _group_tabs_by_browser(tabs: list[dict]) -> dict[str, list[dict]]:
    """Bucket tabs by their ``browser`` field. Preserves insertion order."""
    by_browser: dict[str, list[dict]] = {}
    for tab in tabs:
        by_browser.setdefault(tab["browser"], []).append(tab)
    return by_browser


def _bankruptcy_ram_total_mb() -> float:
    """Return total RAM used by tracked browsers in MB, 0.0 on non-macOS."""
    if SYSTEM != "Darwin":
        return 0.0
    samples = _get_memory_macos()
    return sum(m["memory_mb"] for m in samples)


def _bankruptcy_close_all_except_one_per_browser(tabs: list[dict]) -> int:
    """Close every tab except the last per browser. Returns total closed count.

    Walks the tab list in reverse so the 'last' kept tab per browser is the
    one that was most recently active — matches the user expectation that
    bankruptcy preserves the newest work.
    """
    closed = 0
    browsers_kept: set[str] = set()
    for tab in reversed(tabs):
        if tab["browser"] not in browsers_kept:
            browsers_kept.add(tab["browser"])
            continue
        try:
            r = close_tabs(tab["url"])
            closed += r["total_closed"]
        except Exception:
            pass
    return closed


def tab_bankruptcy(dry_run: bool = True) -> dict:
    """Archive all tabs to markdown, then close all except one per browser."""
    tabs = get_all_tabs()
    if not tabs:
        return {"error": "No tabs to archive"}

    by_browser = _group_tabs_by_browser(tabs)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    md_content = _generate_bankruptcy_markdown(tabs, by_browser)
    md_path = SNAPSHOTS_DIR / f"bankruptcy-{ts}.md"

    ram_before_total = _bankruptcy_ram_total_mb()
    result = {
        "tabs_archived": len(tabs),
        "browsers": list(by_browser.keys()),
        "markdown_path": str(md_path),
        "markdown_content": md_content,
        "ram_before_mb": round(ram_before_total, 1),
        "dry_run": dry_run,
    }

    if dry_run:
        return result

    md_path.write_text(md_content)
    save_snapshot(f"bankruptcy-{ts}", tabs)
    result["tabs_closed"] = _bankruptcy_close_all_except_one_per_browser(tabs)

    import time
    time.sleep(2)
    ram_after_total = _bankruptcy_ram_total_mb()
    result["ram_after_mb"] = round(ram_after_total, 1)
    result["ram_saved_mb"] = round(ram_before_total - ram_after_total, 1)

    return result


# ── CLI ─────────────────────────────────────────────────────────────────────

def _format_tabs(tabs: list[dict]) -> str:
    """Format tabs for terminal display.

    Pre-computes per-browser counts in O(n) so the header line does not
    trigger a second O(n) scan per tab (the prior implementation was O(n²)).
    """
    if not tabs:
        return "  No tabs found."
    from collections import Counter
    browser_counts = Counter(tab["browser"] for tab in tabs)
    lines = []
    current_browser = ""
    for tab in tabs:
        if tab["browser"] != current_browser:
            current_browser = tab["browser"]
            lines.append(f"\n  {current_browser} ({browser_counts[current_browser]} tabs)")
            lines.append(f"  {'─' * 60}")
        title = tab["title"][:55] + "..." if len(tab["title"]) > 55 else tab["title"]
        lines.append(f"    {tab.get('tab_index', '?'):>2}. {title}")
        lines.append(f"        {tab['url'][:70]}")
    return "\n".join(lines)


def _cmd_list():
    tabs = get_all_tabs()
    print(f"\n  Open tabs: {len(tabs)}")
    print(_format_tabs(tabs))


def _cmd_snapshot():
    name = sys.argv[2] if len(sys.argv) > 2 else None
    tabs = get_all_tabs()
    path = save_snapshot(name, tabs)
    print(f"  Snapshot saved: {path.name} ({len(tabs)} tabs)")


def _cmd_restore():
    if len(sys.argv) < 3:
        print("  Usage: tab_manager.py restore <snapshot-name>")
        return
    result = restore_snapshot(sys.argv[2])
    if "error" in result:
        print(f"  Error: {result['error']}")
    else:
        print(f"  Restored {result['tabs_restored']}/{result['total_in_snapshot']} tabs from '{result['snapshot']}'")


def _cmd_close():
    if len(sys.argv) < 3:
        print("  Usage: tab_manager.py close <url-pattern>")
        return
    result = close_tabs(sys.argv[2])
    print(f"  Closed {result['total_closed']} tabs matching '{sys.argv[2]}'")
    for browser, count in result["per_browser"].items():
        print(f"    {browser}: {count}")


def _cmd_gc():
    dry = "--force" not in sys.argv
    report = garbage_collect(dry_run=dry)
    print(f"\n  Tab audit: {report['total_tabs']} total")
    print(f"  Duplicates: {report['duplicates']}")
    print(f"  Low-value: {report['low_value']}")
    if dry:
        print(f"  Would close: {report['would_close']} tabs")
        print(f"  Run with --force to execute")
    else:
        print(f"  Closed: {report.get('actually_closed', 0)} tabs")


def _cmd_search():
    if len(sys.argv) < 3:
        print("  Usage: tab_manager.py search <query>")
        return
    results = search_snapshots(sys.argv[2])
    print(f"\n  Found {len(results)} matching tabs across snapshots:")
    for r in results[:20]:
        print(f"    [{r['snapshot']}] {r['title'][:50]}")
        print(f"      {r['url'][:70]}")


def _cmd_snapshots():
    snaps = list_snapshots()
    if not snaps:
        print("  No snapshots saved yet.")
        return
    print(f"\n  Saved snapshots ({len(snaps)}):")
    for s in snaps:
        print(f"    {s['name']:30s}  {s['tab_count']:>3} tabs  {s['created_at'][:10]}  {s['size_kb']}KB")


def _cmd_memory():
    if SYSTEM != "Darwin":
        print("  Memory reporting currently macOS only.")
        return
    mem = _get_memory_macos()
    total = sum(m["memory_mb"] for m in mem)
    print(f"\n  Browser memory usage: {total:.0f} MB total")
    for m in mem:
        bar = "\u2588" * int(m["memory_mb"] / 100)
        print(f"    {m['browser']:20s}  {m['memory_mb']:>8.1f} MB  {bar}")


def _cmd_nuke():
    browser = sys.argv[2] if len(sys.argv) > 2 else None
    result = nuke(browser)
    if "error" in result:
        print(f"  {result['error']}")
    else:
        print(f"\n  Snapshot saved: {result['snapshot_name']}")
        print(f"  Tabs captured: {result['tabs_captured']}")
        print(f"  Tabs closed: {result['tabs_closed']}")
        if result.get("ram_saved_mb", 0) > 0:
            print(f"\n  RAM before: {result['ram_before_mb']:.0f} MB")
            print(f"  RAM after:  {result['ram_after_mb']:.0f} MB")
            print(f"  RAM saved:  {result['ram_saved_mb']:.0f} MB")
        print(f"\n  Restore with: /tabs restore {result['snapshot_name']}")


_HLINE_45 = "\u2500" * 45
_HLINE_55 = "\u2500" * 55


def _cmd_stats():
    stats = tab_stats()
    print(f"\n  Tab Statistics")
    print(f"  {_HLINE_45}")
    print(f"  Total tabs: {stats['total_tabs']}")
    print(f"  Estimated RAM: ~{stats['estimated_ram_mb']} MB ({stats['estimated_ram_mb'] / 1024:.1f} GB)")
    print(f"\n  Per browser:")
    for browser, count in sorted(stats["per_browser"].items(), key=lambda x: -x[1]):
        print(f"    {browser:20s}  {count:>3} tabs")
    print(f"\n  Top domains:")
    for domain, count in stats["top_domains"]:
        bar = "\u25aa" * count
        print(f"    {domain:25s}  {count:>2}  {bar}")


def _cmd_archaeology():
    report = tab_archaeology()
    print(f"\n  Tab Archaeology \u2014 {report['total']} tabs analysed")
    print(f"  {_HLINE_55}")
    for entry in report["tabs"]:
        age_str = f"{entry['age_days']:.0f}d" if entry["age_days"] is not None else "??"
        colour = entry["colour"]
        print(f"    {colour}[{entry['band']:>7s} {age_str:>4s}]{_RESET}  {entry['title'][:45]}")
    print(f"\n  Summary:")
    for band, count in sorted(report["summary"].items()):
        print(f"    {band:>10s}: {count}")


def _print_category_section(cat: str, cat_tabs: list[dict]) -> None:
    """Print one category: header, up to 10 titles, 'and N more' trailer."""
    print(f"\n  {cat} ({len(cat_tabs)})")
    for tab in cat_tabs[:10]:
        title = tab["title"][:50] + "..." if len(tab["title"]) > 50 else tab["title"]
        print(f"    - {title}")
    if len(cat_tabs) > 10:
        print(f"    ... and {len(cat_tabs) - 10} more")


def _cmd_categorise():
    groups = categorise_tabs()
    total = sum(len(tabs) for tabs in groups.values())
    print(f"\n  Tab Categories \u2014 {total} tabs")
    print(f"  {_HLINE_55}")
    for cat, cat_tabs in sorted(groups.items(), key=lambda x: -len(x[1])):
        _print_category_section(cat, cat_tabs)


def _cmd_bankrupt():
    dry = "--dry-run" in sys.argv
    if not dry:
        # Default to dry-run for safety
        dry = "--force" not in sys.argv
    result = tab_bankruptcy(dry_run=dry)
    if "error" in result:
        print(f"  {result['error']}")
    else:
        print(f"\n  Tab Bankruptcy {'(DRY RUN)' if dry else ''}")
        print(f"  {_HLINE_45}")
        print(f"  Tabs to archive: {result['tabs_archived']}")
        print(f"  Browsers: {', '.join(result['browsers'])}")
        if dry:
            print(f"\n  Preview:\n{result['markdown_content'][:500]}")
            print(f"\n  Run with --force to execute")
        else:
            print(f"  Tabs closed: {result.get('tabs_closed', 0)}")
            print(f"  Markdown saved: {result['markdown_path']}")
            if result.get("ram_saved_mb", 0) > 0:
                print(f"  RAM saved: {result['ram_saved_mb']:.0f} MB")


# Command dispatch table — maps CLI verbs to handler functions
_COMMANDS = {
    "list": _cmd_list,
    "snapshot": _cmd_snapshot,
    "restore": _cmd_restore,
    "close": _cmd_close,
    "gc": _cmd_gc,
    "search": _cmd_search,
    "snapshots": _cmd_snapshots,
    "memory": _cmd_memory,
    "nuke": _cmd_nuke,
    "stats": _cmd_stats,
    "archaeology": _cmd_archaeology,
    "categorise": _cmd_categorise,
    "categorize": _cmd_categorise,  # US English alias
    "bankrupt": _cmd_bankrupt,
}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()
    handler = _COMMANDS.get(cmd)
    if handler is None:
        print(f"  Unknown command: {cmd}")
        print(__doc__)
        return
    handler()


if __name__ == "__main__":
    main()
