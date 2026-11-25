"""Fetch the statutes a legal playbook relies on into a matter's own folder.

A playbook such as ``skills/navigating-sa-divorce-procedure/`` lists the Acts
and Rules it relies on in a ``sources.json`` manifest. GRIP stores no statute
text. At matter time ``fetch`` downloads each source into the matter's
``sources/`` folder, converts it to plain text that ``/prove-it`` can quote
against, and appends a ledger row to ``SOURCES.json`` in that folder. The row
holds the SHA-256 of both files, the URL and the UTC fetch time.

Every file is named by its own content, ``<id>-<first 12 hex of its SHA-256>``,
so a later fetch of a changed consolidation writes new files beside the old ones
and never overwrites a file an earlier ledger row names.

``check`` re-hashes every file the ledger names. With ``--receipt`` it also binds
a legal receipt (``lib/legal_receipt.py``) to the ledger: each source the receipt
proved quotes against must hash to a ledger row's text, which names the
original's hash and URL. A text edited after the fetch therefore fails the check
even when its own receipt is green.

The fetcher refuses to write inside a GRIP checkout, because a matter folder
belongs in a private repository (``rules/no-sensitive-info-in-grip.md``). It
refuses an empty download and an empty conversion too, so a broken fetch cannot
produce a ledger row that looks like a good one. The download and the conversion
are injectable, so tests need no network and no ``pdftotext``.

CLI (the script path works from any directory; ``-m lib.legal_sources`` works
from the checkout root):
    bin/grip-py lib/legal_sources.py fetch --manifest <path> --dest <dir> [--only <id>]
    bin/grip-py lib/legal_sources.py check --dest <dir> [--receipt <receipt.json>]

Exit codes: 0 when every requested source was fetched, or every file and receipt
source matches the ledger; 1 when one was not or does not; 2 for unreadable
input, an unknown --only id, or a destination inside GRIP.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from itertools import chain
from pathlib import Path
from typing import Any, Callable

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from legal_frame import grip_root_holding  # noqa: E402

KINDS = ("pdf", "html")
LEDGER_NAME = "SOURCES.json"
USER_AGENT = "GRIP-legal-sources/1.0"
FETCH_TIMEOUT_S = 120
CONVERT_TIMEOUT_S = 120
HASH_PREFIX = 12
# An id becomes part of a file name, so it may not carry a separator or a dot run.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# A manifest URL is percent-encoded: urllib refuses whitespace and control
# characters with http.client.InvalidURL, so they are refused at load instead.
_URL_BAD_CHAR_RE = re.compile(r"[\s\x00-\x1f\x7f]")

Fetch = Callable[[str], bytes]
Convert = Callable[[Path, str], str]


class SourceError(RuntimeError):
    """A source that cannot be fetched, recorded or checked as asked."""


# --- Manifest ---------------------------------------------------------------


def load_manifest(path: Path) -> list[dict[str, Any]]:
    """The manifest's source entries, each checked before anything is fetched."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise SourceError(f"{path}: expected a JSON object with a 'sources' list")
    for entry in entries:
        check_entry(entry)
    return entries


def check_entry(entry: Any) -> None:
    """Raise SourceError unless ``entry`` is a fetchable or honestly-unlocated source.

    ``url`` must be present: an https address, or null with a ``note`` saying
    the source has to be located by hand. A guessed URL is worse than none.
    """
    if not isinstance(entry, dict):
        raise SourceError(f"manifest entry is not an object: {entry!r}")
    missing = [key for key in ("id", "title", "kind") if not entry.get(key)]
    missing += [] if "url" in entry else ["url"]
    if missing:
        raise SourceError(f"manifest entry {entry.get('id')!r} lacks {', '.join(missing)}")
    if not _ID_RE.match(str(entry["id"])):
        raise SourceError(f"source id {entry['id']!r} must be lowercase letters, digits and hyphens")
    if entry["kind"] not in KINDS:
        raise SourceError(f"{entry['id']}: kind must be one of {', '.join(KINDS)}")
    url = entry["url"]
    if url is None and not entry.get("note"):
        raise SourceError(f"{entry['id']}: a null url needs a note saying where to look")
    if url is not None and not str(url).startswith("https://"):
        raise SourceError(f"{entry['id']}: only https URLs are fetched, got {url!r}")
    if url is not None and _URL_BAD_CHAR_RE.search(str(url)):
        raise SourceError(f"{entry['id']}: the URL holds whitespace or a control "
                          "character; percent-encode it (a space is %20)")


# --- Fetch -----------------------------------------------------------------


def fetch_source(entry: dict[str, Any], dest: Path, fetch: Fetch | None = None,
                 convert: Convert | None = None) -> dict[str, Any]:
    """Fetch one manifest entry into ``dest`` and append its ledger row.

    Writes ``<id>-<hash>.<kind>`` (the bytes as served) and ``<id>-<hash>.txt``
    (the text conversion), appends a row to ``<dest>/SOURCES.json``, and returns
    the row. Both hashes are taken from the files as written, not from memory.
    """
    check_entry(entry)
    if entry["url"] is None:
        raise SourceError(f"{entry['id']}: {entry['note']} (no URL in the manifest)")
    dest = Path(dest)
    refuse_grip_destination(dest)
    raw = (fetch or _download)(entry["url"])
    if not raw:
        raise SourceError(f"{entry['id']}: the download was empty")
    dest.mkdir(parents=True, exist_ok=True)
    original, original_sha = _store_once(dest, entry["id"], entry["kind"], raw)
    # /prove-it reads the text file in text mode, which turns \r\n and \r into
    # \n; normalising first keeps its hash equal to the ledger's.
    text = (convert or _to_text)(original, entry["kind"]).replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise SourceError(f"{entry['id']}: the conversion produced no text "
                          "(a scanned PDF needs OCR first); no ledger row written")
    text_path, text_sha = _store_once(dest, entry["id"], "txt", text.encode("utf-8"))
    row = {
        "id": entry["id"],
        "title": entry["title"],
        "url": entry["url"],
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "original": original.name,
        "original_sha256": original_sha,
        "text": text_path.name,
        "text_sha256": text_sha,
    }
    _append_ledger(dest / LEDGER_NAME, row)
    return row


def _store_once(dest: Path, source_id: str, ext: str, data: bytes) -> tuple[Path, str]:
    """Write ``data`` as ``<id>-<hash>.<ext>``, never over different content.

    Returns the path and the SHA-256 of the file as read back. A file of that
    name already holding these bytes is left as it is; one holding anything else
    is refused.
    """
    digest = hashlib.sha256(data).hexdigest()
    path = dest / f"{source_id}-{digest[:HASH_PREFIX]}.{ext}"
    if not path.exists():
        path.write_bytes(data)
    on_disk = hashlib.sha256(path.read_bytes()).hexdigest()
    if on_disk != digest:
        raise SourceError(f"{path.name} holds other content; not overwriting it")
    return path, on_disk


def refuse_grip_destination(dest: Path) -> None:
    """Raise SourceError when ``dest`` is in a GRIP install, checkout or worktree.

    The check is ``legal_frame.grip_root_holding``, the same one the matter
    store applies to its own root.
    """
    root = grip_root_holding(dest)
    if root is not None:
        raise SourceError(
            f"refusing to write sources inside the GRIP checkout ({root}); "
            "a matter's sources/ folder belongs in its private repository"
        )


def _append_ledger(path: Path, row: dict[str, Any]) -> None:
    """Append ``row`` to the JSON list at ``path``; never overwrite a bad ledger."""
    rows = load_ledger(path.parent) if path.exists() else []
    rows.append(row)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another https URL.

    urllib's default handler follows https to plain http. This one refuses before
    the plain request is made, so a statute is never fetched off https.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise SourceError(f"{req.full_url} redirected to {newurl}, which is not https")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str) -> bytes:
    """GET ``url`` over https, following redirects only to https, and return the body."""
    if not url.startswith("https://"):
        raise SourceError(f"only https URLs are fetched, got {url!r}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(_HttpsOnlyRedirects)
    with opener.open(request, timeout=FETCH_TIMEOUT_S) as response:
        return response.read()


# --- Conversion -------------------------------------------------------------


def _to_text(path: Path, kind: str) -> str:
    """Plain text for ``/prove-it``: pdftotext for a PDF, visible text for HTML."""
    if kind == "html":
        return _html_to_text(path.read_bytes())
    argv = ["pdftotext", "-enc", "UTF-8", str(path.resolve()), "-"]
    try:
        done = subprocess.run(argv, capture_output=True, check=True, timeout=CONVERT_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise SourceError("pdftotext is not installed (poppler: `brew install poppler` "
                          "or `apt install poppler-utils`)") from exc
    return done.stdout.decode("utf-8", errors="replace")


_SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template"})
_BLOCK_TAGS = frozenset({
    "address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre",
    "section", "table", "td", "th", "tr", "ul",
})
_CHARSET_RE = re.compile(rb"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.IGNORECASE)


class _VisibleText(HTMLParser):
    """The text a reader sees: no tags, scripts or styles, entities decoded."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIPPED_TAGS:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skipping:
            self.parts.append(data)


def _decode_html(raw: bytes) -> str:
    """UTF-8 when the bytes are valid UTF-8, else the declared charset, else cp1252."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    declared = _CHARSET_RE.search(raw[:4096])
    codecs = ([declared.group(1).decode("ascii")] if declared else []) + ["cp1252"]
    for codec in codecs:
        try:
            return raw.decode(codec, errors="replace")
        except LookupError:
            continue
    return raw.decode("latin-1")


def _html_to_text(raw: bytes) -> str:
    """Visible text, one block per line, with whitespace runs collapsed."""
    parser = _VisibleText()
    parser.feed(_decode_html(raw))
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


# --- Check ------------------------------------------------------------------


def load_ledger(dest: Path) -> list[dict[str, Any]]:
    """The rows of ``<dest>/SOURCES.json``; raises SourceError on a bad ledger."""
    path = Path(dest) / LEDGER_NAME
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SourceError(f"{path} is not valid JSON; not overwriting it") from exc
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SourceError(f"{path} is not a JSON list of rows; not overwriting it")
    return rows


def _file_problem(dest: Path, row: dict[str, Any], name_key: str, hash_key: str) -> str | None:
    """Why the file a row names under ``name_key`` fails, or None when it matches."""
    name = str(row.get(name_key) or "")
    path = Path(dest) / name
    if not name or Path(name).name != name or not path.is_file():
        return f"{row.get('id')}: {name or name_key} is missing from {dest}"
    if hashlib.sha256(path.read_bytes()).hexdigest() != row.get(hash_key):
        return f"{row.get('id')}: {name} no longer matches its ledger hash"
    return None


def _row_problems(dest: Path, row: dict[str, Any]) -> list[str]:
    found = (_file_problem(dest, row, "original", "original_sha256"),
             _file_problem(dest, row, "text", "text_sha256"))
    return [problem for problem in found if problem]


def ledger_problems(dest: Path, rows: list[dict[str, Any]]) -> list[str]:
    """Every file a ledger row names must be in ``dest`` and still hash as recorded."""
    return list(chain.from_iterable(_row_problems(dest, row) for row in rows))


def receipt_bindings(receipt: dict[str, Any],
                     rows: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """(bound, problems): each receipt source tied to a ledger row by its text hash."""
    sources = receipt.get("sources")
    if not isinstance(sources, dict) or not sources:
        return [], ["the receipt names no sources"]
    by_text = {row.get("text_sha256"): row for row in rows}
    bound: list[str] = []
    problems: list[str] = []
    for source_id, meta in sources.items():
        row = by_text.get(meta.get("sha256") if isinstance(meta, dict) else None)
        if row is None:
            problems.append(f"receipt source {source_id}: its text matches no ledger row")
        else:
            bound.append(f"{source_id} -> {row.get('id')} ({row.get('url')}, "
                         f"original sha256 {str(row.get('original_sha256'))[:HASH_PREFIX]})")
    return bound, problems


# --- CLI --------------------------------------------------------------------


def _select(entries: list[dict[str, Any]], only: str | None) -> list[dict[str, Any]]:
    if only is None:
        return entries
    chosen = [entry for entry in entries if entry["id"] == only]
    if not chosen:
        raise SourceError(f"no source with id {only!r} in the manifest")
    return chosen


def _fetch_each(entries: list[dict[str, Any]], dest: Path) -> int:
    """Fetch every entry, reporting each; return how many were not fetched."""
    failed = 0
    for entry in entries:
        try:
            row = fetch_source(entry, dest)
        # HTTPException is not an OSError: a truncated body (IncompleteRead) or a
        # rejected URL (InvalidURL) would otherwise end the run with a traceback.
        except (SourceError, OSError, ValueError, http.client.HTTPException,
                subprocess.SubprocessError) as exc:
            failed += 1
            print(f"not fetched  {entry['id']}: {exc}", file=sys.stderr)
            continue
        print(f"fetched      {row['id']}  sha256 {row['original_sha256'][:12]}  -> {row['text']}")
    return failed


def _cmd_fetch(args: argparse.Namespace) -> int:
    try:
        entries = _select(load_manifest(args.manifest), args.only)
        refuse_grip_destination(args.dest)
    except (SourceError, OSError, ValueError) as exc:
        print(f"legal_sources: {exc}", file=sys.stderr)
        return 2
    failed = _fetch_each(entries, args.dest)
    if failed:
        print(f"legal_sources: {failed} of {len(entries)} source(s) not fetched", file=sys.stderr)
    return 1 if failed else 0


def _read_receipt(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise SourceError(f"{path} is not a receipt (expected a JSON object)")
    return receipt


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        rows = load_ledger(args.dest)
        receipt = _read_receipt(args.receipt)
    except (SourceError, OSError, ValueError) as exc:
        print(f"legal_sources: {exc}", file=sys.stderr)
        return 2
    problems = ledger_problems(args.dest, rows)
    if receipt is not None:
        bound, unbound = receipt_bindings(receipt, rows)
        problems += unbound
        for line in bound:
            print(f"bound        {line}")
    for problem in problems:
        print(f"MISMATCH     {problem}", file=sys.stderr)
    print(f"legal_sources: {len(rows)} ledger row(s) checked, {len(problems)} problem(s)")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="legal_sources", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="fetch a manifest's sources into a matter folder")
    fetch.add_argument("--manifest", required=True, type=Path, help="playbook sources.json")
    fetch.add_argument("--dest", required=True, type=Path,
                       help="the matter's sources/ folder, outside the GRIP checkout")
    fetch.add_argument("--only", help="fetch only the source with this id")
    check = sub.add_parser("check", help="re-hash the ledger's files; bind a receipt to them")
    check.add_argument("--dest", required=True, type=Path, help="the matter's sources/ folder")
    check.add_argument("--receipt", type=Path, help="a legal receipt JSON to bind to the ledger")
    args = parser.parse_args(argv)
    handlers = {"fetch": _cmd_fetch, "check": _cmd_check}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
