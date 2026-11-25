"""
Integration adapters for company copilot.

Provides unified interfaces to external services:
- SlackAdapter: Uses existing Slack MCP tools / direct API
- GitHubAdapter: Uses gh CLI
- LocalDataAdapter: Reads company.json, decision-log, KPIs
Reuses patterns from scripts/daily-team-digest.py for Slack API access.
"""

import json
import logging
import os
import re
import subprocess
import time
import urllib.request
import urllib.error
import urllib.parse
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


def _col_width(rows: List[List[str]], col_idx: int) -> int:
    widths = []
    for row in rows:
        if col_idx < len(row):
            widths.append(len(row[col_idx]))
    return max(widths) if widths else 0


def _md_table_to_text(table_lines: List[str]) -> str:
    """Convert markdown table lines to aligned Slack-readable text.

    Strips separator rows (|---|---|) and formats remaining rows
    as fixed-width columns using Slack code block for alignment.
    """
    rows = []
    for line in table_lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        # Skip separator rows (|---|---|)
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.match(r'^[-:]+$', c) for c in cells):
            continue
        rows.append(cells)

    if not rows:
        return ""

    col_count = max(len(r) for r in rows)
    widths = [_col_width(rows, i) for i in range(col_count)]

    def _format_row(row: List[str]) -> str:
        cells = [(row[i] if i < len(row) else "").ljust(widths[i]) for i in range(col_count)]
        return "  ".join(cells)

    formatted = [_format_row(rows[0]), "  ".join("-" * w for w in widths)]
    formatted.extend(_format_row(r) for r in rows[1:])

    return "\n".join(formatted)


def _md_header_to_text(line: str) -> str:
    """Convert markdown header to Slack bold text."""
    m = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
    if m:
        return m.group(2)
    return line


_DIVIDER_RE = re.compile(r'^[-_]{3,}\s*$')
_HEADER_RE = re.compile(r'^#{1,3}\s+')


def _is_table_line(stripped: str) -> bool:
    return stripped.startswith("|") and "|" in stripped[1:]


def _is_special_line(stripped: str) -> bool:
    if not stripped:
        return True
    return bool(_DIVIDER_RE.match(stripped)) or bool(_HEADER_RE.match(stripped)) or _is_table_line(stripped)


def _build_header_block(stripped: str) -> Dict:
    header_text = _md_header_to_text(stripped)
    if len(header_text) <= 150:
        return {"type": "header", "text": {"type": "plain_text", "text": header_text[:150]}}
    return {"type": "section", "text": {"type": "mrkdwn", "text": f"*{header_text[:3000]}*"}}


def _build_table_block(table_lines: List[str]) -> Optional[Dict]:
    table_text = _md_table_to_text(table_lines)
    if not table_text:
        return None
    return {"type": "section", "text": {"type": "mrkdwn", "text": f"```\n{table_text[:2990]}\n```"}}


def _build_text_block(text_lines: List[str]) -> Optional[Dict]:
    if not text_lines:
        return None
    chunk = re.sub(r'\*\*(.+?)\*\*', r'*\1*', "\n".join(text_lines))
    return {"type": "section", "text": {"type": "mrkdwn", "text": chunk[:3000]}}


def _collect_until(lines: List[str], start: int, predicate) -> tuple:
    """Walk lines from start while predicate(stripped_line) holds. Returns (collected, next_index)."""
    i = start
    collected: List[str] = []
    while i < len(lines) and predicate(lines[i].strip()):
        collected.append(lines[i])
        i += 1
    return collected, i


def text_to_blocks(text: str) -> List[Dict]:
    """Convert markdown text to Slack Block Kit blocks.

    Handles headers, tables, dividers, regular text, and **bold** -> *bold*.
    """
    blocks: List[Dict] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if _DIVIDER_RE.match(stripped):
            blocks.append({"type": "divider"})
            i += 1
            continue
        if _HEADER_RE.match(stripped):
            blocks.append(_build_header_block(stripped))
            i += 1
            continue
        if _is_table_line(stripped):
            table_lines, i = _collect_until(lines, i, lambda s: s.startswith("|"))
            block = _build_table_block(table_lines)
            if block:
                blocks.append(block)
            continue
        text_lines, i = _collect_until(lines, i, lambda s: not _is_special_line(s))
        block = _build_text_block(text_lines)
        if block:
            blocks.append(block)
    return blocks[:50]


def _resolve_mcp_env_file() -> Path:
    """Locate the Desktop MCP .env, preferring the renamed dir.

    Falls back to the legacy "grip-mcp" path so installed Desktop/Docker
    configs pinned to the old layout keep resolving (the in-tree dir symlink
    covers old->new for checkouts that have it).
    """
    mcp_dir = CLAUDE_DIR / "mcp-servers"
    new_path = mcp_dir / "grip-mcp-desktop" / ".env"
    if new_path.exists():
        return new_path
    return mcp_dir / "grip-mcp" / ".env"


ENV_FILE = _resolve_mcp_env_file()


def load_slack_token() -> str:
    """Load Slack bot token from grip-mcp .env file."""
    if not ENV_FILE.exists():
        return ""
    with open(ENV_FILE) as f:
        for line in f:
            if line.startswith("SLACK_BOT_TOKEN="):
                return line.strip().split("=", 1)[1]
    return ""


MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0


def _retry_on_transient(func, *args, context: str = "", max_retries: int = MAX_RETRIES, **kwargs) -> dict:
    """Retry a function with exponential backoff on transient network errors.

    Args:
        func: Callable that returns a dict
        context: Description for logging
        max_retries: Maximum number of attempts

    Returns:
        Result dict from func, or error dict after exhausting retries
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            result = func(*args, **kwargs)
            # Retry on Slack rate limit errors
            if not result.get("ok") and result.get("error") == "ratelimited":
                if attempt < max_retries - 1:
                    delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
                    logger.warning("%s rate limited, retrying in %.1fs (attempt %d/%d)",
                                   context, delay, attempt + 1, max_retries)
                    time.sleep(delay)
                    continue
            return result
        except urllib.error.URLError as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
                logger.warning("%s network error (attempt %d/%d), retrying in %.1fs: %s",
                               context, attempt + 1, max_retries, delay, e)
                time.sleep(delay)
            continue

    logger.error("%s failed after %d attempts: %s", context, max_retries, last_error)
    return {"ok": False, "error": f"network_error after {max_retries} retries"}


def _slack_api_get_once(token: str, method: str, params: dict) -> dict:
    """Single Slack API GET request (no retry)."""
    query = urllib.parse.urlencode(params)
    url = f"https://slack.com/api/{method}?{query}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"}
    )
    resp = urllib.request.urlopen(req, timeout=30)
    body = resp.read().decode("utf-8")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error("Slack GET %s returned non-JSON (status %s): %s", method, resp.status, e)
        return {"ok": False, "error": f"json_parse_error: {e}"}
    if not result.get("ok"):
        logger.warning("Slack GET %s failed: %s", method, result.get("error"))
    return result


def slack_api_get(token: str, method: str, params: dict) -> dict:
    """Make a Slack API GET request with retry/backoff."""
    return _retry_on_transient(
        _slack_api_get_once, token, method, params,
        context=f"Slack GET {method}",
    )


def _slack_api_post_once(token: str, method: str, payload: dict) -> dict:
    """Single Slack API POST request (no retry)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    resp = urllib.request.urlopen(req, timeout=30)
    body = resp.read().decode("utf-8")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error("Slack POST %s returned non-JSON (status %s): %s", method, resp.status, e)
        return {"ok": False, "error": f"json_parse_error: {e}"}
    if not result.get("ok"):
        logger.warning("Slack POST %s failed: %s", method, result.get("error"))
    return result


def slack_api_post(token: str, method: str, payload: dict) -> dict:
    """Make a Slack API POST request with retry/backoff."""
    return _retry_on_transient(
        _slack_api_post_once, token, method, payload,
        context=f"Slack POST {method}",
    )


class IntegrationAdapter(ABC):
    """Base class for integration adapters."""

    @abstractmethod
    def fetch(self, resource: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """Fetch data from the integration."""

    @abstractmethod
    def available(self) -> bool:
        """Check if the integration is available."""

    def get_details(self) -> Dict[str, Any]:
        """Get adapter-specific details for status reporting.

        Override in subclasses to provide additional status info.
        Default returns just availability status.
        """
        return {"available": self.available()}


class SlackAdapter(IntegrationAdapter):
    """
    Slack integration via direct API calls.

    Reuses the Slack API pattern from daily-team-digest.py.
    """

    def __init__(self, token: str = ""):
        self.token = token or load_slack_token()

    def available(self) -> bool:
        return bool(self.token)

    def get_details(self) -> Dict[str, Any]:
        """Return Slack-specific status details."""
        info = {"available": self.available()}
        if info["available"] and self.token:
            info["token_prefix"] = self.token[:8] + "..."
        return info

    def fetch(self, resource: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        params = params or {}
        if resource == "channel_history":
            return self._fetch_channel_history(
                params.get("channel_id", ""),
                params.get("hours", 24),
                params.get("limit", 20),
            )
        elif resource == "user_info":
            return self._fetch_user_info(params.get("user_id", ""))
        return {"error": f"Unknown resource: {resource}"}

    def _fetch_channel_history(
        self, channel_id: str, hours: int = 24, limit: int = 20
    ) -> Dict:
        """Fetch recent channel messages."""
        oldest = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
        result = slack_api_get(
            self.token,
            "conversations.history",
            {"channel": channel_id, "oldest": str(oldest), "limit": limit},
        )
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error", "unknown")}

        messages = result.get("messages", [])
        messages.reverse()  # Chronological order
        return {"ok": True, "messages": messages}

    def _fetch_user_info(self, user_id: str) -> Dict:
        """Fetch user profile info."""
        result = slack_api_get(self.token, "users.info", {"user": user_id})
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error", "unknown")}
        user = result["user"]
        return {
            "ok": True,
            "name": (
                user.get("profile", {}).get("display_name")
                or user.get("profile", {}).get("real_name")
                or user.get("name", user_id)
            ),
            "user": user,
        }

    def _build_message_payload(
        self, channel: str, text: str,
        metadata: Optional[Dict] = None, blocks: Optional[List] = None,
    ) -> Dict:
        """Build a Slack chat.postMessage payload."""
        payload: Dict[str, Any] = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        if metadata:
            payload["metadata"] = {
                "event_type": "copilot_message",
                "event_payload": metadata,
            }
        return payload

    def _post_and_format(self, payload: Dict) -> Dict[str, Any]:
        """Post a message and return standardized result."""
        result = slack_api_post(self.token, "chat.postMessage", payload)
        if result.get("ok"):
            return {
                "ok": True,
                "ts": result.get("ts"),
                "channel": result.get("channel"),
            }
        return {"ok": False, "error": result.get("error", "unknown")}

    def send_dm(self, user_id: str, text: str, metadata: Optional[Dict] = None, blocks: Optional[List] = None) -> Dict[str, Any]:
        """Send a direct message to a user."""
        # Open DM channel
        result = slack_api_post(
            self.token, "conversations.open", {"users": user_id}
        )
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error", "unknown")}

        dm_channel = result["channel"]["id"]
        payload = self._build_message_payload(dm_channel, text, metadata, blocks)
        return self._post_and_format(payload)

    def post_channel(self, channel_id: str, text: str, metadata: Optional[Dict] = None, blocks: Optional[List] = None) -> Dict[str, Any]:
        """Post a message to a channel."""
        payload = self._build_message_payload(channel_id, text, metadata, blocks)
        return self._post_and_format(payload)

    def respond_to_thread(self, channel_id: str, thread_ts: str, text: str) -> bool:
        """Reply to a Slack thread."""
        payload = {
            "channel": channel_id,
            "thread_ts": thread_ts,
            "text": text,
        }
        blocks = text_to_blocks(text)
        if blocks:
            payload["blocks"] = blocks
        result = slack_api_post(self.token, "chat.postMessage", payload)
        return result.get("ok", False)

    def fetch_thread_replies(
        self,
        channel_id: str,
        thread_ts: str,
        limit: int = 50,
        oldest: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch replies in a thread using conversations.replies API."""
        params = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": limit,
        }
        if oldest:
            params["oldest"] = oldest

        result = slack_api_get(self.token, "conversations.replies", params)
        if result.get("ok"):
            return {
                "ok": True,
                "messages": result.get("messages", []),
            }
        return {"ok": False, "error": result.get("error", "unknown")}


class GitHubAdapter(IntegrationAdapter):
    """
    GitHub integration via gh CLI.
    """

    def available(self) -> bool:
        try:
            result = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def fetch(self, resource: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        params = params or {}
        if resource == "notifications":
            return self._fetch_notifications(params.get("repo", ""))
        elif resource == "prs":
            return self._fetch_prs(params.get("repo", ""))
        elif resource == "issues":
            return self._fetch_issues(params.get("repo", ""))
        elif resource == "merged_today":
            return self._fetch_merged_today(params.get("repo", ""))
        elif resource == "dependabot_alerts":
            return self._fetch_dependabot_alerts(params.get("repo", ""))
        return {"error": f"Unknown resource: {resource}"}

    def _fetch_dependabot_alerts(self, repo: str) -> Dict:
        if not repo:
            repo = self._default_repo()
        if not repo:
            return {"ok": False, "error": "No repo specified"}
        result = self._safe_subprocess_call(
            ["gh", "api", f"repos/{repo}/dependabot/alerts", "--jq", ".[].security_advisory.summary"],
            "dependabot_alerts",
        )
        if not result.get("ok"):
            return {"ok": True, "alerts": [], "note": "Dependabot not available or no alerts"}
        alerts = [line for line in result.get("stdout", "").splitlines() if line.strip()]
        return {"ok": True, "alerts": alerts, "count": len(alerts)}

    def _safe_subprocess_call(
        self, cmd: List[str], context: str = "gh command", timeout: int = 30
    ) -> Dict:
        """Execute a subprocess command with consistent error handling.

        Args:
            cmd: Command and arguments to execute
            context: Description for logging (e.g., "notifications", "pr list")
            timeout: Timeout in seconds

        Returns:
            Dict with 'ok' and either 'data'/'stdout' or 'error'
        """
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode != 0:
                return {"ok": False, "error": result.stderr.strip()}
            return {"ok": True, "stdout": result.stdout}
        except FileNotFoundError:
            return {"ok": False, "error": "gh CLI not found"}
        except subprocess.TimeoutExpired:
            logger.warning("gh %s timed out after %ds", context, timeout)
            return {"ok": False, "error": f"timeout after {timeout}s"}

    def _parse_json_output(self, stdout: str, context: str = "gh output") -> Dict:
        """Parse JSON from subprocess stdout with error handling."""
        try:
            data = json.loads(stdout) if stdout.strip() else []
            return {"ok": True, "data": data}
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse %s JSON: %s", context, e)
            return {"ok": False, "error": f"json_parse_error: {e}"}

    def _run_gh(self, args: List[str]) -> Dict:
        """Run a gh CLI command and parse JSON output."""
        result = self._safe_subprocess_call(["gh"] + args + ["--json"], "gh " + args[0])
        if not result.get("ok"):
            return result
        return self._parse_json_output(result["stdout"], "gh " + args[0])

    def _fetch_notifications(self, repo: str = "") -> Dict:
        result = self._safe_subprocess_call(
            ["gh", "api", "notifications"], "notifications"
        )
        if not result.get("ok"):
            return result

        parsed = self._parse_json_output(result["stdout"], "notifications")
        if not parsed.get("ok"):
            return parsed

        data = parsed["data"]
        # Filter by repo in Python (safe from injection vs --jq interpolation)
        if repo:
            data = [n for n in data if n.get("repository", {}).get("full_name") == repo]
        return {"ok": True, "notifications": data}

    def _default_repo(self) -> str:
        """Auto-resolve default repo from company.json integrations config."""
        try:
            from .company import CompanyProfile
            company = CompanyProfile.load()
            integrations = company.raw_data.get("integrations", {})
            gh = integrations.get("github", {})
            org = gh.get("org", "")
            repos = gh.get("repos", [])
            if org and repos:
                return f"{org}/{repos[0]}"
        except Exception:
            pass
        return ""

    def _resolve_repo(self, repo: str) -> Optional[str]:
        return repo or self._default_repo() or None

    def _run_gh_json(self, argv: List[str], ctx: str, data_key: str) -> Dict:
        result = self._safe_subprocess_call(argv, ctx)
        if not result.get("ok"):
            return result
        parsed = self._parse_json_output(result["stdout"], ctx)
        if not parsed.get("ok"):
            return parsed
        return {"ok": True, data_key: parsed["data"]}

    def _fetch_prs(self, repo: str) -> Dict:
        repo = self._resolve_repo(repo)
        if not repo:
            return {"ok": False, "error": "repo required"}
        return self._run_gh_json(
            ["gh", "pr", "list", "-R", repo, "--json",
             "number,title,author,state,createdAt,updatedAt"],
            f"pr list {repo}",
            "prs",
        )

    def _fetch_issues(self, repo: str) -> Dict:
        repo = self._resolve_repo(repo)
        if not repo:
            return {"ok": False, "error": "repo required"}
        return self._run_gh_json(
            ["gh", "issue", "list", "-R", repo, "--json",
             "number,title,author,state,createdAt,labels"],
            f"issue list {repo}",
            "issues",
        )

    def _fetch_merged_today(self, repo: str) -> Dict:
        """Fetch PRs merged today (last 24 hours)."""
        repo = self._resolve_repo(repo)
        if not repo:
            return {"ok": False, "error": "repo required"}
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d")
        return self._run_gh_json(
            ["gh", "pr", "list", "-R", repo, "--state", "merged",
             "--search", f"merged:>={since}",
             "--json", "number,title,author,mergedAt"],
            f"merged prs {repo}",
            "prs",
        )


class LocalDataAdapter(IntegrationAdapter):
    """
    Local data adapter for company.json, decision-log, KPIs.
    """

    def __init__(self, copilot_dir: Optional[Path] = None):
        self.copilot_dir = copilot_dir or Path.home() / ".claude" / "data" / "aide"

    def available(self) -> bool:
        return self.copilot_dir.exists()

    def get_details(self) -> Dict[str, Any]:
        """Return local data adapter status details."""
        info = {"available": self.available()}
        if info["available"]:
            info["data_dir"] = str(self.copilot_dir)
        return info

    # Dispatch table: resource name -> callable(self, params) -> Dict
    # Lambdas are used for fixed-arg calls; methods are passed directly for
    # variable-param calls. All handlers receive (self, params).
    _RESOURCE_HANDLERS: Dict[str, Any] = {}  # populated after class body

    def fetch(self, resource: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        params = params or {}
        handler = self._RESOURCE_HANDLERS.get(resource)
        if handler is None:
            return {"error": f"Unknown resource: {resource}"}
        return handler(self, params)

    def _generate_team_standup(self, params: Dict) -> Dict:
        """Generate team standup via standup_bot."""
        try:
            from .standup_bot import generate_team_standup
            from dataclasses import asdict
            hours = params.get("hours", 24)
            standup = generate_team_standup(hours)
            return {"ok": True, "data": asdict(standup)}
        except Exception as e:
            logger.warning("generate_team_standup failed: %s", e)
            return {"ok": False, "error": str(e)}

    def _format_celebration(self, params: Dict) -> Dict:
        """Return badge and streak celebration data."""
        data_file = self.copilot_dir / "celebrations.json"
        if data_file.exists():
            try:
                with open(data_file) as f:
                    return {"ok": True, "data": json.load(f)}
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("celebrations.json parse error: %s", e)

        # Return structured stub when no persisted data exists
        return {
            "ok": True,
            "data": {
                "badges": [],
                "streaks": [],
                "recent_achievements": [],
            },
        }

    def _synthesize_mood(self, params: Dict) -> Dict:
        """Return team mood and burnout indicators."""
        data_file = self.copilot_dir / "mood.json"
        if data_file.exists():
            try:
                with open(data_file) as f:
                    return {"ok": True, "data": json.load(f)}
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("mood.json parse error: %s", e)

        # Return structured stub when no persisted data exists
        return {
            "ok": True,
            "data": {
                "team_mood": "neutral",
                "burnout_risk": "low",
                "signals": [],
            },
        }

    def _fetch_kpis(self) -> Dict:
        """Return current KPI values from company.json."""
        result = self._load_json("company.json")
        company = result.get("data", result) if result.get("ok") else {}
        kpis = company.get("kpis", [])
        on_track = sum(1 for k in kpis if k.get("current_value", 0) >= k.get("target_value", 1) * 0.8)
        return {
            "ok": True,
            "kpis": kpis,
            "summary": {"total": len(kpis), "on_track": on_track, "at_risk": len(kpis) - on_track},
        }

    # Allowlisted command prefixes for exec_script/exec_command
    _SAFE_CMD_PREFIXES = (
        "python3 ", "python ", "node ", "git ", "gh ", "npm ", "pip ",
        "jq ", "rg ", "fd ", "bat ", "wc ", "ls ", "cat ",
    )

    def _exec_script(self, params: Dict) -> Dict:
        """Execute a local script and return its output."""
        import shlex
        import subprocess
        command = params.get("command", "")
        if not command:
            return {"ok": False, "error": "No command specified"}
        if not any(command.lstrip().startswith(p) for p in self._SAFE_CMD_PREFIXES):
            return {"ok": False, "error": f"Command not in allowlist. Allowed prefixes: {', '.join(p.strip() for p in self._SAFE_CMD_PREFIXES)}"}
        try:
            result = subprocess.run(
                shlex.split(command), capture_output=True, text=True,
                timeout=30, cwd=str(Path.home() / ".claude"),
            )
            output = result.stdout.strip()
            try:
                return {"ok": True, "data": json.loads(output)}
            except (json.JSONDecodeError, ValueError):
                return {"ok": True, "data": output, "raw": True}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Script timed out (30s)"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _exec_command(self, params: Dict) -> Dict:
        """Execute a command and return its output."""
        import shlex
        import subprocess
        command = params.get("command", "")
        if not command:
            return {"ok": False, "error": "No command specified"}
        if not any(command.lstrip().startswith(p) for p in self._SAFE_CMD_PREFIXES):
            return {"ok": False, "error": f"Command not in allowlist. Allowed prefixes: {', '.join(p.strip() for p in self._SAFE_CMD_PREFIXES)}"}
        try:
            result = subprocess.run(
                shlex.split(command), capture_output=True, text=True,
                timeout=30, cwd=str(Path.home() / ".claude"),
            )
            output = result.stdout.strip()
            return {"ok": result.returncode == 0, "output": output, "stderr": result.stderr.strip()[:500]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Command timed out (30s)"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _fetch_grip_health(self) -> Dict:
        """Fetch GRIP system health metrics."""
        claude_dir = Path.home() / ".claude"
        skills_count = len(list((claude_dir / "skills").glob("*/SKILL.*"))) if (claude_dir / "skills").exists() else 0
        agents_count = len(list((claude_dir / "agents").glob("*.md"))) if (claude_dir / "agents").exists() else 0
        workflows_count = len(list(self.copilot_dir.parent.parent.glob("data/aide/workflows/*.yaml")))
        return {
            "ok": True,
            "skills": skills_count,
            "agents": agents_count,
            "workflows": workflows_count,
            "data_dir_exists": self.copilot_dir.exists(),
        }

    def _kpi_context_switches(self) -> Optional[float]:
        import subprocess
        result = subprocess.run(
            ["git", "log", "--since=7 days ago", "--format=%D", "--all"],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path.home() / ".claude"),
        )
        all_refs: List[str] = []
        for line in result.stdout.splitlines():
            all_refs.extend(line.split(","))
        branches = {r.strip() for r in all_refs if r.strip() and "HEAD" not in r}
        return float(len(branches))

    def _kpi_sprint_commitment(self) -> Optional[float]:
        import subprocess
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", "grip-platform/GRIP",
             "--state", "all", "--limit", "50", "--json", "state"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        issues = json.loads(result.stdout)
        total = len(issues)
        closed = sum(1 for i in issues if i.get("state") == "CLOSED")
        return round((closed / total * 100) if total > 0 else 0, 1)

    def _kpi_time_to_pr(self) -> Optional[float]:
        import subprocess
        result = subprocess.run(
            ["gh", "pr", "list", "--repo", "grip-platform/GRIP",
             "--state", "merged", "--limit", "10", "--json", "createdAt,mergedAt"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        prs = json.loads(result.stdout)
        if not prs:
            return None
        total_hours = 0.0
        count = 0
        for pr in prs:
            if pr.get("createdAt") and pr.get("mergedAt"):
                created = datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))
                merged = datetime.fromisoformat(pr["mergedAt"].replace("Z", "+00:00"))
                total_hours += (merged - created).total_seconds() / 3600
                count += 1
        return round(total_hours / count, 1) if count > 0 else 0

    def _kpi_code_quality(self) -> Optional[float]:
        metrics_path = Path.home() / ".claude" / "learning" / "converge-metrics.jsonl"
        if not metrics_path.exists():
            return None
        scores = []
        for line in metrics_path.read_text().splitlines():
            try:
                entry = json.loads(line)
                if entry.get("event") == "converge_end" and entry.get("final_score"):
                    scores.append(entry["final_score"])
            except json.JSONDecodeError:
                continue
        if not scores:
            return None
        return round(sum(scores) / len(scores), 1)

    def collect_kpis(self) -> Dict:
        """Collect KPI current values from real data sources and update company.json."""
        company_path = self.copilot_dir / "company.json"
        if not company_path.exists():
            return {"ok": False, "error": "company.json not found"}

        collectors = {
            ("git-log", "context-switches"): self._kpi_context_switches,
            ("github", "sprint-commitment"): self._kpi_sprint_commitment,
            ("github", "time-to-pr"): self._kpi_time_to_pr,
            ("rsi-metrics", "code-quality"): self._kpi_code_quality,
        }

        company = json.loads(company_path.read_text())
        kpis = company.get("kpis", [])
        updated = 0
        for kpi in kpis:
            key = (kpi.get("source", ""), kpi.get("id", ""))
            collector = collectors.get(key)
            if not collector:
                continue
            try:
                value = collector()
            except Exception as e:
                logger.warning("KPI collection failed for %s: %s", key[1], e)
                continue
            if value is not None:
                kpi["current_value"] = value
                updated += 1

        if updated > 0:
            company["kpis"] = kpis
            company_path.write_text(json.dumps(company, indent=2) + "\n")

        return {"ok": True, "updated": updated, "total": len(kpis)}

    _RITUAL_TO_OKR = {
        "morning-brief": "Morning briefs delivered daily via Slack",
        "eod-digest": "EOD digests delivered daily via Slack",
        "weekly-review": "Weekly review ritual running on Fridays",
    }

    def _scan_recent_ritual_successes(self, history_path: Path, days: int = 7) -> set:
        cutoff = datetime.now().astimezone() - timedelta(days=days)
        successes = set()
        for line in history_path.read_text().strip().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ritual_id = entry.get("ritual_id", "")
            if ritual_id not in self._RITUAL_TO_OKR or entry.get("status") != "success":
                continue
            try:
                ts = datetime.fromisoformat(entry.get("timestamp", "").replace("Z", "+00:00"))
                if ts >= cutoff:
                    successes.add(ritual_id)
            except (ValueError, TypeError):
                successes.add(ritual_id)
        return successes

    @staticmethod
    def _flatten_key_results(objectives: List[Dict]) -> List[Dict]:
        all_krs: List[Dict] = []
        for obj in objectives:
            all_krs.extend(obj.get("key_results", []))
        return all_krs

    def _apply_okr_updates(self, objectives: List[Dict], ritual_successes: set) -> int:
        okr_desc_to_ritual = {v: k for k, v in self._RITUAL_TO_OKR.items()}
        all_krs = self._flatten_key_results(objectives)
        updated = 0
        for kr in all_krs:
            ritual_id = okr_desc_to_ritual.get(kr.get("description", ""))
            if ritual_id is None:
                continue
            new_value = 1.0 if ritual_id in ritual_successes else 0.0
            if kr.get("current") != new_value:
                kr["current"] = new_value
                updated += 1
                logger.info("OKR updated: %s -> %.1f", kr.get("description", ""), new_value)
        return updated

    def sync_okr_from_rituals(self) -> Dict:
        """Sync quarterly OKR key results from ritual execution history."""
        company_path = self.copilot_dir / "company.json"
        history_path = self.copilot_dir / "rituals" / "history.jsonl"
        if not company_path.exists():
            return {"ok": False, "error": "company.json not found"}
        if not history_path.exists():
            return {"ok": True, "updated": 0, "note": "No ritual history found"}

        company = json.loads(company_path.read_text())
        objectives = company.get("goals", {}).get("current_quarter", {}).get("objectives", [])
        ritual_successes = self._scan_recent_ritual_successes(history_path)
        updated = self._apply_okr_updates(objectives, ritual_successes)
        if updated > 0:
            company_path.write_text(json.dumps(company, indent=2) + "\n")
        return {
            "ok": True,
            "updated": updated,
            "active_rituals": list(ritual_successes),
            "total_okr_keys": sum(len(obj.get("key_results", [])) for obj in objectives),
        }

    def _load_jsonl_from_path(self, path: Path, limit: int = 10) -> Dict:
        """Load JSONL from an arbitrary path."""
        if not path.exists():
            return {"ok": True, "entries": []}
        entries = []
        for line in path.read_text().strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if limit:
            entries = entries[-limit:]
        return {"ok": True, "entries": entries}

    def _load_json(self, filename: str) -> Dict:
        fp = self.copilot_dir / filename
        if not fp.exists():
            return {"ok": False, "error": f"{filename} not found"}
        try:
            with open(fp) as f:
                return {"ok": True, "data": json.load(f)}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": str(e)}

    def _load_jsonl(self, filename: str, limit: int = 10) -> Dict:
        fp = self.copilot_dir / filename
        if not fp.exists():
            return {"ok": True, "data": []}
        entries = []
        with open(fp) as f:
            for line in f:
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return {"ok": True, "data": entries[-limit:]}


def _local_data_git_branches(self, params: Dict) -> Dict:
    return self._exec_command({"command": "git -C " + str(Path.home() / ".claude") + " branch -a --format='%(refname:short) %(committerdate:relative)'"})


def _local_data_git_staged_diff(self, params: Dict) -> Dict:
    return self._exec_command({"command": "git -C " + str(Path.home() / ".claude") + " diff --cached --stat"})


def _local_data_kono_stats(self, params: Dict) -> Dict:
    return self._exec_script({"command": "python3 " + str(Path.home() / ".claude" / "lib" / "kono_substrate.py") + " stats"})


def _local_data_learning_insights(self, params: Dict) -> Dict:
    return self._load_jsonl_from_path(
        self.copilot_dir.parent.parent / "learning" / "converge-metrics.jsonl",
        params.get("limit", 20),
    )


def _local_data_lint_results(self, params: Dict) -> Dict:
    return self._exec_command({"command": "cd " + str(Path.home() / ".claude") + " && rg -c 'TODO|FIXME|HACK' lib/ 2>/dev/null | head -20 || true"})


def _local_data_type_check(self, params: Dict) -> Dict:
    # Two defects, both of which made this report "ok" for work never done:
    #
    #   * it compiled lib/copilot/integrations.py, a path that has not existed
    #     since the copilot -> aide rename (75900c66f, 2026-06-22). py_compile
    #     exited non-zero with "No such file or directory" every time.
    #   * `; echo ok` ended the guarded chain, so "ok" printed regardless — on a
    #     failed compile, a missing file, even a failed cd. The check could not
    #     fail, which is the one property a check must have.
    #
    # Now compiles the package this module actually lives in, and says what
    # happened. `|| echo` keeps the never-fail-the-command convention the
    # sibling probes use, without discarding the result.
    root = Path.home() / ".claude"
    return self._exec_command({
        "command": (
            # The `ls` is load-bearing and was added after the first version
            # of this fix was caught by its own test: `compileall -q` on a
            # MISSING directory prints "Can't list" to stderr and EXITS 0, so
            # without this the check would report ok for a package that is not
            # there — the same defect being fixed, in a new costume. Requiring
            # a .py to exist makes "there was something to compile" part of the
            # claim rather than an assumption.
            f"cd {root} && ls lib/aide/*.py >/dev/null 2>&1 "
            "&& python3 -m compileall -q lib/aide 2>&1 "
            "&& echo 'type-check ok' || echo 'type-check FAILED'"
        )
    })


def _local_data_test_coverage(self, params: Dict) -> Dict:
    return self._exec_command({"command": "cd " + str(Path.home() / ".claude") + " && python3 -m pytest tests/ --co -q 2>/dev/null | tail -1 || echo '0 tests'"})


def _local_data_npm_audit(self, params: Dict) -> Dict:
    return self._exec_command({"command": "cd " + str(Path.home() / ".claude" / "apps" / "grip-web") + " && npm audit --json 2>/dev/null || echo '{}'"})


def _local_data_rsi_patterns(self, params: Dict) -> Dict:
    return self._load_jsonl_from_path(
        self.copilot_dir.parent.parent / "learning" / "converge-metrics.jsonl",
        params.get("limit", 50),
    )


LocalDataAdapter._RESOURCE_HANDLERS = {
    "company":              lambda s, p: s._load_json("company.json"),
    "preferences":          lambda s, p: s._load_json("preferences.json"),
    "decisions":            lambda s, p: s._load_jsonl("decision-log.jsonl", p.get("limit", 10)),
    "state":                lambda s, p: s._load_json("state.json"),
    "kpis":                 lambda s, p: s._fetch_kpis(),
    "kpi_refresh":          lambda s, p: s.collect_kpis(),
    "okr_refresh":          lambda s, p: s.sync_okr_from_rituals(),
    "script":               lambda s, p: s._exec_script(p),
    "command":              lambda s, p: s._exec_command(p),
    "git_branches":         _local_data_git_branches,
    "git_staged_diff":      _local_data_git_staged_diff,
    "grip_health":          lambda s, p: s._fetch_grip_health(),
    "kono_stats":           _local_data_kono_stats,
    "learning_insights":    _local_data_learning_insights,
    "lint_results":         _local_data_lint_results,
    "type_check":           _local_data_type_check,
    "test_coverage":        _local_data_test_coverage,
    "npm_audit":            _local_data_npm_audit,
    "rsi_patterns":         _local_data_rsi_patterns,
    "implementation_plan":  lambda s, p: s._load_json("implementation_plan.json"),
    "test_results":         lambda s, p: s._load_json("test_results.json"),
    "generate_team_standup": lambda s, p: s._generate_team_standup(p),
    "format_celebration":   lambda s, p: s._format_celebration(p),
    "synthesize_mood":      lambda s, p: s._synthesize_mood(p),
}



class GitAdapter(IntegrationAdapter):
    """Git operations adapter for log, diff, and branch data."""

    def __init__(self, repo_dir: Optional[str] = None):
        self.repo_dir = repo_dir or str(Path.home() / ".claude")

    def available(self) -> bool:
        import subprocess
        try:
            subprocess.run(["git", "rev-parse", "--git-dir"],
                          capture_output=True, cwd=self.repo_dir, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def get_details(self) -> Dict[str, Any]:
        return {"available": self.available(), "repo_dir": self.repo_dir}

    def fetch(self, resource: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        params = params or {}
        if resource == "log":
            return self._fetch_log(params)
        elif resource == "diff":
            return self._fetch_diff(params)
        elif resource == "branches":
            return self._fetch_branches()
        return {"error": f"Unknown resource: {resource}"}

    def _run_git(self, args: List[str], timeout: int = 15) -> Dict:
        import subprocess
        try:
            result = subprocess.run(
                ["git"] + args, capture_output=True, text=True,
                timeout=timeout, cwd=self.repo_dir,
            )
            return {"ok": result.returncode == 0, "output": result.stdout.strip(), "stderr": result.stderr.strip()[:500]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Git command timed out"}
        except FileNotFoundError:
            return {"ok": False, "error": "git not found"}

    def _fetch_log(self, params: Dict) -> Dict:
        days = params.get("days", 7)
        limit = params.get("limit", 50)
        result = self._run_git([
            "log", f"--since={days} days ago", f"-{limit}",
            "--format=%H|%an|%ae|%aI|%s",
        ])
        if not result.get("ok"):
            return result
        commits = []
        for line in result["output"].splitlines():
            parts = line.split("|", 4)
            if len(parts) == 5:
                commits.append({
                    "sha": parts[0][:8],
                    "author": parts[1],
                    "email": parts[2],
                    "date": parts[3],
                    "message": parts[4],
                })
        return {"ok": True, "commits": commits, "count": len(commits)}

    def _fetch_diff(self, params: Dict) -> Dict:
        args = ["diff", "--stat"]
        if params.get("staged"):
            args.append("--cached")
        return self._run_git(args)

    def _fetch_branches(self) -> Dict:
        result = self._run_git(["branch", "-a", "--format=%(refname:short) %(committerdate:relative)"])
        if not result.get("ok"):
            return result
        branches = []
        for line in result["output"].splitlines():
            parts = line.strip().split(" ", 1)
            branches.append({"name": parts[0], "last_commit": parts[1] if len(parts) > 1 else ""})
        return {"ok": True, "branches": branches}


class IntegrationHub:
    """
    Central hub for all integration adapters.

    Provides unified access to all external services with
    availability checking and graceful fallbacks.
    """

    def __init__(self, org_slug: Optional[str] = None):
        self.org_slug = org_slug
        self.slack = SlackAdapter()
        self.github = GitHubAdapter()
        self.local = LocalDataAdapter()

        self.desktop = DesktopNotifier()
        self.mobile = MobilePushNotifier()
        self.reminders = SlackReminderAdapter(token=self.slack.token)
        self.self_reminder = SelfReminder
        self.notifications = NotificationRouter(slack=self.slack)

        self.git = GitAdapter()
        self._adapters: Dict[str, IntegrationAdapter] = {
            "slack": self.slack,
            "github": self.github,
            "local": self.local,
            "git": self.git,
        }

    def get(self, name: str) -> Optional[IntegrationAdapter]:
        return self._adapters.get(name)

    def status(self) -> Dict[str, bool]:
        """Check availability of all integrations.

        Returns:
            Dict mapping integration name to availability boolean.
            For detailed status info, use detailed_status() instead.
        """
        return {name: adapter.available() for name, adapter in self._adapters.items()}

    def detailed_status(self) -> Dict[str, Dict[str, Any]]:
        """Check availability of all integrations with detailed info.

        Uses the adapter pattern - each adapter provides its own details
        via get_details(), avoiding if/elif chains when adding new adapters.

        Returns:
            Dict mapping integration name to status dict containing:
            - available: bool - whether the integration is available
            - Additional adapter-specific details (token_prefix, data_dir, etc.)
        """
        return {name: adapter.get_details() for name, adapter in self._adapters.items()}


# =========================================================================
# Notification Infrastructure - Multi-channel async delivery
# =========================================================================
# Design learnings:
#   - OpenClaw WhatsApp: configurable allowlist, fail-closed security
#   - Slack DM -> phone push inherently (Slack app handles mobile)
#   - Desktop: macOS native osascript (non-blocking)
#   - Each channel independent, fail-independent (one failure != all fail)
#   - Reminders: Slack API + GRIP self-reminder for session continuity


class DesktopNotifier:
    """
    macOS desktop notification with click-to-open URL support.

    Primary: terminal-notifier (supports -open URL for click actions).
    Fallback: osascript display notification (no click support).

    Non-blocking, fail-silent. Only works on macOS.
    Delivers to Notification Center which syncs to all Apple devices
    if the user has notification forwarding enabled.
    """

    _tn_path_cache: Optional[str] = None
    _tn_path_checked: bool = False

    @classmethod
    def _terminal_notifier_path(cls) -> Optional[str]:
        """Find terminal-notifier binary path (cached after first lookup)."""
        if cls._tn_path_checked:
            return cls._tn_path_cache
        cls._tn_path_checked = True
        try:
            result = subprocess.run(
                ["which", "terminal-notifier"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                cls._tn_path_cache = result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return cls._tn_path_cache

    @classmethod
    def _reset_cache(cls) -> None:
        """Reset the terminal-notifier path cache (for test isolation)."""
        cls._tn_path_cache = None
        cls._tn_path_checked = False

    @staticmethod
    def available() -> bool:
        """Check if desktop notifications are available (macOS only)."""
        import platform
        if platform.system() != "Darwin":
            return False
        try:
            result = subprocess.run(
                ["which", "osascript"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _notify_via_terminal_notifier(tn_path: str, title: str, message: str,
                                      sound: str, url: str, group: str) -> bool:
        cmd = [tn_path, "-title", title, "-message", message, "-sound", sound]
        if url:
            cmd.extend(["-open", url])
        if group:
            cmd.extend(["-group", group])
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (FileNotFoundError, OSError):
            return False

    @staticmethod
    def _notify_via_osascript(title: str, message: str, sound: str) -> bool:
        safe_title = title.replace('"', '\\"')
        safe_message = message.replace('"', '\\"')
        script = (
            f'display notification "{safe_message}" '
            f'with title "{safe_title}" '
            f'sound name "{sound}"'
        )
        try:
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except (FileNotFoundError, OSError):
            return False

    @classmethod
    def notify(cls, title: str, message: str, sound: str = "default",
               url: str = "", group: str = "") -> bool:
        """Send a macOS desktop notification.

        Preference order:
        1. terminal-notifier — only path that supports click-to-open URLs
           and grouping, so we keep it first.
        2. HAL osa registry — hardened dispatch; used when terminal-notifier
           is absent AND the caller doesn't rely on url/group/custom sound
           (the registry script takes only title + body).
        3. Direct osascript — final fallback for hosts without HAL.
        """
        tn_path = cls._terminal_notifier_path()
        if tn_path and cls._notify_via_terminal_notifier(tn_path, title, message, sound, url, group):
            return True
        if not url and not group and sound in ("default", ""):
            try:
                import hal_osa
                if hal_osa.notify(title, message):
                    return True
            except ImportError:
                pass
        return cls._notify_via_osascript(title, message, sound)


class SlackReminderAdapter:
    """
    Slack reminders via the reminders.add API.

    Creates timed reminders that Slack delivers as DM notifications.
    Inherits Slack token from SlackAdapter.
    """

    def __init__(self, token: str = ""):
        self.token = token or load_slack_token()

    def available(self) -> bool:
        return bool(self.token)

    def set_reminder(self, user_id: str, text: str, time: str) -> bool:
        """
        Set a Slack reminder for a user.

        Args:
            user_id: Slack user ID
            text: Reminder text
            time: Unix timestamp or natural language (e.g., "in 2 hours", "tomorrow at 9am")
        """
        result = slack_api_post(self.token, "reminders.add", {
            "user": user_id,
            "text": text,
            "time": time,
        })
        return result.get("ok", False)

    def list_reminders(self) -> list:
        """List pending reminders."""
        result = slack_api_get(self.token, "reminders.list", {})
        if result.get("ok"):
            return result.get("reminders", [])
        return []


class SelfReminder:
    """
    GRIP self-reminder system for cross-session persistence.

    Stores reminders in a JSONL file that gets checked on session start.
    Connects to /remind-yourself (history mining) but adds proactive
    scheduled reminders that surface automatically.

    Reminders are:
    - Written by copilot rituals or user requests
    - Checked on every session start
    - Delivered via desktop + Slack when due
    - Cleared after delivery
    """

    REMINDERS_FILE = Path(os.environ.get(
        "GRIP_REMINDERS_FILE",
        str(Path.home() / ".claude" / "data" / "aide" / "reminders.jsonl"),
    ))

    @classmethod
    def schedule(cls, text: str, due_at: str = "", context: str = "", tags: Optional[List[str]] = None) -> str:
        """
        Schedule a self-reminder.

        Args:
            text: What to remember
            due_at: ISO timestamp when to surface (empty = next session)
            context: Additional context (ritual_id, workflow, etc.)
            tags: Optional tags for filtering

        Returns:
            Reminder ID
        """
        import hashlib
        reminder_id = hashlib.sha256(
            f"{text}{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()[:12]

        entry = {
            "id": reminder_id,
            "text": text,
            "due_at": due_at or "",
            "context": context,
            "tags": tags or [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "delivered": False,
        }

        from .atomic_io import atomic_append_jsonl
        try:
            atomic_append_jsonl(cls.REMINDERS_FILE, entry)
            logger.debug("Reminder scheduled: %s", reminder_id)
        except Exception as e:
            logger.warning("Failed to schedule reminder: %s", e)
            return ""

        return reminder_id

    @classmethod
    def get_due(cls) -> list:
        """Get all due reminders (due_at <= now, or empty due_at = always due)."""
        from .atomic_io import read_jsonl

        now = datetime.now(timezone.utc)
        due = []

        for entry in read_jsonl(cls.REMINDERS_FILE):
            if entry.get("delivered"):
                continue

            due_at = entry.get("due_at", "")
            if not due_at:
                # No due time = surface on next check
                due.append(entry)
            else:
                try:
                    if datetime.fromisoformat(due_at) <= now:
                        due.append(entry)
                except ValueError:
                    continue

        return due

    @classmethod
    def mark_delivered(cls, reminder_id: str) -> bool:
        """Mark a reminder as delivered (atomic rewrite with locking).

        Uses locked_read_modify_write to prevent contention during
        high-frequency mark operations.

        Returns:
            True if successfully marked, False on failure.
        """
        from .atomic_io import read_jsonl, locked_read_modify_write

        entries = read_jsonl(cls.REMINDERS_FILE)
        if not entries:
            return True  # Nothing to mark, not a failure

        for entry in entries:
            if entry.get("id") == reminder_id:
                entry["delivered"] = True
                entry["delivered_at"] = datetime.now(timezone.utc).isoformat()

        try:
            locked_read_modify_write(cls.REMINDERS_FILE, entries)
            logger.debug("Reminder marked delivered: %s", reminder_id)
            return True
        except Exception as e:
            logger.warning("Failed to mark reminder delivered: %s", e)
            return False

    @classmethod
    def clear_delivered(cls) -> bool:
        """Remove all delivered reminders (atomic compact with locking).

        Returns:
            True if successfully cleared, False on failure.
        """
        from .atomic_io import read_jsonl, locked_read_modify_write

        entries = read_jsonl(cls.REMINDERS_FILE)
        active = [e for e in entries if not e.get("delivered")]
        try:
            locked_read_modify_write(cls.REMINDERS_FILE, active)
            logger.debug("Cleared delivered reminders, %d active remain", len(active))
            return True
        except Exception as e:
            logger.warning("Failed to clear delivered reminders: %s", e)
            return False



class MobilePushNotifier:
    """
    Mobile push notifications via ntfy.sh (open source, self-hostable).

    ntfy.sh works on iOS + Android without building a native app:
    - Users install the ntfy app and subscribe to their topic
    - Server sends HTTP POST to ntfy.sh/topic -> push to all subscribers
    - Self-hostable on Hetzner (Phase 2) for zero external dependency

    Topic naming: grip-{company_slug}-{user_slug} (private by default)
    """

    def __init__(self, server_url: str = "https://ntfy.sh", topic_prefix: str = "grip"):
        self.server_url = server_url.rstrip("/")
        self.topic_prefix = topic_prefix

    def available(self) -> bool:
        """Check if ntfy server is reachable."""
        try:
            req = urllib.request.Request(
                f"{self.server_url}/v1/health",
                method="GET",
            )
            resp = urllib.request.urlopen(req, timeout=5)
            return resp.status == 200
        except (urllib.error.URLError, OSError) as e:
            logger.debug("ntfy health check failed: %s", e)
            return False

    def notify(
        self,
        topic: str,
        title: str,
        message: str,
        priority: int = 3,
        tags: Optional[List[str]] = None,
        retries: int = 2,
    ) -> bool:
        """
        Send a push notification via ntfy.sh with retry on transient failures.

        Args:
            topic: ntfy topic (e.g., "grip-codetonight-laurie")
            title: Notification title
            message: Notification body
            priority: 1-5 (1=min, 3=default, 5=urgent)
            tags: Optional emoji tags
            retries: Number of retry attempts on transient failure
        """
        if not topic:
            return False

        headers = {
            "Title": title,
            "Priority": str(priority),
        }
        if tags:
            headers["Tags"] = ",".join(tags)

        data = message.encode("utf-8")
        last_error = None

        for attempt in range(1 + retries):
            try:
                req = urllib.request.Request(
                    f"{self.server_url}/{topic}",
                    data=data,
                    headers=headers,
                    method="POST",
                )
                resp = urllib.request.urlopen(req, timeout=10)
                return resp.status == 200
            except urllib.error.URLError as e:
                last_error = e
                if attempt < retries:
                    import time
                    time.sleep(0.5 * (attempt + 1))  # Linear backoff
                continue
            except (OSError, ValueError) as e:
                logger.warning("ntfy push to %s unexpected error: %s", topic, e)
                return False

        logger.warning("ntfy push to %s failed after %d attempts: %s", topic, 1 + retries, last_error)
        return False

    def build_topic(self, company_slug: str, user_slug: str) -> str:
        """Build a topic name for a user."""
        return f"{self.topic_prefix}-{company_slug}-{user_slug}"


class NotificationRouter:
    """
    Multi-channel notification delivery with fail-independence.

    Each channel delivers independently - one failure doesn't block others.
    Channels:
      - Slack DM: Primary. Also delivers to phone via Slack app.
      - Desktop: macOS Notification Center (syncs to Apple devices).
      - Slack Reminder: Timed async delivery via Slack reminder API.
      - Self-Reminder: GRIP cross-session reminder for next session start.

    Design principles (from OpenClaw WhatsApp learning):
      - Configurable channels per recipient (not hardcoded)
      - Fail-closed: missing channel config = skip channel
      - Each channel independent, fail-independent
    """

    def __init__(self, slack: Optional[SlackAdapter] = None):
        self.slack = slack or SlackAdapter()
        self.desktop = DesktopNotifier()
        self.mobile = MobilePushNotifier()
        self.reminders = SlackReminderAdapter(token=self.slack.token)
        self.self_reminder = SelfReminder()

    def deliver(
        self,
        recipient_name: str,
        recipient_slack_id: str,
        title: str,
        message: str,
        channels: Optional[List[str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Deliver notification across multiple channels.

        Args:
            recipient_name: Human name for logging
            recipient_slack_id: Slack user ID
            title: Short title (used in desktop notification)
            message: Full message body
            channels: Which channels to use. Default: ["slack_dm", "desktop"]

        Returns:
            Dict of channel -> success/failure
        """
        channels = channels or ["slack_dm"]
        results: Dict[str, Any] = {}

        for channel in channels:
            try:
                if channel == "slack_dm" and self.slack.available():
                    dm_result = self.slack.send_dm(
                        recipient_slack_id, message,
                        blocks=text_to_blocks(message),
                    )
                    results["slack_dm"] = dm_result.get("ok", False)

                    # Register as Copilot thread for reply continuity
                    if dm_result.get("ok") and dm_result.get("ts") and dm_result.get("channel"):
                        from .thread_registry import CopilotThreadRegistry
                        CopilotThreadRegistry.register(
                            channel_id=dm_result["channel"],
                            thread_ts=dm_result["ts"],
                            origin="notification",
                            context={"recipient": recipient_name, "title": title},
                        )
                elif channel == "desktop" and self.desktop.available():
                    # Truncate for desktop notification (max ~200 chars useful)
                    short_msg = message[:200] + "..." if len(message) > 200 else message
                    results["desktop"] = self.desktop.notify(
                        title, short_msg,
                        url=kwargs.get("url", ""),
                        group=kwargs.get("notification_group", ""),
                    )
                elif channel == "mobile_push" and self.mobile.available():
                    results["mobile_push"] = self.mobile.notify(
                        topic=kwargs.get("ntfy_topic", ""),
                        title=title,
                        message=message[:500],
                        priority=kwargs.get("priority", 3),
                        tags=kwargs.get("ntfy_tags"),
                    )
                elif channel == "slack_reminder" and self.reminders.available():
                    results["slack_reminder"] = self.reminders.set_reminder(
                        recipient_slack_id, message, "in 5 minutes"
                    )
                elif channel == "self_reminder":
                    rid = self.self_reminder.schedule(
                        text=message,
                        context=f"notification for {recipient_name}",
                        tags=["copilot", "notification"],
                    )
                    results["self_reminder"] = bool(rid)
                else:
                    results[channel] = False  # Channel unavailable
            except Exception as e:
                logger.error("Notification channel %s failed for %s: %s", channel, recipient_name, e)
                results[channel] = False  # Fail-independent

        return results

    def deliver_reminder(
        self,
        recipient_slack_id: str,
        text: str,
        when: str = "in 2 hours",
    ) -> Dict[str, bool]:
        """
        Set a reminder via Slack + GRIP self-reminder.

        Args:
            recipient_slack_id: Who to remind
            text: Reminder text
            when: Slack natural language time or ISO timestamp
        """
        results = {}

        if self.reminders.available():
            results["slack_reminder"] = self.reminders.set_reminder(
                recipient_slack_id, text, when
            )

        # Also store as self-reminder for GRIP session continuity
        rid = self.self_reminder.schedule(
            text=text,
            context="user_reminder",
            tags=["reminder", "user"],
        )
        results["self_reminder"] = bool(rid)

        return results
