#!/usr/bin/env python3
"""A COLD store backed by the Antepaste clipboard history.

What this is
------------
`lib/cold_context_pager.py` pages a cold context slice through an injectable
``ColdStore`` — ``put`` / ``get`` / ``evict`` / ``list``, defined as a Protocol
in ``prompt_cache_keepalive.coldstore``. This implements that shape over
Antepaste's local MCP server, so a reference of the form ``clip:8f3a2c1d`` can
be paged back into context without the operator finding and re-pasting the clip.

What it deliberately CANNOT do
------------------------------
It is READ-ONLY, and that is a property of the thing rather than a gap in the
work. A clipboard history is written by the person using the machine — Antepaste
exposes no tool to add a clip, and inventing one here would mean a GRIP session
could push content into the operator's clipboard. So ``put`` and ``evict``
refuse permanently and say why.

The consequence for the pager is exact, and worth stating rather than blurring:
``page_back`` works against this store with no upstream change, and ``offload``
does not, because there is nowhere to offload TO. This store is a source of cold
context, never a sink for it.

Why urllib rather than a client library
---------------------------------------
GRIP's requirements are generated from imports, so reaching for ``requests`` or
an MCP SDK here would add a dependency to the whole repo for one adapter.
``urllib`` is stdlib and speaks the two verbs this needs.

Why the package import is optional
-----------------------------------
``prompt_cache_keepalive`` is an optional substrate and is frequently absent —
it is absent in this checkout. So nothing here imports it at module scope. The
Protocol is satisfied STRUCTURALLY, which is all a Protocol asks, and a
``ColdRef`` is accepted by duck type: anything carrying ``.key`` and ``.scope``.
That is what lets this module, and its tests, run in an environment where the
cold tier is not installed — where an import-or-skip would have quietly tested
nothing at all.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, NoReturn, Optional

DEFAULT_URL = "http://127.0.0.1:2547/mcp"
KEYCHAIN_ITEM = "antepaste-mcp-token"
TOKEN_ENV = "ANTEPASTE_MCP_TOKEN"
URL_ENV = "ANTEPASTE_MCP_URL"

#: Named once so the send side and the read side cannot drift apart.
SESSION_HEADER = "Mcp-Session-Id"

#: The MCP protocol revision this client announces, in one place so a future
#: bump has a single source of truth rather than a literal buried in a handshake.
PROTOCOL_VERSION = "2025-06-18"

#: A ref this store answers for: ``clip:`` plus the eight hex characters that
#: Antepaste's ``mintClipToken`` takes from the content hash.
CLIP_REF = re.compile(r"^clip:([0-9a-fA-F]{8})$")

#: Total wall-clock budget for resolving one ref, handshake included.
DEFAULT_BUDGET_S = 2.0

#: Most body this will buffer. Far above any real clip, far below what an
#: unbounded read would accept from a faulty or hostile server.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

_LOOPBACK_V4 = re.compile(r"^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
_LOOPBACK_V6 = re.compile(r"^(::1|0*:0*:0*:0*:0*:0*:0*:0*1|::ffff:127(\.\d{1,3}){3})$")


def is_loopback_endpoint(raw: str) -> bool:
    """Does ``raw`` name a loopback address?

    The endpoint carries the bearer, so whatever host it names receives the
    credential. Antepaste's server binds loopback and refuses every other
    interface; this holds the same line on the client side, so a stray or
    hostile ``ANTEPASTE_MCP_URL`` cannot turn this adapter into a way of posting
    the token somewhere else.

    The host is parsed rather than string-matched, so alternate spellings are
    covered and a hostname that merely *begins* with "127.0.0.1" is not.
    """
    try:
        host = (urllib.parse.urlparse(raw).hostname or "").lower()
    except ValueError:
        return False
    if host == "localhost":
        return True
    return bool(_LOOPBACK_V4.match(host) or _LOOPBACK_V6.match(host))


# The real CacheMiss when the cold tier is installed, so a caller written against
# the ColdStore contract catches this store's misses with the same except clause.
# A local base otherwise, so this module imports in a checkout without it.
try:  # pragma: no cover - exercised by whichever environment runs it
    from prompt_cache_keepalive.coldstore import CacheMiss as _MissBase
except ModuleNotFoundError:  # pragma: no cover
    class _MissBase(RuntimeError):  # type: ignore[no-redef]
        """Stand-in for the cold tier's CacheMiss where it is not installed."""


class ClipCacheMiss(_MissBase):
    """A clip could not be paged back, and the message says which way it failed.

    A typed miss rather than an empty string: a caller must decide to re-derive,
    never proceed on content that silently is not there.
    """


class _StaleSession(Exception):
    """Internal: the server rejected the cached session id, so re-handshake.

    Never escapes ``_call`` — it exists only to carry that one fact up from the
    POST to the retry, without conflating it with a real miss.
    """


@dataclass(frozen=True)
class ClipRef:
    """A handle naming one clip. Shaped to match the cold tier's ``ColdRef``.

    ``slot`` exists only so this is substitutable for a ``ColdRef``; a clipboard
    history has no ring slots, so it is always 0 and nothing reads it.
    """

    key: str
    scope: str
    slot: int = 0


def resolve_token(
    env: Optional[dict] = None,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> Optional[str]:
    """The bearer token, or None when there is none to be had.

    The environment wins so a caller that already manages secrets never causes a
    keychain prompt. The keychain is read only on darwin, where ``security`` is
    the documented reader.
    """
    environ = os.environ if env is None else env
    from_env = environ.get(TOKEN_ENV)
    if from_env:
        return from_env
    if environ.get("ANTEPASTE_KEYCHAIN_OFF") or sys.platform != "darwin":
        return None
    try:
        done = runner(
            ["security", "find-generic-password", "-s", KEYCHAIN_ITEM, "-w"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = (done.stdout or "").strip()
    return token or None


def _placeholder(detail: dict) -> str:
    """A one-line stand-in for a clip whose content is not text.

    An image or a file is described rather than inlined: a data URL is useless
    as context and a local file path discloses things about the machine that a
    clipboard read has no business disclosing.
    """
    kind = detail.get("kind")
    if kind == "image":
        image = detail.get("image") or {}
        mime = image.get("mime", "image")
        if image.get("too_large"):
            return f"[image {mime}, {image.get('byte_size', 0)} bytes, too large to inline]"
        return f"[image {mime} {image.get('width')}x{image.get('height')}]"
    if kind == "file":
        handle = detail.get("file") or {}
        return f"[file {handle.get('name', 'unnamed')}, {handle.get('byte_size', 0)} bytes]"
    return f"[clip of kind {kind!r} carries no text]"


def _sse_events(text: str) -> List[str]:
    """Every event in an SSE body, each event's ``data:`` lines joined by a newline.

    One pass over the lines: a blank line closes the current event, a ``data:``
    line appends to it, anything else (``event:``, ``id:``) is framing this does
    not need. Joining multi-line data is what the spec says a data field means;
    treating each line as a whole payload would work only until one was not.
    """
    events: List[str] = []
    current: List[str] = []
    for line in re.split(r"\r?\n", text):
        if not line.strip():
            if current:
                events.append("\n".join(current))
                current = []
        elif line.startswith("data:"):
            current.append(line[5:].removeprefix(" "))
    if current:
        events.append("\n".join(current))
    return [event for event in events if event.strip()]


def _read_capped(response: Any) -> bytes:
    """Read a body, refusing one that runs past ``MAX_RESPONSE_BYTES``.

    ``response.read()`` with no argument buffers whatever arrives, so a faulty
    or hostile server could hand over far more than any clip could be. The
    budget bounds how LONG that can take and says nothing about how MUCH
    arrives, which is a separate limit needing its own check.

    One byte past the cap is read deliberately: it is what distinguishes a body
    that exactly fills the cap from one that exceeds it.
    """
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ClipCacheMiss(
            f"the server sent more than {MAX_RESPONSE_BYTES} bytes; refusing to buffer it"
        )
    return raw


def _parse_body(content_type: str, raw: bytes) -> Optional[dict]:
    """Decode a JSON or SSE response body. The last SSE event is the reply."""
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" not in content_type:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ClipCacheMiss("the server sent a body that is not JSON") from exc

    events = _sse_events(text)
    if not events:
        raise ClipCacheMiss("the server sent an empty event stream")
    try:
        return json.loads(events[-1])
    except json.JSONDecodeError as exc:
        raise ClipCacheMiss("the server sent an event that is not JSON") from exc


class ClipHistoryColdStore:
    """Read clips out of Antepaste's history through its local MCP server.

    Satisfies the cold tier's ``ColdStore`` Protocol structurally. Injectable
    ``opener`` and ``clock`` so tests drive a fake HTTP server and a fake clock
    without reaching the operator's real clipboard or waiting out a budget.
    """

    def __init__(
        self,
        *,
        url: Optional[str] = None,
        token: Optional[str] = None,
        budget_s: float = DEFAULT_BUDGET_S,
        opener: Callable[..., Any] = urllib.request.urlopen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url or os.environ.get(URL_ENV) or DEFAULT_URL
        # Refused at construction, BEFORE the credential is resolved, so a
        # non-loopback endpoint never causes the token to be read let alone
        # sent. Loud here rather than silent: unlike the Claude Code hook, which
        # must never block a prompt, GRIP code may raise on a misconfiguration.
        if not is_loopback_endpoint(self._url):
            raise ValueError(
                f"{self._url!r} is not a loopback address. This endpoint carries the "
                "bearer token, and the Antepaste server binds loopback only, so a "
                "remote URL could only ever send the credential somewhere it does "
                "not belong."
            )
        self._token = token if token is not None else resolve_token()
        self._budget_s = budget_s
        self._opener = opener
        self._clock = clock
        self._session: Optional[str] = None
        self._next_id = 0

    # -- the two operations a clipboard history cannot support ---------------

    def put(self, key: str, text: str, *, scope: str) -> NoReturn:
        """Always refuses. A clipboard history is written by the person, not by GRIP.

        This is a complete implementation of "this operation does not exist",
        not an unfinished one: Antepaste exposes no tool that adds a clip, and a
        GRIP session writing into the operator's clipboard is not a capability
        worth inventing.
        """
        raise PermissionError(
            "the clipboard history is read-only: it is written by the person at "
            "the machine, and Antepaste exposes no tool to add a clip. Use the "
            "file ring to offload; use this store only to page back."
        )

    def evict(self, ref: Any) -> NoReturn:
        """Always refuses. Deleting someone's clipboard entry is theirs to do."""
        raise PermissionError(
            "the clipboard history is read-only: deleting a clip is the "
            "operator's decision, made in Antepaste, not GRIP's to make here."
        )

    # -- reading -------------------------------------------------------------

    def get(self, ref: Any, *, scope: Optional[str] = None) -> str:
        """Page back the clip named by ``ref.key``.

        Raises ``ClipCacheMiss`` naming the reason — ``not_found``,
        ``unauthorized``, a refusal the server gave, an unreachable server, or a
        budget overrun. Never returns a placeholder for a failure; a miss is a
        miss, and only a non-text clip yields a description of itself.
        """
        key = getattr(ref, "key", ref)
        ref_scope = getattr(ref, "scope", None)
        if scope is not None and ref_scope is not None and scope != ref_scope:
            raise PermissionError(f"scope {scope!r} may not read ref scoped {ref_scope!r}")

        match = CLIP_REF.match(str(key))
        if match is None:
            raise ClipCacheMiss(
                f"{key!r} is not a clip reference; they look like 'clip:8f3a2c1d'"
            )

        detail = self._call("clip_get", {"token": f"⟦clip:{match.group(1).lower()}⟧"})
        text = detail.get("text")
        return text if isinstance(text, str) else _placeholder(detail)

    def list(self) -> List[ClipRef]:
        """Refs for the clips the server is currently willing to describe.

        Returns an empty list when the server cannot be reached: a listing is a
        convenience, and an unreachable server means "nothing to offer" rather
        than an error a caller must handle. ``get`` is the operation that
        reports honestly, because that is the one a decision rides on.
        """
        try:
            result = self._call("clip_list", {"limit": 50})
        except ClipCacheMiss:
            return []
        items: Iterable[dict] = result.get("items") or []
        refs: List[ClipRef] = []
        for item in items:
            found = re.search(r"clip:([0-9a-fA-F]{8})", str(item.get("token", "")))
            if found:
                refs.append(ClipRef(key=f"clip:{found.group(1).lower()}", scope="clipboard"))
        return refs

    # -- MCP plumbing --------------------------------------------------------

    def _call(self, tool: str, arguments: dict) -> dict:
        """Handshake if needed, then call one tool. Refusals become misses.

        A cached session id can go stale — Antepaste restarts, the session is
        evicted, or it simply expires — and the server then rejects it. Without
        a retry this store would repeat a dead id forever and stay broken until
        the whole object was rebuilt, which for a long-lived pager means until
        the process ends. So a rejected session is cleared and the handshake run
        once more. Once, deliberately: a second failure is a real fault and
        should surface rather than become a loop.
        """
        deadline = self._clock() + self._budget_s
        try:
            return self._attempt(tool, arguments, deadline)
        except _StaleSession:
            self._session = None
            return self._attempt(tool, arguments, deadline)

    def _attempt(self, tool: str, arguments: dict, deadline: float) -> dict:
        if self._session is None:
            self._handshake(deadline)
        response = self._post(
            {"method": "tools/call", "params": {"name": tool, "arguments": arguments}},
            deadline,
        )
        result = (response or {}).get("result")
        if result is None:
            raise ClipCacheMiss(f"{tool} returned no result")
        if result.get("isError"):
            raise ClipCacheMiss(self._reason(result))
        return result.get("structuredContent") or {}

    @staticmethod
    def _reason(result: dict) -> str:
        """The server's machine-readable refusal code, e.g. ``not_found``."""
        content = result.get("content") or []
        text = str(content[0].get("text", "")) if content else ""
        return text.split(":")[0].strip() or "refused"

    def _handshake(self, deadline: float) -> None:
        self._post(
            {
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "grip-clip-coldstore", "version": "0.1.0"},
                },
            },
            deadline,
        )
        if self._session is None:
            raise ClipCacheMiss("the server issued no session id")
        self._post({"method": "notifications/initialized"}, deadline, notify=True)

    def _post(self, body: dict, deadline: float, *, notify: bool = False) -> Optional[dict]:
        """One JSON-RPC POST. Adds the bearer, tracks the session id."""
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise ClipCacheMiss("budget exhausted before the server answered")

        payload = dict(body)
        payload["jsonrpc"] = "2.0"
        if not notify:
            self._next_id += 1
            payload["id"] = self._next_id

        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=self._headers(),
        )
        try:
            with self._opener(request, timeout=remaining) as response:
                session = response.headers.get(SESSION_HEADER)
                if session:
                    self._session = session
                raw = _read_capped(response)
                content_type = response.headers.get("content-type") or ""
        except urllib.error.HTTPError as exc:
            # 404 or 400 on a request carrying a session id means the server does
            # not know it — the stale-session case, recoverable by handshaking
            # again rather than a miss to report.
            if self._session is not None and exc.code in (400, 404):
                raise _StaleSession() from exc
            raise ClipCacheMiss(
                "unauthorized" if exc.code == 401 else f"server answered HTTP {exc.code}"
            ) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ClipCacheMiss(f"server unreachable at {self._url}") from exc

        # urllib's timeout is per socket operation, not a total. A server that
        # trickles bytes can keep every individual read inside `remaining` and
        # still run well past the budget, so the elapsed time is checked once
        # the body is in hand. This cannot interrupt a read already in flight —
        # the byte cap is what bounds that — but it does stop a call returning
        # successfully after the budget it advertised has gone.
        if self._clock() > deadline:
            raise ClipCacheMiss("the server answered, but past the budget")

        return _parse_body(content_type, raw) if raw else None

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._session:
            headers[SESSION_HEADER] = self._session
        return headers
