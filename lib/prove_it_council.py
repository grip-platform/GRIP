"""prove_it_council.py — L2 multi-LLM citation-support council for /prove-it.

L1 (`prove_it.verify_quote`) proves a quote EXISTS verbatim in the source. L2
asks a panel of external, non-Claude LLMs the next question: does that quote
actually *support* the claim? Each provider votes supported/partial/unsupported;
a majority (MoE) is taken; an LLM-as-judge (Gemini 3.1 Pro) synthesises a final
verdict + one-line reason.

Two invariants from `rules/llm-augmentation-pattern.md`:
- **Monotonicity**: L2 runs ONLY on citations L1 already located (verified/fuzzy).
  A `not_found` quote has nothing to support — L2 can never upgrade the floor,
  so a fabricated quote can never earn a "supported" badge.
- **Fail-open**: any provider/judge/import/network error degrades to the
  majority (or "unavailable"); the deterministic L1 artifact always still renders.

Gemini/Groq reuse the `lib/providers/*` shims (syscall-doctrine); Anthropic uses
the direct external-keychain helper below. Origin: 2026-06-19, /ultrado wave B
(L2 layer on the /prove-it dogfood).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

VERDICTS = ("supported", "partial", "unsupported")

SUPPORT_PROMPT = (
    "A claim is cited to a quoted passage from a source document. Judge ONLY "
    "whether the passage supports the claim — do not judge whether the claim is "
    "wise or true in the world.\n\n"
    "CLAIM: {claim}\n\nQUOTED PASSAGE (IN SITU): {quote}\n\n"
    'Reply with ONLY a JSON object: {{"verdict": "supported|partial|unsupported", '
    '"why": "one short sentence"}}'
)

JUDGE_PROMPT = (
    "Independent reviewers each judged whether a quoted passage supports a claim. "
    "Synthesise their votes into one final verdict.\n\n"
    "CLAIM: {claim}\n\nQUOTED PASSAGE: {quote}\n\nREVIEWER VOTES:\n{votes}\n\n"
    'Reply with ONLY JSON: {{"verdict": "supported|partial|unsupported", '
    '"synthesis": "one short sentence explaining the consensus"}}'
)


def _parse_verdict(text: str) -> str:
    """Extract a verdict token from a provider/judge response. '' if none."""
    if not text:
        return ""
    low = text.lower()
    start = low.find("{")
    if start != -1:
        try:
            obj = json.loads(text[start:text.rfind("}") + 1])
            v = str(obj.get("verdict", "")).strip().lower()
            if v in VERDICTS:
                return v
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass
    # keyword fallback — 'unsupported' contains 'supported', so test it first
    for v in ("unsupported", "partial", "supported"):
        if re.search(rf"\b{v}\b", low):
            return v
    return ""


def _parse_why(text: str, key: str = "why") -> str:
    if not text:
        return ""
    start = text.find("{")
    if start != -1:
        try:
            obj = json.loads(text[start:text.rfind("}") + 1])
            return str(obj.get(key, "")).strip()[:200]
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass
    return ""


#: Gemini 2.5 and 3.x models think before they answer, and the thinking counts
#: against the output budget. At 200 tokens every Gemini seat and the Gemini judge
#: stopped at MAX_TOKENS mid-JSON (measured 2026-10-17: Gemini 2.5 Flash used about
#: 190 tokens thinking, 2.5 Pro about 680, the 3.1 Pro judge about 190), so each
#: returned no verdict and the judge always fell through to Groq. 2048 leaves room.
_GEMINI_MAX_TOKENS = 2048


def _gemini_vote(claim: str, quote: str, model: str = "gemini-3.5-flash-lite"):
    from providers.gemini import GeminiProvider  # type: ignore
    p = GeminiProvider()
    r = p.generate(SUPPORT_PROMPT.format(claim=claim, quote=quote),
                   model=model, temperature=0.1, max_tokens=_GEMINI_MAX_TOKENS)
    txt = r.text if hasattr(r, "text") else str(r)
    return _parse_verdict(txt), _parse_why(txt)


def _groq_vote(claim: str, quote: str, model: str):
    from providers.groq import GroqProvider  # type: ignore
    p = GroqProvider()
    r = p.generate(SUPPORT_PROMPT.format(claim=claim, quote=quote),
                   model=model, temperature=0.1, max_tokens=200)
    txt = r.text if hasattr(r, "text") else str(r)
    return _parse_verdict(txt), _parse_why(txt)


_ANTHROPIC_KEYCHAIN = "grip-anthropic-external"
#: The Anthropic seat's model, read by the panel's tests.
_ANTHROPIC_MODEL = "claude-haiku-4-5"
#: grip-anthropic-external is a per-token Anthropic API key, so the Haiku seat sits
#: only when the operator has opted in to paid Anthropic spend: never the paid key
#: without it (rules/claude-cap-moves-to-the-next-subscription.md). The value must be
#: exactly "1", compared as bin/grip-auth-failover's _paid_allowed compares it, so
#: " 1" or "1\n" is not consent.
_PAID_OPT_IN = "GRIP_CLAUDE_ALLOW_PAID"


def _paid_anthropic_allowed() -> bool:
    return os.environ.get(_PAID_OPT_IN) == "1"


def _shield(value):
    """``value`` scrubbed by lib/review_redaction.py before it leaves, in every
    invocation shape; with no shield to load it raises OSError and sends nothing."""
    try:
        from review_redaction import shield_payload
    except ImportError:
        try:
            from lib.review_redaction import shield_payload
        except ImportError as exc:  # no shield, no send: callers treat OSError as a failed send
            raise OSError("the redaction shield is unavailable, so nothing was sent") from exc
    return shield_payload(value)


def _anthropic_text(prompt: str, max_tokens: int = 200) -> str:
    """One direct Anthropic API call (Haiku) via the external keychain credential.

    A SECOND, separate credential — not the harness/subscription path — so it
    survives a CCH subscription rate-limit (the failure mode that degraded the
    2026-06-19 run). Small local helper; a shared lib/anthropic_external.py shim
    is eligible at the functor's N=2 since 2026-10-03 (lib/moe/external_scorer.py
    is the 2nd instance), not yet built; its first build goes through the
    promotion gate.

    The prompt passes the redaction shield before the key is even read; when
    it cannot, this raises OSError and nothing is sent, which the panel already
    treats as a seat that did not answer. Without GRIP_CLAUDE_ALLOW_PAID=1 it
    raises before the shield or the key, so nothing is billed."""
    import subprocess
    import urllib.request

    if not _paid_anthropic_allowed():
        raise RuntimeError(f"the paid Anthropic key needs {_PAID_OPT_IN}=1")
    body = json.dumps(_shield({
        "model": _ANTHROPIC_MODEL, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    })).encode("utf-8")
    key = subprocess.run(["security", "find-generic-password", "-s", _ANTHROPIC_KEYCHAIN, "-w"],
                         capture_output=True, text=True, timeout=5)
    if key.returncode != 0 or not key.stdout.strip():
        raise RuntimeError("no anthropic-external key")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"Content-Type": "application/json", "x-api-key": key.stdout.strip(),
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _anthropic_vote(claim: str, quote: str):
    txt = _anthropic_text(SUPPORT_PROMPT.format(claim=claim, quote=quote))
    return _parse_verdict(txt), _parse_why(txt)


def _default_panel(mode: str = "standard") -> list[tuple]:
    """Provider panel — 3 providers (Gemini, Groq, Anthropic-external) so a single
    provider outage cannot drop the council below 3 votes (the Groq trio alone
    yields 3). Each entry: (name, fn(claim, quote) -> (verdict, why)).

    Every Groq id here was in Groq's own ``GET /v1/models`` list on 2026-10-17.
    Three earlier seats were not: ``qwen/qwen3-32b`` (decommissioned 2026-07-17),
    and ``meta-llama/llama-4-scout-17b-16e-instruct`` and
    ``deepseek-r1-distill-llama-70b`` (retired 2026-08-30), so the panel ran short.
    Groq's live chat roster is GPT-OSS and Qwen 3.8 only, so the replacements
    add seats, not families: four weight families in both modes (Gemini, GPT-OSS,
    Qwen, Claude). HAL's catalogue named ``qwen/qwen3.6-27b`` for the proof seat
    until 2026-11-24, but Groq no longer serves it, so the proof seat is Gemini
    3.8 Flash instead (repointed from gemini-2.5-pro the same day, GRIP#9389:
    Google auto-routes that id to gemini-3.8-flash from 2026-12-01).

    The Haiku seat bills a per-token Anthropic key, so it sits only with
    GRIP_CLAUDE_ALLOW_PAID=1. Without it the panel is four seats (five in proof
    mode) in three families, every one of them free of per-token Anthropic spend.
    """
    panel = [
        ("gemini-3.5-flash-lite", lambda c, q: _gemini_vote(c, q, "gemini-3.5-flash-lite")),
        ("gpt-oss-120b", lambda c, q: _groq_vote(c, q, "openai/gpt-oss-120b")),
        ("qwen3.8-27b", lambda c, q: _groq_vote(c, q, "qwen/qwen3.8-27b")),
        ("gpt-oss-20b", lambda c, q: _groq_vote(c, q, "openai/gpt-oss-20b")),
    ]
    if _paid_anthropic_allowed():
        panel.append(("anthropic-haiku", _anthropic_vote))
    if mode == "proof":
        panel.append(("gemini-3.8-flash", lambda c, q: _gemini_vote(c, q, "gemini-3.8-flash")))
    return panel


def _gemini_judge(claim: str, quote: str, votes: dict, whys: dict):
    """LLM-as-judge synthesis (Gemini 3.1 Pro). Returns (verdict, synthesis)."""
    from providers.gemini import GeminiProvider  # type: ignore
    vote_lines = "\n".join(f"- {name}: {v} ({whys.get(name, '')})" for name, v in votes.items())
    p = GeminiProvider()
    r = p.generate(JUDGE_PROMPT.format(claim=claim, quote=quote, votes=vote_lines),
                   model="gemini-3.1-pro-preview", temperature=0.1,
                   max_tokens=_GEMINI_MAX_TOKENS)
    txt = r.text if hasattr(r, "text") else str(r)
    return _parse_verdict(txt), _parse_why(txt, "synthesis")


def _anthropic_judge(claim: str, quote: str, votes: dict, whys: dict):
    vote_lines = "\n".join(f"- {name}: {v} ({whys.get(name, '')})" for name, v in votes.items())
    txt = _anthropic_text(JUDGE_PROMPT.format(claim=claim, quote=quote, votes=vote_lines))
    return _parse_verdict(txt), _parse_why(txt, "synthesis")


def _groq_judge(claim: str, quote: str, votes: dict, whys: dict):
    from providers.groq import GroqProvider  # type: ignore
    vote_lines = "\n".join(f"- {name}: {v} ({whys.get(name, '')})" for name, v in votes.items())
    r = GroqProvider().generate(JUDGE_PROMPT.format(claim=claim, quote=quote, votes=vote_lines),
                                model="openai/gpt-oss-120b", temperature=0.1, max_tokens=200)
    txt = r.text if hasattr(r, "text") else str(r)
    return _parse_verdict(txt), _parse_why(txt, "synthesis")


#: The judge ladder is EXOGENEITY-CRITICAL and is therefore non-Anthropic only.
#:
#: GRIP's claims are authored by Claude, so an Anthropic judge is the author
#: marking its own work — exogenous-anchor-law.md ("evaluated by a process the
#: generating model neither controls nor can predict-to-please").
#:
#: _anthropic_judge previously sat SECOND here, ahead of the Groq judge. That
#: made the violation a RUNTIME event rather than an edit: a Gemini rate-limit
#: promoted Anthropic to judge, while a perfectly good non-Anthropic fallback
#: sat unused one rung below. Nothing in the output said the anchor had moved.
#:
#: Exhausting this ladder returns ('', ''), and council_review falls back to the
#: MoE majority of the panel. That degradation is SAFE — a missing judge is
#: visible and conservative, where a self-marking judge is neither. The panel
#: itself is unchanged and still carries Anthropic as a VOTER, which is not an
#: exogeneity question; only the synthesiser is.
#:
#: _anthropic_judge is deliberately left defined and injectable via `judges=`,
#: so a caller reviewing NON-Anthropic-authored material can still use it.
_DEFAULT_JUDGES = [_gemini_judge, _groq_judge]


def _judge_ladder(claim: str, quote: str, votes: dict, whys: dict, judges=None):
    """Try judges in order (Gemini -> Groq); first to return a verdict wins, so a
    synthesis survives a single-provider outage. ('', '') if all fail
    (council_review then falls back to the MoE majority).

    The ladder excludes Anthropic by construction — see _DEFAULT_JUDGES."""
    judges = judges if judges is not None else list(_DEFAULT_JUDGES)
    for judge in judges:
        try:
            verdict, reason = judge(claim, quote, votes, whys)
        except Exception:
            continue
        if verdict:
            return verdict, reason
    return "", ""


def _majority(values) -> tuple[str, float]:
    """Most-common verdict + agreement fraction. Ties resolve toward caution
    (the *weaker* of the tied verdicts: unsupported < partial < supported)."""
    vals = [v for v in values if v]
    if not vals:
        return "", 0.0
    counts = {v: vals.count(v) for v in set(vals)}
    top = max(counts.values())
    tied = [v for v, n in counts.items() if n == top]
    order = {"unsupported": 0, "partial": 1, "supported": 2}
    winner = min(tied, key=lambda v: order.get(v, 1))
    return winner, top / len(vals)


def council_review(claim: str, quote: str, mode: str = "standard",
                   panel=None, judge_fn=None) -> dict:
    """Run the L2 council on a single (claim, quote). Fail-open.

    panel / judge_fn are injectable (DIP) so tests run with zero network.
    Returns {verdict, confidence, votes, providers, judge_reason}.
    """
    panel = panel if panel is not None else _default_panel(mode)
    judge_fn = judge_fn if judge_fn is not None else _judge_ladder

    votes: dict[str, str] = {}
    whys: dict[str, str] = {}
    for name, fn in panel:
        try:
            verdict, why = fn(claim, quote)
        except Exception:
            continue  # fail-open per provider
        if verdict:
            votes[name] = verdict
            whys[name] = why

    if not votes:
        return {"verdict": "unavailable", "confidence": 0.0, "votes": {},
                "providers": 0, "judge_reason": "no external providers reachable"}

    maj, conf = _majority(votes.values())
    try:
        j_verdict, j_reason = judge_fn(claim, quote, votes, whys)
    except Exception:
        j_verdict, j_reason = "", ""
    return {
        "verdict": j_verdict or maj,
        "confidence": round(conf, 2),
        "votes": votes,
        "providers": len(votes),
        "judge_reason": j_reason or f"{len(votes)}-model majority: {maj}",
    }


def _extract_window(text: str, start: int, end: int, num_paragraphs: int = 1) -> str:
    if start == -1 or end == -1 or not text:
        return ""
    import re
    paras = [(m.start(0), m.end(0), m.group(0)) for m in re.finditer(r'(?s).*?(?:\n\n+|$)', text)]
    if not paras:
        return text[max(0, start-500):min(len(text), end+500)].strip()
    idx_start, idx_end = 0, len(paras) - 1
    for i, (p_start, p_end, _) in enumerate(paras):
        if p_end > start and (i == 0 or paras[i-1][1] <= start):
            idx_start = i
        if p_start <= end:
            idx_end = i
    idx_start = max(0, idx_start - num_paragraphs)
    idx_end = min(len(paras) - 1, idx_end + num_paragraphs)
    return text[paras[idx_start][0] : paras[idx_end][1]].strip()


def review_citations(cites, sources, mode: str = "standard", panel=None, judge_fn=None) -> list:
    """Annotate each citation with an L2 support verdict.

    MONOTONICITY: only citations L1 located (verified/fuzzy) are reviewed; a
    not_found quote is marked n/a and NEVER receives a support verdict — L2
    cannot rescue a quote the deterministic floor could not find.
    """
    from prove_it import STATUS_VERIFIED, STATUS_FUZZY  # type: ignore
    by_id = {s.id: s for s in sources}
    for c in cites:
        if c.status in (STATUS_VERIFIED, STATUS_FUZZY):
            src = by_id.get(c.source_id)
            window = _extract_window(src.text, c.start, c.end, 1) if src else c.quote
            v = council_review(c.claim, window, mode=mode, panel=panel, judge_fn=judge_fn)
            c.support = v["verdict"]
            c.support_detail = f'{v["judge_reason"]} ({v["providers"]} models, {int(v["confidence"]*100)}% agree)'
        else:
            c.support = "n/a"
            c.support_detail = "quote not located in source — nothing to support (L1)"
    return cites

