#!/usr/bin/env python3
"""GRIP Telegram relay — query GRIP from your phone, anywhere.

Purpose-built for an always-on WINDOWS machine (runs unchanged on
macOS/Linux). One Python file, stdlib only (syscall-doctrine + KISS):
one HTTP boundary (Telegram Bot API long-poll), one subprocess boundary
(a short-lived ``claude -p`` per message), atomic file persistence.

Architecture (mirrors the proven VPS relay, lib/relay_dispatcher.py)::

    [phone: Telegram app] -> Telegram Bot API (cloud)
        ^ long-poll getUpdates (OUTBOUND https only - works behind any
        | NAT/firewall; the host machine needs no open inbound port)
    [host: this relay, a scheduled task / service]
        | sender allowlist: ONLY the enrolled Telegram user id(s)
        | spawn: claude -p --output-format json
        |        --dangerously-skip-permissions --strict-mcp-config
        |        --tools=Read,Grep,Glob   (capability lock, see below)
        v cwd = the GRIP install dir -> answers ARE GRIP-grounded
    reply -> sendMessage back to the chat, chunked to Telegram's limit

Security model (rules/untrusted-content-capability-lock.md):
  * The SENDER is authenticated by a Telegram user-id allowlist, enrolled
    once via a one-time code printed by the setup script (closes the
    "first stranger to find the bot wins" race). Everyone else is ignored
    silently and logged.
  * The CONTENT is never trusted: the spawned claude is capability-locked
    to the read-only tool set ``Read,Grep,Glob`` BY CONSTRUCTION.
    ``--dangerously-skip-permissions`` is required (no TTY -> a permission
    prompt would hang), but it only bypasses permission PROMPTS; the
    ``--tools=`` allowlist is the orthogonal capability layer, so forwarded
    / attacker-crafted text can never drive host write/exec (pal #417
    class). Widening the set is an explicit config edit, never an accident.
  * EQUALS form ``--tools=X`` is load-bearing: the space form is variadic
    in the claude CLI and swallows the trailing positional prompt
    (verified 2026-06-10 on the VPS relay).

This module is the SECOND instance of the relay-spawn pattern
(relay_dispatcher.py is the first). Shared shapes (argv construction,
3-shape result parse, stale-resume retry, session bloat cap) are
copy-adapted per rules/knowledge-maturation-functor.md. Under its N=2 (since
2026-10-03; it was the third instance before) this second instance makes the
shared shim eligible. It is not built yet, and because this path spawns on
untrusted inbound content its first build goes through the promotion gate with
a person in the loop.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://api.telegram.org"
TELEGRAM_MAX_CHARS = 4096
CHUNK_CHARS = 4000  # headroom under Telegram's hard 4096 limit
POLL_TIMEOUT_S = 50  # Telegram long-poll ceiling; one cheap request/50s idle
DEFAULT_SPAWN_TIMEOUT_S = 300  # matches the VPS relay EVENT_TIMEOUT rationale
DEFAULT_MODEL = "claude-haiku-4-5-20251001"  # relay economics: short phone turns
_RELAY_SAFE_TOOLS = "Read,Grep,Glob"  # mirror relay_dispatcher._RELAY_SAFE_TOOLS
_SESSION_MAX_BYTES = 2_000_000  # resume-bloat cap (relay_dispatcher rationale)
_LOG_MAX_BYTES = 5_000_000

DEFAULT_SYSTEM_PROMPT = (
    "You are GRIP, answering your operator over a Telegram phone relay. "
    "Replies render as plain text on a phone: lead with the answer, keep it "
    "concise, and avoid markdown tables or long code blocks unless asked. "
    "Prefix '/fresh' starts a new conversation; otherwise the chat continues "
    "the previous session."
)

HELP_TEXT = (
    "GRIP Telegram relay commands:\n"
    "/ping - liveness check (no AI spawn)\n"
    "/fresh <question> - answer in a NEW session (drop chat history)\n"
    "/help - this text\n"
    "Anything else is sent to GRIP with per-chat session continuity."
)


class RelayApiError(RuntimeError):
    """Telegram API returned ok=false or an HTTP error."""

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = retry_after


# ---------------------------------------------------------------- config ----

def _default_grip_dir() -> Path:
    """The GRIP install dir: ~/.grip when present, else ~/.claude."""
    for name in (".grip", ".claude"):
        cand = Path.home() / name
        if cand.is_dir():
            return cand
    return Path.home() / ".grip"


def config_path() -> Path:
    """Resolve the relay config file (env override first)."""
    env = os.environ.get("GRIP_TELEGRAM_CONFIG", "").strip()
    if env:
        return Path(env)
    return _default_grip_dir() / "state" / "telegram-relay" / "config.json"


def load_config(path: Path | None = None) -> tuple[dict, Path]:
    """Load config; SystemExit with a plain-language remedy when absent."""
    path = path or config_path()
    if not path.exists():
        raise SystemExit(
            f"No relay config at {path}.\n"
            "Run scripts/windows-telegram-relay-setup.ps1 (Windows) or create "
            "the file with at least {\"bot_token\": \"...\", \"enroll_code\": "
            "\"123456\"} — see docs/windows-telegram-relay.md."
        )
    # utf-8-sig: Windows PowerShell's Set-Content writes a UTF-8 BOM; a plain
    # utf-8 read would leave ﻿ in the stream and json.loads would fail.
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    if not str(cfg.get("bot_token", "")).strip():
        raise SystemExit(f"Config {path} has no bot_token.")
    return cfg, path


def save_config(cfg: dict, path: Path) -> None:
    """Atomic config write (temp + os.replace) — never a half-written token."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _grip_dir(cfg: dict) -> Path:
    configured = str(cfg.get("grip_dir", "")).strip()
    return Path(configured) if configured else _default_grip_dir()


def _state_dir(cfg_path: Path) -> Path:
    return cfg_path.parent


# ------------------------------------------------------------- telemetry ----

def _redact(text: str, token: str) -> str:
    """Bot token must never reach a log line (it embeds in API URLs)."""
    return text.replace(token, "<TOKEN>") if token else text


def log_event(cfg_path: Path, record: dict) -> None:
    """Append one JSONL telemetry line; size-capped by simple rotation."""
    log = _state_dir(cfg_path) / "relay-log.jsonl"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists() and log.stat().st_size > _LOG_MAX_BYTES:
            os.replace(log, log.with_suffix(".jsonl.1"))
        record = {"ts": round(time.time(), 3), **record}
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass  # telemetry is best-effort; the relay never dies for a log line


# ------------------------------------------------------------ Telegram API --

def api_call(token: str, method: str, params: dict, timeout: float) -> dict:
    """POST one Bot API method; return the ``result`` payload.

    Raises RelayApiError (with retry_after when Telegram supplies one) on
    ok=false or transport errors — callers decide backoff, per
    rules/rate-limit-never-blocks.md a 429 is a wait-then-continue signal,
    never a reason to exit.
    """
    url = f"{API_BASE}/bot{token}/{method}"
    data = urllib.parse.urlencode(
        {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
         for k, v in params.items()}
    ).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # includes 409/429 with JSON bodies
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"ok": False, "description": f"HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RelayApiError(_redact(f"{method}: {exc}", token)) from exc
    if not body.get("ok"):
        retry = float((body.get("parameters") or {}).get("retry_after", 0))
        raise RelayApiError(
            _redact(f"{method}: {body.get('description', 'unknown error')}",
                    token),
            retry_after=retry,
        )
    return body.get("result", {})


def get_updates(token: str, offset: int) -> list:
    return api_call(
        token, "getUpdates",
        {"offset": offset, "timeout": POLL_TIMEOUT_S,
         "allowed_updates": ["message"]},
        timeout=POLL_TIMEOUT_S + 15,
    ) or []


def send_typing(token: str, chat_id: int) -> None:
    try:
        api_call(token, "sendChatAction",
                 {"chat_id": chat_id, "action": "typing"}, timeout=10)
    except RelayApiError:
        pass  # cosmetic only


def chunk_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Split into <=limit chunks, preferring newline boundaries.

    Index-based single pass — O(n) over the reply (repeated slicing of the
    remainder would be O(n^2) on long replies).
    """
    text = text or ""
    if not text.strip():
        return []
    chunks: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + limit, n)
        next_start = end
        if end < n:
            cut = text.rfind("\n", start + 1, end)
            if cut != -1 and cut - start >= limit // 2:
                end, next_start = cut, cut + 1  # split at, and skip, the newline
        chunks.append(text[start:end])
        start = next_start
    return chunks


def send_chunks(token: str, chat_id: int, text: str) -> int:
    """Send a (possibly long) reply as sequential plain-text messages."""
    sent = 0
    for chunk in chunk_text(text):
        api_call(token, "sendMessage",
                 {"chat_id": chat_id, "text": chunk,
                  "disable_web_page_preview": True}, timeout=30)
        sent += 1
    return sent


# ------------------------------------------------- claude spawn (locked) ----

def _claude_bin(cfg: dict) -> str:
    configured = str(cfg.get("claude_bin", "")).strip()
    if configured:
        return configured
    return shutil.which("claude") or "claude"


def build_argv(text: str, session_id: str | None, cfg: dict) -> list[str]:
    """One-shot ``claude -p`` argv — capability tiers mirror the VPS relay.

    Default is the read-only lock ``--tools=Read,Grep,Glob``. A config
    ``tools`` value widens it EXPLICITLY (e.g. add Write,Edit for document
    work); ``tools: "full"`` removes the restriction entirely — only sane
    on a machine you own, for a bot only you can message, and it is still
    an allowlist decision recorded in config, never a content-driven one.
    EQUALS form is load-bearing (variadic space form eats the prompt).
    """
    argv = [
        _claude_bin(cfg), "-p", "--output-format", "json",
        "--dangerously-skip-permissions", "--strict-mcp-config",
    ]
    tools = str(cfg.get("tools", "")).strip() or _RELAY_SAFE_TOOLS
    if tools.lower() != "full":
        argv += [f"--tools={tools}"]
    if session_id:
        argv += ["--resume", session_id]
    prompt = str(cfg.get("system_prompt", "")).strip() or DEFAULT_SYSTEM_PROMPT
    argv += ["--append-system-prompt", prompt]
    model = str(cfg.get("model", DEFAULT_MODEL)).strip()
    if model and model.lower() not in ("off", "none", "default"):
        argv += ["--model", model]
    argv += [text]
    return argv


def _result_from_objs(objs: list) -> tuple[str | None, str]:
    """(session_id, result_text) from a list of claude -p event objects."""
    dicts = [o for o in objs if isinstance(o, dict)]
    sid = next((o["session_id"] for o in reversed(dicts)
                if o.get("session_id")), None)
    res = [o for o in dicts if o.get("type") == "result" and "result" in o]
    if not res:
        res = [o for o in dicts if "result" in o]
    return sid, (str(res[-1].get("result") or "") if res else "")


def _ndjson_objs(stdout: str) -> list:
    """Parse newline-delimited JSON objects, skipping un-parseable lines."""
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except (ValueError, TypeError):
            continue
    return out


def _parse_result(stdout: str) -> tuple[str | None, str]:
    """(session_id, reply_text) from ``claude -p --output-format json``.

    Handles the three shapes the CLI emits: one result object, a JSON array
    of event objects, and newline-delimited objects (copy-adapted from
    relay_dispatcher._parse_result — see module docstring on the N=2 shim).
    """
    try:
        whole = json.loads(stdout)
    except (ValueError, TypeError):
        whole = None
    if isinstance(whole, list):
        return _result_from_objs(whole)
    if isinstance(whole, dict):
        return whole.get("session_id"), str(whole.get("result", ""))
    return _result_from_objs(_ndjson_objs(stdout))


# ------------------------------------------------- per-chat session state ---

def _session_file(cfg_path: Path, chat_id: int) -> Path:
    return _state_dir(cfg_path) / "sessions" / f"{chat_id}.txt"


def read_session(cfg_path: Path, chat_id: int) -> str | None:
    path = _session_file(cfg_path, chat_id)
    if not path.exists():
        return None
    sid = path.read_text(encoding="utf-8").strip()
    return sid or None


def write_session(cfg_path: Path, chat_id: int, session_id: str) -> None:
    path = _session_file(cfg_path, chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(session_id.strip() + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _session_too_large(cfg: dict, session_id: str) -> bool:
    """Bloat guard: resuming a huge transcript churns then fails — go fresh.

    The transcript lives at <grip_dir>/projects/<cwd-slug>/<sid>.jsonl; the
    slug encodes the spawn cwd with separators/dots as dashes. Any lookup
    miss returns False (fail-open toward continuity), matching the VPS relay.
    """
    try:
        base = _grip_dir(cfg)
        slug = str(base).replace("\\", "-").replace("/", "-").replace(
            ":", "-").replace(".", "-")
        p = base / "projects" / slug / f"{session_id}.jsonl"
        return p.exists() and p.stat().st_size > _SESSION_MAX_BYTES
    except OSError:
        return False


# ---------------------------------------------------------- ask + handle ----

def ask_grip(text: str, chat_id: int, cfg: dict, cfg_path: Path,
             fresh: bool = False) -> str:
    """Run one capability-locked spawn; persist session; honest failures."""
    session_id = None if fresh else read_session(cfg_path, chat_id)
    if session_id and _session_too_large(cfg, session_id):
        session_id = None
    timeout = int(cfg.get("spawn_timeout_s", DEFAULT_SPAWN_TIMEOUT_S))
    grip_dir = _grip_dir(cfg)
    started = time.time()

    def spawn(sid: str | None) -> subprocess.CompletedProcess:
        return subprocess.run(
            build_argv(text, sid, cfg), cwd=str(grip_dir),
            capture_output=True, text=True, timeout=timeout,
        )

    try:
        proc = spawn(session_id)
        if proc.returncode != 0 and session_id:
            proc = spawn(None)  # stale/GC'd resume target — one fresh retry
    except subprocess.TimeoutExpired:
        log_event(cfg_path, {"event": "spawn_timeout", "chat_id": chat_id,
                             "timeout_s": timeout})
        return (f"GRIP timed out after {timeout}s on that one. "
                "Try a narrower question, or /fresh to reset the session.")
    except OSError as exc:
        log_event(cfg_path, {"event": "spawn_oserror", "error": str(exc)[:200]})
        return ("GRIP could not start the claude CLI on the relay host "
                f"({exc}). Check that `claude` is installed and logged in.")

    sid, reply = _parse_result(proc.stdout or "")
    log_event(cfg_path, {
        "event": "spawn", "chat_id": chat_id, "exit": proc.returncode,
        "elapsed_ms": int((time.time() - started) * 1000),
        "reply_chars": len(reply), "resumed": bool(session_id),
    })
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        return ("GRIP hit an error answering that (exit "
                f"{proc.returncode}). {tail}")
    if sid:
        write_session(cfg_path, chat_id, sid)
    return reply.strip() or "(GRIP returned an empty reply.)"


def _try_enroll(cfg: dict, cfg_path: Path, uid: int, text: str) -> str | None:
    """One-time-code enrollment. Returns reply text, or None to stay silent.

    Fail-closed: with no allowlist AND no enroll code the relay refuses to
    answer anyone (main_loop refuses to even start in that state).
    """
    code = str(cfg.get("enroll_code") or "").strip()
    if code and text.strip() == code:
        cfg["allowed_user_ids"] = [uid]
        cfg["enroll_code"] = None
        save_config(cfg, cfg_path)
        log_event(cfg_path, {"event": "enrolled", "user_id": uid})
        return ("Enrolled. This relay now answers ONLY this Telegram account. "
                "Send /ping to test, /help for commands, or just ask GRIP "
                "something.")
    log_event(cfg_path, {"event": "ignored_unenrolled", "user_id": uid})
    return None


def handle_text(cfg: dict, cfg_path: Path, uid: int, chat_id: int,
                text: str) -> str | None:
    """Route one inbound message. Returns the reply, or None (ignore)."""
    allowed = cfg.get("allowed_user_ids") or []
    if not allowed:
        return _try_enroll(cfg, cfg_path, uid, text)
    if uid not in allowed:
        log_event(cfg_path, {"event": "ignored_not_allowlisted",
                             "user_id": uid})
        return None
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered in ("/start", "/help"):
        return HELP_TEXT
    if lowered == "/ping":
        import platform
        return (f"GRIP relay alive on {platform.node()} "
                f"({sys.platform}, python {sys.version.split()[0]}).")
    for prefix in ("/fresh", "/new"):
        if lowered == prefix or lowered.startswith(prefix + " "):
            rest = stripped[len(prefix):].strip()
            if not rest:
                return "Session reset noted — send your question after /fresh."
            return ask_grip(rest, chat_id, cfg, cfg_path, fresh=True)
    return ask_grip(stripped, chat_id, cfg, cfg_path)


# ------------------------------------------------------------- main loop ----

def _offset_file(cfg_path: Path) -> Path:
    return _state_dir(cfg_path) / "offset.txt"


def read_offset(cfg_path: Path) -> int:
    try:
        return int(_offset_file(cfg_path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def write_offset(cfg_path: Path, offset: int) -> None:
    path = _offset_file(cfg_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(str(offset) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def process_update(cfg: dict, cfg_path: Path, token: str, upd: dict) -> None:
    msg = upd.get("message") or {}
    text = msg.get("text")
    uid = (msg.get("from") or {}).get("id")
    chat_id = (msg.get("chat") or {}).get("id")
    if not text or uid is None or chat_id is None:
        return  # stickers/photos/joins — v1 answers text only
    if cfg.get("allowed_user_ids") and uid in cfg["allowed_user_ids"]:
        send_typing(token, chat_id)
    reply = handle_text(cfg, cfg_path, uid, chat_id, text)
    if reply:
        send_chunks(token, chat_id, reply)


def _drain_updates(cfg_path: Path, token: str, updates: list,
                   offset: int) -> int:
    """Process one getUpdates batch; persist offset per update.

    Config is re-read per update so an enrollment (or an operator config
    edit) takes effect immediately, without a relay restart. A send failure
    on one update never poisons the rest of the batch.
    """
    for upd in updates:
        offset = max(offset, int(upd.get("update_id", offset)))
        write_offset(cfg_path, offset)
        try:
            cfg, _ = load_config(cfg_path)
            process_update(cfg, cfg_path, token, upd)
        except RelayApiError as exc:
            log_event(cfg_path, {"event": "send_error",
                                 "error": str(exc)[:200]})
    return offset


def main_loop(cfg_path: Path | None = None) -> None:
    """Poll forever. Errors back off and continue — the loop never exits
    on a transient failure (rules/rate-limit-never-blocks.md)."""
    cfg, cfg_path = load_config(cfg_path)
    if not (cfg.get("allowed_user_ids") or cfg.get("enroll_code")):
        raise SystemExit(
            "Refusing to start OPEN: config has neither allowed_user_ids nor "
            "an enroll_code. Set an enroll_code, restart, then send that code "
            "to the bot from your phone."
        )
    token = str(cfg["bot_token"]).strip()
    offset = read_offset(cfg_path)
    backoff = 1.0
    print(f"[telegram-relay] polling as configured in {cfg_path}", flush=True)
    while True:
        try:
            updates = get_updates(token, offset + 1)
            backoff = 1.0
            offset = _drain_updates(cfg_path, token, updates, offset)
        except RelayApiError as exc:
            wait = exc.retry_after or backoff
            log_event(cfg_path, {"event": "poll_error",
                                 "error": str(exc)[:200], "wait_s": wait})
            time.sleep(min(wait, 60))
            backoff = min(backoff * 2, 60)
        except Exception as exc:  # noqa: BLE001 — the relay must never die
            log_event(cfg_path, {"event": "loop_error",
                                 "error": f"{type(exc).__name__}: {exc}"[:200]})
            time.sleep(min(backoff, 60))
            backoff = min(backoff * 2, 60)


def _status(cfg_path: Path | None) -> int:
    cfg, cfg_path = load_config(cfg_path)
    print(f"config:   {cfg_path}")
    print(f"grip_dir: {_grip_dir(cfg)}")
    print(f"claude:   {_claude_bin(cfg)}")
    print(f"model:    {cfg.get('model', DEFAULT_MODEL)}")
    print(f"tools:    {cfg.get('tools') or _RELAY_SAFE_TOOLS}")
    print(f"enrolled: {cfg.get('allowed_user_ids') or '(awaiting enroll code)'}")
    print(f"offset:   {read_offset(cfg_path)}")
    log = _state_dir(cfg_path) / "relay-log.jsonl"
    if log.exists():
        lines = log.read_text(encoding="utf-8").splitlines()[-5:]
        print("recent events:")
        for line in lines:
            print(f"  {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cfg_override = None
    if "--config" in argv:
        cfg_override = Path(argv[argv.index("--config") + 1])
    if "--status" in argv:
        return _status(cfg_override)
    main_loop(cfg_override)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
