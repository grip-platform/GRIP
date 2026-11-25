"""Pool-B adversarial verification of converge-fleet findings.

WHY THIS EXISTS. A converge-fleet run's verify phase is its largest fan-out — one agent
per finding — and on 2026-08-31 that fan-out is what killed run wf_bef8da20-5e1: all six
discovery lanes succeeded and returned 318 findings, then 24 verify agents and the
synthesis agent died on the Anthropic monthly spend limit. Discovery survived; refutation
did not, which by the fleet's own doctrine leaves 318 claims and zero evidence.

Routing verification to Pool B fixes two separate problems with one change, and the second
is the more important:

  1. COST. Pool A (Anthropic) is weekly-limited and is what the interactive session spends.
     `rules/pool-a-offload-routing.md` is explicit that LLM-heavy cognition routes through
     Pool B by default. A per-finding fan-out is the definition of LLM-heavy.
  2. EPISTEMICS. An Anthropic agent checking an Anthropic agent's work is a weak exogenous
     anchor. `rules/exogenous-anchor-law.md` requires that the verifier is never the author;
     a non-Anthropic model grading Anthropic-authored findings satisfies the vendor-identity
     test that a same-vendor checker does not. The cheap fix is also the sound one.

TWO ROUTES, and the difference is not cosmetic.

  route="poolb"  -> HALLLMAdapter (lib.hal_llm_adapter). This is a CLOUD path. Note the
                    naming trap documented by the fleet itself: `LlamaEngine` in that module
                    is an ALIAS FOR HALLLMAdapter, i.e. the cloud adapter, NOT a local llama.
                    Anything sent this way LEAVES THE MACHINE, so redaction is MANDATORY and
                    fails closed.
  route="local"  -> ollama on 127.0.0.1:11434. Nothing leaves the machine, which is the only
                    route admissible for content under an operator "never shared" condition.
                    Still a different vendor from the author (Alibaba-lineage qwen weights),
                    so it remains a valid exogenous anchor.

WHAT THIS MODULE WILL NOT DO. A finding whose check fails, times out, or returns unparseable
output becomes UNCHECKED. It never becomes CONFIRMED, and it is never dropped. Dropping it
would make a run look cleaner the more of its checkers died — the exact silent failure the
converge-fleet template's own verify phase is written to prevent, and it would reappear here
if this module filtered instead of marking.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from itertools import chain, zip_longest
from pathlib import Path
from typing import Callable, Iterable, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# Verdicts a checker may return. UNCHECKED is reserved for this module — a checker never
# returns it; it is what we record when the checker itself failed.
VERDICT_CONFIRMED = "CONFIRMED"
VERDICT_WEAK = "WEAK"
VERDICT_REFUTED = "REFUTED"
VERDICT_UNCHECKED = "UNCHECKED"
_CHECKER_VERDICTS = (VERDICT_CONFIRMED, VERDICT_WEAK, VERDICT_REFUTED)

# Bearings that gate a converge-fleet criterion, in the order they earn verification budget.
# "weak" is deliberately absent: the template already excludes weak findings from verify.
#
# These are the vocabulary of the WhatsApp-forensics run this module was first written for,
# and they are ONLY an ordering. They were previously duplicated into a CANONICAL_BEARINGS
# tuple that normalise_bearing used as an ACCEPT-LIST, which silently erased every other
# run's vocabulary — measured in run wf_1f710b7b-fc7 (moat decoupling), where all 40
# findings carried "coupling" / "break-strategy" / "cycle" / "blocker", all 40 became
# "unknown", and the round-robin below then dealt the whole cap from a single bucket.
# A bearing absent from this tuple is ordered last by `prioritise`; it is never rejected.
PRIORITY_BEARINGS = (
    "capture-path",
    "gap-evidence",
    "authorisation-gate",
    "egress-path",
    "at-rest-encryption",
    "sleep-behaviour",
    "ml-precedent",
    "reusable",
)

# Environment overrides for the local route (both fall back to the values below):
#
#   GRIP_FLEET_VERIFY_OLLAMA_URL    endpoint the local checker posts to
#   GRIP_FLEET_VERIFY_LOCAL_MODEL   model the local checker asks for
#
# TIMING DIFFERS BETWEEN THE TWO, deliberately noted rather than left to surprise
# someone: the URL is read from the module global inside the request, so it tracks
# a reload; the model is a DEFAULT ARGUMENT of `_local_engine`, bound once when the
# function is defined. Both are therefore import-time configuration in practice --
# set them before importing this module, not after.
#
#: Where the local route sends its checks. Overridable because the endpoint is a
#: VARIABLE OF THE EXPERIMENT, not a constant of the module: measuring whether the
#: server's KV-cache quantisation is what makes verdicts irreproducible (GRIP#6142)
#: requires pointing this at a second, differently-configured server. With the URL
#: hardcoded, the only way to run that comparison was to restart the shared ollama
#: every other process on the machine is using, which is why it had not been run.
OLLAMA_URL = os.environ.get("GRIP_FLEET_VERIFY_OLLAMA_URL") or "http://127.0.0.1:11434/api/generate"

#: Same reasoning: pinning the model is how a re-run stays comparable, and changing
#: it is how you test whether a finding survives a different judge.
DEFAULT_LOCAL_MODEL = os.environ.get("GRIP_FLEET_VERIFY_LOCAL_MODEL") or "qwen3:8b"

# Splits a polluted bearing from its trailing prose. Observed in run wf_bef8da20-5e1: several
# lanes wrote "egress-path — LIVE CODE, DORMANT PROCESS. The `LlamaEngine` imported at ..."
# into a field whose schema asked for one token, because the schema's own description was a
# long prose enumeration and invited prose back. Split on an em-dash, en-dash, double hyphen
# or colon, whichever comes first.
_BEARING_SPLIT = re.compile(r"\s*(?:—|–|--|:)\s*")


#: A bearing is one lowercase kebab-case token. Shape, not membership — the vocabulary
#: belongs to whoever asked the question, so this can only check that a token IS one.
_BEARING_TOKEN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_BEARING_MAX_LEN = 40


def normalise_bearing(raw: object) -> str:
    """Reduce a possibly-polluted bearing to a single token, or "unknown".

    Accepts any well-formed token rather than a fixed list, because /converge-fleet is a
    generic skill: a run about dependency coupling legitimately emits "coupling", and a run
    about message capture legitimately emits "capture-path". Rejecting either would erase
    the field the verifier is asked to judge against.

    "unknown" is reserved for a value that is genuinely not usable as a label — a non-string,
    an empty string, unsplit prose, or something implausibly long. It is returned rather than
    raised, because one lane's schema failure must not abort verification of the others.
    """
    if not isinstance(raw, str):
        return "unknown"
    head = _BEARING_SPLIT.split(raw.strip(), maxsplit=1)[0].strip().lower()
    if not head or len(head) > _BEARING_MAX_LEN or not _BEARING_TOKEN.match(head):
        return "unknown"
    return head


def dedup_findings(findings: Iterable[dict]) -> list[dict]:
    """Drop repeats, keyed on locator plus a quote prefix.

    The quote prefix is part of the key because one file:line can legitimately carry two
    distinct findings under different bearings (a write call is both the capture path and
    the at-rest-encryption evidence), and collapsing those would lose one of them.
    Insertion order is preserved so a re-run verifies the same findings in the same order.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for f in findings:
        key = (str(f.get("locator", "")), str(f.get("quote", ""))[:60])
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def prioritise(findings: Iterable[dict], *, cap: int) -> list[dict]:
    """Order by bearing priority and truncate to `cap`.

    Drops "weak" (the template never verifies those) and "unknown" is sorted last rather
    than dropped, so a schema failure costs priority but never silently deletes a finding.
    """
    normalised = [dict(f, bearing=normalise_bearing(f.get("bearing"))) for f in findings]
    keep = [f for f in normalised if f["bearing"] != "weak"]

    # ROUND-ROBIN, not sort-then-truncate. Measured 2026-08-31: a plain priority sort put
    # 36 of 40 verification slots into `capture-path` alone, because it simply exhausted the
    # highest-priority bearing before reaching any other. A cap spent entirely on one class
    # tells you nothing about the other eight, so the budget is dealt one finding per bearing
    # per pass, in priority order, until it runs out.
    buckets: dict[str, list[dict]] = {}
    for f in keep:
        buckets.setdefault(f["bearing"], []).append(f)
    order = {b: i for i, b in enumerate(PRIORITY_BEARINGS)}
    names = sorted(buckets, key=lambda b: order.get(b, len(PRIORITY_BEARINGS)))
    # zip_longest deals the passes: its first tuple is every bearing's first finding in
    # priority order, its second every bearing's second, and so on. A bearing that has run
    # out contributes None, which is skipped. One linear walk, no loop inside a loop.
    dealt = chain.from_iterable(zip_longest(*(buckets[n] for n in names)))
    return [f for f in dealt if f is not None][:max(cap, 0)]


def build_check_prompt(finding: dict) -> str:
    """The adversarial brief for one finding.

    Mirrors the converge-fleet template's verify prompt, including its default-to-negative
    stance and its enumerated failure modes, so a Pool-B check and a Pool-A check are asking
    the same question and their verdicts are comparable.
    """
    today = _dt.date.today().isoformat()
    this_year = today[:4]
    # A bearing this module could not parse is the MODULE's gap, never evidence against the
    # finding. Showing "unknown" to the judge invites it to refute a claim for failing to
    # support a label that this code destroyed. Measured in run wf_1f710b7b-fc7: the sole
    # REFUTED verdict reasoned "provides no evidence about the 'unknown' bearing", and WEAK
    # verdicts cited it too. So an unknown bearing is withheld, and the clause asking the
    # judge to check the quote against it is dropped with it.
    shown = {k: v for k, v in finding.items() if not (k == "bearing" and v == "unknown")}
    bearing_clause = (
        "on whether the quote actually supports the stated bearing, and "
        if shown.get("bearing")
        else ""
    )
    return f"""You are an adversarial verifier. You did NOT write the finding below and your
job is to disagree with it if the evidence disagrees with it.

FINDING:
{json.dumps(shown, indent=1)}

Judge whether this finding is true AS STATED. The failure modes you are hunting:
- a doc, comment, plan or rule describing something ASPIRATIONAL, reported as BUILT;
- a code path that exists but has NO caller, so it never runs;
- a config present in a repo but NOT loaded by the live process;
- a same-named symbol from a different module (note: `LlamaEngine` in lib/hal_llm_adapter.py
  is an alias for the CLOUD adapter, not a local model — a finding that reads it as local
  inference is wrong);
- a claim about a remote host that was actually read off local code, or vice versa;
- a file-mtime or log gap presented as proof of data loss when it is equally consistent with
  nothing having been sent in that window;
- a cursor, allowlist or gate file that is WRITTEN but never READ, so it looks like
  protection and provides none;
- a scope claim wider than the evidence given for it.

You cannot open the repository. Judge on internal coherence, {bearing_clause}on whether the
claim's scope exceeds its quote.

USE THE THREE VERDICTS AS FOLLOWS. This matters more than anything else in this prompt:
- REFUTED requires POSITIVE evidence the claim is WRONG — the quote says something different
  from the claim, the scope plainly exceeds the quote, or the reasoning contradicts itself.
- WEAK is the answer when you simply cannot see enough. "I would need to read the file",
  "this depends on whether it has callers", "the quote alone does not show usage" are ALL
  WEAK. They are not refutations.
- CONFIRMED means the quote plainly supports the claim as stated. Never say CONFIRMED for a
  claim you merely find plausible.

Absence of evidence is NOT evidence of absence: returning REFUTED because the quote is short
or because you lack context is a FALSE refutation, and it is worse than WEAK because a reader
will treat it as a finding having been checked and rejected.

TODAY'S DATE IS {today}. Dates in {this_year} and the recent past are CURRENT, not fabricated.
Do not treat a timestamp later than your training data as evidence of forgery — your knowledge
of the calendar is not evidence about this repository.

Reply with ONLY a JSON object, no prose around it:
{{"verdict": "CONFIRMED" | "WEAK" | "REFUTED", "reasoning": "one or two sentences"}}"""


def parse_verdict(text: object) -> dict:
    """Parse a checker reply into a verdict record.

    Anything that cannot be parsed into one of the three checker verdicts becomes UNCHECKED.
    This is the load-bearing direction: an unparseable reply must never read as CONFIRMED,
    because that would convert a broken checker into apparent evidence.
    """
    if not isinstance(text, str) or not text.strip():
        return {"verdict": VERDICT_UNCHECKED, "reasoning": "checker returned no output"}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"verdict": VERDICT_UNCHECKED, "reasoning": "checker output contained no JSON object"}
    try:
        obj = json.loads(match.group(0))
    except (ValueError, TypeError):
        return {"verdict": VERDICT_UNCHECKED, "reasoning": "checker output was not valid JSON"}
    if not isinstance(obj, dict):
        return {"verdict": VERDICT_UNCHECKED, "reasoning": "checker output was not a JSON object"}
    verdict = str(obj.get("verdict", "")).strip().upper()
    if verdict not in _CHECKER_VERDICTS:
        return {"verdict": VERDICT_UNCHECKED, "reasoning": f"checker returned unknown verdict {verdict!r}"}
    return {"verdict": verdict, "reasoning": str(obj.get("reasoning", ""))[:600]}


def _redact(text: str) -> str:
    """Shield text before it leaves the machine. Raises on failure — fail closed.

    Uses the llm_egress profile, which `lib/redaction_shield.py` documents as the profile for
    exactly this case. A redaction failure must abort rather than send, so this deliberately
    does not swallow the exception.
    """
    from redaction_shield import PROFILE_LLM_EGRESS, Shield, load_denylist  # noqa: E402

    return Shield(load_denylist(), profile=PROFILE_LLM_EGRESS).redact(text).text


def _poolb_engine() -> Callable[[str], str]:
    """Cloud Pool-B caller. Content sent through this LEAVES THE MACHINE."""
    from hal_llm_adapter import HALLLMAdapter  # noqa: E402

    adapter = HALLLMAdapter().connect()

    def call(prompt: str) -> str:
        return adapter.generate(_redact(prompt), max_tokens=512, temperature=0.0)

    return call


def _local_engine(model: str = DEFAULT_LOCAL_MODEL, timeout_s: float = 120.0) -> Callable[[str], str]:
    """Local ollama caller. Nothing leaves the machine.

    Redaction is not applied here: there is no egress to shield against, and redacting would
    degrade the checker's input for no privacy gain.
    """

    def call(prompt: str) -> str:
        payload = json.dumps(
            {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.0}}
        ).encode()
        req = urllib.request.Request(
            OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode()).get("response", "")

    return call


#: Prompt for the reproducibility smoke test. Long enough that the reply has several
#: near-ties in it -- a one-word answer is trivially stable even on a server that is
#: not, which would make the probe worse than useless by reporting false calm.
_DETERMINISM_PROBE = (
    "List exactly five distinct risks of relying on automated code review. "
    "One short sentence each, numbered."
)


def probe_determinism(engine: Callable[[str], str]) -> Optional[bool]:
    """Ask the same question twice and report whether the answers match.

    Measured 2026-09-17 (GRIP#6142): verdicts from the local route were reproducible
    only 56% of the time pairwise, with temperature already 0.0 on every call. The
    cause was the serving config, not the sampler -- an 8-bit quantised KV cache makes
    the logits vary between requests, and a verdict token is decided by a near-tie
    between two words, so the tie flips. Re-serving the same model with an f16 cache
    took pairwise stability from 56% to 97%.

    This probes the PROPERTY (do identical inputs give identical outputs) rather than
    the proxy (what cache type is configured), because the cache type is not visible
    from the client and a future cause of drift would not be either.

    THE ASYMMETRY MATTERS AND IS NOT A DEFECT: ``False`` is trustworthy -- two
    identical requests genuinely disagreed, so this server cannot give reproducible
    verdicts. ``True`` is weak evidence, because one prompt sampled twice can easily
    miss instability that shows up across forty. Treat a pass as "no problem detected",
    never as "reproducible".

    Returns None when the probe could not run at all, which is not evidence either way.
    """
    try:
        first = engine(_DETERMINISM_PROBE)
        second = engine(_DETERMINISM_PROBE)
    except Exception:
        return None
    if not first or not second:
        return None
    return first == second


def make_engine(route: str, *, model: Optional[str] = None) -> Callable[[str], str]:
    """Resolve a route name to a caller. Unknown routes raise rather than defaulting."""
    if route == "poolb":
        return _poolb_engine()
    if route == "local":
        return _local_engine(model or DEFAULT_LOCAL_MODEL)
    raise ValueError(f"unknown route {route!r}; expected 'poolb' or 'local'")


def _check_one(finding: dict, engine: Callable[[str], str]) -> dict:
    """One finding with its `check` attached. A checker that raises yields UNCHECKED."""
    try:
        check = parse_verdict(engine(build_check_prompt(finding)))
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
        check = {
            "verdict": VERDICT_UNCHECKED,
            "reasoning": f"checker failed: {type(exc).__name__}: {exc}"[:400],
        }
    return {**finding, "check": check}


def verify_findings(
    findings: Iterable[dict],
    *,
    engine: Callable[[str], str],
    cap: int = 40,
    workers: int = 1,
) -> list[dict]:
    """Adversarially check findings, returning each with a `check` record attached.

    Every returned finding carries a check. A finding whose checker raised is marked
    UNCHECKED with the exception text, never dropped and never confirmed.

    `workers` above 1 runs that many checks at once in a thread pool (GRIP#6976). Results
    keep the selection order either way, so a re-run lists the same findings in the same
    order. The local engine opens a fresh HTTP request per call, and the Pool-B engine's
    adapter keeps no state of its own between calls. The default stays 1, so a caller that
    does not ask for a pool checks one finding at a time exactly as before.
    """
    if workers < 1:
        raise ValueError(f"workers must be at least 1, got {workers}")
    selected = prioritise(dedup_findings(findings), cap=cap)
    if workers == 1:
        return [_check_one(f, engine) for f in selected]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda f: _check_one(f, engine), selected))


def summarise(verified: Iterable[dict]) -> dict:
    """Counts by verdict and by bearing, plus the survivor set size."""
    verified = list(verified)
    by_verdict: dict[str, int] = {}
    by_bearing: dict[str, int] = {}
    for f in verified:
        v = (f.get("check") or {}).get("verdict", VERDICT_UNCHECKED)
        by_verdict[v] = by_verdict.get(v, 0) + 1
        b = f.get("bearing", "unknown")
        by_bearing[b] = by_bearing.get(b, 0) + 1
    return {
        "total": len(verified),
        "by_verdict": by_verdict,
        "by_bearing": by_bearing,
        "confirmed": by_verdict.get(VERDICT_CONFIRMED, 0),
    }


def _parse_args(argv: Optional[list]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Pool-B adversarial verification of fleet findings")
    ap.add_argument("findings", help="JSON file with a top-level 'findings' array, or - for stdin")
    ap.add_argument("--route", default="poolb", choices=("poolb", "local"),
                    help="poolb sends to the cloud (redacted, fails closed); local uses ollama")
    ap.add_argument("--model", default=None, help="model override for --route local")
    ap.add_argument("--cap", type=int, default=40, help="max findings to verify")
    ap.add_argument("--workers", type=int, default=1,
                    help="checks to run at once (default 1, one after another)")
    ap.add_argument("--out", default=None, help="write verified findings here (default: stdout)")
    ap.add_argument("--skip-probe", action="store_true",
                    help="skip the reproducibility smoke test (2 extra model calls)")
    return ap.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    ns = _parse_args(argv)

    raw = sys.stdin.read() if ns.findings == "-" else Path(ns.findings).read_text()
    doc = json.loads(raw)
    findings = doc["findings"] if isinstance(doc, dict) else doc

    engine = make_engine(ns.route, model=ns.model)

    # Reproducibility smoke test, before spending the budget on verdicts nobody can
    # reproduce. AGORA flagged that this probe shipped with zero callers, which was
    # correct -- it existed and detected nothing. WARN only: it reports, never gates,
    # because a checker able to stop its own verification run is the deadlock shape
    # gate-resolution-escape-hatch.md forbids.
    stable: Optional[bool] = None
    if not ns.skip_probe:
        stable = probe_determinism(engine)
        if stable is False:
            print(
                "WARNING: this server gave two different answers to one identical "
                "question, so its verdicts will not reproduce. On ollama set "
                "OLLAMA_KV_CACHE_TYPE=f16 and restart it; measured 2026-09-17, a "
                "q8_0 cache agreed on 22 of 39 verdicts where f16 agreed on 38. "
                "Continuing anyway -- treat every REFUTED below as provisional.",
                file=sys.stderr,
            )

    verified = verify_findings(findings, engine=engine, cap=ns.cap, workers=ns.workers)
    payload = {
        "route": ns.route,
        # Whether the SERVER was reproducible for this run, so a later reader of this
        # file can tell a settled verdict from a provisional one without re-running.
        # true = no instability detected (weak evidence), false = two identical
        # requests disagreed (trustworthy), null = not probed or not measurable.
        "reproducible": stable,
        "summary": summarise(verified),
        "findings": verified,
    }
    text = json.dumps(payload, indent=1)
    if ns.out:
        Path(ns.out).write_text(text)
        print(json.dumps(payload["summary"], indent=1))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
