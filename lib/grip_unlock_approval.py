"""grip-unlock approval layer — dual-approval MEMBERSHIP access gate (v1).

Fronts the existing creator-approval admin-unlock machinery
(``lib/license_handshake.py``) with a HUMAN-APPROVAL governance layer for
GRIP/HAPPIverse membership decisions. Two distinct, deliberately-named scopes:

* **suite admin-unlock** (existing, ``license_handshake``): a machine-bound,
  signed unlock code minted by the creator. UNCHANGED by this module — for a
  ``private-overlay`` grant, the emitted provisioning includes the existing
  ``grip-unlock issue`` step as the hand-off into that machinery.
* **membership access** (this module): who may hold org/repo write, private
  substrate, infra, or community roles. Governance per
  ``docs/access/VETTING.md`` (council seal ``5d55b65c58c808aa``).

Design contract (v1):

* **State store = the GitHub access-request issue.** Labels
  (``access-request`` / ``access-approved-1of2`` / ``access-approved`` /
  ``access-denied``) plus approver-attributed marker comments — no local DB.
  Approval marks only count from comments whose ``author_association`` is
  OWNER/MEMBER/COLLABORATOR, so a stranger pasting the marker text on the
  public issue cannot forge an approval.
* **Approver roster** = ``config/team-ratification.json`` ``ratifiers`` (one
  roster, one truth with PR ratification). Dual approval means two DISTINCT
  humans; a deny by any roster approver ends the request.
* **72h no-response auto-deny** (``sweep``) with a courteous reply. A request
  holding one approval is NOT swept — that is the bus-factor rail's territory
  (process rule in VETTING.md, not code).
* **Every decision emits a signed IDR leaf** via the SAME emitter the suite
  unlock uses (``license_handshake._default_idr_emitter``, imported — not
  copied) under its own kind ``grip-access-decision``.
* **WhatsApp notification** delegates to ``license_handshake._default_notifier``
  resolved at CALL time through a module reference, so test monkeypatches of
  the transport always intercept (memory: feedback_test_seam_dual_module_wa_leak).
* **Approved decisions EMIT provisioning commands; they never execute them.**
  Emitting-not-executing is the complete v1 contract by design (the council's
  KISS-aligned shape) — the operator runs the commands and closes the issue.

Every effectful function REQUIRES ``gateway`` (GitHub) and ``roster`` args —
there is no ambient default inside the logic, so tests are offline by
construction and can never touch the real roster file or the real repo.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Form-symmetric module reference (package vs flat) — attribute lookups on
# ``_lh`` happen at CALL time, so monkeypatching the bound module works and the
# real WhatsApp sink (lib.whatsapp_transport.post) stays the single choke point.
try:  # package context binds lib.license_handshake;
    from lib import license_handshake as _lh
except ImportError:  # flat context (lib/ on sys.path) binds license_handshake
    import license_handshake as _lh

from claude_dir import get_claude_dir  # noqa: E402

__all__ = [
    "load_roster",
    "primary_jid",
    "required_approvals",
    "scope_of",
    "prior_approvers",
    "is_stale",
    "list_requests",
    "notify_approvers",
    "approve",
    "deny",
    "sweep",
    "provisioning_commands",
    "GhIssueGateway",
]

LABEL_REQUEST = "access-request"
LABEL_1OF2 = "access-approved-1of2"
LABEL_APPROVED = "access-approved"
LABEL_DENIED = "access-denied"
SCOPE_PRIVATE = "private-overlay"
SCOPE_COMMUNITY = "community-role"
STALE_WINDOW_S = 72 * 60 * 60
IDR_KIND_ACCESS = "grip-access-decision"
IDR_PROMPT = "grip membership access decision"
APPROVAL_MARK = "[grip-unlock] APPROVED by "
DENIAL_MARK = "[grip-unlock] DENIED by "
AUTO_ACTOR = "auto-sweep-72h"
DEFAULT_ORG = "CodeTonight-SA"
# GitHub comment author associations trusted to carry an approval mark.
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

IdrEmitter = Optional[Callable[..., Optional[str]]]
Roster = dict[str, list[str]]


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def _default_roster_path() -> Path:
    return get_claude_dir() / "config" / "team-ratification.json"


def load_roster(*, path: Optional[Path | str] = None, data: Optional[dict] = None) -> Roster:
    """Load the approver roster as ``{name: [jids]}``.

    Source is ``config/team-ratification.json`` ``ratifiers`` (a JID -> name
    map; one human may hold several JID forms). Tests MUST inject ``data`` or
    ``path`` — only the runtime CLI uses the real default file.
    """
    if data is None:
        source = Path(path) if path else _default_roster_path()
        data = json.loads(source.read_text(encoding="utf-8"))
    roster: Roster = {}
    for jid, name in (data.get("ratifiers") or {}).items():
        roster.setdefault(str(name), []).append(str(jid))
    if not roster:
        raise ValueError(
            "approver roster is empty — check team-ratification.json 'ratifiers'"
        )
    return roster


def primary_jid(jids: list[str]) -> str:
    """Prefer the phone-form JID (canonical; @lid resolves to it)."""
    for jid in jids:
        if jid.endswith("@s.whatsapp.net"):
            return jid
    return jids[0]


def _require_approver(approver: str, roster: Roster) -> None:
    if approver not in roster:
        known = ", ".join(sorted(roster))
        raise ValueError(f"'{approver}' is not on the approver roster ({known})")


# ---------------------------------------------------------------------------
# Pure state-machine core
# ---------------------------------------------------------------------------

def required_approvals(scope: str) -> int:
    """Dual approval for the private overlay; single for community roles."""
    return 2 if scope == SCOPE_PRIVATE else 1


def scope_of(body: str) -> str:
    """Parse the requested scope from the issue-form body (deterministic:
    the dropdown values carry the literal scope tokens)."""
    return SCOPE_PRIVATE if SCOPE_PRIVATE in (body or "") else SCOPE_COMMUNITY


def _trusted_lines(comments: list[dict]) -> Iterator[str]:
    """Comment-body lines from trusted author associations only — an outsider
    pasting the marker text on the public issue is ignored."""
    for comment in comments:
        if comment.get("author_association", "NONE") in TRUSTED_ASSOCIATIONS:
            yield from comment.get("body", "").splitlines()


def prior_approvers(comments: list[dict], roster: Roster) -> list[str]:
    """Distinct roster approvers who left an approval mark, in order."""
    ordered: list[str] = []
    seen: set[str] = set()
    for line in _trusted_lines(comments):
        if not line.startswith(APPROVAL_MARK):
            continue
        name = line[len(APPROVAL_MARK):].split(" (")[0].strip()
        if name in roster and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def is_stale(created_ts: float, now: float, window_s: float = STALE_WINDOW_S) -> bool:
    """True once a request's age strictly exceeds the 72h response window."""
    return (now - created_ts) > window_s


def _decided(labels: list[str]) -> bool:
    return LABEL_APPROVED in labels or LABEL_DENIED in labels


def _require_open_request(issue: dict) -> None:
    number = issue.get("number")
    if issue.get("state") != "open":
        raise ValueError(f"issue #{number} is not open")
    labels = issue.get("labels", [])
    if LABEL_REQUEST not in labels:
        raise ValueError(
            f"issue #{number} is not an access request (missing '{LABEL_REQUEST}' label)"
        )
    if _decided(labels):
        raise ValueError(f"issue #{number} is already decided")


def _result(number: int, verdict: str, scope: str, **extra: Any) -> dict:
    """Single result shape shared by pending / grant / deny outcomes."""
    return {"ok": True, "number": number, "verdict": verdict, "scope": scope, **extra}


# ---------------------------------------------------------------------------
# Issue-form parsing
# ---------------------------------------------------------------------------

def _form_value(body: str, label: str) -> str:
    """Value under a ``### <label>`` heading in a rendered issue-form body."""
    heading = f"### {label}".lower()
    in_section = False
    for line in (body or "").splitlines():
        text = line.strip()
        if not in_section:
            in_section = text.lower() == heading
            continue
        if text.startswith("### "):
            return ""
        if text and text.lower() != "_no response_":
            return text
    return ""


def handle_of(issue: dict) -> str:
    """Requester's GitHub handle — form field first, ``[access] x`` title fallback."""
    handle = _form_value(issue.get("body", ""), "GitHub handle").lstrip("@")
    if handle:
        return handle
    title = issue.get("title", "")
    return title.split("]", 1)[1].strip().lstrip("@") if "]" in title else ""


def requester_of(issue: dict) -> str:
    return _form_value(issue.get("body", ""), "Who are you") or handle_of(issue)


# ---------------------------------------------------------------------------
# Provisioning (EMITTED for the operator — never executed here, by design)
# ---------------------------------------------------------------------------

def _default_repo() -> str:
    return os.environ.get("GRIP_ACCESS_REPO", f"{DEFAULT_ORG}/GRIP")


def provisioning_commands(handle: str, scope: str, *, org: str = DEFAULT_ORG,
                          repo: Optional[str] = None) -> list[str]:
    """The exact operator commands an APPROVED decision emits.

    v1 contract is decide + record + EMIT: the operator executes. For the
    private overlay this includes the hand-off into the EXISTING suite
    admin-unlock machinery (``grip-unlock issue``).
    """
    repo = repo or _default_repo()
    target = handle or "<github-handle>"
    if scope == SCOPE_COMMUNITY:
        return [
            f"gh api repos/{repo}/collaborators/{target} -X PUT -f permission=pull",
        ]
    return [
        f'gh api orgs/{org}/invitations -F invitee_id="$(gh api users/{target} '
        '--jq .id)" -f role=direct_member',
        f"# then, once the requester runs `grip-unlock request {target}` on their",
        "# machine, mint their suite admin-unlock code from the challenge it sends:",
        "grip-unlock issue <challenge-from-request>",
    ]


# ---------------------------------------------------------------------------
# WhatsApp notification (call-time seam — leak-safe by construction)
# ---------------------------------------------------------------------------

def _default_notifier(recipient: str, text: str) -> dict:
    """CALL-TIME delegation to the suite unlock's WhatsApp notifier.

    ``_lh._default_notifier`` itself resolves ``lib.whatsapp_transport.post``
    inside its body, so a test monkeypatch of the transport module attribute
    always intercepts — no import-time frozen reference
    (feedback_test_seam_dual_module_wa_leak).
    """
    return _lh._default_notifier(recipient, text)


def _format_notify_message(request: dict) -> str:
    return (
        "GRIP here — new membership access request.\n\n"
        f"Issue   : #{request.get('number')} {request.get('title', '')}\n"
        f"Handle  : {request.get('handle', '?')}\n"
        f"Scope   : {request.get('scope', '?')} "
        f"(needs {required_approvals(request.get('scope', ''))} approval(s))\n"
        f"Link    : {request.get('url', '')}\n\n"
        "Vet from PUBLIC footprint only (docs/access/VETTING.md, ~15 min).\n"
        f"Approve : grip-unlock approve {request.get('number')} --as <your-name>\n"
        f"Deny    : grip-unlock deny {request.get('number')} --reason \"...\" "
        "--as <your-name>\n"
        "No response within 72h auto-declines courteously."
    )


def notify_approvers(request: dict, *, roster: Roster,
                     notifier: Optional[Callable[[str, str], dict]] = None) -> list[dict]:
    """WhatsApp-DM every distinct roster approver about one request."""
    send = notifier or _default_notifier
    text = _format_notify_message(request)
    deliveries = []
    for name, jids in roster.items():
        jid = primary_jid(jids)
        deliveries.append({"approver": name, "jid": jid, "delivery": send(jid, text)})
    return deliveries


# ---------------------------------------------------------------------------
# Decision finalisation (shared effect bundle for grant/deny)
# ---------------------------------------------------------------------------

class _Decider:
    """Bundles the effect collaborators (gateway + IDR emitter) so the three
    public entry points share ONE finalisation path instead of re-plumbing
    the same keyword arguments."""

    def __init__(self, gateway: Any, idr_emitter: IdrEmitter, idr_path: Optional[str]):
        self.gateway = gateway
        self.idr_emitter = idr_emitter
        self.idr_path = idr_path

    def _emit_idr(self, decision: dict, issue: dict) -> tuple[Optional[str], Optional[str]]:
        """One signed leaf per decision; best-effort like the suite unlock —
        the decision stands and any audit gap is surfaced, never swallowed."""
        emitter = self.idr_emitter or _lh._default_idr_emitter
        ref = issue.get("url") or f"access-request-{issue['number']}"
        evidence = (
            "public-footprint vetting per docs/access/VETTING.md; "
            f"request: {ref} — {issue.get('title', '')}"
        )
        try:
            leaf = emitter(
                decision=decision,
                fingerprint=hashlib.sha256(ref.encode()).hexdigest()[:32],
                inputs={"evidence": evidence},
                idr_path=self.idr_path,
                kind=IDR_KIND_ACCESS,
                prompt=IDR_PROMPT,
            )
            return leaf, None
        except Exception as exc:  # noqa: BLE001 — surface the audit gap in the result
            return None, f"{type(exc).__name__}: {str(exc)[:160]}"

    def _seal(self, issue: dict, verdict: str, scope: str, approvers: list[str],
              base: dict, reason: Optional[str] = None) -> dict:
        """Clear the interim label, emit the IDR leaf, and shape the result."""
        if LABEL_1OF2 in issue.get("labels", []):
            self.gateway.remove_label(issue["number"], LABEL_1OF2)
        decision = {
            "action": "membership-access",
            "verdict": verdict,
            "scope": scope,
            "requester": requester_of(issue),
            "github_handle": handle_of(issue),
            "issue": issue.get("url") or f"#{issue['number']}",
            "approvers": approvers,
        }
        if reason:
            decision["reason"] = reason
        base["idr_leaf"], idr_error = self._emit_idr(decision, issue)
        if idr_error:
            base["idr_error"] = idr_error
        return base

    def grant(self, issue: dict, approvals: list[str], scope: str) -> dict:
        number = issue["number"]
        self.gateway.add_labels(number, [LABEL_APPROVED])
        self.gateway.comment(number, _grant_comment(approvals))
        base = _result(number, "approved", scope, approvals=approvals,
                       needed=len(approvals),
                       provisioning=provisioning_commands(handle_of(issue), scope))
        return self._seal(issue, "approved", scope, approvals, base)

    def deny(self, issue: dict, actor: str, reason: str) -> dict:
        number = issue["number"]
        scope = scope_of(issue.get("body", ""))
        self.gateway.comment(number, _deny_comment(actor, reason))
        self.gateway.add_labels(number, [LABEL_DENIED])
        self.gateway.close_issue(number)
        base = _result(number, "denied", scope, actor=actor, reason=reason)
        return self._seal(issue, "denied", scope, [actor], base, reason=reason)


def _grant_comment(approvals: list[str]) -> str:
    names = " and ".join(approvals) if len(approvals) == 2 else ", ".join(approvals)
    return (
        f"[grip-unlock] Decision: APPROVED by {names} per docs/access/VETTING.md. "
        "Provisioning will follow at the approved scope."
    )


def _deny_comment(actor: str, reason: str) -> str:
    return (
        f"{DENIAL_MARK}{actor}: {reason}\n\n"
        "Thank you for your interest in GRIP. This request was not approved this "
        "time — the reason is above, per the written criteria in "
        "docs/access/VETTING.md. You are welcome to re-apply."
    )


# ---------------------------------------------------------------------------
# Public decisions (state machine over the GitHub issue)
# ---------------------------------------------------------------------------

def approve(number: int, approver: str, *, gateway: Any, roster: Roster,
            idr_emitter: IdrEmitter = None, idr_path: Optional[str] = None) -> dict:
    """Record one attributed approval; finalise the grant when quorum is met."""
    _require_approver(approver, roster)
    issue = gateway.get_issue(number)
    _require_open_request(issue)
    scope = scope_of(issue.get("body", ""))
    needed = required_approvals(scope)
    prior = prior_approvers(issue.get("comments", []), roster)
    if approver in prior:
        return _result(issue["number"], "pending", scope, needed=needed,
                       approvals=prior, note="already approved by this approver")
    approvals = prior + [approver]
    gateway.comment(issue["number"], f"{APPROVAL_MARK}{approver} ({len(approvals)}/{needed})")
    if len(approvals) < needed:
        gateway.add_labels(issue["number"], [LABEL_1OF2])
        return _result(issue["number"], "pending", scope, needed=needed, approvals=approvals)
    return _Decider(gateway, idr_emitter, idr_path).grant(issue, approvals, scope)


def deny(number: int, approver: str, reason: str, *, gateway: Any, roster: Roster,
         idr_emitter: IdrEmitter = None, idr_path: Optional[str] = None) -> dict:
    """Deny by any roster approver, at any point — deny wins over 1of2."""
    _require_approver(approver, roster)
    issue = gateway.get_issue(number)
    _require_open_request(issue)
    return _Decider(gateway, idr_emitter, idr_path).deny(
        issue, approver, reason or "not approved")


# ---------------------------------------------------------------------------
# Listing + 72h sweep
# ---------------------------------------------------------------------------

def _request_state(labels: list[str]) -> str:
    if LABEL_APPROVED in labels:
        return "approved"
    if LABEL_DENIED in labels:
        return "denied"
    return "1of2" if LABEL_1OF2 in labels else "pending"


def list_requests(*, gateway: Any, now: Optional[float] = None) -> list[dict]:
    """Open access requests with parsed scope, state, age, and staleness."""
    now = time.time() if now is None else now
    out = []
    for issue in gateway.list_open_requests():
        state = _request_state(issue.get("labels", []))
        out.append({
            "number": issue["number"],
            "title": issue.get("title", ""),
            "handle": handle_of(issue),
            "scope": scope_of(issue.get("body", "")),
            "state": state,
            "url": issue.get("url", ""),
            "age_hours": round((now - issue["created_ts"]) / 3600, 1),
            "stale": state == "pending" and is_stale(issue["created_ts"], now),
        })
    return out


def sweep(*, gateway: Any, roster: Roster, idr_emitter: IdrEmitter = None,
          idr_path: Optional[str] = None, now: Optional[float] = None) -> list[dict]:
    """Auto-deny requests with ZERO approver responses after 72h (courteous).

    A request holding one approval is left alone — it is waiting on the second
    approver, which the bus-factor rail (VETTING.md) governs as process.
    """
    now = time.time() if now is None else now
    decider = _Decider(gateway, idr_emitter, idr_path)
    results = []
    for summary in gateway.list_open_requests():
        if not is_stale(summary["created_ts"], now):
            continue
        issue = gateway.get_issue(summary["number"])
        if _decided(issue.get("labels", [])):
            continue
        if prior_approvers(issue.get("comments", []), roster):
            continue
        results.append(decider.deny(
            issue, AUTO_ACTOR, "no approver response within 72 hours"))
    return results


# ---------------------------------------------------------------------------
# Runtime GitHub gateway (gh CLI; explicit repo per rules/gh-repo-resolution.md)
# ---------------------------------------------------------------------------

def _parse_iso(ts: str) -> float:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).timestamp()


def _normalise_issue(raw: dict, comments: list[dict]) -> dict:
    return {
        "number": raw["number"],
        "title": raw.get("title", ""),
        "body": raw.get("body") or "",
        "state": raw.get("state", "open"),
        "labels": [
            label["name"] if isinstance(label, dict) else str(label)
            for label in raw.get("labels", [])
        ],
        "created_ts": _parse_iso(raw["created_at"]),
        "url": raw.get("html_url", ""),
        "comments": [
            {
                "body": c.get("body", ""),
                "author": (c.get("user") or {}).get("login", ""),
                "author_association": c.get("author_association", "NONE"),
            }
            for c in comments
        ],
    }


class GhIssueGateway:
    """GitHub issue state store via the ``gh`` CLI (explicit repo, never the
    ambient cwd default — rules/gh-repo-resolution.md). Tests inject a fake
    with the same five methods; this class is runtime-only."""

    def __init__(self, repo: Optional[str] = None):
        self.repo = repo or _default_repo()

    def _api(self, path: str, *args: str) -> Any:
        proc = subprocess.run(
            ["gh", "api", path, *args], capture_output=True, text=True, check=True,
        )
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def _issue_path(self, number: int, suffix: str = "") -> str:
        return f"repos/{self.repo}/issues/{number}{suffix}"

    def list_open_requests(self) -> list[dict]:
        raw = self._api(
            f"repos/{self.repo}/issues?labels={LABEL_REQUEST}&state=open&per_page=100"
        ) or []
        return [_normalise_issue(item, []) for item in raw]

    def get_issue(self, number: int) -> dict:
        raw = self._api(self._issue_path(number))
        comments = self._api(self._issue_path(number, "/comments")) or []
        return _normalise_issue(raw, comments)

    def comment(self, number: int, body: str) -> None:
        self._api(self._issue_path(number, "/comments"), "-f", f"body={body}")

    def add_labels(self, number: int, labels: list[str]) -> None:
        args: list[str] = []
        for label in labels:
            args.extend(["-f", f"labels[]={label}"])
        self._api(self._issue_path(number, "/labels"), *args)

    def remove_label(self, number: int, label: str) -> None:
        try:
            self._api(self._issue_path(number, f"/labels/{label}"), "-X", "DELETE")
        except subprocess.CalledProcessError:
            pass  # label already absent — removal is idempotent

    def close_issue(self, number: int) -> None:
        self._api(self._issue_path(number), "-X", "PATCH", "-f", "state=closed")
