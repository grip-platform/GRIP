"""LinkedIn Feed Scout — Safari extractor (Tier-1, the operator's REAL Safari).

Sibling to `lib/linkedin_feed.py` (Brave / Chrome DevTools Protocol). Safari
cannot do CDP network interception, so instead of intercepting the feed stream
and calling the Voyager API from Python, this runs the Voyager `fetch()`es
INSIDE the logged-in Safari page via AppleScript `do JavaScript`:

  * activity URNs come from the feed DOM (`[data-urn^="urn:li:activity:"]`);
  * each URN is hydrated by a SAME-ORIGIN `fetch('/voyager/api/feed/updates/…')`
    executed in the page, so the httpOnly `li_at` cookie is auto-attached by the
    browser and NEVER leaves it (strictly more secure than the Brave path, which
    extracts cookies into the Python process);
  * the CSRF token is the `JSESSIONID` value, readable from `document.cookie`.

The JS returns the SAME Voyager JSON shape the Brave path produces, so the whole
downstream — `linkedin_feed._parse_voyager_post` → scorer → drafter → report —
is reused UNCHANGED (syscall-doctrine: swap the backend behind a stable API).

Design ratified by the Pool-B sovereign council (approach C, unanimous 3/3 +
exogenous non-Anthropic judge; provenance sha256 8d3b6854, zero Anthropic).

Requirements (preflight-checked, operator-actionable on failure):
  1. Safari > Develop > "Allow JavaScript from Apple Events" enabled.
  2. Automation permission (TCC) for the controlling process to control Safari.
  3. A Safari tab open + logged in at linkedin.com.

`do JavaScript` returns the last expression SYNCHRONOUSLY and does NOT await
Promises, so the in-page async work writes `window.__gripFeed` + `__gripDone`
and Python polls (mirrors `linkedin_feed._wait_for_rsc`).
"""
from __future__ import annotations

import datetime
import json
import logging
import random
import subprocess
import time
from typing import Optional

from linkedin_feed import (
    FeedConfig,
    _parse_voyager_post,
    _persist,
    load_seen,
)
from linkedin_scorer import Post

log = logging.getLogger(__name__)

_OSA_TIMEOUT_S = 20
_POLL_TIMEOUT_S = 45.0
_POLL_INTERVAL_MIN_S = 0.8
_POLL_INTERVAL_MAX_S = 1.6


class SafariExtractionError(RuntimeError):
    """Raised on an unrecoverable Safari-automation condition, with
    operator-actionable text (setting off / no tab / not logged in).

    ``kind`` routes operator guidance (AGORA #3903 DEFER — the caller can
    tell a setup problem from a mid-run loss without parsing prose):
    "preflight" = fix Safari setup / open+sign-in the tab, then re-run;
    "context-lost" = the tab closed, navigated or reloaded mid-run;
    "timeout" = the in-page hydration never completed (retry may work);
    "transient" = a one-off in-page failure.
    """

    def __init__(self, message: str, *, kind: str = "transient") -> None:
        super().__init__(message)
        self.kind = kind


# ---- AppleScript plumbing -------------------------------------------------


def _escape_for_applescript(js: str) -> str:
    """Escape a JS payload for embedding inside an AppleScript double-quoted
    string. Only backslash and double-quote are special there."""
    return js.replace("\\", "\\\\").replace('"', '\\"')


def _wrap_in_linkedin_tab(js: str) -> str:
    """AppleScript that runs `js` in the FIRST Safari tab whose URL is on
    linkedin.com; returns a sentinel if none is open."""
    escaped = _escape_for_applescript(js)
    return (
        'tell application "Safari"\n'
        "  repeat with w in windows\n"
        "    repeat with t in tabs of w\n"
        '      if (URL of t) contains "linkedin.com" then\n'
        f'        return (do JavaScript "{escaped}" in t)\n'
        "      end if\n"
        "    end repeat\n"
        "  end repeat\n"
        '  return "__GRIP_NO_LINKEDIN_TAB__"\n'
        "end tell"
    )


def _run_safari_js(js: str, timeout: int = _OSA_TIMEOUT_S) -> str:
    """Run `js` in the operator's LinkedIn Safari tab; return stripped stdout.

    Raises SafariExtractionError with actionable text on the two structural
    failures: no LinkedIn tab, or the Apple-Events JS bridge being disabled.
    """
    script = _wrap_in_linkedin_tab(js)
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SafariExtractionError(
            f"Safari automation timed out after {timeout}s — is Safari responsive?",
            kind="timeout",
        ) from exc
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if "__GRIP_NO_LINKEDIN_TAB__" in out:
        raise SafariExtractionError(
            "No linkedin.com tab open in Safari — open your LinkedIn feed in "
            "Safari (logged in), then re-run.",
            kind="preflight",
        )
    if proc.returncode != 0:
        # The canonical failure: the Develop-menu bridge is off. Errors here are
        # emitted on stderr by osascript (e.g. -1743 / "not allowed to send
        # Apple events" or the JS-from-Apple-Events refusal).
        raise SafariExtractionError(
            "Safari rejected the automation. Fix: (a) Safari > Settings > "
            "Advanced > 'Show Develop menu'; (b) Develop > 'Allow JavaScript "
            "from Apple Events'; (c) grant Automation permission when prompted. "
            f"osascript said: {err or 'non-zero exit'}",
            kind="preflight",
        )
    return out


# ---- The in-page JS (the elegant core) ------------------------------------


def build_hydrate_js(max_posts: int, nonce: str = "") -> str:
    """The kick-off payload: read CSRF, collect activity URNs from the feed DOM,
    hydrate each via a same-origin Voyager fetch, store into window globals.

    Returns immediately with 'started' (the async IIFE is not awaited — Python
    polls `build_poll_js(nonce)` for the result). ``nonce`` (hex, JS-safe) is
    stamped into ``window.__gripNonce`` so the poll can detect a navigated or
    reloaded page (wiped globals) instead of waiting out the full timeout
    (AGORA #3903 DEFER concern 1)."""
    return (
        "(function(){"
        f"window.__gripNonce='{nonce}';"
        "window.__gripDone=false;window.__gripFeed=null;"
        "(async function(){try{"
        "var m=document.cookie.match(/JSESSIONID=([^;]+)/);"
        "var csrf=(m?m[1]:'').replace(/\"/g,'');"
        "var urns=Array.from(new Set(Array.prototype.slice.call("
        "document.querySelectorAll('[data-urn^=\"urn:li:activity:\"]'))"
        ".map(function(e){return e.getAttribute('data-urn');})))"
        f".slice(0,{int(max_posts)});"
        "var out=[];"
        "for(var i=0;i<urns.length;i++){try{"
        "var r=await fetch('/voyager/api/feed/updates/'+encodeURIComponent(urns[i]),"
        "{headers:{'accept':'application/vnd.linkedin.normalized+json+2.1',"
        "'csrf-token':csrf,'x-restli-protocol-version':'2.0.0','x-li-lang':'en_US'},"
        "credentials:'include'});"
        "if(r.ok){out.push(await r.json());}"
        "}catch(e){}}"
        "window.__gripFeed=out;"
        "}catch(e){window.__gripFeed={error:String(e)};}"
        "finally{window.__gripDone=true;}})();"
        "})(); 'started';"
    )


def build_scroll_js(fraction: float = 0.9) -> str:
    """Scroll the feed to load more cards (more data-urn nodes) between runs."""
    return f"window.scrollBy(0, window.innerHeight*{float(fraction)}); 'scrolled';"


_CARD_COUNT_JS = (
    "document.querySelectorAll('[data-urn^=\"urn:li:activity:\"]').length;"
)


def _card_count() -> int:
    """Feed-card count in the tab, or -1 when unmeasurable. Fail-open: the
    scroll settle is best-effort and must never kill the run (AppleScript may
    stringify the number as '12' or '12.0')."""
    try:
        return int(float(_run_safari_js(_CARD_COUNT_JS)))
    except (SafariExtractionError, ValueError):
        return -1


def _scroll_and_settle(min_s: float, max_s: float) -> None:
    """Scroll, then wait for the DOM to actually gain feed cards instead of a
    blind random sleep (AGORA #3903 DEFER concern 4 — the scroll's lazy-load is
    async, and a fixed delay can undershoot slow connections). Bounded by
    [min_s, max_s]; returns quietly either way — more cards is an optimisation,
    never a requirement."""
    before = _card_count()
    _run_safari_js(build_scroll_js())
    time.sleep(min_s)  # politeness floor, matches the prior minimum delay
    if before < 0:
        return  # cannot measure growth — the floor delay is all we can do
    deadline = time.monotonic() + max(0.0, max_s - min_s)
    while time.monotonic() < deadline:
        if _card_count() > before:
            return
        time.sleep(0.5)


# Poll contract: nonce mismatch (page navigated/reloaded → globals wiped, or a
# stale invocation) → the CTX_LOST sentinel; done → JSON payload; else '' (still
# running). The nonce check makes tab loss a DETECTED state, not a timeout.
_CTX_LOST_SENTINEL = "__GRIP_CTX_LOST__"


def _poll_for_feed(nonce: str = "", timeout_s: float = _POLL_TIMEOUT_S) -> list:
    """Poll the page global until the async hydration finishes; return the raw
    list of Voyager JSON responses. Raises with a specific ``kind`` on the
    in-page error envelope, a lost page context, or a genuine timeout."""
    poll_js = build_poll_js(nonce)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        raw = _run_safari_js(poll_js)
        if raw == _CTX_LOST_SENTINEL:
            raise SafariExtractionError(
                "The LinkedIn tab navigated, reloaded or was replaced "
                "mid-hydration (page globals lost) — re-run once the feed "
                "tab is stable.",
                kind="context-lost",
            )
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "error" in parsed:
                raise SafariExtractionError(f"In-page error: {parsed['error']}")
            return parsed if isinstance(parsed, list) else []
        time.sleep(random.uniform(_POLL_INTERVAL_MIN_S, _POLL_INTERVAL_MAX_S))
    raise SafariExtractionError(
        f"Timed out after {timeout_s}s waiting for the feed hydration — the "
        "tab is open and its page context is intact (nonce verified), but the "
        "in-page Voyager fetches never completed; LinkedIn may be slow or "
        "throttling. A retry may succeed.",
        kind="timeout",
    )


# ---- Pure parse (testable without Safari) ---------------------------------


def parse_voyager_batch(batch: list, seen: set[str], max_posts: int) -> list[Post]:
    """Map the reused `_parse_voyager_post` over the raw Voyager batch, dedup
    against the cross-run `seen` ledger, and cap. Pure — the unit-testable core."""
    posts: list[Post] = []
    for item in batch:
        if len(posts) >= max_posts:
            break
        if not isinstance(item, dict):
            continue
        post = _parse_voyager_post(item)
        if post is None or post.post_id in seen:
            continue
        posts.append(post)
    return posts


# ---- Preflight + main entry -----------------------------------------------


def preflight() -> tuple[bool, str]:
    """Verify the Safari automation path is live: the Apple-Events JS bridge is
    on AND a logged-in LinkedIn tab is open. Returns (ok, message)."""
    try:
        two = _run_safari_js("1+1;")
    except SafariExtractionError as exc:
        return False, str(exc)
    # AppleScript coerces the JS number 2 to a real, stringified "2.0" — compare
    # numerically, not against the literal "2" (a live-run finding: the test mock
    # returned "2" and hid this real-Safari coercion).
    try:
        bridge_ok = abs(float(two.strip()) - 2.0) < 1e-9
    except ValueError:
        bridge_ok = False
    if not bridge_ok:
        return False, f"Safari JS bridge returned {two!r}, expected 2."
    # li_at is httpOnly (invisible to document.cookie), so login CANNOT be
    # detected via li_at (a live-run finding — the mock hid it). Detect it via
    # JSESSIONID presence AND the absence of an auth/join wall.
    signed_in = _run_safari_js(
        "((document.cookie.indexOf('JSESSIONID=')>-1)"
        "&&location.href.indexOf('/login')<0"
        "&&location.href.indexOf('authwall')<0"
        "&&!document.querySelector('.authwall,.join-form'))?'yes':'no';"
    )
    if signed_in != "yes":
        return False, (
            "Safari has no logged-in LinkedIn session — open linkedin.com in "
            "Safari and sign in, then re-run."
        )
    return True, "Safari path ready."


def _navigate_to_feed(feed_url: str, timeout_s: float = 15.0) -> None:
    """Point the LinkedIn Safari tab at the feed and wait for it to load.

    The operator may be on any LinkedIn page; the activity cards that supply the
    URNs only exist on /feed/ (a live-run finding: the tab was on /mynetwork/,
    0 cards). Navigates only if not already on the feed; the reload naturally
    resets the window globals the hydration JS re-initialises. Raises on a
    non-LinkedIn feed_url (injection guard) or if the feed does not load."""
    # Injection guard (AGORA #3904): feed_url is interpolated into a JS string,
    # so refuse anything but a quote-free https LinkedIn URL.
    if not (
        feed_url.startswith("https://www.linkedin.com/")
        and not any(c in feed_url for c in "'\"\\<>")
    ):
        raise SafariExtractionError(f"Refusing non-LinkedIn feed URL: {feed_url!r}")
    _run_safari_js(
        f"if(location.href.indexOf('/feed')<0){{location.href='{feed_url}';}} 'nav';"
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ready = _run_safari_js(
            "(location.href.indexOf('/feed')>-1 && document.readyState==='complete')"
            "?'ready':'';"
        )
        if ready == "ready":
            return
        time.sleep(1.0)
    raise SafariExtractionError(
        f"LinkedIn feed did not load in Safari within {timeout_s}s (tab closed?)."
    )


def extract_feed_safari(
    config: Optional[FeedConfig] = None,
) -> tuple[list[Post], dict]:
    """Safari analogue of `linkedin_feed.extract_feed`. Same return contract:
    (new_posts, telemetry). Reuses the Brave path's persistence + parser."""
    cfg = config or FeedConfig()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    telemetry = {
        "scroll_count": 0,
        "raw_extracted": 0,
        "deduplicated": 0,
        "errors": [],
        "extract_path": "safari_voyager",
        "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }

    ok, msg = preflight()
    if not ok:
        raise SafariExtractionError(msg)

    seen = load_seen(cfg.state_dir)
    _navigate_to_feed(cfg.feed_url)  # ensure the tab is on /feed/ (URNs live there)
    # A gentle scroll first so the DOM carries more feed cards (more URNs);
    # settles on measured card growth rather than a blind delay.
    try:
        _scroll_and_settle(cfg.scroll_delay_min_s, cfg.scroll_delay_max_s)
        telemetry["scroll_count"] = 1
    except SafariExtractionError as exc:
        telemetry["errors"].append(f"scroll: {exc}")

    # Per-invocation nonce: lets the poll detect a navigated/reloaded page
    # (or a stale prior invocation) instead of hanging to timeout.
    nonce = f"g{time.monotonic_ns():x}"
    _run_safari_js(build_hydrate_js(cfg.max_posts, nonce))  # kick off hydration
    batch = _poll_for_feed(nonce)
    telemetry["raw_extracted"] = len(batch)

    posts = parse_voyager_batch(batch, seen, cfg.max_posts)
    telemetry["deduplicated"] = len(posts)
    telemetry["finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    _persist(cfg, posts)
    return posts, telemetry


__all__ = [
    "SafariExtractionError",
    "build_hydrate_js",
    "build_poll_js",
    "build_scroll_js",
    "extract_feed_safari",
    "parse_voyager_batch",
    "preflight",
]


def build_poll_js(nonce: str = "") -> str:
    """The tiny poll payload: verify the invocation nonce first (a wiped or
    foreign page global returns the CTX_LOST sentinel), then gate on done."""
    return (
        f"(window.__gripNonce!=='{nonce}')?'{_CTX_LOST_SENTINEL}':"
        "((window.__gripDone===true)?JSON.stringify(window.__gripFeed):'');"
    )
