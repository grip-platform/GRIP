#!/usr/bin/env python3
"""The nightly model watch: what each provider serves today, against what the catalogues carry.

This is the deterministic floor (L1) of the watch, in the shape of
rules/llm-augmentation-pattern.md: no model is called and nothing is guessed. Each
provider's own ``GET /models`` is the source, so every id reported as new is SERVED
to this account's key -- the second of the three claims skills/dogfood-domain keeps
apart (documented, served, working), settled for free before anyone writes a row.

What it cannot see, said plainly: a deprecation the vendor announces while still
serving the id (gemini-3.5-flash on 2026-10-08 stayed in the list), and the facts a
catalogue row needs beyond the id (window, price, modalities, the thinking fields).
Those belong to the L2 pass, which reads the vendor's pages on Pool B and is a
separate change (H836 in learning/hypotheses.jsonl tracks the first PR it opens).

Origin: 2026-10-18, the Gemini 4 Argon ultraplan. The monthly job this replaces,
bin/hal-provider-refresh, called a lib/workflow_runner.py no commit ever added, so
it never ran (docs/measurements/2026-10-18-gemini-4-argon-onboarding.md, defect 1).

Usage:
    bin/grip-py lib/model_watch.py [--hal-root ~/.hal] [--state-dir state/model-watch]
                                   [--markdown issue.md] [--json] [--provider NAME ...]
                                   [--probe-suspects]

The last line printed is machine-readable: ``MODEL_WATCH new=N chat=C gone=M named=W
providers=P skipped=K``. ``named`` counts an id named in a routing chain, council roster,
catalogue row or `models:` list that the provider's own served list does not contain --
offline and free, part of every run (GRIP#9375). ``--probe-suspects`` is the opt-in second
half: one tiny chat call per such id, plus a served and a nonsense control per provider,
never OpenAI or Anthropic (subscription-only) -- never run by the nightly default.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from grip_harness_model import _parse_providers_yaml, service_from  # noqa: E402
from provider_secret import resolve_provider_key  # noqa: E402

GRIP_ROOT = _LIB.parent

# Providers the watch does not list, each with its reason. The report carries the
# reason, so a provider that is never watched is a stated fact, not a silent gap.
POLICY_SKIPS: dict[str, str] = {
    "openai": ("rules/gpt-on-the-subscription-never-credits.md: the OpenAI key is not "
               "used without GRIP_CODEX_ALLOW_PAID=1, and Codex has no list endpoint"),
    "anthropic": ("rules/claude-cap-moves-to-the-next-subscription.md: Claude is reached "
                  "on the subscription; its /v1/models takes the vendor's own headers and "
                  "HAL's scripts/probe_models.py lists it on the CCH path"),
    "openrouter": ("an aggregator: its list is the whole market, not a roster to mirror; "
                   "GRIP's openrouter rows are chosen by hand"),
    "grip-vps": "GRIP's own relay, not a vendor",
}

_ADDRESS = re.compile(r"^\s*-\s*address:\s*([A-Za-z0-9_.-]+)/(\S+)\s*$")

# Ids that are not text-generation models, or are moving aliases, judged by name.
# A heuristic, said so: the first live run (2026-10-18) listed 489 unlisted ids
# across 12 providers and most were speech, image, video, embedding, live and
# alias ids. The JSON keeps every id; only the issue's checklists are filtered.
NOT_CHAT = re.compile(
    r"tts|speech|audio|transcri|whisper|embed|rerank|moderation|image|imagen|"
    r"nano-banana|veo|lyria|video|live|realtime|robotics|deep-research|antigravity|"
    r"computer-use|customtools|\baqa\b|-latest$|guard|ocr|dall-e|sora", re.IGNORECASE)
CHECKLIST_CAP = 12        # full checklists per issue; the rest are named in a line per provider
ISSUE_BODY_LIMIT = 60_000  # GitHub refuses an issue body over 65,536 characters


def is_chat_candidate(model_id: str) -> bool:
    """True unless the id names a non-text modality, a helper, or a moving alias."""
    return NOT_CHAT.search(model_id) is None

CHECKLIST = (
    "Documented: the vendor's model page, pricing page and changelog, quoted "
    "(id, window, output limit, cutoff, prices and any dated change)",
    "Catalogue row in config/model_catalog.yaml of GRIP and HAL, placed after the "
    "family's current pin so no first-row resolver moves",
    "Provider-list entry, LAST, in config/providers.yaml (GRIP; HAL and its "
    "providers.windows.yaml)",
    "A price row of its OWN in both lib/hal/cost_tracker.py and HAL's "
    "config/model_prices.yaml; a dated change goes in _PRICE_SCHEDULE",
    "A display rule, and the previous generation's guards audited (endpoint "
    "version, thinking fields, output caps)",
    "Parity tests red first, then green (tests/test_model_onboarding_parity.py; "
    "HAL's tests/test_gemini_catalogue_parity.py or its sibling for the family)",
    "One controlled probe beside a known-good id and a nonsense id, the three "
    "statuses recorded verbatim, after the price row exists",
    "No default, routing chain, flagship pin or council seat moves without its own "
    "dated decision and test",
)


@dataclass
class ProviderWatch:
    name: str
    base_url: str = ""
    served: list[str] = field(default_factory=list)
    listed: dict[str, list[str]] = field(default_factory=dict)
    catalogued: dict[str, list[str]] = field(default_factory=dict)
    new: dict[str, list[str]] = field(default_factory=dict)
    gone: dict[str, list[str]] = field(default_factory=dict)
    named_but_not_listed: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    skipped: Optional[str] = None
    error: Optional[str] = None


@dataclass
class Report:
    date: str
    providers: list[ProviderWatch]

    def to_dict(self) -> dict:
        return {"date": self.date, "providers": [asdict(p) for p in self.providers]}

    def new_count(self) -> int:
        """Distinct served ids with no row in at least one repository."""
        return sum(len(ids) for ids in _new_ids(self).values())

    def gone_count(self) -> int:
        return sum(len(ids) for p in self.providers for ids in p.gone.values())

    def skipped_count(self) -> int:
        return sum(1 for p in self.providers if p.skipped)

    def chat_count(self) -> int:
        """Distinct new ids that look like text models, across repositories."""
        return sum(1 for ids in _new_ids(self).values() for model_id in ids if is_chat_candidate(model_id))

    def named_count(self) -> int:
        """Distinct ids named in a routing chain, council roster, catalogue row or `models:`
        list that the provider's own served list does not contain, deduped across repos."""
        return sum(len(ids) for ids in _named_unserved(self).values())

    def digest(self) -> str:
        return (f"MODEL_WATCH new={self.new_count()} chat={self.chat_count()} gone={self.gone_count()} "
                f"named={self.named_count()} providers={len(self.providers)} "
                f"skipped={self.skipped_count()}")


def normalise_id(raw: str) -> str:
    """'models/gemini-4-argon' -> 'gemini-4-argon': Google's list spells ids with a prefix."""
    return raw[len("models/"):] if raw.startswith("models/") else raw


def read_key(service: str, *, env=None, platform: Optional[str] = None) -> Optional[str]:
    """The API key for ``service``, or None, through lib/provider_secret's ladder.

    The provider's env var, then ``GRIP_<PROVIDER>_API_KEY``, then the macOS
    keychain (darwin only). On Linux, the VPS, a systemd unit supplies the env
    vars from HAL's secrets file, and no other process's environment is read:
    the resolver's /proc rung stays off. Never printed by anything here.
    """
    return resolve_provider_key(service, env=env, platform=platform)


def fetch_served(base_url: str, key: str, timeout: float = 30.0) -> list[str]:
    """``GET {base_url}/models`` on an OpenAI-compatible provider: the raw ids served to this key.

    The key travels in the Authorization header, never in the URL, so an error
    message that quotes the URL cannot leak it. Ids are normalised by the caller.
    """
    request = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {key}", "User-Agent": "GRIP model-watch"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return sorted({str(row["id"]) for row in rows or [] if isinstance(row, dict) and row.get("id")})


def _split_ref(ref: str) -> Optional[tuple[str, str]]:
    """'xai/grok-4.3' -> ('xai', 'grok-4.3'); None for a ref with no '/' -- a bare
    routing mode name (routing.default: 'fallback') names no provider, not a model."""
    if "/" not in ref:
        return None
    provider, _, model_id = ref.partition("/")
    return (provider, model_id) if provider and model_id else None


def _inline_list(text: str) -> list[str]:
    """'[a, b, c]' -> ['a', 'b', 'c']."""
    inner = text.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    return [item.strip() for item in inner.split(",") if item.strip()]


def _routing_block_lines(providers_yaml_text: str) -> list[str]:
    """Every line under the top-level ``routing:`` key, still indented. [] if absent."""
    lines = providers_yaml_text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.rstrip() == "routing:"]
    if not starts:
        return []
    block: list[str] = []
    for line in lines[starts[0] + 1:]:
        if line.strip() and not line.startswith((" ", "\t")):
            break  # the next top-level key
        block.append(line)
    return block


def _routing_refs(providers_yaml_text: str) -> list[tuple[str, str, str]]:
    """[(provider, model_id, source), ...] for every provider/model ref in the routing:
    block -- default_model, fallback_chain, quality_cost, tier_chains, council -- however
    each is written (inline '[a, b]' or one '- item' per line). Two levels deep only
    (routing.key, or routing.key.subkey for quality_cost/tier_chains): neither GRIP's nor
    HAL's providers.yaml nests any deeper today.
    """
    refs: list[tuple[str, str, str]] = []

    def add(raw_items: list[str], source: str) -> None:
        for raw in raw_items:
            split = _split_ref(raw)
            if split:
                refs.append((split[0], split[1], source))

    open_key: Optional[str] = None
    for raw_line in _routing_block_lines(providers_yaml_text):
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent <= 2:
            key, _, val = stripped.partition(":")
            key, val = key.strip(), val.strip()
            if val.startswith("["):
                add(_inline_list(val), f"routing.{key}")
                open_key = None
            elif val:
                add([val], f"routing.{key}")
                open_key = None
            else:
                open_key = key
            continue

        if indent == 4 and open_key:
            if stripped.startswith("- "):
                add([stripped[2:].strip()], f"routing.{open_key}")
            else:
                subkey, _, val = stripped.partition(":")
                subkey, val = subkey.strip(), val.strip()
                items = _inline_list(val) if val.startswith("[") else ([val] if val else [])
                add(items, f"routing.{open_key}.{subkey}")

    return refs


def _named_ids(providers: dict[str, dict], providers_text: str,
               catalogue: dict[str, list[str]]) -> dict[str, dict[str, list[str]]]:
    """{provider: {model_id: [sources]}} -- every id this repo names anywhere: a
    provider's own `models:` list, the routing: block, and model_catalog.yaml's
    `address:` rows. Each source names the file and key, e.g.
    'config/providers.yaml:routing.tier_chains.premium' (GRIP#9375)."""
    out: dict[str, dict[str, list[str]]] = {}

    def add(provider: str, model_id: str, source: str) -> None:
        out.setdefault(provider, {}).setdefault(model_id, []).append(source)

    for name, prov in providers.items():
        models = prov.get("models")
        if isinstance(models, list):  # 'auto' (local providers) names no ids
            for model_id in models:
                add(name, model_id, "config/providers.yaml:models")

    for provider, model_id, source in _routing_refs(providers_text):
        add(provider, model_id, f"config/providers.yaml:{source}")

    for provider, ids in catalogue.items():
        for model_id in ids:
            add(provider, model_id, "config/model_catalog.yaml:address")

    return out


def load_repo(root: Path) -> dict:
    """A repository's providers, catalogue and every id its configs name anywhere:
    {'providers': {name: row}, 'catalogue': {provider: ids}, 'named': {provider: {id: [sources]}}}."""
    providers: dict[str, dict] = {}
    providers_text = ""
    providers_path = root / "config" / "providers.yaml"
    if providers_path.exists():
        providers_text = providers_path.read_text(encoding="utf-8")
        providers = {p["name"]: p for p in _parse_providers_yaml(providers_path) if p.get("name")}
    catalogue: dict[str, list[str]] = {}
    catalogue_path = root / "config" / "model_catalog.yaml"
    if catalogue_path.exists():
        for line in catalogue_path.read_text(encoding="utf-8").splitlines():
            found = _ADDRESS.match(line)
            if found:
                catalogue.setdefault(found.group(1), []).append(found.group(2))
    named = _named_ids(providers, providers_text, catalogue)
    return {"providers": providers, "catalogue": catalogue, "named": named}


def _skip_reason(prov: dict) -> Optional[str]:
    name = prov.get("name", "")
    if name in POLICY_SKIPS:
        return POLICY_SKIPS[name]
    if prov.get("type") != "cloud":
        return "a local provider; there is no vendor list to read"
    if not str(prov.get("api_key_source") or "").startswith("keychain:"):
        return "no keychain key source in providers.yaml"
    if not prov.get("base_url"):
        return "no base_url in providers.yaml"
    return None


def _unserved(named_for_provider: dict[str, list[str]], served: set[str]) -> dict[str, list[str]]:
    """Every named id NOT in *served*, each with its sources de-duplicated and sorted."""
    return {model_id: sorted(set(sources)) for model_id, sources in named_for_provider.items()
            if model_id not in served}


def watch_provider(name: str, prov: dict, repos: dict[str, dict],
                   fetcher: Callable[[str, str], list[str]],
                   key_reader: Callable[[str], Optional[str]]) -> ProviderWatch:
    """One provider against every repository: served, listed, catalogued, new, gone,
    named_but_not_listed."""
    result = ProviderWatch(name=name, base_url=str(prov.get("base_url") or ""))
    for repo, data in repos.items():
        result.listed[repo] = list(data["providers"].get(name, {}).get("models", []))
        result.catalogued[repo] = list(data["catalogue"].get(name, []))
    result.skipped = _skip_reason(prov)
    if result.skipped:
        return result
    service = service_from(prov["api_key_source"])
    key = key_reader(service)
    if not key:
        result.skipped = f"no key for {service} (provider env var or macOS keychain)"
        return result
    try:
        result.served = sorted({normalise_id(i) for i in fetcher(result.base_url, key)})
    except Exception as exc:  # one provider's failure is reported, never hides the rest
        result.error = f"{type(exc).__name__}: {str(exc)[:160]}"
        return result
    served = set(result.served)
    for repo, data in repos.items():
        known = set(result.listed[repo]) | set(result.catalogued[repo])
        result.new[repo] = sorted(served - known)
        result.gone[repo] = sorted(set(result.listed[repo]) - served)
        result.named_but_not_listed[repo] = _unserved(data["named"].get(name, {}), served)
    return result


def _load_repos(grip_root: Path, hal_root: Optional[Path]) -> dict[str, dict]:
    repos = {"GRIP": load_repo(grip_root)}
    if hal_root is not None and (hal_root / "config").exists():
        repos["HAL"] = load_repo(hal_root)
    return repos


def _merged_provider_names(repos: dict[str, dict]) -> list[str]:
    return sorted({name for data in repos.values() for name in data["providers"]})


def _provider_row(name: str, repos: dict[str, dict]) -> dict:
    """One authoritative provider dict for *name* -- GRIP's row wins when both list it."""
    return next(d["providers"][name] for d in repos.values() if name in d["providers"])


def provider_lookup(grip_root: Path, hal_root: Optional[Path] = None) -> dict[str, dict]:
    """{name: provider-dict}, the same per-provider row `watch` resolves -- exposed so
    --probe-suspects can read base_url/api_key_source without re-deriving it."""
    repos = _load_repos(grip_root, hal_root)
    return {name: _provider_row(name, repos) for name in _merged_provider_names(repos)}


def watch(grip_root: Path, hal_root: Optional[Path] = None,
          fetcher: Callable[[str, str], list[str]] = fetch_served,
          key_reader: Callable[[str], Optional[str]] = read_key,
          only: Optional[set[str]] = None) -> Report:
    """Every provider named by either repository, in name order."""
    repos = _load_repos(grip_root, hal_root)
    watched = []
    for name in _merged_provider_names(repos):
        if only and name not in only:
            continue
        watched.append(watch_provider(name, _provider_row(name, repos), repos, fetcher, key_reader))
    return Report(date=datetime.now(timezone.utc).date().isoformat(), providers=watched)


def _new_ids(report: Report) -> dict[str, dict[str, list[str]]]:
    """{provider: {id: [repos lacking it]}}."""
    out: dict[str, dict[str, list[str]]] = {}
    for p in report.providers:
        for repo, ids in p.new.items():
            for model_id in ids:
                out.setdefault(p.name, {}).setdefault(model_id, []).append(repo)
    return out


def _named_unserved(report: Report) -> dict[str, dict[str, dict[str, list[str]]]]:
    """{provider: {id: {repo: [sources]}}} -- every named-but-unserved id, deduped across
    GRIP and HAL (an id missing everywhere is counted once, with each repo's own sources
    kept so a report can say exactly where it lives)."""
    out: dict[str, dict[str, dict[str, list[str]]]] = {}
    for p in report.providers:
        for repo, ids in p.named_but_not_listed.items():
            for model_id, sources in ids.items():
                out.setdefault(p.name, {}).setdefault(model_id, {})[repo] = sources
    return out


def _table_rows(report: Report) -> list[str]:
    """The per-provider summary table: header + one row per watched provider."""
    header = ["| provider | served | new (GRIP) | new (HAL) | gone (GRIP) | gone (HAL) | "
              "named (GRIP) | named (HAL) | note |",
              "|---|---|---|---|---|---|---|---|---|"]
    rows = [f"| {p.name} | {len(p.served)} | {len(p.new.get('GRIP', []))} | "
            f"{len(p.new.get('HAL', []))} | {len(p.gone.get('GRIP', []))} | "
            f"{len(p.gone.get('HAL', []))} | {len(p.named_but_not_listed.get('GRIP', {}))} | "
            f"{len(p.named_but_not_listed.get('HAL', {}))} | {p.skipped or p.error or ''} |"
            for p in report.providers]
    return header + rows


def _new_ids_section(new_ids: dict[str, dict[str, list[str]]]) -> list[str]:
    """One checklist per served-but-unknown text model, budgeted to CHECKLIST_CAP per issue."""
    if not new_ids:
        return []
    lines = ["", "## Served text models with no row (one checklist each; skills/dogfood-domain)",
             "", "Which ids count as text models is judged by NAME (lib/model_watch.py NOT_CHAT); "
             "the rest are counted below and kept in full in state/model-watch/<date>.json."]
    budget = CHECKLIST_CAP
    for provider, ids in new_ids.items():
        chat = [(m, r) for m, r in ids.items() if is_chat_candidate(m)]
        detailed, named = chat[:budget], chat[budget:]
        budget -= len(detailed)
        for model_id, repos in detailed:
            lines += ["", f"### `{provider}/{model_id}` (missing in {', '.join(repos)})"]
            lines += [f"- [ ] {item}" for item in CHECKLIST]
        if named:
            lines += ["", f"_{provider}: {len(named)} more text-model ids, the same checklist each: "
                      + ", ".join(f"`{m}`" for m, _ in named) + "_"]
        other = [m for m in ids if not is_chat_candidate(m)]
        if other:
            lines += ["", f"_{provider}: {len(other)} other served ids (speech, image, video, "
                      f"embedding, live or alias) not listed: {', '.join(other[:12])}"
                      f"{', ...' if len(other) > 12 else ''}_"]
    return lines


def _gone_section(report: Report) -> list[str]:
    """Ids `models:` lists but the vendor's own /models no longer serves."""
    gone = [(p.name, repo, ids) for p in report.providers for repo, ids in p.gone.items() if ids]
    if not gone:
        return []
    lines = ["", "## Listed here, absent from the vendor's list",
             "", "A hint, not a verdict: a vendor's list can be narrower than what it accepts "
             "(DeepSeek lists two alias ids while billing the v4 names). Routing is never "
             "repointed by this job."]
    lines += [f"- {name} ({repo}): {', '.join(ids)}" for name, repo, ids in gone]
    return lines


def _named_section(report: Report) -> list[str]:
    """Ids named in a routing chain, council roster, catalogue row or `models:` list that
    the provider's own served list does not contain -- the file:key provenance GRIP#9375
    asked for, so the fix is one edit away instead of a fresh grep."""
    named = _named_unserved(report)
    if not named:
        return []
    lines = ["", "## Named in routing, council, catalogue or `models:`, not in the vendor's list",
             "", "Offline and free, part of every run: every id GRIP's or HAL's config names "
             "anywhere, checked against this run's own `/models` listing. A hint the id retired "
             "or a chain/roster/row still points at it -- `--probe-suspects` tells alias from gone."]
    for provider in sorted(named):
        for model_id in sorted(named[provider]):
            by_repo = named[provider][model_id]
            parts = [f"{repo} {', '.join(sorted(set(sources)))}" for repo, sources in sorted(by_repo.items())]
            lines.append(f"- `{provider}/{model_id}`: " + "; ".join(parts))
    return lines


def render_markdown(report: Report) -> str:
    """The standing GitHub issue's body: the table, then a checklist per new id."""
    lines = [f"# Model watch, {report.date}",
             "",
             "Each provider's own `GET /models`, read with this account's key, against the "
             "`models:` lists and catalogue rows of GRIP and HAL. An id listed here is served; "
             "nothing is guessed. Written by `bin/grip-model-watch` (`lib/model_watch.py`).",
             ""]
    lines += _table_rows(report)
    lines += _new_ids_section(_new_ids(report))
    lines += _gone_section(report)
    lines += _named_section(report)
    lines += ["", f"_{report.digest()}_"]
    body = "\n".join(lines) + "\n"
    if len(body) > ISSUE_BODY_LIMIT:
        body = (body[:ISSUE_BODY_LIMIT - 200].rsplit("\n", 1)[0]
                + "\n\n_Cut here to fit GitHub's issue-body limit; the full report is "
                f"state/model-watch/{report.date}.json._\n")
    return body


def write_state(report: Report, state_dir: Path) -> Path:
    """Write ``<date>.json`` and ``latest.json``; return the dated path."""
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
    dated = state_dir / f"{report.date}.json"
    dated.write_text(payload, encoding="utf-8")
    (state_dir / "latest.json").write_text(payload, encoding="utf-8")
    return dated


# --- --probe-suspects: the opt-in second half (GRIP#9375) ------------------------------
#
# Part 1 above is offline and free. This part spends real provider calls, so it never
# runs unless asked: one tiny chat completion per named-but-unserved id, plus a served
# and a nonsense control per probed provider, classified into the three verdicts the
# issue names (plus two honest extras for a call that could not be classified at all).

# rules/gpt-on-the-subscription-never-credits.md + rules/claude-cap-moves-to-the-next-
# subscription.md: these two run on a subscription, never a per-token key, so a probe
# call is never sent to them (also already true today because watch_provider's own
# POLICY_SKIPS returns before named_but_not_listed is ever computed for either).
_PROBE_NEVER = frozenset({"openai", "anthropic"})
_PROBE_MAX_TOKENS = 300  # a few hundred, so a thinking model's hidden reasoning still
                         # leaves room for an actual answer in the completion
_PROBE_NONSENSE_SUFFIX = "-grip-watch-nonsense-control"


@dataclass
class ProbeResult:
    provider: str
    model_id: str
    role: str                       # "suspect" | "served_control" | "nonsense_control"
    verdict: str                    # see classify_probe
    served_as: Optional[str] = None  # the response's own 'model' field, when one came back


def probe_chat(base_url: str, key: str, model_id: str, timeout: float = 30.0,
               max_tokens: int = _PROBE_MAX_TOKENS) -> tuple[int, Optional[str], Optional[str]]:
    """POST one tiny chat completion to an OpenAI-compatible {base_url}/chat/completions.

    Returns (http_status, the response's own 'model' field or None, an error message or
    None). The key travels in the Authorization header only -- never returned, printed
    or logged by this function or any caller of it.
    """
    body = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply with one word: ok"}],
        "max_tokens": max_tokens,
    }).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "GRIP model-watch probe"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
            return response.status, payload.get("model"), None
    except urllib.error.HTTPError as exc:
        return exc.code, None, None
    except Exception as exc:  # connection error, timeout, bad JSON -- a row, never a crash
        return 0, None, f"{type(exc).__name__}: {str(exc)[:160]}"


def classify_probe(model_id: str, status: int, served_as: Optional[str], error: Optional[str]) -> str:
    """The three verdicts GRIP#9375 names, plus 'error: ...' for a call that returned no
    HTTP status at all (a connection failure, not a vendor answer)."""
    if error is not None:
        return f"error: {error}"
    if status != 200:
        return f"not served (HTTP {status})"
    if served_as and served_as != model_id:
        return f"alias of {served_as}"
    return "answers as itself"


def plan_probes(report: Report, providers: dict[str, dict],
                skip: frozenset = _PROBE_NEVER) -> list[tuple[str, str, str]]:
    """[(provider, model_id, role), ...] -- every call --probe-suspects would make: one
    per id in named_but_not_listed, plus a served control and a nonsense control for each
    provider that has at least one suspect. Pure and network-free, so the count can be
    printed before any call is sent. Never plans a call for a provider in *skip*, or one
    *providers* does not carry (no base_url/key to call with)."""
    plan: list[tuple[str, str, str]] = []
    for p in report.providers:
        if p.name in skip or p.name not in providers:
            continue
        suspects = sorted({model_id for ids in p.named_but_not_listed.values() for model_id in ids})
        if not suspects:
            continue
        plan += [(p.name, model_id, "suspect") for model_id in suspects]
        if p.served:
            plan.append((p.name, sorted(p.served)[0], "served_control"))
        plan.append((p.name, f"{p.name}{_PROBE_NONSENSE_SUFFIX}", "nonsense_control"))
    return plan


def run_probes(plan: list[tuple[str, str, str]], providers: dict[str, dict],
               key_reader: Callable[[str], Optional[str]] = read_key,
               prober: Callable[..., tuple] = probe_chat) -> list[ProbeResult]:
    """Execute a plan from plan_probes. One key resolution per provider (cached across
    its own calls), through the same read_key ladder the rest of the watch uses."""
    results: list[ProbeResult] = []
    keys: dict[str, Optional[str]] = {}
    for provider, model_id, role in plan:
        if provider not in keys:
            keys[provider] = key_reader(service_from(providers[provider]["api_key_source"]))
        key = keys[provider]
        if not key:
            results.append(ProbeResult(provider, model_id, role, "skipped: no key"))
            continue
        status, served_as, error = prober(providers[provider]["base_url"], key, model_id)
        results.append(ProbeResult(provider, model_id, role,
                                   classify_probe(model_id, status, served_as, error), served_as))
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--grip-root", type=Path, default=GRIP_ROOT)
    parser.add_argument("--hal-root", type=Path, default=Path.home() / ".hal")
    parser.add_argument("--state-dir", type=Path, default=None,
                        help="write <date>.json and latest.json here")
    parser.add_argument("--markdown", type=Path, default=None, help="write the issue body here")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--provider", action="append", default=[], help="watch only these")
    parser.add_argument("--probe-suspects", action="store_true",
                        help="opt-in, never the nightly default: one tiny chat call per "
                             "named-but-unserved id, plus a served and a nonsense control "
                             "per provider; never OpenAI or Anthropic; prints the call "
                             "count before making any")
    args = parser.parse_args(argv)
    report = watch(args.grip_root, args.hal_root, only=set(args.provider) or None)
    if args.state_dir is not None:
        write_state(report, args.state_dir)
    if args.markdown is not None:
        args.markdown.write_text(render_markdown(report), encoding="utf-8")
    probe_results: list[ProbeResult] = []
    if args.probe_suspects:
        providers = provider_lookup(args.grip_root, args.hal_root)
        plan = plan_probes(report, providers)
        print(f"MODEL_WATCH_PROBE calls={len(plan)}")
        probe_results = run_probes(plan, providers)
        for r in probe_results:
            print(f"PROBE {r.provider}/{r.model_id} {r.role} -> {r.verdict}")
    if args.json:
        payload = report.to_dict()
        if args.probe_suspects:
            payload["probe"] = [asdict(r) for r in probe_results]
        print(json.dumps(payload, indent=2, sort_keys=True))
    print(report.digest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
