"""LinkedIn Feed Scout — Tier-1 feed extraction via CDP-attach to V>>'s Brave.

Tier-1 default per plans/log-into-linkedin-with-polished-flurry.md:
attaches to V>>'s actually-installed Brave (launched via bin/brave-cdp
with `--remote-debugging-port=9222`) over Chrome DevTools Protocol.
Reuses V>>'s real session, fingerprint, and home network. Nothing to
spoof — same shape LinkedIn has seen from V>> for years.

Extraction strategy (ordered, primary-first):

  PATH 1 — Voyager API capture (primary, since ~2026-05)
    LinkedIn now delivers its feed as RSC (React Server Components) streaming
    via `flagship-web/rsc-action/actions/pagination`. We use CDP Fetch.enable
    to intercept that response, regex-extract `urn:li:activity:\\d+` from the
    RSC wire body, then call the stable Voyager per-post API
    (`/voyager/api/feed/updates/{urn}`) once per activity to get full post data.
    Auth is the live session from V>>'s Brave (cookies + CSRF from JSESSIONID).
    Telemetry records `extract_path = "voyager_api"`.

  PATH 2 — CSS selector DOM scrape (fallback)
    The original Playwright DOM scraper. Now rarely fires because LinkedIn's
    DOM obfuscation removed the reliable selectors. Kept for resilience:
    if the Voyager API shape changes, this path may still recover partial data.
    Telemetry records `extract_path = "css_fallback"`.

Read-only contract: this module reads feed content only. It NEVER:
  - clicks like / comment / share / connect / message buttons
  - submits forms
  - calls any LinkedIn POST/PUT/DELETE endpoint
The publish gate stays with V>> (manual paste into Brave).

Tier-2 stealth (`--mode stealth`) is deferred to lib/linkedin_stealth.py
and only imported when the operator explicitly opts in (KISS).
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import itertools
import json
import logging
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from linkedin_scorer import Post


log = logging.getLogger(__name__)


# ---- Pacing + limits (human-envelope defaults; tunable via FeedConfig) -----

DEFAULT_SCROLL_DELAY_MIN_S = 3.0
DEFAULT_SCROLL_DELAY_MAX_S = 8.0
DEFAULT_MAX_SCROLLS = 25
DEFAULT_MAX_POSTS_PER_RUN = 50
DEFAULT_CDP_URL = "http://localhost:9222"


@dataclass
class FeedConfig:
    """Tunable pacing for one feed-extraction run."""

    cdp_url: str = DEFAULT_CDP_URL
    cdp_timeout_ms: int = 10000  # per-attempt timeout for connect_over_cdp
    cdp_retry_attempts: int = 3  # transient-flap retries (Brave still booting)
    cdp_retry_backoff_s: float = 2.0  # seconds between retries
    scroll_delay_min_s: float = DEFAULT_SCROLL_DELAY_MIN_S
    scroll_delay_max_s: float = DEFAULT_SCROLL_DELAY_MAX_S
    max_scrolls: int = DEFAULT_MAX_SCROLLS
    max_posts: int = DEFAULT_MAX_POSTS_PER_RUN
    feed_url: str = "https://www.linkedin.com/feed/"
    state_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "state" / "linkedin"
    )


# ---- Post extraction (LinkedIn DOM selectors, with fallbacks) ------------

# LinkedIn DOM is obfuscated but stable on these data-attribute hooks +
# semantic class prefixes. Multiple fallbacks per field cover the slow
# A/B-rollout drift LinkedIn ships ~quarterly. Update when extraction
# yields empty fields on a known-good feed — first symptom of selector rot.
_POST_CONTAINER_SELECTORS = (
    "div[data-finite-scroll-hotkey-item]",
    "div.feed-shared-update-v2",
    "div[data-urn^='urn:li:activity:']",
)
_AUTHOR_NAME_SELECTORS = (
    ".update-components-actor__name span[dir='ltr'] span",
    ".update-components-actor__name span[dir='ltr']",
    ".feed-shared-actor__name",
)
_AUTHOR_HEADLINE_SELECTORS = (
    ".update-components-actor__description span[dir='ltr']",
    ".update-components-actor__description",
    ".feed-shared-actor__description",
)
_BODY_TEXT_SELECTORS = (
    ".feed-shared-update-v2__description .break-words",
    ".feed-shared-update-v2__description",
    ".update-components-text span[dir='ltr']",
    "[data-test-id='feed-shared-update-v2__description']",
)
_PERMALINK_SELECTORS = (
    "a[href*='/feed/update/']",
    "a[href*='/posts/']",
)


def _hash_post_id(url_or_urn: str) -> str:
    """SHA-256 hex digest (first 16 chars) of a URL/URN — dedup key."""
    return hashlib.sha256(url_or_urn.encode("utf-8", errors="replace")).hexdigest()[:16]


# ---- Voyager API capture (Path 1 — primary) --------------------------------

# RSC pagination endpoint URL fragment — what we intercept.
_RSC_PAGINATION_URL_FRAGMENT = "pagination?sduiid=com.linkedin.sdui.pagers.feed.mainFeed"

# Regex to pull activity URNs from the RSC wire body.
_ACTIVITY_URN_RE = re.compile(r"urn:li:activity:(\d+)")

# Voyager single-post API endpoint template.
_VOYAGER_POST_TMPL = "https://www.linkedin.com/voyager/api/feed/updates/{urn}"

# Voyager API request timeout (seconds).
_VOYAGER_TIMEOUT_S = 10

# Maximum URNs to hydrate in one run (bounds API calls to ~max_posts).
_MAX_URNS_TO_HYDRATE = 80


def _cookies_from_context(context) -> dict[str, str]:
    """Extract all cookies for linkedin.com from a Playwright BrowserContext."""
    try:
        cookies = context.cookies("https://www.linkedin.com")
        return {c["name"]: c["value"] for c in cookies}
    except Exception:
        return {}


def _csrf_token_from_cookies(cookies: dict[str, str]) -> str:
    """Extract LinkedIn CSRF token: the value of the JSESSIONID cookie
    (LinkedIn's CSRF scheme: CSRF token == JSESSIONID value, no ajax_ prefix).
    """
    raw = cookies.get("JSESSIONID", "")
    # JSESSIONID is typically quoted: "ajax:1234567890" — strip quotes.
    return raw.strip('"').strip("'")


def _build_voyager_request(urn: str, cookies: dict[str, str], csrf_token: str):
    """Build a urllib Request for the Voyager single-post API."""
    encoded_urn = urllib.parse.quote(urn, safe="")
    url = _VOYAGER_POST_TMPL.format(urn=encoded_urn)
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    headers = {
        "accept": "application/vnd.linkedin.normalized+json+2.1",
        "csrf-token": csrf_token,
        "x-restli-protocol-version": "2.0.0",
        "x-li-lang": "en_US",
        "referer": "https://www.linkedin.com/feed/",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
        "cookie": cookie_header,
    }
    return urllib.request.Request(url, headers=headers, method="GET")


def _select_update_v2(included: list, activity_urn: str) -> Optional[dict]:
    """Pick the UpdateV2 entry that corresponds to `activity_urn`.

    A Voyager single-post response can carry MORE THAN ONE UpdateV2 in
    `included` — e.g. a reshare embeds the original post's UpdateV2. Breaking
    on the first one risks returning the WRONG post's content (cross-post data
    leakage, AGORA #3337). Match by URN: prefer the UpdateV2 whose entityUrn /
    updateMetadata.urn contains the requested activity_urn; fall back to the
    first UpdateV2 only when none carry an identifying urn (the common
    single-post case, where matching is unambiguous anyway).
    """
    update_items = [
        it for it in included
        if isinstance(it, dict) and "UpdateV2" in it.get("$type", "")
    ]
    if activity_urn:
        for it in update_items:
            entity_urn = it.get("entityUrn", "") or ""
            meta = it.get("updateMetadata") or {}
            meta_urn = meta.get("urn", "") if isinstance(meta, dict) else ""
            if activity_urn in entity_urn or activity_urn == meta_urn:
                return it
    return update_items[0] if update_items else None


def _parse_voyager_post(data: dict) -> Optional[Post]:
    """Parse a Voyager single-post API response into a Post.

    Response shape:
      data: { urn, permalink, $type, ... }
      included: [ { $type: "com.linkedin.voyager.feed.render.UpdateV2",
                    entityUrn: "...(urn:li:activity:NNN)...",
                    actor: { name: {text}, description: {text} },
                    commentary: { text: {text} },
                    ... }, ... ]
    """
    top = data.get("data", {})
    activity_urn = top.get("urn", "")
    permalink = top.get("permalink", "")
    if not activity_urn:
        return None

    url = permalink or f"https://www.linkedin.com/feed/update/{activity_urn}"
    author_display = ""
    author_headline = ""
    body_text = ""
    timestamp = ""

    item = _select_update_v2(data.get("included", []), activity_urn)
    if item is not None:
        actor = item.get("actor") or {}
        name_obj = actor.get("name") or {}
        desc_obj = actor.get("description") or {}
        author_display = name_obj.get("text", "") if isinstance(name_obj, dict) else ""
        author_headline = desc_obj.get("text", "") if isinstance(desc_obj, dict) else ""
        commentary = item.get("commentary") or {}
        text_obj = commentary.get("text") or {}
        body_text = text_obj.get("text", "") if isinstance(text_obj, dict) else ""

    if not (author_display or body_text):
        return None

    return Post(
        post_id=_hash_post_id(activity_urn),
        url=url,
        author_display=author_display,
        author_headline=author_headline,
        body_text=body_text,
        timestamp=timestamp,
    )


def _hydrate_urns(
    urns: list[str],
    cookies: dict[str, str],
    csrf_token: str,
    max_posts: int,
    telemetry: dict,
) -> list[Post]:
    """Fetch Voyager single-post API for each URN; return Post list."""
    posts: list[Post] = []
    for urn in urns[: min(len(urns), _MAX_URNS_TO_HYDRATE)]:
        if len(posts) >= max_posts:
            break
        req = _build_voyager_request(urn, cookies, csrf_token)
        try:
            with urllib.request.urlopen(req, timeout=_VOYAGER_TIMEOUT_S) as resp:
                raw = resp.read()
                data = json.loads(raw)
        except urllib.error.HTTPError as exc:
            log.warning("Voyager API %s: HTTP %d", urn, exc.code)
            telemetry["errors"].append(f"voyager_http_{exc.code}:{urn}")
            continue
        except json.JSONDecodeError as exc:
            # A 204 / redirect / HTML body (e.g. an auth-wall interstitial)
            # decodes but isn't JSON. Telemeter distinctly from a network
            # error so the failure mode is diagnosable (AGORA #3337).
            log.warning("Voyager API %s: non-JSON body: %s", urn, exc)
            telemetry["errors"].append(f"voyager_parse:{urn}:{exc}")
            continue
        except Exception as exc:
            log.warning("Voyager API %s: %s", urn, exc)
            telemetry["errors"].append(f"voyager_error:{urn}:{exc}")
            continue
        telemetry["raw_extracted"] += 1
        post = _parse_voyager_post(data)
        if post is not None:
            posts.append(post)
    return posts


def _resume_fetch(cdp, request_id: str, is_response_stage: bool, telemetry: dict) -> None:
    """Resume a paused Fetch event with the stage-correct verb.

    A pause at the Response stage MUST be resumed with Fetch.continueResponse;
    a Request-stage pause uses Fetch.continueRequest — the wrong verb can leave
    the request hung (AGORA #3337). Best-effort: a failed resume can't be
    usefully retried (the page's own timeout covers it), but it IS recorded in
    telemetry so a real CDP fault is observable rather than silently masked.
    """
    verb = "Fetch.continueResponse" if is_response_stage else "Fetch.continueRequest"
    try:
        cdp.send(verb, {"requestId": request_id})
    except Exception as exc:
        telemetry["errors"].append(f"fetch_resume ({verb}): {exc}")


def _make_fetch_handler(cdp, rsc_state: list, telemetry: dict):
    """Return a CDP Fetch.requestPaused handler that writes the RSC body into
    rsc_state[0] (a one-element list used as a mutable cell).

    Fetch.enable is scoped to the RSC pagination URL only (see
    _intercept_rsc_body), so in practice only the feed-pagination response is
    paused; the else-branch is a defensive stage-correct resume for anything
    else. Isolated into its own function so its branches don't inflate the CC
    of the coordinator (_intercept_rsc_body).
    """
    def handler(params: dict) -> None:
        url = params.get("request", {}).get("url", "")
        request_id = params.get("requestId", "")
        # responseStatusCode is only present on the Response stage.
        is_response_stage = params.get("responseStatusCode") is not None

        if _RSC_PAGINATION_URL_FRAGMENT in url and is_response_stage:
            try:
                body_resp = cdp.send("Fetch.getResponseBody", {"requestId": request_id})
                body = body_resp.get("body", "")
                if body_resp.get("base64Encoded"):
                    body = base64.b64decode(body).decode("utf-8", errors="replace")
                rsc_state[0] = body
            except Exception as exc:
                telemetry["errors"].append(f"fetch_body: {exc}")
            finally:
                _resume_fetch(cdp, request_id, is_response_stage, telemetry)
        else:
            _resume_fetch(cdp, request_id, is_response_stage, telemetry)

    return handler


def _wait_for_rsc(rsc_state: list, timeout_s: float = 20.0) -> None:
    """Spin until rsc_state[0] is non-empty or timeout_s elapses."""
    deadline = time.monotonic() + timeout_s
    while not rsc_state[0] and time.monotonic() < deadline:
        time.sleep(0.2)


def _scroll_for_more_rsc(page, cfg: FeedConfig, rsc_state: list) -> None:
    """Scroll the feed a few times so LinkedIn triggers more RSC pagination."""
    for _ in range(min(cfg.max_scrolls, 3)):
        if len(_ACTIVITY_URN_RE.findall(rsc_state[0])) >= cfg.max_posts:
            break
        try:
            page.evaluate("window.scrollBy(0, window.innerHeight * 0.9)")
            time.sleep(random.uniform(cfg.scroll_delay_min_s, cfg.scroll_delay_max_s))
        except Exception:
            pass


def _intercept_rsc_body(page, context, cfg: FeedConfig, telemetry: dict) -> str:
    """Coordinator: attach CDP Fetch interception, navigate, scroll, return body.

    Delegates the handler closure, spin-wait, and scroll loop to focused
    helpers so each piece stays below the CC threshold.
    """
    try:
        cdp = context.new_cdp_session(page)
    except Exception as exc:
        telemetry["errors"].append(f"cdp_session: {exc}")
        return ""

    rsc_state: list = [""]  # mutable cell shared with the closure
    try:
        cdp.on("Fetch.requestPaused", _make_fetch_handler(cdp, rsc_state, telemetry))
        # Scope interception to the feed RSC pagination stream ONLY. A "*"
        # pattern would pause EVERY response on the page and round-trip each
        # through Python, interfering with page loads (AGORA #3337). The
        # mainFeed pager substring is stable + unique; '.' is a literal in a
        # CDP urlPattern (only '*' and '?' are wildcards), so no escaping.
        cdp.send("Fetch.enable", {
            "patterns": [{
                "urlPattern": "*com.linkedin.sdui.pagers.feed.mainFeed*",
                "requestStage": "Response",
            }],
        })
        try:
            page.goto(cfg.feed_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            telemetry["errors"].append(f"goto: {exc}")
        _wait_for_rsc(rsc_state)
        _scroll_for_more_rsc(page, cfg, rsc_state)
    finally:
        try:
            cdp.send("Fetch.disable")
        except Exception:
            pass
        try:
            cdp.detach()
        except Exception:
            pass

    return rsc_state[0]


def _dedup_new_urns(raw_uids: list[str], seen: set[str]) -> list[str]:
    """Build a de-duplicated 'urn:li:activity:<id>' list from raw activity ids.

    Skips any URN whose post_id hash is already in the cross-run `seen` ledger,
    and collapses intra-batch duplicates while preserving discovery order. Pure
    function — the testable core of the Voyager-path dedup (AGORA #3337).
    """
    seen_urns: set[str] = set()
    unique: list[str] = []
    for uid in raw_uids:
        urn = f"urn:li:activity:{uid}"
        if urn in seen_urns or _hash_post_id(urn) in seen:
            continue
        seen_urns.add(urn)
        unique.append(urn)
    return unique


def _extract_via_voyager_api(
    page,
    context,
    cfg: FeedConfig,
    seen: set[str],
    telemetry: dict,
) -> list[Post]:
    """Path 1: capture RSC pagination → extract URNs → hydrate via Voyager API.

    Returns a (possibly empty) list of new Posts. On any non-fatal error,
    logs to telemetry and returns whatever was recovered (best-effort).
    """
    cookies = _cookies_from_context(context)
    if not cookies.get("li_at"):
        telemetry["errors"].append("voyager_api: no li_at cookie — not logged in")
        return []

    csrf_token = _csrf_token_from_cookies(cookies)
    if not csrf_token:
        telemetry["errors"].append("voyager_api: no JSESSIONID — cannot build CSRF")
        return []

    rsc_body = _intercept_rsc_body(page, context, cfg, telemetry)
    if not rsc_body:
        telemetry["errors"].append("voyager_api: no RSC body captured")
        return []

    # Deduplicate URNs (intra-batch + against the cross-run seen ledger)
    # while preserving discovery order. Extracted to a pure helper so the
    # dedup contract is unit-testable without Playwright (AGORA #3337).
    unique_urns = _dedup_new_urns(_ACTIVITY_URN_RE.findall(rsc_body), seen)

    log.info("voyager_api: %d unique new URNs from RSC body", len(unique_urns))

    posts = _hydrate_urns(unique_urns, cookies, csrf_token, cfg.max_posts, telemetry)
    return posts


def _try_selectors(node, selectors: Iterable[str], attr: Optional[str] = None) -> str:
    """First selector that returns a non-empty result wins; else empty string."""
    for sel in selectors:
        try:
            el = node.query_selector(sel)
            if el is None:
                continue
            val = el.get_attribute(attr) if attr else el.inner_text()
            if val and val.strip():
                return val.strip()
        except Exception:
            continue
    return ""


def _extract_one(node) -> Optional[Post]:
    """Extract one Post from a feed-card DOM node; None if too thin."""
    urn = ""
    try:
        urn = node.get_attribute("data-urn") or ""
    except Exception:
        pass

    permalink = _try_selectors(node, _PERMALINK_SELECTORS, attr="href")
    if permalink and permalink.startswith("/"):
        permalink = f"https://www.linkedin.com{permalink}"

    id_source = urn or permalink
    if not id_source:
        return None

    author_display = _try_selectors(node, _AUTHOR_NAME_SELECTORS)
    author_headline = _try_selectors(node, _AUTHOR_HEADLINE_SELECTORS)
    body_text = _try_selectors(node, _BODY_TEXT_SELECTORS)

    if not (author_display or body_text):
        return None

    return Post(
        post_id=_hash_post_id(id_source),
        url=permalink or f"https://www.linkedin.com/feed/update/{urn}",
        author_display=author_display,
        author_headline=author_headline,
        body_text=body_text,
        timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
    )


# ---- Dedup ledger ---------------------------------------------------------


def load_seen(state_dir: Path) -> set[str]:
    """Load the cross-run dedup ledger (set of post_ids already seen)."""
    seen_file = state_dir / "seen.jsonl"
    if not seen_file.exists():
        return set()
    seen: set[str] = set()
    for line in seen_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            seen.add(json.loads(line)["post_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return seen


def append_seen(state_dir: Path, posts: list[Post]) -> None:
    """Append newly-extracted posts to the dedup ledger."""
    state_dir.mkdir(parents=True, exist_ok=True)
    seen_file = state_dir / "seen.jsonl"
    ts = datetime.datetime.now(datetime.UTC).isoformat()
    with open(seen_file, "a", encoding="utf-8") as fh:
        for p in posts:
            fh.write(json.dumps({"post_id": p.post_id, "url": p.url, "seen_at": ts}) + "\n")


def write_snapshot(state_dir: Path, posts: list[Post]) -> Path:
    """Write a per-run snapshot to state_dir/feed-snapshots/<ts>.jsonl."""
    snap_dir = state_dir / "feed-snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = snap_dir / f"{ts}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for p in posts:
            fh.write(json.dumps({
                "post_id": p.post_id,
                "url": p.url,
                "author_display": p.author_display,
                "author_headline": p.author_headline,
                "body_text": p.body_text,
                "timestamp": p.timestamp,
            }) + "\n")
    return path


# ---- Playwright driver (Tier-1 CDP attach) --------------------------------


class CDPConnectionError(RuntimeError):
    """Raised when Playwright cannot reach the CDP endpoint.

    Usually means: V>> hasn't launched Brave via `bin/brave-cdp` yet,
    OR Brave is launched but a different port is in use, OR another
    process has already taken port 9222.
    """


def _connect_browser(pw, cfg: FeedConfig):
    """Connect Playwright to V>>'s Brave over CDP with bounded retry.

    Transient CDP socket flaps are common when Brave is still booting
    (AGORA #3162). We retry `cfg.cdp_retry_attempts` times with
    `cfg.cdp_retry_backoff_s` between attempts, then surface a single
    operator-actionable CDPConnectionError.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, cfg.cdp_retry_attempts + 1):
        try:
            return pw.chromium.connect_over_cdp(cfg.cdp_url, timeout=cfg.cdp_timeout_ms)
        except Exception as exc:
            last_exc = exc
            log.warning(
                "CDP connect attempt %d/%d failed: %s",
                attempt, cfg.cdp_retry_attempts, exc,
            )
            if attempt < cfg.cdp_retry_attempts:
                time.sleep(cfg.cdp_retry_backoff_s)
    raise CDPConnectionError(
        f"Cannot connect to Brave CDP at {cfg.cdp_url} after"
        f" {cfg.cdp_retry_attempts} attempts — is Brave launched via"
        f" bin/brave-cdp? Last error: {last_exc}"
    ) from last_exc


def _is_linkedin_page(page) -> bool:
    """Predicate isolated so we can flatten the contexts × pages scan."""
    try:
        return "linkedin.com" in (page.url or "")
    except Exception:
        return False


def _find_linkedin_page(browser, feed_url: str):
    """Locate a Brave tab already on linkedin.com; open one if none exist.

    Flattens contexts × pages into a single iterator so we scan once
    (O(total_pages)) instead of nesting loops over the Playwright tree.
    """
    contexts = browser.contexts
    if not contexts:
        return browser.new_context().new_page()
    all_pages = itertools.chain.from_iterable(c.pages for c in contexts)
    found = next((p for p in all_pages if _is_linkedin_page(p)), None)
    if found is not None:
        return found
    page = contexts[0].new_page()
    page.goto(feed_url, wait_until="domcontentloaded", timeout=30000)
    return page


def _ensure_logged_in(page) -> None:
    """Detect a login redirect and bail with operator-actionable text."""
    url = (page.url or "").lower()
    if "login" in url or "uas/login" in url:
        raise CDPConnectionError(
            "Brave is on a LinkedIn login page — log in manually in"
            " Brave first, then re-run the scout."
        )


def _scroll_once(page, telemetry: dict) -> None:
    """Best-effort scroll; record any failure as telemetry, don't crash."""
    try:
        page.evaluate("window.scrollBy(0, window.innerHeight * 0.9)")
    except Exception as exc:
        telemetry["errors"].append(f"scroll: {exc}")
        raise


def _extract_from_selector(page, selector: str, telemetry: dict):
    """Query one selector; return node list or empty on error."""
    try:
        return page.query_selector_all(selector)
    except Exception as exc:
        telemetry["errors"].append(f"query {selector}: {exc}")
        return []


def _ingest_node(node, extracted_by_id, seen, cfg, telemetry) -> bool:
    """Extract + dedup one DOM node; return True when cap reached."""
    try:
        post = _extract_one(node)
    except Exception as exc:
        telemetry["errors"].append(f"extract: {exc}")
        return False
    if post is None:
        return False
    telemetry["raw_extracted"] += 1
    if post.post_id in extracted_by_id or post.post_id in seen:
        return False
    extracted_by_id[post.post_id] = post
    return len(extracted_by_id) >= cfg.max_posts


def _extract_visible_posts(
    page,
    extracted_by_id: dict[str, Post],
    seen: set[str],
    cfg: FeedConfig,
    telemetry: dict,
) -> bool:
    """Extract all visible feed cards on `page`; True if cap reached."""
    nodes = _first_non_empty_node_list(page, telemetry)
    return any(
        _ingest_node(n, extracted_by_id, seen, cfg, telemetry) for n in nodes
    )


def _first_non_empty_node_list(page, telemetry: dict) -> list:
    """Return the first selector's node list that is non-empty (else [])."""
    candidates = map(
        lambda sel: _extract_from_selector(page, sel, telemetry),
        _POST_CONTAINER_SELECTORS,
    )
    return next(filter(None, candidates), [])


def _scroll_and_extract(
    page,
    cfg: FeedConfig,
    seen: set[str],
    telemetry: dict,
) -> dict[str, Post]:
    """Inner scroll-and-extract loop. Pure orchestration; helpers do the work.

    Tolerates up to MAX_CONSECUTIVE_SCROLL_ERRORS transient scroll
    failures before bailing — one CDP hiccup should not abort the whole
    feed walk (AGORA round-3). Total per-error events captured in
    telemetry["errors"] for post-run inspection.
    """
    max_consecutive_errors = 3
    consecutive_errors = 0
    extracted_by_id: dict[str, Post] = {}
    for scroll_idx in range(cfg.max_scrolls):
        telemetry["scroll_count"] = scroll_idx + 1
        if _extract_visible_posts(page, extracted_by_id, seen, cfg, telemetry):
            break
        try:
            _scroll_once(page, telemetry)
            consecutive_errors = 0
        except Exception:
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                log.warning(
                    "Aborting scroll loop after %d consecutive errors",
                    consecutive_errors,
                )
                break
            # Brief backoff before retrying — let the page settle
            time.sleep(1.0)
            continue
        time.sleep(random.uniform(cfg.scroll_delay_min_s, cfg.scroll_delay_max_s))
    return extracted_by_id


def _make_telemetry() -> dict:
    """Empty telemetry envelope (one entry per extract_feed call)."""
    return {
        "scroll_count": 0,
        "raw_extracted": 0,
        "deduplicated": 0,
        "errors": [],
        "extract_path": "",  # "voyager_api" | "css_fallback" | "none"
        "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


def _persist(cfg: FeedConfig, posts: list[Post]) -> None:
    """Snapshot + dedup-ledger writes (always, even on partial extraction)."""
    if not posts:
        return
    write_snapshot(cfg.state_dir, posts)
    append_seen(cfg.state_dir, posts)


def _run_extraction(
    browser,
    cfg: FeedConfig,
    seen: set[str],
    telemetry: dict,
) -> list[Post]:
    """Try Voyager API path first; fall back to CSS selector scrape.

    Path 1 (voyager_api): CDP Fetch interception + Voyager per-post API.
    Path 2 (css_fallback): DOM scroll-and-scrape (pre-2026 method).

    Records the winning path in telemetry["extract_path"].
    """
    contexts = browser.contexts
    if not contexts:
        # CDP attach to V>>'s running Brave always exposes the existing
        # logged-in context. Zero contexts means the attach carries no
        # session — a fresh new_context() would lack li_at/JSESSIONID and
        # guarantee an empty Voyager result, so take the error path instead of
        # fabricating a doomed, session-less context (AGORA #3337).
        telemetry["errors"].append("no_browser_context: CDP attach lacks a session")
        telemetry["extract_path"] = "none"
        return []
    context = contexts[0]
    page = _find_linkedin_page(browser, cfg.feed_url)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception as exc:
        telemetry["errors"].append(f"load_state: {exc}")
    _ensure_logged_in(page)

    # Path 1 — Voyager API capture.
    api_posts = _extract_via_voyager_api(page, context, cfg, seen, telemetry)
    if api_posts:
        telemetry["extract_path"] = "voyager_api"
        return api_posts

    # Path 2 — CSS selector DOM scrape (fallback).
    log.info("voyager_api yielded 0 posts; falling back to CSS scrape")
    extracted_by_id = _scroll_and_extract(page, cfg, seen, telemetry)
    css_posts = list(extracted_by_id.values())
    telemetry["extract_path"] = "css_fallback" if css_posts else "none"
    return css_posts


def extract_feed(config: Optional[FeedConfig] = None) -> tuple[list[Post], dict]:
    """Tier-1 feed extraction. Returns (new_posts, telemetry).

    Tries the Voyager API capture path first; falls back to CSS DOM scrape.
    Telemetry keys: scroll_count, raw_extracted, deduplicated, errors,
    extract_path ("voyager_api" | "css_fallback" | "none").

    Raises CDPConnectionError with operator-actionable text on CDP failure.
    All other errors are logged into telemetry["errors"] (best-effort —
    a partial feed is better than a crash).
    """
    cfg = config or FeedConfig()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    telemetry = _make_telemetry()

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "playwright not installed in this venv — run:"
            " ~/.claude/venv/bin/python3 -m pip install playwright"
        ) from exc

    seen = load_seen(cfg.state_dir)

    with sync_playwright() as pw:
        browser = _connect_browser(pw, cfg)
        new_posts = _run_extraction(browser, cfg, seen, telemetry)

    telemetry["deduplicated"] = len(new_posts)
    telemetry["finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    _persist(cfg, new_posts)
    return new_posts, telemetry


# ---- Pure-Python helpers (testable without Playwright) -------------------


def deduplicate_posts(posts: list[Post], seen: set[str]) -> list[Post]:
    """Filter posts: keep only those whose post_id is NOT in seen.

    Pure function; the integration-test analogue of the in-memory dedup
    happening inside extract_feed(). Lives here so tests can exercise
    the dedup contract without booting Playwright.
    """
    return [p for p in posts if p.post_id not in seen]


__all__ = [
    "CDPConnectionError",
    "DEFAULT_CDP_URL",
    "DEFAULT_MAX_POSTS_PER_RUN",
    "DEFAULT_MAX_SCROLLS",
    "DEFAULT_SCROLL_DELAY_MAX_S",
    "DEFAULT_SCROLL_DELAY_MIN_S",
    "FeedConfig",
    "append_seen",
    "deduplicate_posts",
    "extract_feed",
    "load_seen",
    "write_snapshot",
]
