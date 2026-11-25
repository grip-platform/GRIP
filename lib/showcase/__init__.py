"""Generalised video-showcase tracker — persistent, multi-project.

See `plans/enchanted-spinning-emerson.md` for architecture.

Public API:
  log_event(project, rec)           — write JSONL event (V>>--<<V watermarked, IP-hashed)
  hash_ip(ip) -> str                — SHA-256(salt + ip)[:12]
  load_config(project) -> dict      — read state/showcases/<project>/config.json
  render_page(config, recipient)    — HTML for /v/<project>/<recipient>
  is_valid_slug(s) -> bool          — ^[a-z0-9][a-z0-9_-]{0,31}$
  is_genuine_view(events) -> bool   — page_view OR pixel + ≥1 play
  main()                            — run the HTTP server (port from SHOWCASE_PORT)
"""
from __future__ import annotations

__version__ = "0.1.0"

from .log import log_event, hash_ip  # noqa: F401
