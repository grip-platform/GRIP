#!/usr/bin/env python3
"""Suno Client — Python bridge to teslasolar/suno (Konomi Suno API).

Reverse-engineered API client for the Guild's Suno music generation proxy.
The proxy (Express, port 3456) handles Clerk JWT auth and routes to
studio-api.suno.ai. This client talks to the LOCAL proxy, not Suno directly.

Auth flow: browser cookie -> Clerk client -> JWT -> studio-api.suno.ai
The proxy manages auth automatically once a cookie is provided.

Endpoints covered:
  - Health/status checks
  - Cookie-based authentication
  - Song generation (description + custom modes)
  - Clip polling and library browsing
  - Lyrics generation
  - Credit balance

Usage:
    from lib.suno_client import SunoClient

    client = SunoClient()  # defaults to localhost:3456
    if client.is_healthy():
        clips = client.generate("A lo-fi track about coding at midnight")
        result = client.poll(clips)

CLI:
    python3 -m lib.suno_client health
    python3 -m lib.suno_client generate "A jazz track about recursion"
    python3 -m lib.suno_client credits
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

SUNO_API_URL = os.environ.get("SUNO_API_URL", "http://localhost:3456")
TIMEOUT_S = 30
POLL_INTERVAL_S = 5
POLL_MAX_ATTEMPTS = 60  # 5 minutes max polling


@dataclass
class SunoClip:
    """A generated music clip."""
    clip_id: str
    status: str = "submitted"
    title: str = ""
    audio_url: str = ""
    image_url: str = ""
    duration: float = 0.0
    metadata: dict = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"

    @property
    def is_failed(self) -> bool:
        return self.status in ("error", "failed")


@dataclass
class SunoHealth:
    """Server health status."""
    ok: bool
    version: str = ""
    authenticated: bool = False
    credits: int = -1
    uptime: float = 0.0


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request(
    method: str, path: str, body: Optional[dict] = None,
    base_url: str = SUNO_API_URL, timeout: int = TIMEOUT_S,
) -> dict:
    """Make HTTP request to Suno proxy. Returns parsed JSON."""
    url = f"{base_url}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get(path: str, **kwargs) -> dict:
    return _request("GET", path, **kwargs)


def _post(path: str, body: dict, **kwargs) -> dict:
    return _request("POST", path, body=body, **kwargs)


# ---------------------------------------------------------------------------
# Client — ISP decomposition: Auth (3), Generation (5), Browse (3)
# ---------------------------------------------------------------------------

class _SunoBase:
    """Shared base with base_url."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or SUNO_API_URL


class SunoAuth(_SunoBase):
    """Authentication and health checking (3 methods)."""

    def is_healthy(self) -> bool:
        """Check if the Suno proxy is running and authenticated."""
        try:
            data = _get("/health", base_url=self.base_url, timeout=5)
            return data.get("ok", False)
        except (URLError, OSError, json.JSONDecodeError):
            return False

    def health(self) -> SunoHealth:
        """Get detailed health status."""
        try:
            data = _get("/health", base_url=self.base_url, timeout=5)
            return SunoHealth(
                ok=data.get("ok", False),
                version=data.get("version", ""),
                authenticated=data.get("authenticated", False),
                credits=data.get("credits", -1),
                uptime=data.get("uptime", 0.0),
            )
        except (URLError, OSError):
            return SunoHealth(ok=False)

    def authenticate(self, cookie: str) -> dict:
        """Set Suno session cookie for authentication."""
        return _post("/auth/cookie", {"cookie": cookie}, base_url=self.base_url)


class SunoGeneration(_SunoBase):
    """Music generation and polling (5 methods)."""

    def generate(
        self, prompt: str, style: Optional[str] = None,
        title: Optional[str] = None, instrumental: bool = False,
    ) -> list[str]:
        """Generate a song. Returns list of clip IDs."""
        body: dict = {"prompt": prompt}
        if style:
            body["style"] = style
        if title:
            body["title"] = title
        if instrumental:
            body["instrumental"] = True
        data = _post("/generate", body, base_url=self.base_url)
        return data.get("clipIds", [])

    def poll(self, clip_ids: list[str]) -> list[SunoClip]:
        """Poll clip status. Returns current state of all clips."""
        ids_str = ",".join(clip_ids)
        data = _get(f"/poll?ids={ids_str}", base_url=self.base_url)
        clips_data = data if isinstance(data, list) else data.get("clips", [])
        return [_parse_clip(c) for c in clips_data]

    def wait_for_completion(
        self, clip_ids: list[str],
        interval: float = POLL_INTERVAL_S,
        max_attempts: int = POLL_MAX_ATTEMPTS,
    ) -> list[SunoClip]:
        """Poll until all clips are complete or failed."""
        for _ in range(max_attempts):
            clips = self.poll(clip_ids)
            if all(c.is_complete or c.is_failed for c in clips):
                return clips
            time.sleep(interval)
        return self.poll(clip_ids)  # final state

    def generate_lyrics(self, prompt: str) -> dict:
        """Generate lyrics via Suno's AI."""
        return _post("/lyrics", {"prompt": prompt}, base_url=self.base_url)

    def credits(self) -> int:
        """Get current credit balance. Returns -1 if unavailable."""
        try:
            data = _get("/credits", base_url=self.base_url)
            return data.get("credits", -1)
        except (URLError, OSError):
            return -1


class SunoBrowse(_SunoBase):
    """Library browsing and system status (3 methods)."""

    def library(self, page: int = 0) -> list[dict]:
        """Get paginated song library."""
        data = _get(f"/library?page={page}", base_url=self.base_url)
        return data if isinstance(data, list) else data.get("clips", [])

    def clip(self, clip_id: str) -> SunoClip:
        """Get a single clip by ID."""
        data = _get(f"/clip/{clip_id}", base_url=self.base_url)
        return _parse_clip(data)

    def status(self) -> dict:
        """Get full system state (ISA-95 equipment, alarms, metrics)."""
        return _get("/status", base_url=self.base_url)


class SunoClient(SunoAuth, SunoGeneration, SunoBrowse):
    """Composed Suno client — all capabilities via ISP mixins."""
    pass


def _parse_clip(data: dict) -> SunoClip:
    """Parse API response into SunoClip dataclass."""
    return SunoClip(
        clip_id=data.get("id", data.get("clip_id", "")),
        status=data.get("status", "unknown"),
        title=data.get("title", ""),
        audio_url=data.get("audio_url", ""),
        image_url=data.get("image_url", data.get("image_large_url", "")),
        duration=data.get("duration", 0.0),
        metadata=data.get("metadata", {}),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Suno Client — teslasolar/suno proxy")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("health", help="Check proxy health")
    sub.add_parser("credits", help="Get credit balance")
    sub.add_parser("status", help="Full system status")

    gen = sub.add_parser("generate", help="Generate a song")
    gen.add_argument("prompt", help="Song description or lyrics")
    gen.add_argument("--style", help="Style tags (custom mode)")
    gen.add_argument("--title", help="Song title (custom mode)")
    gen.add_argument("--wait", action="store_true", help="Wait for completion")

    lib = sub.add_parser("library", help="Browse song library")
    lib.add_argument("--page", type=int, default=0)

    args = parser.parse_args()
    client = SunoClient()

    HANDLERS = {
        "health": lambda: _cli_health(client),
        "credits": lambda: print(f"Credits: {client.credits()}"),
        "status": lambda: print(json.dumps(client.status(), indent=2)),
        "generate": lambda: _cli_generate(client, args),
        "library": lambda: _cli_library(client, args),
    }

    handler = HANDLERS.get(args.command)
    if handler:
        handler()
    else:
        parser.print_help()


def _cli_health(client: SunoClient) -> None:
    h = client.health()
    print(f"OK: {h.ok} | Auth: {h.authenticated} | Credits: {h.credits} | v{h.version}")


def _cli_generate(client: SunoClient, args) -> None:
    clip_ids = client.generate(args.prompt, style=args.style, title=args.title)
    print(f"Generating: {clip_ids}")
    if args.wait:
        clips = client.wait_for_completion(clip_ids)
        for c in clips:
            print(f"  {c.clip_id}: {c.status} — {c.title} ({c.audio_url})")


def _cli_library(client: SunoClient, args) -> None:
    songs = client.library(page=args.page)
    for s in songs[:10]:
        title = s.get("title", "untitled")
        status = s.get("status", "?")
        print(f"  {s.get('id', '?')}: {title} [{status}]")
