#!/usr/bin/env python3
"""PDF → spoken briefing (a client contact's "brief me in 10 minutes", 2026-06-09 challenge).

Turns any PDF (tested target: 45 pages) into a listenable audio briefing in
minutes, end to end on free substrate:

    extract (pdftotext / PyMuPDF fallback)
    → map-reduce summarisation via HAL gateway /api/infer
      (provider-laddered: CCH / Groq / Gemini / local — frontier quality, ≈R0)
    → spoken-briefing script (conversational, ~700 words ≈ 4-5 listening min)
    → macOS `say` (British voice) → afconvert m4a
    → optional WhatsApp delivery via the grip-channel daemon (raw MCP JSON-RPC,
      self-contained initialize → notify → channel_send_file)

Usage:
    pdf_to_audio_brief.py INPUT.pdf [--send-to JID] [--voice Daniel]
                          [--title "Name"] [--out /tmp/brief.m4a]

Fail-soft per stage with plain-language errors; exit 0 only when audio exists.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

HAL_INFER_URL = "http://127.0.0.1:3850/api/infer"
DAEMON_MCP_URL = "http://127.0.0.1:3101/mcp"
CHUNK_PAGES = 9          # pages per map-summary
MAP_TOKENS = 350         # per-chunk summary budget
REDUCE_TOKENS = 1100     # final spoken script budget (~700 words)


def extract_pages(pdf: Path) -> list[str]:
    """Per-page text. pdftotext first; PyMuPDF fallback. Raises on no text."""
    pages: list[str] = []
    try:
        import fitz  # PyMuPDF — gives an exact page count cheaply
        doc = fitz.open(pdf)
        for page in doc:
            pages.append(page.get_text())
        doc.close()
    except Exception:
        out = subprocess.run(["pdftotext", str(pdf), "-"],
                             capture_output=True, text=True, timeout=180)
        if out.returncode != 0:
            raise RuntimeError(f"text extraction failed: {out.stderr[:120]}")
        pages = out.stdout.split("\f")
    if not any(p.strip() for p in pages):
        raise RuntimeError("PDF contains no extractable text (scanned image?)")
    return pages


def _shield(payload: dict) -> dict:
    """``payload`` scrubbed by lib/review_redaction.py before it leaves; raises
    OSError, so nothing is sent, when it cannot be scrubbed."""
    try:
        from lib.review_redaction import shield_payload
    except ImportError as exc:  # no shield, no send
        raise OSError("the redaction shield is unavailable, so nothing was sent") from exc
    return shield_payload(payload)


def hal(prompt: str, max_tokens: int, retries: int = 2) -> str:
    """One HAL gateway call. Provider ladder lives server-side. The prompt passes
    the redaction shield first; when it cannot, nothing is sent and this raises."""
    try:
        data = json.dumps(_shield({"prompt": prompt, "max_tokens": max_tokens})).encode()
    except OSError as exc:  # a failed scrub, or no shield to load: nothing is sent
        raise RuntimeError(f"HAL gateway not called: {exc}") from exc
    req = urllib.request.Request(
        HAL_INFER_URL, data=data, headers={"Content-Type": "application/json"},
    )
    last = ""
    for _ in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read().decode())
            if body.get("ok") and body.get("text"):
                return str(body["text"]).strip()
            last = body.get("error_message") or "empty response"
        except Exception as exc:  # noqa: BLE001 — retry any transport error
            last = str(exc)
        time.sleep(2)
    raise RuntimeError(f"HAL gateway failed after retries: {last[:140]}")


def summarise(pages: list[str], title: str) -> str:
    """Map-reduce the document into a spoken-briefing script."""
    chunks = ["\n".join(pages[i:i + CHUNK_PAGES])
              for i in range(0, len(pages), CHUNK_PAGES)]
    maps: list[str] = []
    for n, chunk in enumerate(chunks, 1):
        print(f"[brief] summarising chunk {n}/{len(chunks)}…", flush=True)
        maps.append(hal(
            "Summarise this document section in 5-8 tight bullet points. "
            "Keep every load-bearing fact, name, number and obligation. "
            f"Section {n} of {len(chunks)} of '{title}':\n\n{chunk[:24000]}",
            MAP_TOKENS,
        ))
    print("[brief] reducing to spoken script…", flush=True)
    return hal(
        "You write spoken briefings for a busy lawyer who is walking the dog. "
        "Combine these section summaries into ONE flowing spoken briefing "
        "script: start with one sentence saying what the document is, then "
        "the key points in plain English, then implications, then any "
        "deadlines or actions. Conversational, no markdown, no headings, no "
        "bullet symbols — it will be read aloud verbatim. About 650 words. "
        f"Document: '{title}'.\n\nSection summaries:\n\n" + "\n\n".join(maps),
        REDUCE_TOKENS,
    )


def render_audio(script: str, out_m4a: Path, voice: str) -> None:
    txt = out_m4a.with_suffix(".txt")
    aiff = out_m4a.with_suffix(".aiff")
    txt.write_text(script)
    subprocess.run(["say", "-v", voice, "-o", str(aiff), "-f", str(txt)],
                   check=True, timeout=600)
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", str(aiff), str(out_m4a)],
                   check=True, timeout=300)
    aiff.unlink(missing_ok=True)


def send_whatsapp_file(jid: str, path: Path, caption: str) -> str:
    """Self-contained MCP JSON-RPC: initialize → initialized → send_file."""
    def post(payload: dict, session: str | None = None, want_headers: bool = False):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if session:
            headers["Mcp-Session-Id"] = session
        req = urllib.request.Request(DAEMON_MCP_URL, data=json.dumps(payload).encode(),
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.headers.get("Mcp-Session-Id") if want_headers else resp.read().decode()

    sess = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                            "clientInfo": {"name": "pdf-brief", "version": "1.0"}}},
                want_headers=True)
    post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, sess)
    out = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "channel_send_file",
                           "arguments": {"recipient": jid, "file_path": str(path),
                                          "caption": caption}}}, sess)
    return out[-300:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--send-to", help="WhatsApp JID (group or contact)")
    ap.add_argument("--voice", default="Daniel")
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    t0 = time.time()
    title = args.title or args.pdf.stem.replace("-", " ").replace("_", " ")
    out = args.out or Path("/tmp") / (args.pdf.stem + "-brief.m4a")

    pages = extract_pages(args.pdf)
    print(f"[brief] {len(pages)} pages extracted")
    script = summarise(pages, title)
    render_audio(script, out, args.voice)
    mins = (time.time() - t0) / 60
    print(f"[brief] audio ready: {out} ({out.stat().st_size // 1024}KB) in {mins:.1f} min")

    if args.send_to:
        caption = (f"◈ GRIP here: 🎧 your spoken briefing of '{title}' "
                   f"({len(pages)} pages → {out.stat().st_size // 1024}KB of audio, "
                   f"produced in {mins:.1f} minutes). Press play.")
        print("[brief] sent:", send_whatsapp_file(args.send_to, out, caption))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
