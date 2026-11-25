"""The two network seams: ``http`` (urllib) and ``gh`` (the gh CLI).

Every byte radar reads from the Internet passes through one of these two
functions. The sources receive them injected, so a test that makes
``urllib.request.urlopen`` and ``subprocess.run`` raise proves that no source
touches the network on its own.

Both return a ``Fetched``. A refusal is a value, never an exception: 403, 404,
429, a DNS failure and a timeout each set ``skip`` to a short reason, and the
caller decides what that means for its source. A 429 honours ``Retry-After`` up
to 60 seconds once and then gives up for this tick
(``rules/rate-limit-never-blocks.md``: a rate limit is retried, never alerted).
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

UA = "grip-radar/0.1 (+https://codetonight.co.za; read-only monitor)"
HTTP_TIMEOUT_S = 15
GH_TIMEOUT_S = 30
RETRY_AFTER_CAP_S = 60

_GH_HTTP_RE = re.compile(r"\(HTTP (\d{3})\)")
_GH_AUTH_HINTS = ("not logged in", "gh auth login", "bad credentials", "requires authentication")
_GH_NETWORK_HINTS = ("could not resolve", "dial tcp", "connection refused", "network is unreachable",
                     "no such host", "tls handshake", "i/o timeout")


@dataclass(frozen=True)
class Fetched:
    status: int
    body: str = ""
    skip: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.skip is None and 200 <= self.status < 300

    def json(self) -> Any:
        """The parsed body, or None when it is empty or not JSON."""
        if not self.body:
            return None
        try:
            return json.loads(self.body)
        except ValueError:
            return None


def skip_for_status(status: int) -> str:
    return str(status) if status in (401, 403, 404, 429) else f"http {status}"


def retry_after_seconds(headers: dict) -> float | None:
    """``Retry-After`` as seconds when it is a number within the cap, else None."""
    raw = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    try:
        secs = float(raw)
    except (TypeError, ValueError):
        return None
    return secs if 0 <= secs <= RETRY_AFTER_CAP_S else None


def _one_http(req: urllib.request.Request, timeout: float, opener: Callable) -> Fetched:
    try:
        with opener(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return Fetched(int(resp.status), body, None, dict(resp.headers.items()))
    except urllib.error.HTTPError as exc:  # before URLError: HTTPError is its subclass
        try:
            body = exc.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            body = ""
        headers = dict(exc.headers.items()) if exc.headers else {}
        return Fetched(int(exc.code), body, skip_for_status(exc.code), headers)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Fetched(0, "", f"network: {type(exc).__name__}")


def http(url: str, *, headers: dict | None = None, timeout: float = HTTP_TIMEOUT_S, ua: str = UA,
         opener: Callable | None = None, sleep: Callable[[float], None] = time.sleep,
         data: bytes | None = None, method: str | None = None) -> Fetched:
    """GET ``url``, or POST it when ``data`` is given. Resolves ``urlopen`` at
    call time so a test can patch it."""
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(url, data=data, headers={"User-Agent": ua, **(headers or {})}, method=method)
    result = _one_http(req, timeout, opener)
    if result.status == 429:
        wait = retry_after_seconds(result.headers)
        if wait is not None:
            sleep(wait)
            result = _one_http(req, timeout, opener)
    return result


def _gh_failure(stdout: str, stderr: str) -> Fetched:
    low = (stderr or "").lower()
    if any(hint in low for hint in _GH_AUTH_HINTS):
        return Fetched(401, stdout, "gh auth")
    match = _GH_HTTP_RE.search(stderr or "")
    if match:
        status = int(match.group(1))
        return Fetched(status, stdout, "gh auth" if status == 401 else skip_for_status(status))
    if any(hint in low for hint in _GH_NETWORK_HINTS):
        return Fetched(0, stdout, "network: gh")
    lines = [ln for ln in (stderr or "").strip().splitlines() if ln.strip()]
    return Fetched(0, stdout, f"gh: {lines[-1][:80] if lines else 'exit non-zero'}")


def gh(path: str, accept: str | None = None, *, timeout: float = GH_TIMEOUT_S,
       run: Callable | None = None) -> Fetched:
    """``gh api <path>`` through the CLI's own login. Resolves ``subprocess.run``
    at call time so a test can patch it."""
    run = run or subprocess.run
    argv = ["gh", "api", path] + (["-H", f"Accept: {accept}"] if accept else [])
    try:
        proc = run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return Fetched(0, "", "gh not installed")
    except subprocess.TimeoutExpired:
        return Fetched(0, "", "timeout: gh")
    except OSError as exc:
        return Fetched(0, "", f"gh: {type(exc).__name__}")
    if proc.returncode == 0:
        return Fetched(200, proc.stdout)
    return _gh_failure(proc.stdout, proc.stderr)


class Pacer:
    """At least ``gap_s`` seconds between successive ``wait()`` calls, for APIs
    that ask for spacing (gh search 2 s, Reddit 1 s, the RATS archive 1 s).
    The sleep and the clock are injectable so a test never waits."""

    def __init__(self, gap_s: float, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.gap_s = float(gap_s)
        self._sleep, self._clock = sleep, clock
        self._last: float | None = None

    def wait(self) -> float:
        """Block until the gap has passed; returns the seconds slept."""
        now = self._clock()
        slept = 0.0
        if self._last is not None:
            remaining = self.gap_s - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                slept, now = remaining, now + remaining
        self._last = now
        return slept
