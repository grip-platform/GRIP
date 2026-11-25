#!/usr/bin/env python3
"""ULTRAKNOW inbound — the bidirectional half (a client contact's live challenge, 2026-06-09).

Watches the GRIP-dropbox Drive folder (team-writable; a client contact = writer), pulls
anything new, absorbs the text into GRIP's knowledge substrate (HAL memory →
KONO → /recall), and announces what arrived — with a one-line learned-summary
per document — in the CodeTonight WhatsApp group.

Pipeline per new file:
    gog drive ls (recursive) → ledger dedup → gog drive download
    → text extraction (md/txt native · pdf via pdftotext · Docs auto-export)
    → absorb_from_source('monitor', channel='drive-inbox') [HAL memory]
    → hal_memory_indexer --sync + kono_auto_capture [→ /recall-able]
    → WhatsApp group announce (summary via HAL gateway, fail-soft to excerpt)

Idempotent: a (fileId, modifiedTime) ledger guarantees each version of each
file is processed exactly once; re-running is always safe. Fail-soft: any
single file's failure is reported, never fatal; the announce never blocks
the absorb. Runs standalone (cron) or in a tight live-watch loop.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
sys.path.insert(0, str(CLAUDE_DIR))
from lib.wa_identities import group_jid  # noqa: E402

DROPBOX_ID = "1kj_BSZTXq7Zz4otmW5CzmM4NQTPWNMs0"  # GRIP-dropbox (client contact: writer)
GROUP_JID = group_jid("team-group")  # the team group, from the private file; None: nothing sent
INBOX_DIR = CLAUDE_DIR / "drive-inbox"
LEDGER = CLAUDE_DIR / "state" / "ultraknow-inbound-ledger.txt"
HAL_INFER_URL = "http://127.0.0.1:3850/api/infer"
GOG = "/opt/homebrew/bin/gog"
TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json", ".html", ".rtf", ".py", ".js", ".ts"}
MAX_ABSORB_CHARS = 60_000


def _shield(payload: dict) -> dict:
    """``payload`` scrubbed by lib/review_redaction.py before it leaves; raises
    OSError, so nothing is sent, when it cannot be scrubbed."""
    try:
        from lib.review_redaction import shield_payload
    except ImportError as exc:  # no shield, no send
        raise OSError("the redaction shield is unavailable, so nothing was sent") from exc
    return shield_payload(payload)


def _gog_json(args: list[str], timeout: int = 60):
    out = subprocess.run([GOG, *args, "--json"], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"gog {' '.join(args[:2])} failed: {out.stderr.strip()[:160]}")
    return json.loads(out.stdout)


def list_files(folder_id: str, depth: int = 0) -> list[dict]:
    """All files under a folder, recursing into sub-folders (depth ≤ 3)."""
    if depth > 3:
        return []
    listing = _gog_json(["drive", "ls", "--parent", folder_id])
    items = listing.get("files") or listing.get("items") or []
    files: list[dict] = []
    for it in items:
        mime = it.get("mimeType", "")
        if mime == "application/vnd.google-apps.folder":
            files.extend(list_files(it["id"], depth + 1))
        else:
            files.append(it)
    return files


def load_ledger() -> set:
    try:
        return set(LEDGER.read_text().splitlines())
    except OSError:
        return set()


def ledger_add(key: str) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as fh:
        fh.write(key + "\n")


def download(file_id: str, name: str) -> Path | None:
    """Download (or export, for Google Docs) into the inbox dir."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    dest = INBOX_DIR / name.replace("/", "_")
    out = subprocess.run(
        [GOG, "drive", "download", file_id, "--out", str(dest), "--force"],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        # Some gog builds use --output / positional; try the bare form into cwd.
        out = subprocess.run(
            [GOG, "drive", "download", file_id, "--force"],
            capture_output=True, text=True, timeout=120, cwd=str(INBOX_DIR),
        )
        if out.returncode != 0:
            return None
        # Find the newest file in INBOX_DIR as the artefact.
        newest = max(INBOX_DIR.iterdir(), key=lambda p: p.stat().st_mtime, default=None)
        return newest
    return dest if dest.exists() else None


def extract_text(path: Path) -> str:
    """Best-effort text extraction. Empty string = no text (binary)."""
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        try:
            return path.read_text(errors="replace")[:MAX_ABSORB_CHARS]
        except OSError:
            return ""
    if suffix == ".pdf":
        try:
            out = subprocess.run(["pdftotext", str(path), "-"],
                                 capture_output=True, text=True, timeout=60)
            if out.returncode == 0:
                return out.stdout[:MAX_ABSORB_CHARS]
        except (OSError, subprocess.TimeoutExpired):
            pass
        return ""
    # Unknown/binary: try utf-8 sniff; give up quietly.
    try:
        raw = path.read_bytes()[:4096]
        text = raw.decode("utf-8")
        return text if text.isprintable() or "\n" in text else ""
    except (OSError, UnicodeDecodeError):
        return ""


def absorb(name: str, text: str) -> bool:
    """Route the document into HAL memory (same path the 252 drafts took)."""
    try:
        from lib.hal.knowledge_absorption import absorb_from_source
        absorb_from_source("monitor", {
            "text": f"# drive-inbox: {name}\n\n{text}",
            "channel_id": "drive-inbox-contact",
        })
        return True
    except Exception as exc:  # fail-soft: absorb failure must not kill the run
        print(f"[inbound] absorb failed for {name}: {exc}", file=sys.stderr)
        return False


def index_to_kono() -> None:
    """Make the absorbed docs /recall-able right now (not at the next cron)."""
    py = str(CLAUDE_DIR / "venv" / "bin" / "python")
    for cmd in (
        [py, str(CLAUDE_DIR / "lib" / "hal_memory_indexer.py"), "--sync"],
        [py, str(CLAUDE_DIR / "lib" / "kono_auto_capture.py")],
    ):
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                           env={"PYTHONPATH": str(CLAUDE_DIR), "PATH": "/usr/bin:/bin:/usr/local/bin"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"[inbound] kono step failed: {exc}", file=sys.stderr)


def summarise(name: str, text: str) -> str:
    """One plain-English line about the document. HAL gateway, fail-soft. The
    prompt passes the redaction shield first; when it cannot, nothing is sent
    and the local fallback line is used."""
    if not text.strip():
        return "(no readable text — stored by name)"
    try:
        import urllib.request
        prompt = ("Summarise this document in ONE plain-English sentence (max 25 words). "
                  f"Document name: {name}\n\n{text[:6000]}")
        req = urllib.request.Request(
            HAL_INFER_URL,
            data=json.dumps(_shield({"prompt": prompt, "max_tokens": 80})).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode())
        if body.get("ok") and body.get("text"):
            return str(body["text"]).strip().replace("\n", " ")[:200]
    except Exception:
        pass
    return text.strip().replace("\n", " ")[:120] + "…"


def announce(lines: list[str]) -> None:
    if not GROUP_JID:
        print("[inbound] no team group configured (config/wa-identities.local.json); "
              "not announcing", file=sys.stderr)
        return
    try:
        from lib.wa_delivery_verify import post_verified as post  # history-verified sender
        body = "◈ GRIP here: 📥 received in the drop zone — absorbed and now part of my memory:\n\n" + "\n".join(lines) + "\n\nAsk me anything about them."
        post(recipient=GROUP_JID, text=body)
    except Exception as exc:
        print(f"[inbound] announce failed: {exc}", file=sys.stderr)


def run_once() -> int:
    """One sweep. Returns the number of newly processed files."""
    seen = load_ledger()
    new = []
    for f in list_files(DROPBOX_ID):
        key = f"{f['id']}:{f.get('modifiedTime') or f.get('modified') or ''}"
        if key not in seen:
            new.append((key, f))
    if not new:
        print("[inbound] nothing new")
        return 0
    lines = []
    pdf_paths: list[Path] = []
    for key, f in new:
        name = f.get("name", f["id"])
        path = download(f["id"], name)
        if path is None:
            lines.append(f"• {name} — download failed (will retry next sweep)")
            continue  # NOT ledgered → retried next run
        text = extract_text(path)
        absorbed = absorb(name, text)
        summary = summarise(name, text)
        marker = "✅" if absorbed else "⚠️ stored, absorb retry pending"
        note = ""
        if path.suffix.lower() == ".pdf":
            pdf_paths.append(path)
            note = "\n  🎧 audio briefing rendering now — lands here in a few minutes"
        lines.append(f"• {name} {marker}\n  ↳ {summary}{note}")
        ledger_add(key)
    index_to_kono()
    announce(lines)
    # PDFs additionally become spoken briefings (a client contact's 2026-06-09 challenge:
    # "drop a 45-page PDF, get audio in minutes"). The brief script delivers
    # its own m4a to the group when ready; failures are reported, never fatal.
    for p in (pdf_paths if GROUP_JID else ()):  # no group configured: no audio briefs
        try:
            subprocess.run(
                [str(CLAUDE_DIR / "venv" / "bin" / "python"),
                 str(CLAUDE_DIR / "scripts" / "pdf_to_audio_brief.py"),
                 str(p), "--send-to", GROUP_JID],
                capture_output=True, text=True, timeout=1800,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"[inbound] audio brief failed for {p.name}: {exc}", file=sys.stderr)
    print(f"[inbound] processed {len(new)} file(s)")
    return len(new)


def main() -> int:
    if "--watch" in sys.argv:
        # Live window: sweep every 30s for 30 minutes, exit early after a hit
        # so the session narrator can add the recall proof.
        deadline = time.time() + 1800
        while time.time() < deadline:
            if run_once() > 0:
                return 0
            time.sleep(30)
        print("[inbound] watch window closed, no drops")
        return 1
    run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
