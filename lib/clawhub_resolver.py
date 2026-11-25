"""ClawHub reference resolver — read-only, digest-verified, fail-safe.

Resolves a ``clawhub:<package>[@<version|tag>]`` reference against the public
ClawHub HTTP API (``https://clawhub.ai/api/v1/``), verifies the artifact's
content digest is well-formed and consistent, surfaces the registry's trust
verdict, and returns normalised metadata.

This is **Layer 1** of GRIP's ClawHub compatibility (see
``docs/research/clawhub-compat-design.md``). Its entire job is *information
retrieval*. It deliberately does the least dangerous thing GRIP can do with a
third-party registry:

  * **Read-only HTTP** — ``GET`` only, via stdlib ``urllib``. It never POSTs,
    never downloads-and-unpacks an artifact, never installs.
  * **No code-execution path** — this module imports neither ``subprocess`` nor
    ``os.system``/``exec``/``eval``, and writes no files. A consumer cannot use
    it to run third-party code (asserted by ``tests/test_clawhub_resolver.py``).
  * **Digest is mandatory** — a version whose ``artifactSha256`` is missing or
    malformed is REJECTED (``ok=False``), never trusted. When a download
    ``X-ClawHub-Artifact-Sha256`` header is also seen, a header/manifest
    mismatch is REJECTED.
  * **Trust verdict gates** — if the registry reports
    ``trust.blockedFromDownload`` (e.g. ``scan:malicious``,
    ``manual:quarantined``), the resolution is marked unsafe so no higher layer
    proceeds.
  * **Rate-limit aware** — a ``429`` on the version endpoint surfaces
    ``retry_after_s`` so the caller can schedule a retry without parsing the
    error string. A ``429`` on the trust endpoint is treated as trust-unavailable
    → blocked (fail-safe, conservative). Neither case retries automatically.
  * **Fail-safe** — every error (bad ref, network failure, malformed JSON,
    HTTP error, 429) returns an unsafe ``ClawHubResolution`` with a plain-language
    ``error`` string. It NEVER raises to the caller.

Design follows ``rules/syscall-doctrine.md`` (one stable API behind the
registry boundary) and ``rules/untrusted-content-capability-lock.md`` (a
resolved ref is untrusted content — resolve ≠ install). The HTTP edge is
DIP-injected (``fetcher=``) so tests run with zero network.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

DEFAULT_BASE_URL = "https://clawhub.ai"
API_PREFIX = "/api/v1"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")  # SHA-256 hex, lowercased
_REF_RE = re.compile(r"^clawhub:(?P<name>[^@\s]+)(?:@(?P<ver>[^@\s]+))?$")
_TIMEOUT_S = 15
# A bare semver (digits.dots.digits...) is treated as an exact version; anything
# else after @ (e.g. "latest", "beta") is treated as a tag. Both are optional;
# absent ⇒ resolve the "latest" tag.
_SEMVER_RE = re.compile(r"^\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.\-]+)?$")


@dataclass(frozen=True)
class FetchResult:
    """One read-only HTTP GET outcome. ``status`` 0 means transport failure."""

    status: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)


class Fetcher(Protocol):
    """Injected read-only HTTP boundary (DIP). Implementations must only GET."""

    def __call__(self, url: str) -> FetchResult: ...


@dataclass(frozen=True)
class ClawHubResolution:
    """Normalised, verified result of resolving a clawhub ref.

    ``ok`` is the single gate a higher layer checks: it is True only when the
    ref resolved, the digest verified, AND the registry did not block the
    version. Any failure leaves ``ok=False`` with a plain-language ``error``.

    ``retry_after_s`` is set when ``ok=False`` and the failure was a ``429``
    rate-limit response on the version metadata endpoint. Callers should wait
    this many seconds before retrying. It is ``None`` for all other failures.
    """

    ok: bool
    ref: str
    name: str = ""
    version: str = ""
    digest: str = ""  # verified SHA-256 hex
    artifact_kind: str = ""  # "npm-pack" | "legacy-zip" | ""
    trust_status: str = ""  # registry scan verdict, e.g. "clean"
    blocked: bool = False  # trust.blockedFromDownload
    trust_reasons: tuple[str, ...] = ()
    error: str = ""
    retry_after_s: Optional[int] = None  # seconds to wait before retrying; None = not a rate-limit error


def _urlopen_fetch(url: str) -> FetchResult:
    """Default fetcher: stdlib urllib GET, no side effects. Never raises."""
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # nosec - GET only
            raw = resp.read().decode("utf-8", errors="replace")
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return FetchResult(status=getattr(resp, "status", 200) or 200, body=raw, headers=hdrs)
    except urllib.error.HTTPError as exc:  # 4xx/5xx — readable status, not a crash
        # Extract headers first: exc.headers is set at HTTPError construction
        # time (from the HTTP response) and is always available, even when
        # exc.read() fails (fp=None, connection reset after headers, etc.).
        # A single try-block that reads body before headers silently drops
        # Retry-After if read() raises — keeping these separate preserves the
        # header regardless of body-read success.
        try:
            hdrs = {k.lower(): v for k, v in exc.headers.items()}
        except Exception:
            hdrs = {}
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return FetchResult(status=exc.code, body=body, headers=hdrs)
    except Exception:  # DNS, timeout, connection reset, TLS — all fail-safe
        return FetchResult(status=0, body="", headers={})


def _parse_ref(ref: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse ``clawhub:<name>[@<ver|tag>]`` → (name, version, tag).

    Returns (None, None, None) on a malformed ref. Exactly one of version/tag
    is non-None when a selector is given; both None ⇒ caller defaults to latest.
    """
    if not isinstance(ref, str):
        return (None, None, None)
    m = _REF_RE.match(ref.strip())
    if not m:
        return (None, None, None)
    name = m.group("name")
    selector = m.group("ver")
    if not selector:
        return (name, None, None)
    if _SEMVER_RE.match(selector):
        return (name, selector, None)
    return (name, None, selector)


def _build_version_url(base: str, name: str, version: Optional[str], tag: Optional[str]) -> str:
    """Compose the ClawHub version-metadata URL (exact version or tag query)."""
    safe_name = urllib.parse.quote(name, safe="@/-_.")
    root = f"{base}{API_PREFIX}/packages/{safe_name}/versions"
    if version:
        return f"{root}/{urllib.parse.quote(version, safe='-_.')}"
    chosen_tag = tag or "latest"
    return f"{root}?tag={urllib.parse.quote(chosen_tag, safe='')}"


def _build_security_url(base: str, name: str, version: str) -> str:
    safe_name = urllib.parse.quote(name, safe="@/-_.")
    safe_ver = urllib.parse.quote(version, safe="-_.")
    return f"{base}{API_PREFIX}/packages/{safe_name}/versions/{safe_ver}/security"


def _extract_digest(version_obj: dict) -> str:
    """Pull the SHA-256 hex digest from a version object, lowercased.

    Looks at the canonical ``artifactSha256`` field. Returns "" if absent or
    not a well-formed 64-char hex string (caller treats "" as REJECT).
    """
    raw = version_obj.get("artifactSha256")
    if not isinstance(raw, str):
        return ""
    candidate = raw.strip().lower()
    return candidate if _DIGEST_RE.match(candidate) else ""


def _parse_retry_after(headers: dict[str, str]) -> Optional[int]:
    """Extract the Retry-After wait time (integer seconds) from response headers.

    Tries both ``retry-after`` and ``x-ratelimit-reset`` (lowercased).
    Returns ``None`` if absent, zero, or unparseable — callers treat None as
    "no retry hint available" and should apply their own backoff.
    """
    for key in ("retry-after", "x-ratelimit-reset"):
        raw = headers.get(key, "").strip()
        if raw:
            try:
                secs = int(raw)
                if secs > 0:
                    return secs
            except (ValueError, TypeError):
                pass
    return None


def _fail(ref: str, error: str) -> ClawHubResolution:
    return ClawHubResolution(ok=False, ref=ref, error=error)


def _fail_rate_limited(ref: str, name: str, retry_after_s: Optional[int]) -> ClawHubResolution:
    """Build a fail-safe resolution for a 429 response on the version endpoint."""
    ra_msg = f" (retry after {retry_after_s}s)" if retry_after_s else ""
    return ClawHubResolution(
        ok=False,
        ref=ref,
        name=name,
        error=f"ClawHub rate-limit (429) on version lookup for {name!r}{ra_msg} — back off before retrying.",
        retry_after_s=retry_after_s,
    )


def _harden(fetcher: Fetcher) -> Fetcher:
    """Wrap any fetcher so a raised exception becomes a fail-safe FetchResult.

    The production ``_urlopen_fetch`` is already exception-safe, but an injected
    fetcher (DIP seam) is caller-supplied and may raise. Wrapping it once here —
    rather than guarding each of resolve's call sites — keeps ``resolve`` itself
    fail-safe against ANY fetcher with one adapter (DRY, low cyclomatic
    complexity). A raise degrades to ``status=0``, the same signal a transport
    failure produces, so downstream logic treats it identically.
    """

    def _wrapped(url: str) -> FetchResult:
        try:
            return fetcher(url)
        except Exception:
            return FetchResult(status=0, body="", headers={})

    return _wrapped


def _decode_json(fr: FetchResult) -> Optional[dict]:
    """Decode a successful FetchResult body to a dict, else None."""
    if fr.status != 200 or not fr.body:
        return None
    try:
        obj = json.loads(fr.body)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _unwrap_version(payload: dict) -> dict:
    """ClawHub nests version metadata under a top-level ``version`` key."""
    inner = payload.get("version")
    return inner if isinstance(inner, dict) else payload


def _read_trust(base: str, name: str, version: str, fetcher: Fetcher) -> tuple[str, bool, tuple[str, ...]]:
    """Fetch the per-version trust verdict. Fail-safe → treat as BLOCKED.

    A registry that cannot confirm a version is safe must not be assumed safe:
    on any fetch/parse failure this returns ``blocked=True`` so the resolution
    is marked unsafe (conservative, per the threat model in the design doc).

    A ``429`` rate-limit response is treated as trust-unavailable → blocked:
    the trust verdict could not be obtained, so fail-safe applies. The reason
    tuple includes ``"registry:rate-limited"`` so callers can distinguish a
    temporary rate-limit from a permanent block.
    """
    fr = fetcher(_build_security_url(base, name, version))
    if fr.status == 429:
        return ("rate-limited", True, ("registry:rate-limited",))
    sec = _decode_json(fr)
    if sec is None:
        return ("unverified", True, ("registry:trust-unavailable",))
    trust = sec.get("trust")
    if not isinstance(trust, dict):
        return ("unverified", True, ("registry:trust-malformed",))
    status = str(trust.get("scanStatus", "") or "")
    blocked = bool(trust.get("blockedFromDownload", False))
    reasons_raw = trust.get("reasons", [])
    reasons = tuple(str(r) for r in reasons_raw) if isinstance(reasons_raw, list) else ()
    return (status, blocked, reasons)


def resolve(
    ref: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    fetcher: Optional[Callable[[str], FetchResult]] = None,
) -> ClawHubResolution:
    """Resolve a ``clawhub:<pkg>[@<ver|tag>]`` ref. Read-only. Never raises.

    Returns a ``ClawHubResolution``; check ``.ok`` before using it. ``ok`` is
    True only when: the ref parsed, the version metadata loaded, the digest is
    a well-formed SHA-256, AND the registry did not block the version. Inject
    ``fetcher`` to test without network — even a fetcher that raises is handled
    fail-safe (it degrades to an unsafe resolution; see ``_harden``).

    When the version metadata endpoint returns ``429``, the resolution has
    ``ok=False`` and ``retry_after_s`` set to the value from the ``Retry-After``
    response header (or ``None`` if absent). Callers should honour this hint and
    not retry immediately — ClawHub auto-bans clients that ignore rate limits.
    """
    do_fetch: Fetcher = _harden(fetcher or _urlopen_fetch)
    base = base_url.rstrip("/")

    name, version, tag = _parse_ref(ref)
    if not name:
        return _fail(ref, f"Not a valid clawhub reference: {ref!r}. Expected 'clawhub:<package>[@<version|tag>]'.")

    ver_fr = do_fetch(_build_version_url(base, name, version, tag))
    if ver_fr.status == 429:
        return _fail_rate_limited(ref, name, _parse_retry_after(ver_fr.headers))

    payload = _decode_json(ver_fr)
    if payload is None:
        sel = version or tag or "latest"
        return _fail(ref, f"Could not resolve {name!r}@{sel} from ClawHub (not found, unreachable, or malformed response).")

    version_obj = _unwrap_version(payload)
    resolved_version = str(version_obj.get("version") or version or "")
    if not resolved_version:
        return _fail(ref, f"ClawHub returned no version string for {name!r}.")

    digest = _extract_digest(version_obj)
    if not digest:
        return _fail(ref, f"{name!r}@{resolved_version} has no well-formed SHA-256 artifact digest — REJECTED (cannot verify integrity).")

    artifact = version_obj.get("artifact")
    artifact_kind = str(artifact.get("kind", "")) if isinstance(artifact, dict) else ""

    trust_status, blocked, reasons = _read_trust(base, name, resolved_version, do_fetch)
    if blocked:
        return ClawHubResolution(
            ok=False, ref=ref, name=name, version=resolved_version, digest=digest,
            artifact_kind=artifact_kind, trust_status=trust_status, blocked=True,
            trust_reasons=reasons,
            error=f"{name!r}@{resolved_version} is blocked by ClawHub trust verdict ({', '.join(reasons) or trust_status or 'blocked'}) — REJECTED.",
        )

    return ClawHubResolution(
        ok=True, ref=ref, name=name, version=resolved_version, digest=digest,
        artifact_kind=artifact_kind, trust_status=trust_status, blocked=False,
        trust_reasons=reasons,
    )


def verify_download_digest(resolution: ClawHubResolution, header_value: Optional[str]) -> bool:
    """Check a download's ``X-ClawHub-Artifact-Sha256`` header against the
    verified manifest digest. Returns True iff they match (case-insensitive
    hex). A missing or mismatched header is REJECTED (returns False).

    Kept separate from ``resolve`` because the download header is only seen if a
    higher layer actually fetches the artifact (a gated decision); resolve
    itself never downloads. This lets a future consume-layer re-verify bytes
    against the identity resolve already pinned.
    """
    if not resolution.ok or not resolution.digest:
        return False
    if not isinstance(header_value, str):
        return False
    return header_value.strip().lower() == resolution.digest
