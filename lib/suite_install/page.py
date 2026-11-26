"""The suite installer's setup page: a local HTTP server and a minimal page, now wired
to the signed setup record (GRIP Suite, W3 step 4, part 2; part 1 was the server alone).

Binds `127.0.0.1` only, on a port the OS picks, and hands back a one-run URL carrying a
fresh token (`secrets.token_urlsafe(32)`). Every POST must carry that token in the
`X-Setup-Token` header; `GET /` must carry it in the `t` query parameter; a missing or
wrong token is refused with 403 (compared as bytes, never a bare `str`, so a non-ASCII
guess is a clean refusal rather than an unhandled crash), and the token is never written
to a log line or an error body. A request whose Host header is not exactly
`127.0.0.1:<port>`, or whose POST Origin is not exactly `http://127.0.0.1:<port>`, is
refused with 403 (DNS rebinding and cross-origin writes). Every response -- success or
error alike, including ones the stdlib raises on our behalf such as an unsupported
method or a malformed request line -- carries a fixed set of security headers, added in
one `end_headers` override so no response path can skip them. The three assets
(`page.html`, `page.js`, `page.css`) are read ONCE at start-up, from this module's own
folder, and served from memory from then on, so no request path ever reaches the
filesystem; any path or method outside the small allowlist below gets a plain 404 (or,
for a method this handler does not define at all, the stdlib's own 501), with no
directory listing. Each accepted connection gets a bounded read timeout, so a client
that opens a socket and sends nothing cannot block this single-threaded server from
ever answering anyone else or ever re-checking its own idle shutdown. The same timeout
also bounds a declared-but-unsent request body: a POST that names a real
`Content-Length` and then sends no body is answered 408, never left to drop the
connection on a bare, uncaught read timeout. The server stops on `POST /done`, or once
nothing has been read or written for `idle_limit_seconds` (injectable, so a test can
use a fraction of a second).

This module runs each step through the existing engine (`lib.suite_install.steps`) and
reports its result as JSON. Every `POST /run` also appends one line to this run's step
chain (`lib.suite_install.ledger`), under a folder the caller names (`setup_dir`, never
guessed) holding `steps.jsonl`, `ledger.jsonl` and `tenant.txt`. The tenant id is made
once -- `"suite-" + secrets.token_hex(4)` -- and read back from `tenant.txt` on every
later run, this module's own `__main__` and a future terminal mode alike. A step named
`KEY_STEP_NAME`, built by `build_key_step`, makes this tenant's own signing key
(`lib.aide.key_provisioning.provision_tenant_pq_keys`) and proves itself by that key
existing and this interpreter being able to use it; only once THAT step holds does the
server seal the chain (`ledger.seal`), and never with the shared demo key -- a refusal
is reported in the response, never written over as a weaker seal. `POST /done`
re-proves every step against the end state, appends one line for that, seals once
more, and answers with `ledger.check`'s verdict before stopping. `POST /check` answers
the same verdict on demand, behind the same token, Host and Origin rules as every
other route.
"""
from __future__ import annotations

import hmac
import html
import json
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

from . import ledger
from . import steps as steps_mod

ASSET_DIR = Path(__file__).resolve().parent
TOKEN_HEADER = "X-Setup-Token"
TOKEN_PARAM = "t"
DEFAULT_IDLE_LIMIT_SECONDS = 30 * 60.0
POLL_INTERVAL_SECONDS = 0.2
# How long an accepted connection may sit silent before this handler gives up on it
# (`socketserver.StreamRequestHandler`'s own per-connection socket timeout). Without
# this, a client that opens a socket and sends nothing blocks `handle_request` forever:
# HTTPServer serves one request at a time, so that one silent connection stops every
# other request AND the idle-shutdown loop, which can only re-check once `handle_request`
# returns. Overridable per server so a test can use a fraction of a second.
DEFAULT_HANDLER_TIMEOUT_SECONDS = 10.0
# Far larger than any real body here (`{"step": "<name>"}`); just a sane ceiling so a
# bad-faith or broken `Content-Length` cannot make this single-threaded server stall on
# a huge read.
MAX_BODY_BYTES = 65536
# Named once so the static-asset route table and the one place each file is actually
# read can never drift apart (a rename remembered in only one of the two is otherwise
# silent: the route would 404, or the file would load under the wrong path).
PAGE_JS_NAME = "page.js"
PAGE_CSS_NAME = "page.css"
# This run's setup folder (the caller's `setup_dir`) holds exactly these three files:
# the step chain, the sealed records over it, and the tenant id both are written
# under and checked against.
STEP_FILE_NAME = "steps.jsonl"
LEDGER_FILE_NAME = "ledger.jsonl"
TENANT_FILE_NAME = "tenant.txt"
# The one step the server ever seals the chain after -- see _attempt_seal.
KEY_STEP_NAME = "Make your signing key"

# Applied to every response this server sends -- success or error, ours or the stdlib's
# own -- via the `end_headers` override below. No inline script or style exists on the
# page for this CSP to forbid; it is here so nothing added later can introduce one
# unnoticed.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def load_or_create_tenant(setup_dir: Path) -> str:
    """This run's tenant id: read back from ``setup_dir``/``tenant.txt`` when an
    earlier run already made one, else a fresh ``suite-<8 hex chars>`` id, written
    there for every later run -- this module's own ``__main__`` and a future terminal
    mode alike -- to reuse."""
    tenant_file = setup_dir / TENANT_FILE_NAME
    if tenant_file.exists():
        return tenant_file.read_text(encoding="utf-8").strip()
    setup_dir.mkdir(parents=True, exist_ok=True)
    tenant = "suite-" + secrets.token_hex(4)
    tenant_file.write_text(tenant, encoding="utf-8")
    return tenant


def _prove_key(tenant: str) -> steps_mod.Proof:
    from lib.aide import key_provisioning

    if not key_provisioning.pq_backend_available():
        return steps_mod.Proof(False, "this interpreter cannot sign with a dual key yet")
    if not key_provisioning.tenant_keys_exist(tenant):
        return steps_mod.Proof(False, "no signing key exists yet for this computer")
    return steps_mod.Proof(True, "a signing key exists that only this computer holds")


def _install_key(tenant: str) -> None:
    from lib.aide import key_provisioning

    key_provisioning.provision_tenant_pq_keys(tenant)


def build_key_step(tenant: str) -> steps_mod.Step:
    """"Make your signing key": installs this tenant's own dual signing key and
    proves itself by that key existing and this interpreter being able to use it --
    the one step the server seals the chain after (see ``_attempt_seal``)."""
    return steps_mod.Step(
        KEY_STEP_NAME, lambda: _prove_key(tenant), lambda: _install_key(tenant),
        command=f"provision-pq {tenant}")


def _steps_with_key(base_steps: list[steps_mod.Step], tenant: str) -> list[steps_mod.Step]:
    """``base_steps`` with the signing-key step inserted right after the step that
    gets GRIP (matched by name) -- or at the end, when no such step is present (a
    fake-step list in a test)."""
    result = list(base_steps)
    index = next((i for i, step in enumerate(result) if step.name == "GRIP"), len(result) - 1)
    result.insert(index + 1, build_key_step(tenant))
    return result


def _read_asset(name: str) -> str:
    return (ASSET_DIR / name).read_text(encoding="utf-8")


def _render_page(step_names: list[str], token: str) -> bytes:
    """Fill the static page template with this run's step list and token. Both go into
    HTML attributes, never into a script body, so this is not an inline script for the
    CSP to forbid. `html.escape` keeps a quote or an angle bracket in either value from
    breaking out of the attribute; the browser undoes that escaping before JavaScript
    ever reads the attribute back, so `page.js` sees the plain token and plain JSON."""
    page = _read_asset("page.html")
    page = page.replace("__STEPS__", html.escape(json.dumps(step_names), quote=True))
    page = page.replace("__TOKEN__", html.escape(token, quote=True))
    return page.encode("utf-8")


class _SetupServer(HTTPServer):
    """Holds this run's steps, token, idle clock, assets and setup folder, so the
    handler -- a new instance per request -- can read them without any state of its
    own."""

    def __init__(self, address: tuple[str, int], handler_cls: type[BaseHTTPRequestHandler],
                 *, steps: list[steps_mod.Step], token: str, idle_limit_seconds: float,
                 clock: Callable[[], float], setup_dir: Path):
        super().__init__(address, handler_cls)
        self.steps_by_name = {step.name: step for step in steps}
        self.token = token
        self.idle_limit_seconds = idle_limit_seconds
        self.clock = clock
        self.last_activity = clock()
        self.should_stop = False
        host = self.server_address[0]
        self.expected_host = f"{host}:{self.server_port}"
        self.expected_origin = f"http://{host}:{self.server_port}"
        self.page_bytes = _render_page(list(self.steps_by_name.keys()), token)
        self.js_bytes = _read_asset(PAGE_JS_NAME).encode("utf-8")
        self.css_bytes = _read_asset(PAGE_CSS_NAME).encode("utf-8")
        self.setup_dir = setup_dir
        self.step_file = setup_dir / STEP_FILE_NAME
        self.ledger_file = setup_dir / LEDGER_FILE_NAME
        self.tenant = load_or_create_tenant(setup_dir)

    def note_activity(self) -> None:
        self.last_activity = self.clock()

    def idle_seconds(self) -> float:
        return self.clock() - self.last_activity


def _attempt_seal(server: "_SetupServer") -> Optional[str]:
    """Seal the chain under this run's tenant; the refusal text when ``seal`` refuses
    rather than writing a weaker record, ``None`` on success. Never raises -- a seal
    attempt that fails is reported in the caller's response, not a 500."""
    try:
        ledger.seal(server.step_file, server.ledger_file, tenant_id=server.tenant)
    except (ledger.ChainBroken, RuntimeError) as exc:
        return str(exc)
    return None


def _handler_class(timeout_seconds: float) -> type["_Handler"]:
    """A `_Handler` subclass whose only difference is its socket read timeout -- set
    per server instance, never as a bare class attribute on `_Handler` itself, so a
    test can use a fraction of a second instead of the real default."""
    return type("_TimedHandler", (_Handler,), {"timeout": timeout_seconds})


class _BodyTimedOut(Exception):
    """A client declared a request body (``Content-Length`` > 0) and then never sent
    it. ``self.rfile.read`` blocks until the handler's own socket read timeout, then
    raises a bare ``TimeoutError`` -- uncaught, that escapes the handler entirely and
    the connection is simply dropped with no HTTP response at all. Caught at the one
    call site and turned into a clean 408 instead."""


class _Handler(BaseHTTPRequestHandler):
    """One request. Every route -- and every error the stdlib raises on our behalf, such
    as an unsupported method or a malformed request line -- ends in `end_headers`, so
    the security headers are never something a new route, or a path this handler does
    not even dispatch itself, can forget to add."""

    server_version = "GripSuiteSetup/1"
    server: _SetupServer  # narrows the inherited Any for readability only

    def end_headers(self) -> None:
        """The one place every response -- ours and the stdlib's own default error
        page alike -- finishes its headers. Adding the fixed security headers here,
        rather than only in `_send_bytes`, is what makes "every response carries them"
        literally true instead of true-only-for-the-routes-this-module-wrote."""
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    def log_request(self, code="-", size="-") -> None:  # noqa: D102 -- stdlib signature
        """Never log the raw request line: `GET /` carries the token in its query
        string, and the token must appear in no log line (the security requirement this
        overrides the stdlib default specifically to meet). `self.path` is set only
        once a request line has been split into method/path/version successfully, so a
        malformed line (one word, no path at all) reaches here -- via `send_error` --
        with no `path` attribute yet; `getattr` keeps that case a clean log line
        instead of an `AttributeError` that aborts the response entirely."""
        path = getattr(self, "path", "").split("?", 1)[0]
        command = getattr(self, "command", None) or "-"
        self.log_message('"%s %s" %s', command, path, str(code))

    def log_error(self, format: str, *args: object) -> None:  # noqa: D102 -- stdlib signature
        """The stdlib's own error paths (a malformed request line, a socket timeout)
        call this directly, before any response is sent, with the raw, unparsed
        request text as one of `args`. For a `GET /` whose query string carried the
        token, that text can be the whole request line. Never interpolate a caller's
        `args` into the log for that reason -- `log_request` above already logs a
        safe line for everything that reaches a real response."""

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def _handle(self, method: str) -> None:
        try:
            self._route(method)
        finally:
            self.server.note_activity()

    def _route(self, method: str) -> None:
        if self.headers.get("Host") != self.server.expected_host:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return
        if method == "POST":
            if self.headers.get("Origin") != self.server.expected_origin:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
                return
            # The POST token, checked ONCE here rather than once per handler --
            # _serve_run and _serve_done no longer check it themselves.
            if not self._token_ok(self.headers.get(TOKEN_HEADER, "")):
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
                return
        path = urlsplit(self.path).path
        handler = _ROUTES.get((method, path))
        if handler is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        handler(self)

    def _token_ok(self, given: str) -> bool:
        """`hmac.compare_digest` refuses two `str` arguments unless both are pure
        ASCII, so a non-ASCII token guess (a stray `?t=%C3%A9...`, say) would otherwise
        raise `TypeError` out of here uncaught. Comparing the UTF-8 bytes instead works
        for any input and keeps the same constant-time guarantee."""
        return hmac.compare_digest(given.encode("utf-8"), self.server.token.encode("utf-8"))

    def _serve_index(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        given = query.get(TOKEN_PARAM, [""])[0]
        if not self._token_ok(given):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return
        self._send_bytes(self.server.page_bytes, "text/html; charset=utf-8")

    def _serve_js(self) -> None:
        self._send_bytes(self.server.js_bytes, "text/javascript; charset=utf-8")

    def _serve_css(self) -> None:
        self._send_bytes(self.server.css_bytes, "text/css; charset=utf-8")

    def _serve_run(self) -> None:
        length = self._content_length()
        if length is None:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "bad content-length"})
            return
        if length > MAX_BODY_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body too large"})
            return
        try:
            body = self._read_json_body(length)
        except _BodyTimedOut:
            self._send_json(HTTPStatus.REQUEST_TIMEOUT, {"error": "request timeout"})
            return
        name = body.get("step") if isinstance(body, dict) else None
        step = self.server.steps_by_name.get(name) if isinstance(name, str) else None
        if step is None:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "no such step"})
            return
        result = steps_mod.run_step(step, dry_run=False)
        seal_error = self._record_step(step, result)
        payload = {"name": result.name, "status": result.status,
                   "detail": result.detail, "seconds": result.seconds}
        if seal_error:
            payload["seal_error"] = seal_error
        self._send_json(HTTPStatus.OK, payload)

    def _record_step(self, step: steps_mod.Step, result: steps_mod.StepResult) -> Optional[str]:
        """Append one ledger line for this run, then seal once the signing-key step
        itself holds -- never before, and never with the shared demo key. Returns the
        seal's refusal text, or ``None`` when nothing was sealed or the seal held."""
        server = self.server
        holds = result.status in (steps_mod.DONE, steps_mod.ALREADY_DONE)
        ledger.append_step(server.step_file, step=result.name, command=step.command or step.name,
                           exit_code=0 if holds else 1, output=result.detail)
        if step.name == KEY_STEP_NAME and holds:
            return _attempt_seal(server)
        return None

    def _serve_check(self) -> None:
        result = ledger.check(self.server.step_file, self.server.ledger_file,
                              expected_tenant_id=self.server.tenant)
        self._send_json(HTTPStatus.OK, {
            "passed": result.passed, "reason": result.reason, "detail": result.detail,
        })

    def _serve_done(self) -> None:
        server = self.server
        rechecked = steps_mod.recheck(tuple(server.steps_by_name.values()))
        all_hold = all(result.status == steps_mod.STILL_TRUE for result in rechecked)
        summary = "; ".join(f"{result.name}: {result.status}" for result in rechecked)
        ledger.append_step(server.step_file, step="Done", command="recheck every step",
                           exit_code=0 if all_hold else 1, output=summary or "no steps")
        seal_error = _attempt_seal(server)
        result = ledger.check(server.step_file, server.ledger_file,
                              expected_tenant_id=server.tenant)
        payload = {"passed": result.passed, "reason": result.reason, "detail": result.detail}
        if seal_error:
            payload["seal_error"] = seal_error
        self._send_json(HTTPStatus.OK, payload)
        server.should_stop = True

    def _content_length(self) -> Optional[int]:
        """The request's declared body size: 0 when the header is absent, `None` when
        it is present but not a plain non-negative integer -- never an uncaught
        `ValueError`. The caller turns `None` into a clean 400, never a crash."""
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0
        try:
            length = int(raw)
        except ValueError:
            return None
        return length if length >= 0 else None

    def _read_json_body(self, length: int):
        """Read exactly `length` bytes -- already checked non-negative and within
        `MAX_BODY_BYTES` by the caller -- and parse them as JSON. Anything that is not
        valid JSON, or not valid UTF-8, is read as no body at all (`{}`). Raises
        `_BodyTimedOut` when a declared body never fully arrives before this
        connection's own socket read timeout -- never lets the bare `TimeoutError`
        escape uncaught. `except TimeoutError` alone is enough: `socket.timeout` is
        the SAME class as the builtin `TimeoutError` from Python 3.10 (the version
        check is `sys.version_info >= (3, 10)`), and this package's own doors
        install Python 3.11+ before it ever runs (see the package docstring).
        This still only bounds a client that sends NONE of the declared body: the
        timeout resets on every successful `recv`, so a client trickling it in one
        byte at a time stays under the per-read timeout without a cumulative
        deadline -- a pre-existing limit of this module's one-timeout-per-read
        design (Qwen4-Max, GRIP#9944), not something this method's own 408
        narrows or widens."""
        if length > 0:
            try:
                raw = self.rfile.read(length)
            except TimeoutError as exc:
                raise _BodyTimedOut() from exc
        else:
            raw = b""
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        self._send_bytes(json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8",
                         status=status)

    def _send_bytes(self, data: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


_ROUTES: dict[tuple[str, str], Callable[[_Handler], None]] = {
    ("GET", "/"): _Handler._serve_index,
    ("GET", f"/{PAGE_JS_NAME}"): _Handler._serve_js,
    ("GET", f"/{PAGE_CSS_NAME}"): _Handler._serve_css,
    ("POST", "/run"): _Handler._serve_run,
    ("POST", "/done"): _Handler._serve_done,
    ("POST", "/check"): _Handler._serve_check,
}


def _serve_until_done(server: _SetupServer, poll_interval: float = POLL_INTERVAL_SECONDS) -> None:
    """Handle one request at a time until told to stop or left idle too long. Each
    request is answered with its connection then closed (the handler offers no HTTP/1.1
    keep-alive), so `handle_request` always returns promptly once a request actually
    arrives -- and now that every accepted connection has its own read timeout, a
    client that opens a socket and sends nothing cannot keep `handle_request` from
    returning either. This loop re-checks the idle limit after every single request
    rather than only every `poll_interval`."""
    server.timeout = min(poll_interval, server.idle_limit_seconds)
    while not server.should_stop and server.idle_seconds() < server.idle_limit_seconds:
        server.handle_request()
    server.server_close()


@dataclass(frozen=True)
class PageServer:
    """A running setup page. `url` is this run's one-time link, token included -- both
    it and `token` itself are kept out of `repr()`, so printing or logging this object
    cannot leak it by accident."""

    url: str = field(repr=False)
    token: str = field(repr=False)
    host: str
    port: int
    thread: threading.Thread = field(repr=False)
    _httpd: _SetupServer = field(repr=False)

    def stop(self) -> None:
        """Ask the server to stop and wait for it to. Safe to call more than once, and
        safe even if the server has already stopped on its own (idle or `/done`)."""
        self._httpd.should_stop = True
        self.thread.join()

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until the server thread stops, or until `timeout` seconds pass if
        given. `wait(None)` -- the default -- blocks until the server stops on its
        own, on `POST /done` or the idle limit, with no timeout at all."""
        self.thread.join(timeout)


def start(steps: list[steps_mod.Step], *, setup_dir: Path, host: str = "127.0.0.1", port: int = 0,
          idle_limit_seconds: float = DEFAULT_IDLE_LIMIT_SECONDS,
          handler_timeout_seconds: float = DEFAULT_HANDLER_TIMEOUT_SECONDS,
          token: Optional[str] = None, clock: Callable[[], float] = time.monotonic) -> PageServer:
    """Bind the server and start serving in a background thread; return at once -- the
    caller decides whether to print the URL, open a browser, and how long to wait.
    ``setup_dir`` is where this run's step chain, ledger and tenant id live -- the
    caller's to name, never a path this function guesses."""
    token = token if token is not None else secrets.token_urlsafe(32)
    httpd = _SetupServer((host, port), _handler_class(handler_timeout_seconds), steps=steps,
                        token=token, idle_limit_seconds=idle_limit_seconds, clock=clock,
                        setup_dir=setup_dir)
    url = f"http://{host}:{httpd.server_port}/?{TOKEN_PARAM}={token}"
    thread = threading.Thread(target=_serve_until_done, args=(httpd,), daemon=True)
    thread.start()
    return PageServer(url=url, token=token, host=host, port=httpd.server_port,
                      thread=thread, _httpd=httpd)


def run(steps: Optional[list[steps_mod.Step]] = None, *, setup_dir: Path,
        open_browser: bool = True, idle_limit_seconds: float = DEFAULT_IDLE_LIMIT_SECONDS,
        out: Callable[[str], None] = print) -> int:
    """The command-line entry point: start the server, print its one-run URL, open the
    browser unless told not to (tests pass `open_browser=False`), and wait for the
    server to stop on `POST /done` or idle timeout. With no `steps` given, builds the
    real product list plus the signing-key step, under this run's own tenant."""
    if steps is None:
        from . import cli as cli_mod, products
        layout = products.Layout(home=Path.home(), source=cli_mod.SOURCE)
        tenant = load_or_create_tenant(setup_dir)
        steps = _steps_with_key(products.suite_steps(layout), tenant)
    server = start(steps, setup_dir=setup_dir, idle_limit_seconds=idle_limit_seconds)
    out(f"Open this in your browser: {server.url}")
    if open_browser:
        webbrowser.open(server.url)
    server.wait()
    return 0


if __name__ == "__main__":
    from . import cli as cli_mod, products
    _layout = products.Layout(home=Path.home(), source=cli_mod.SOURCE)
    raise SystemExit(run(setup_dir=_layout.claude / "state" / "suite-setup"))
