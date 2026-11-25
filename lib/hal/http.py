"""HAL HTTP shim — stable API over urllib.request for all GRIP HTTP calls.

Every external HTTP call in GRIP routes through this module. Callers never
use urllib.request directly; this shim provides a typed, clean,
audit-ready interface per the syscall doctrine (rules/syscall-doctrine.md).

Usage:
    from lib.hal.http import get, post, HttpError

    data = get("http://localhost:11434/api/tags", timeout=2.0)
    result = post("https://slack.com/api/chat.postMessage",
                  json={"channel": "C123", "text": "hello"},
                  headers={"Authorization": "Bearer xoxb-..."})

Plain-language errors are raised as HttpError with a human-readable message.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


class HttpError(OSError):
    """HTTP call failed. Message is human-readable (Rule 17)."""

    def __init__(self, url: str, status: Optional[int], reason: str):
        self.url = url
        self.status = status
        self.reason = reason
        status_str = f" (HTTP {status})" if status else ""
        super().__init__(f"HTTP request to {url} failed{status_str}: {reason}")


@dataclass
class HttpResponse:
    """Normalised HTTP response — never exposes urllib internals."""
    status: int
    body: bytes
    headers: dict[str, str]

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)


def get(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 10.0,
) -> HttpResponse:
    """HTTP GET. Raises HttpError on network or HTTP failure."""
    return _request("GET", url, body=None, headers=headers, timeout=timeout)


def post(
    url: str,
    *,
    json: Optional[Any] = None,
    data: Optional[bytes] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 15.0,
) -> HttpResponse:
    """HTTP POST. Pass json= for JSON body or data= for raw bytes."""
    body, content_type = _encode_body(json, data)
    merged = {"Content-Type": content_type, **(headers or {})} if content_type else (headers or {})
    return _request("POST", url, body=body, headers=merged, timeout=timeout)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _encode_body(
    json_body: Optional[Any],
    raw: Optional[bytes],
) -> tuple[Optional[bytes], Optional[str]]:
    if json_body is not None:
        import json as _json
        return _json.dumps(json_body).encode(), "application/json"
    if raw is not None:
        return raw, None
    return None, None


def _request(
    method: str,
    url: str,
    *,
    body: Optional[bytes],
    headers: Optional[dict[str, str]],
    timeout: float,
) -> HttpResponse:
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(
                status=resp.status,
                body=resp.read(),
                headers=dict(resp.headers),
            )
    except urllib.error.HTTPError as e:
        raise HttpError(url, e.code, e.reason) from e
    except urllib.error.URLError as e:
        raise HttpError(url, None, str(e.reason)) from e
    except TimeoutError:
        raise HttpError(url, None, f"timed out after {timeout}s") from None
    except OSError as e:
        raise HttpError(url, None, str(e)) from e
