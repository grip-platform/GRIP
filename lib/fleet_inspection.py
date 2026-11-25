"""Fleet inspection: land every running session's work, then read it back in one page.

The operator runs many GRIP sessions at once, across harnesses. This module is the
deterministic half of the ``fleet-inspection`` skill (skills/fleet-inspection/SKILL.md):

  prompt         mint a run, render the landing prompt for it, copy it to the clipboard
  wait           wait until every expected session has filed its record
  check-summary  validate the inspecting session's summary.json without rendering
  inspect        read the records, check them against GitHub, render one short page

The judgement layer (the one-line summary, what needs the operator, the plain-words
paragraph) is written by the inspecting session into ``summary.json`` and validated
here. Every item must cite a session that filed a record, and every field is capped,
because a short page is the point. Without a summary the page falls back to a plain,
deterministic one. Whatever the summary says, the page always carries every request
the sessions made, collapsed, so a summary can shorten the page but never hide one.

Records may mention personal matters neutrally, so the page is a LOCAL file. It is
never published, and email addresses are redacted when it is rendered.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import secrets
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import chain
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

TEMPLATE = _LIB.parent / "config" / "fleet-landing-prompt.md"
PROMPT_NAME = "landing-prompt.md"
OWNER = "CodeTonight-SA"
REPOS = ("GRIP", "HAL")
BASE = "main"  # both repos' default branch (gh api repos/<owner>/<repo> --jq .default_branch, 2026-10-02)
NOISE_PREFIX = "chore(changelog)"
INSPECTION_SOURCE = "inspection"  # a finding the inspecting session made itself

# Caps on the summary the inspecting session writes. A short page is the point, so
# the validator refuses anything longer rather than trimming it silently.
LIMITS = {"tldr": 220, "eli5": 650, "need": 300, "line": 170, "needs": 6}

_LABEL = r"(?:who|landed|work this session|unfinished|needs v>>|left uncommitted)"
_SECTION = re.compile(
    rf"^(?:#{{1,3}}\s+(?P<h>.+?)\s*$|[-*]?\s*\*\*(?P<b>{_LABEL}):?\*\*:?\s*(?P<rest>.*)$)", re.I)
_SESSION_LINE = re.compile(r"^\s*[-*]?\s*\**session(?: id)?\**\s*:?\**\s*`?([0-9a-f]{8})", re.I | re.M)
_UUID = re.compile(r"\b([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_MODEL_LINE = re.compile(r"^\s*[-*]?\s*\**model\**\s*:?\**\s*(.+?)\s*$", re.I | re.M)
_REF = re.compile(rf"\b({'|'.join(REPOS)})#(\d+)\b|github\.com/{OWNER}/({'|'.join(REPOS)})/pull/(\d+)")
_LINKABLE = re.compile(rf"https?://[^\s<>\"']+|\b({'|'.join(REPOS)})#(\d+)\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_NOTHING = re.compile(r"^(?:nothing|none)\b", re.I)
_STAMP = re.compile(r"-\d{12,14}(?=-|$)")
_HARNESS_NAMES = {"claude-code": "Claude Code", "kiro-cli": "Kiro CLI", "opencode": "OpenCode",
                  "codex": "Codex", "gemini": "Gemini CLI", "agy": "Antigravity"}
_CLIPBOARDS = (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["clip.exe"])


# ── records ──────────────────────────────────────────────────────────────────

@dataclass
class Record:
    path: Path
    text: str
    sid: str
    harness: str
    model: str
    sections: dict
    refs: set = field(default_factory=set)

    def items(self, label: str) -> list:
        """Bullets (or the paragraph) under every section whose label contains `label`."""
        return _items(list(chain.from_iterable(
            lines for name, lines in self.sections.items() if label in name)))

    @property
    def needs(self) -> list:
        return [item for item in self.items("needs v") if not _NOTHING.match(item)]


def parse_record(path: Path) -> Record:
    text = path.read_text(encoding="utf-8", errors="replace")
    model = _MODEL_LINE.search(text)
    return Record(path=path, text=text, sid=_sid_of(text, path), harness=_harness_of(path.stem),
                  model=_tidy(model.group(1))[:60] if model else "model not stated",
                  sections=_sections(text), refs=_refs(text))


def read_records(run_dir: Path) -> list:
    return [parse_record(p) for p in sorted(run_dir.glob("*.md")) if p.name != PROMPT_NAME]


def _sid_of(text: str, path: Path) -> str:
    """The session's own id: its `session:` line, else the first UUID, else the file suffix."""
    found = _SESSION_LINE.search(text) or _UUID.search(text)
    return found.group(1) if found else path.stem.rsplit("-", 1)[-1]


def _harness_of(stem: str) -> str:
    name = _STAMP.split(stem)[0]
    return _HARNESS_NAMES.get(name, name.replace("-", " ").title())


def _tidy(text: str) -> str:
    return text.replace("**", "").replace("`", "").strip(" :")


def _sections(text: str) -> dict:
    """Split a record at its headings and its bold section labels."""
    out: dict = {"intro": []}
    label = "intro"
    for raw in text.splitlines():
        match = _SECTION.match(raw.strip())
        if match:
            label = (match.group("h") or match.group("b")).strip().lower()
            raw = match.group("rest") or ""
        out.setdefault(label, []).append(raw)
    return out


def _items(lines: list) -> list:
    stripped = [ln.strip() for ln in lines if ln.strip() and not set(ln.strip()) <= set("-*_ ")]
    bullets = [ln[2:].strip() for ln in stripped if ln.startswith(("- ", "* "))]
    if bullets:
        return bullets
    return [" ".join(stripped)] if stripped else []


def _refs(text: str) -> set:
    return {(m.group(1) or m.group(3), int(m.group(2) or m.group(4))) for m in _REF.finditer(text)}


def _one_liner(record: Record) -> str:
    first = (record.items("work this session") or record.items("landed") or [""])[0]
    return first[:LIMITS["line"]]


# ── GitHub and the shared tree ───────────────────────────────────────────────

def default_runner(cmd: list) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120).stdout


def _gh(runner, *args: str) -> list:
    out = runner(["gh", *args])
    return json.loads(out) if out.strip() else []


def fetch_github(runner, since: str) -> dict:
    merged = "number,title,mergedAt,mergedBy,author,url,headRefName,headRefOid,isCrossRepository"
    opened = "number,title,isDraft,author,createdAt,headRefName,url"
    merged_prs = {repo: _gh(runner, "pr", "list", "-R", f"{OWNER}/{repo}", "--state", "merged",
                            "--search", f"merged:>={since}", "--limit", "300", "--json", merged)
                  for repo in REPOS}
    return {
        "merged": merged_prs,
        "stranded": find_stranded(runner, merged_prs),
        "open": {repo: _gh(runner, "pr", "list", "-R", f"{OWNER}/{repo}", "--state", "open",
                           "--limit", "100", "--json", opened) for repo in REPOS},
        "issues": _gh(runner, "search", "issues", "--owner", OWNER, "--created", f">={since}",
                      "--limit", "200", "--json", "repository,number,title,state,url,createdAt"),
    }


def tree_state(runner) -> dict:
    """How far the shared install is behind main, and how many paths are dirty.

    `--no-optional-locks` because live agents run from that tree: a plain `git status` may
    refresh its index under index.lock and collide with a session's own git command.
    """
    git = ["git", "--no-optional-locks", "-C", str(get_claude_dir())]
    try:
        behind = runner([*git, "rev-list", "--count", "HEAD..origin/main"]).strip()
        dirty = len(runner([*git, "status", "--short"]).splitlines())
    except (OSError, subprocess.SubprocessError):
        return {}
    return {"behind": behind, "dirty": dirty}


# ── commits stranded after a merge ───────────────────────────────────────────

def find_stranded(runner, merged_by_repo: dict) -> list:
    """Merged PRs whose branch moved on after the merge, with the commits main never got.

    A PR can merge while its work is incomplete: on 2026-10-01 GRIP#6959 merged, then three
    more commits were pushed to its branch and never reached main. GRIP and HAL squash-merge,
    so a branch's own commits never appear on main by SHA, and `git log origin/<branch> --not
    origin/main` lists every commit of every merged PR. Instead this keeps the non-merge
    commits after the merged head (compare head...tip) that main also lacks (compare
    main...tip): the first set drops merge commits, the second drops what a merge of main into
    the branch brought along. A compare GitHub cannot answer is reported, never dropped.
    """
    return list(chain.from_iterable(_stranded_in(runner, repo, prs)
                                    for repo, prs in merged_by_repo.items()))


def _stranded_in(runner, repo: str, prs: list) -> list:
    moved = _moved_after_merge(prs, _branch_tips(runner, repo))
    entries = (_stranded_entry(runner, repo, pr, tip) for pr, tip in moved)
    return [entry for entry in entries if entry]


def _moved_after_merge(prs: list, tips: dict) -> list:
    """(pr, tip) for same-repo PRs whose head branch still exists at another commit."""
    pairs = ((pr, tips.get(pr.get("headRefName"))) for pr in prs if not pr.get("isCrossRepository"))
    return [(pr, tip) for pr, tip in pairs if tip and pr.get("headRefOid") and tip != pr["headRefOid"]]


def _branch_tips(runner, repo: str) -> dict:
    """Every branch's tip, from one paginated call."""
    out = runner(["gh", "api", "--paginate", f"repos/{OWNER}/{repo}/branches?per_page=100",
                  "--jq", '.[] | "\\(.name)\\t\\(.commit.sha)"'])
    return dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)


def _compare_shas(runner, repo: str, base: str, head: str):
    """Non-merge commits on `head` that `base` lacks, every page; None when GitHub cannot say."""
    try:
        out = runner(["gh", "api", "--paginate",
                      f"repos/{OWNER}/{repo}/compare/{base}...{head}?per_page=100",
                      "--jq", ".commits[] | select((.parents | length) == 1) | .sha"])
    except (OSError, subprocess.SubprocessError):
        return None
    return out.split()


def _post_merge_commits(runner, repo: str, head: str, tip: str):
    after = _compare_shas(runner, repo, head, tip)
    if not after:
        return after
    off_main = _compare_shas(runner, repo, BASE, tip)
    if off_main is None:
        return None
    lacking = set(off_main)
    return [sha for sha in after if sha in lacking]


def _stranded_entry(runner, repo: str, pr: dict, tip: str):
    commits = _post_merge_commits(runner, repo, pr["headRefOid"], tip)
    if commits == []:
        return None
    return {"repo": repo, "number": pr["number"], "title": pr.get("title", ""),
            "url": pr.get("url", ""), "branch": pr["headRefName"], "head": pr["headRefOid"],
            "tip": tip, "commits": commits}


# ── checking the records against GitHub ──────────────────────────────────────

@dataclass
class Checks:
    consulted: bool
    landed_missing: list = field(default_factory=list)  # (sid, repo, number)
    unclaimed: list = field(default_factory=list)       # (repo, pr)
    stranded: list = field(default_factory=list)        # merged PRs whose branch moved on


def cross_check(records: list, github) -> Checks:
    """Landed claims GitHub cannot find, and merged PRs that no record names."""
    if github is None:
        return Checks(consulted=False)
    known = _keys(github["merged"]) | _keys(github["open"]) | _issue_keys(github["issues"])
    landed = chain.from_iterable(_landed_refs(r) for r in records)
    named = set(chain.from_iterable(r.refs for r in records))
    return Checks(consulted=True,
                  landed_missing=sorted({ref for ref in landed if ref[1:] not in known}),
                  unclaimed=[(repo, pr) for repo, pr in _real_merged(github)
                             if (repo, pr["number"]) not in named],
                  stranded=github.get("stranded", []))


def _keys(prs_by_repo: dict) -> set:
    return set(chain.from_iterable(_repo_keys(repo, prs) for repo, prs in prs_by_repo.items()))


def _repo_keys(repo: str, prs: list) -> set:
    return {(repo, pr["number"]) for pr in prs}


def _issue_keys(issues: list) -> set:
    """Issues opened in the window. A landed line that names one ("closes GRIP#7046")
    is citing it, not claiming a PR, so it must not read as a missing landing."""
    return {(issue["repository"]["nameWithOwner"].rsplit("/", 1)[-1], issue["number"])
            for issue in issues if isinstance(issue.get("repository"), dict)}


def _landed_refs(record: Record) -> list:
    return [(record.sid, repo, num) for repo, num in sorted(_refs(" ".join(record.items("landed"))))]


def _real_merged(github: dict) -> list:
    """Merged PRs, without the changelog bot's own."""
    pairs = chain.from_iterable(((repo, pr) for pr in prs) for repo, prs in github["merged"].items())
    return [(repo, pr) for repo, pr in pairs if not pr["title"].startswith(NOISE_PREFIX)]


# ── the inspection and its summary ───────────────────────────────────────────

@dataclass
class Inspection:
    run_id: str
    since: str
    expected: int
    records: list
    github: dict | None
    checks: Checks
    tree: dict
    built: str


def collect(run_dir: Path, meta: dict, runner=None) -> Inspection:
    records = read_records(run_dir)
    github = fetch_github(runner, meta["since"]) if runner else None
    return Inspection(run_id=meta["run_id"], since=meta["since"],
                      expected=meta["expected"] or len(records), records=records, github=github,
                      checks=cross_check(records, github), tree=tree_state(runner) if runner else {},
                      built=_now())


def default_summary(insp: Inspection) -> dict:
    """The page without a written summary: each session's first request, nothing judged."""
    needs = [{"text": r.needs[0][:LIMITS["need"]], "sources": [r.sid], "status": "open"}
             for r in insp.records if r.needs]
    return {"tldr": _default_tldr(insp), "needs": needs[:LIMITS["needs"]],
            "sessions": {r.sid: _one_liner(r) for r in insp.records}, "fallback": True}


def _default_tldr(insp: Inspection) -> str:
    asks = sum(len(r.needs) for r in insp.records)
    landed = f"{len(_real_merged(insp.github))} PRs merged, " if insp.github else ""
    return (f"{len(insp.records)} of {insp.expected} sessions reported: {landed}"
            f"{asks} requests for you.")


def validate_summary(data, sids: set) -> list:
    """Every reason the inspecting session's summary.json would not be rendered."""
    if not isinstance(data, dict):
        return ["the summary must be a JSON object"]
    sources = set(sids) | {INSPECTION_SOURCE}
    return list(chain(_text_problems(data), _needs_problems(data.get("needs", []), sources),
                      _session_problems(data.get("sessions", {}), set(sids))))


def _text_problems(data: dict):
    tldr = data.get("tldr")
    if not isinstance(tldr, str) or not tldr.strip() or "\n" in tldr:
        yield "tldr must be one non-empty line"
    elif len(tldr) > LIMITS["tldr"]:
        yield f"tldr is {len(tldr)} characters; the cap is {LIMITS['tldr']}"
    if len(str(data.get("eli5", ""))) > LIMITS["eli5"]:
        yield f"eli5 is longer than {LIMITS['eli5']} characters"


def _needs_problems(needs, sources: set):
    if not isinstance(needs, list):
        yield "needs must be a list"
        return
    if len(needs) > LIMITS["needs"]:
        yield f"{len(needs)} needs; the cap is {LIMITS['needs']} (the rest stay in the full list)"
    for index, need in enumerate(needs, start=1):
        yield from (f"need {index}: {p}" for p in _need_problems(need, sources))


def _need_problems(need, sources: set):
    if not isinstance(need, dict):
        yield "must be an object"
        return
    if not need.get("text") or len(str(need["text"])) > LIMITS["need"]:
        yield f"text must be 1 to {LIMITS['need']} characters"
    cited = need.get("sources")
    if not cited or not isinstance(cited, list) or not set(cited) <= sources:
        yield f"sources must name sessions that filed a record (or {INSPECTION_SOURCE!r})"
    if need.get("status", "open") not in ("open", "decided"):
        yield "status must be open or decided"
    if need.get("status") == "decided" and not need.get("decision"):
        yield "a decided need must say what was decided"


def _session_problems(lines, sids: set):
    if not isinstance(lines, dict):
        yield "sessions must map a session id to one line"
        return
    for sid, line in lines.items():
        if sid not in sids:
            yield f"session {sid!r} filed no record"
        elif len(str(line)) > LIMITS["line"]:
            yield f"session {sid}: the line is longer than {LIMITS['line']} characters"


def summary_for(insp: Inspection, path: Path) -> tuple:
    """The summary to render and the reasons it cannot be, if any."""
    if not path.exists():
        return default_summary(insp), []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [f"summary.json is not valid JSON: {exc}"]
    return data, validate_summary(data, {r.sid for r in insp.records})


# ── rendering ────────────────────────────────────────────────────────────────

STYLE = """:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--card:#fff;--line:#e4e1d8;--accent:#2f6f5e;--warn:#9a5b00;--done:#5b6b3a}
@media (prefers-color-scheme: dark){:root{--bg:#151614;--fg:#e9e7e1;--muted:#9a988f;--card:#1e1f1c;--line:#33352f;--accent:#7cc4ad;--warn:#e0a54a;--done:#b6c98a}}
body{background:var(--bg);color:var(--fg);font:16px/1.55 -apple-system,system-ui,sans-serif;margin:0;padding:24px 16px 64px}
main{max-width:860px;margin:0 auto} h1{font-size:24px;margin:0 0 4px} h2{font-size:18px;margin:32px 0 10px}
.sub,.muted{color:var(--muted)} .sub{margin:0 0 18px;font-size:14px} a{color:var(--accent)} code{font-size:13.5px;overflow-wrap:anywhere}
.tldr{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:14px 16px;font-size:18px}
.stats{color:var(--muted);font-size:14px;margin:8px 0 0}
ul.needs{list-style:none;padding:0} .need{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:8px;padding:10px 14px;margin:8px 0}
.need.decided{border-left-color:var(--done)} .need .decision{display:block;color:var(--done);font-size:14px;margin-top:4px}
.src{font:12px ui-monospace,monospace;color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:0 5px;margin-left:6px;white-space:nowrap}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:8px 0;padding:8px 12px}
summary{cursor:pointer} pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace;margin:10px 0 2px}
.warn{color:var(--warn)} li{margin:4px 0}"""


def render(insp: Inspection, summary: dict) -> str:
    return (f'<!doctype html><html lang="en-GB"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>GRIP fleet inspection</title><style>{STYLE}</style></head><body><main>"
            f"{_header(insp)}{_tldr(insp, summary)}{_needs(summary)}{_eli5(summary)}"
            f"{_sessions(insp, summary)}{_checks(insp)}{_all_requests(insp)}{_details(insp)}"
            f"{_footer(insp)}</main></body></html>")


def _esc(text: str) -> str:
    return html.escape(_EMAIL.sub("[email redacted]", str(text)))


def _inline(text: str) -> str:
    """Escape, then backtick code spans, then links."""
    parts = _esc(text).split("`")
    return "".join(f"<code>{p}</code>" if i % 2 else _link(p) for i, p in enumerate(parts))


def _link(escaped: str) -> str:
    return _LINKABLE.sub(_anchor, escaped)


def _anchor(match: re.Match) -> str:
    if match.group(1):
        return (f'<a href="https://github.com/{OWNER}/{match.group(1)}/pull/{match.group(2)}">'
                f"{match.group(0)}</a>")
    return f'<a href="{match.group(0)}">{match.group(0)}</a>'


def _header(insp: Inspection) -> str:
    missing = insp.expected - len(insp.records)
    late = f' <span class="warn">{missing} not reported yet.</span>' if missing > 0 else ""
    return (f"<h1>GRIP fleet inspection</h1><p class='sub'>{len(insp.records)} of {insp.expected} "
            f"sessions filed a record.{late} Work since {_esc(insp.since)}; built {_esc(insp.built)}.</p>")


def _tldr(insp: Inspection, summary: dict) -> str:
    return f"<div class='tldr'>{_inline(summary.get('tldr', ''))}</div><p class='stats'>{_stats(insp)}</p>"


def _stats(insp: Inspection) -> str:
    tree = insp.tree
    shared = f" · shared tree {tree['behind']} behind main, {tree['dirty']} dirty paths" if tree else ""
    if not insp.github:
        return "GitHub was not consulted." + shared
    merged = ", ".join(f"{repo} {sum(1 for r, _ in _real_merged(insp.github) if r == repo)}" for repo in REPOS)
    still_open = sum(len(prs) for prs in insp.github["open"].values())
    return (f"Merged: {merged} · still open: {still_open} · issues opened: "
            f"{len(insp.github['issues'])}{shared}")


def _needs(summary: dict) -> str:
    rows = "".join(_need_row(n) for n in summary.get("needs", [])) or "<li class='need'>Nothing.</li>"
    note = ("<p class='muted'>No summary.json was written, so this shows each session's first "
            "request; every request is in the full list below.</p>") if summary.get("fallback") else ""
    return f"<h2>What needs you</h2>{note}<ul class='needs'>{rows}</ul>"


def _need_row(need: dict) -> str:
    status = need.get("status", "open")
    decision = (f"<span class='decision'>Decided: {_inline(need['decision'])}</span>"
                if status == "decided" else "")
    sources = "".join(f"<span class='src'>{_esc(s)}</span>" for s in need.get("sources", []))
    return f"<li class='need {status}'>{_inline(need['text'])}{sources}{decision}</li>"


def _eli5(summary: dict) -> str:
    text = summary.get("eli5")
    return f"<h2>In plain words</h2><p>{_inline(text)}</p>" if text else ""


def _sessions(insp: Inspection, summary: dict) -> str:
    lines = summary.get("sessions", {})
    cards = "".join(_session_card(r, lines.get(r.sid) or _one_liner(r)) for r in insp.records)
    return f"<h2>Each session, in one line</h2>{cards}"


def _session_card(record: Record, line: str) -> str:
    return (f"<details><summary><b>{_esc(record.harness)}</b> · {_esc(record.model)} · "
            f"<code>{_esc(record.sid)}</code> — {_inline(line)}</summary>"
            f"<pre>{_link(_esc(record.text))}</pre></details>")


def _checks(insp: Inspection) -> str:
    if not insp.checks.consulted:
        return "<h2>Checked against GitHub</h2><p class='muted'>Not checked: run without --offline.</p>"
    missing = "".join(f"<li><code>{_esc(sid)}</code> claims {_link(f'{repo}#{num}')} landed, but it is "
                      f"neither merged in the window nor open.</li>"
                      for sid, repo, num in insp.checks.landed_missing)
    verdict = (f"<ul>{missing}</ul>" if missing else
               "<p>Every PR a session says it landed is merged or open.</p>")
    return (f"<h2>Checked against GitHub</h2>{verdict}{_stranded_html(insp.checks.stranded)}"
            f"<p class='muted'>"
            f"{len(insp.checks.unclaimed)} merged PRs are named in no record: background agents, "
            f"teammates, bots, or sessions that kept no account. They are listed below.</p>")


def _stranded_html(stranded: list) -> str:
    if not stranded:
        return "<p>No merged PR's branch moved on after it merged.</p>"
    rows = "".join(_stranded_row(entry) for entry in stranded)
    return ("<p class='warn'>These PRs merged, then more commits reached their branches. Those "
            "commits are not on main: check whether a follow-up PR carried them.</p>"
            f"<ul>{rows}</ul>")


def _stranded_row(entry: dict) -> str:
    found = ("GitHub could not compare it" if entry["commits"] is None
             else f"{len(entry['commits'])} commit(s) after the merge")
    command = f"git log --oneline {entry['tip'][:9]} ^{entry['head'][:9]} ^origin/{BASE}"
    return (f"<li>{_link(_esc(entry['repo'] + '#' + str(entry['number'])))} {_esc(entry['title'])}: "
            f"{found} on <code>{_esc(entry['branch'])}</code>. <code>{_esc(command)}</code></li>")


def _all_requests(insp: Inspection) -> str:
    rows = "".join(f"<li>{_inline(item)}<span class='src'>{_esc(r.sid)}</span></li>"
                   for r, item in _every_request(insp.records))
    count = sum(len(r.needs) for r in insp.records)
    return f"<details><summary>Every request the sessions made ({count})</summary><ul>{rows}</ul></details>"


def _every_request(records: list) -> list:
    return list(chain.from_iterable(((r, item) for item in r.needs) for r in records))


def _details(insp: Inspection) -> str:
    if not insp.github:
        return ""
    merged = "".join(_pr_list(f"{repo} merged", [pr for r, pr in _real_merged(insp.github) if r == repo])
                     for repo in REPOS)
    unclaimed = _pr_list("Merged, named in no record", [pr for _, pr in insp.checks.unclaimed])
    still_open = _pr_list("Still open", list(chain.from_iterable(insp.github["open"].values())))
    return f"<h2>Everything else</h2>{merged}{unclaimed}{still_open}{_issue_list(insp.github['issues'])}"


def _pr_list(title: str, prs: list) -> str:
    rows = "".join(f"<li><a href='{_esc(pr.get('url', ''))}'>#{pr['number']}</a> {_esc(pr['title'])}</li>"
                   for pr in prs)
    return f"<details><summary>{_esc(title)} ({len(prs)})</summary><ul>{rows}</ul></details>"


def _issue_list(issues: list) -> str:
    rows = "".join(f"<li><a href='{_esc(i.get('url', ''))}'>#{i['number']}</a> {_esc(i['title'])} "
                   f"<span class='muted'>{_esc(i.get('state', ''))}</span></li>" for i in issues)
    return f"<details><summary>Issues opened ({len(issues)})</summary><ul>{rows}</ul></details>"


def _footer(insp: Inspection) -> str:
    return (f"<p class='sub' style='margin-top:32px'>Run <code>{_esc(insp.run_id)}</code>. Built by "
            f"<code>grip-fleet inspect</code> from the session records"
            f"{', GitHub' if insp.github else ''} and git. This page is local: never publish it. "
            f"Rebuild with <code>grip-fleet inspect --run {_esc(insp.run_id)}</code>.</p>")


# ── the landing prompt and waiting for records ───────────────────────────────

def render_prompt(run_dir: Path, notes: list, template: Path = TEMPLATE) -> str:
    """The landing prompt for one run, with every placeholder filled."""
    install = get_claude_dir().resolve()
    values = {"{{GRIP_HOME}}": _tilde(install), "{{WORKTREES}}": _tilde(Path(f"{install}-worktrees")),
              "{{RECORDS_DIR}}": _tilde(run_dir), "{{RUN_ID}}": run_dir.name, "{{DATE}}": _now()[:10],
              "{{NOTES}}": "\n".join(f"- {n}" for n in notes) or "- (none)"}
    text = template.read_text(encoding="utf-8")
    for token, value in values.items():
        text = text.replace(token, value)
    leftover = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", text)))
    if leftover:
        raise ValueError(f"the template has placeholders nothing fills: {leftover}")
    return text


def wait_for_records(run_dir: Path, expected: int, timeout: float, interval: float = 15,
                     report=print) -> list:
    """Poll until `expected` records exist or `timeout` seconds pass; report each arrival once."""
    deadline = time.monotonic() + timeout
    seen: list = []
    while True:
        seen += _announce(_new_records(run_dir, set(seen)), len(seen), expected, report)
        if len(seen) >= expected or time.monotonic() >= deadline:
            return seen
        time.sleep(interval)


def _new_records(run_dir: Path, seen: set) -> list:
    return [p.name for p in sorted(run_dir.glob("*.md")) if p.name not in seen and p.name != PROMPT_NAME]


def _announce(names: list, already: int, expected: int, report) -> list:
    for offset, name in enumerate(names, start=1):
        report(f"record {already + offset}/{expected}: {name}")
    return names


def _tilde(path: Path) -> str:
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text == home or text.startswith(home + "/") else text


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _copy(text: str) -> bool:
    for cmd in _CLIPBOARDS:
        if shutil.which(cmd[0]):
            return subprocess.run(cmd, input=text, text=True).returncode == 0
    return False


# ── command line ─────────────────────────────────────────────────────────────

def _root(args) -> Path:
    return Path(args.root).expanduser() if args.root else get_claude_dir() / "tmp" / "fleet-landing"


def _run_dir(args) -> Path:
    if args.records:
        return Path(args.records).expanduser()
    if args.run:
        return _root(args) / args.run
    runs = sorted((p.parent for p in _root(args).glob("*/run.json")), key=lambda p: p.stat().st_mtime)
    if not runs:
        raise SystemExit(f"no fleet run under {_root(args)}: pass --run or --records")
    return runs[-1]


def _meta(run_dir: Path, args) -> dict:
    path = run_dir / "run.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    since = args.since or meta.get("since") or _hours_ago(24)
    return {"run_id": meta.get("run_id", run_dir.name), "since": since,
            "expected": int(args.expect or meta.get("expected") or 0)}


def _hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cmd_prompt(args) -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M-") + secrets.token_hex(2)
    run_dir = _root(args) / run_id
    run_dir.mkdir(parents=True)
    meta = {"run_id": run_id, "created": _now(), "since": args.since or _hours_ago(24),
            "expected": args.expect}
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    text = render_prompt(run_dir, args.note or [])
    (run_dir / PROMPT_NAME).write_text(text, encoding="utf-8")
    copied = _copy(text) if args.copy else False
    print(f"run {run_id} | prompt {run_dir / PROMPT_NAME} | "
          f"{'copied to the clipboard' if copied else 'not copied'}")
    return 0


def _cmd_wait(args) -> int:
    run_dir = _run_dir(args)
    expected = _meta(run_dir, args)["expected"]
    if expected < 1:
        raise SystemExit("wait needs --expect N, or a run minted with --expect")
    seen = wait_for_records(run_dir, expected, args.timeout, args.interval)
    print(f"{'ALL' if len(seen) >= expected else 'ONLY'} {len(seen)} of {expected} records in {run_dir}")
    return 0 if len(seen) >= expected else 3


def _summary_path(args, run_dir: Path) -> Path:
    return Path(args.summary).expanduser() if args.summary else run_dir / "summary.json"


def _cmd_check(args) -> int:
    run_dir = _run_dir(args)
    records = read_records(run_dir)
    path = _summary_path(args, run_dir)
    if not path.exists():
        raise SystemExit(f"no summary at {path}")
    problems = validate_summary(json.loads(path.read_text(encoding="utf-8")), {r.sid for r in records})
    print("\n".join(problems) or f"summary is valid for {len(records)} records")
    return 2 if problems else 0


def _cmd_inspect(args) -> int:
    run_dir = _run_dir(args)
    try:
        insp = collect(run_dir, _meta(run_dir, args), runner=None if args.offline else default_runner)
    except subprocess.CalledProcessError as exc:
        print(f"GitHub could not be read ({exc.cmd[:3]}): {exc.stderr.strip()[:200]}; "
              "retry, or pass --offline", file=sys.stderr)
        return 2
    summary, problems = summary_for(insp, _summary_path(args, run_dir))
    if problems:
        print("summary.json refused:\n" + "\n".join(f"  - {p}" for p in problems), file=sys.stderr)
        return 2
    out = Path(args.out).expanduser() if args.out else run_dir / "index.html"
    out.write_text(render(insp, summary), encoding="utf-8")
    print(f"wrote {out} | {len(insp.records)} of {insp.expected} records | "
          f"{sum(len(r.needs) for r in insp.records)} requests | "
          f"{len(insp.checks.landed_missing)} landed claims not found | "
          f"{len(insp.checks.stranded)} merged PRs with commits stranded after the merge")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grip-fleet", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, helptext in (("prompt", "mint a run and render its landing prompt"),
                           ("wait", "wait for every expected record"),
                           ("check-summary", "validate summary.json"),
                           ("inspect", "render the inspection page")):
        _add_common(sub.add_parser(name, help=helptext), name)
    return parser


def _add_common(cmd: argparse.ArgumentParser, name: str) -> None:
    cmd.add_argument("--root", help="where runs live (default: <GRIP>/tmp/fleet-landing)")
    cmd.add_argument("--expect", type=int, help="how many sessions should file a record")
    cmd.add_argument("--since", help="start of the work window, ISO date or time (default: 24h ago)")
    if name == "prompt":
        cmd.add_argument("--note", action="append", help="a fact for every session to read (repeatable)")
        cmd.add_argument("--no-copy", dest="copy", action="store_false", help="do not copy to the clipboard")
        return
    cmd.add_argument("--run", help="a run id under --root (default: the latest)")
    cmd.add_argument("--records", help="a folder of records to read instead of a run")
    if name == "wait":
        cmd.add_argument("--timeout", type=float, default=3600, help="seconds (default 3600)")
        cmd.add_argument("--interval", type=float, default=15, help="seconds between polls")
        return
    cmd.add_argument("--summary", help="summary.json to use (default: the run folder's)")
    cmd.add_argument("--out", help="page to write (default: <run>/index.html)")
    cmd.add_argument("--open", action="store_true", help="open the page when written")
    cmd.add_argument("--offline", action="store_true", help="skip GitHub and git")


_COMMANDS = {"prompt": _cmd_prompt, "wait": _cmd_wait, "check-summary": _cmd_check,
             "inspect": _cmd_inspect}


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    return _COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
