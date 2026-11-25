"""Check quotes against the text a web page actually serves, with no model involved.

A quote is FOUND when, after one fixed normalisation, it appears word for word in
the page's visible text or in its raw HTML with the tags removed. Nothing else is
loosened, so a one-word change fails. The normalisation makes curly quotes
straight and dashes '-', drops zero-width spaces, folds the escaped ampersand,
quote mark and newline that script data carries, and turns every run of
whitespace into one space; the quote and both haystacks all get it. HTML
entities are decoded exactly once on each side: the visible text the way a
browser renders it, the raw text from its source. A quote's entities are not
decoded, because a quote is copied from what the page shows. So a page that
displays "&amp;" (source "&amp;amp;") matches the quote "&amp;" and not "&";
before GRIP#9620 the visible text was decoded twice and matched both.

Some documentation sites send almost nothing as visible text and keep the page in
script data (measured on ai.google.dev, 2026-11-24: about 60 visible characters of
189 KB). Their quotes are found in the raw text. page_report() flags such a page, so
an empty visible text is never read as "the site is blocked".

Used by bin/grip-quote-check; first used for CodeTonight's LLM SWOT, where 371 of
371 quotes were found on 66 vendor pages.
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Optional

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
_BLOCK_TAGS = re.compile(r"<(script|style|noscript|svg)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_BREAK_TAGS = re.compile(r"<\s*(br|/p|/div|/li|/tr|/h[1-6]|/section|/article|/td|/th|/dd|/dt)\b[^>]*>", re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-",
                       "—": "-", "−": "-", " ": " ", " ": " ", "​": ""})
# A page whose visible text is this short while its raw text is this many times
# longer keeps its content in script data.
_SCRIPT_RENDERED_MAX_VISIBLE = 500
_SCRIPT_RENDERED_RATIO = 20
FOUND, NOT_FOUND, FETCH_FAILED = "VERIFIED", "NOT FOUND", "FETCH FAILED"


def fetch(url: str, timeout: float = 40) -> str:
    """The page as the server sends it to a plain browser request."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
                                               "Accept-Language": "en-GB,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")


def norm(text: str, decode: bool = True) -> str:
    """The one fixed normalisation applied to pages and quotes alike. ``decode`` is
    off for text that is already decoded (the visible text) and for a quote."""
    text = (html.unescape(text) if decode else text).translate(_FOLD)
    text = text.replace("\\u0026", "&").replace('\\"', '"').replace("\\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def visible_lines(raw: str) -> list:
    """The page's visible text, one line per block element, scripts and styles left out."""
    text = _BREAK_TAGS.sub("\n", _BLOCK_TAGS.sub(" ", raw))
    text = html.unescape(_ANY_TAG.sub(" ", text)).translate(_FOLD)
    return [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.splitlines() if line.strip()]


def haystacks(raw: str) -> tuple:
    """(visible text, raw text with tags removed), both normalised and each decoded
    exactly once: visible_lines has already decoded the visible text."""
    return norm(" ".join(visible_lines(raw)), decode=False), norm(_ANY_TAG.sub(" ", raw))


def where_found(quote: str, visible: str, everything: str) -> Optional[str]:
    """'visible', 'raw' or None for one quote against one page's two haystacks."""
    q = norm(quote, decode=False)  # normalised, but its entities are not decoded
    if q and q in visible:
        return "visible"
    if q and q in everything:
        return "raw"
    return None


def page_report(raw: str) -> dict:
    """Sizes of the two haystacks, and whether the page keeps its text in script data."""
    visible, everything = haystacks(raw)
    rendered = (len(visible) < _SCRIPT_RENDERED_MAX_VISIBLE
                and len(everything) > _SCRIPT_RENDERED_RATIO * max(len(visible), 1))
    return {"visible_chars": len(visible), "raw_chars": len(everything), "script_rendered": rendered}


def _load(url: str, fetcher: Callable[[str], str]) -> dict:
    try:
        raw = fetcher(url)
    except Exception as exc:  # noqa: BLE001 -- a fetch failure is recorded, never raised
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    visible, everything = haystacks(raw)
    return {"ok": True, "visible": visible, "raw": everything}


def judge(quote: str, page: dict) -> dict:
    """The check result for one quote on one loaded page."""
    if not page["ok"]:
        return {"status": FETCH_FAILED, "detail": page["error"]}
    where = where_found(quote, page["visible"], page["raw"])
    if where:
        return {"status": FOUND, "detail": f"{where} text"}
    return {"status": NOT_FOUND, "detail": "quote absent from the served page"}


def check_rows(rows: Iterable[dict], fetcher: Optional[Callable[[str], str]] = None,
               workers: int = 8) -> list:
    """Each row (a dict with 'url' and 'quote') with a 'check' result added. Every
    URL is fetched once, however many rows quote it. The default fetcher is looked up
    when called, so replacing this module's fetch() reaches the batch path too (AGORA
    on GRIP#9581: a default bound at definition time let a test hit the network)."""
    if fetcher is None:
        fetcher = fetch
    rows = [dict(row) for row in rows]
    urls = sorted({row["url"] for row in rows})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pages = dict(zip(urls, pool.map(lambda u: _load(u, fetcher), urls)))
    for row in rows:
        row["check"] = judge(row["quote"], pages[row["url"]])
    return rows


def counts(rows: Iterable[dict]) -> dict:
    """How many rows ended in each status."""
    out: dict = {}
    for row in rows:
        status = row["check"]["status"]
        out[status] = out.get(status, 0) + 1
    return out


def check_file(rows_path: str, out_path: str,
               fetcher: Optional[Callable[[str], str]] = None) -> dict:
    """Check the 'rows' of a JSON file and write them, each with its result, to out_path."""
    with open(rows_path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["rows"] = check_rows(data["rows"], fetcher)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    return counts(data["rows"])
