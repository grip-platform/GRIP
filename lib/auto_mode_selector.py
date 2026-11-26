#!/usr/bin/env python3
"""Auto Mode Selector — LLM-inferred mode and skill selection.

Uses free external LLM APIs (Gemini Flash, Groq) to infer the optimal
mode stack for any prompt. Replaces keyword matching with genuine
semantic understanding.

Architecture Synthesis Council L4 survived: "use a cheap fast model
to select expensive context" is a genuinely valuable harness feature.

Usage:
    from lib.auto_mode_selector import select_modes
    result = select_modes("build a secure REST API with tests")
    # result.modes = ["code", "security", "testing"]
    # result.skills = ["feature-complete", "vulnerability-detection", ...]
    # result.commands = ["/converge", "/create-pr"]

CLI:
    python3 lib/auto_mode_selector.py "your prompt here"
    python3 lib/auto_mode_selector.py --json "your prompt here"
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path.home() / ".claude"


def _resolve_model(family: str, fallback: str) -> str:
    """Gemini-tier id from HAL's canonical catalogue, fail-safe.

    Wraps the merged lib.hal_model_resolver shim with import-safety so a
    missing HAL clone degrades to the verbatim fallback (the selector's
    own LLM call must still run). Never raises.
    """
    if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from hal_model_resolver import resolve
        return resolve(family, fallback)
    except Exception:
        return fallback


MODES_DIR = CLAUDE_DIR / "modes" / "definitions"
CACHE_PATH = CLAUDE_DIR / "cache" / "mode-selection-cache.json"
METRICS_PATH = CLAUDE_DIR / "learning" / "mode-selection-metrics.jsonl"


@dataclass
class ModeSelection:
    """Result of LLM-inferred mode selection."""
    modes: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    reasoning: str = ""
    budget: int = 0
    model_used: str = ""


def _parse_mode_yaml(content: str) -> tuple[str, str, list[str]]:
    """Single-pass parse of a mode yaml. Returns (description, budget, skills)."""
    desc = ""
    budget = "3000"
    skills: list[str] = []
    for line in content.split("\n"):
        if line.startswith("description:"):
            desc = line.split(":", 1)[1].strip()
        elif line.startswith("token_budget:"):
            budget = line.split(":", 1)[1].strip().lstrip("~")
        elif line.strip().startswith("- ") and "always:" not in line:
            skill = line.strip().lstrip("- ").strip()
            if skill and not any(c in skill for c in ["{", ":", "["]):
                skills.append(skill)
    return desc, budget, skills


def _format_mode_entry(yaml_file: Path) -> str:
    """Render one mode yaml as a single catalogue line, swallowing errors."""
    name = yaml_file.stem.replace("-mode", "")
    try:
        desc, budget, skills = _parse_mode_yaml(yaml_file.read_text())
        return f"- {name} (~{budget}): {desc}. Skills: {', '.join(skills[:5])}"
    except Exception:
        return f"- {name}: (definition error)"


def _load_mode_catalogue() -> str:
    """Load all mode definitions as a compact catalogue string."""
    return "\n".join(_format_mode_entry(f)
                     for f in sorted(MODES_DIR.glob("*-mode.yaml")))


def _build_selector_prompt(user_prompt: str, catalogue: str) -> str:
    """Build the mode selection prompt for the LLM."""
    return (
        "You are a mode selector for GRIP, an AI coding harness. "
        "Given the user's task, select the optimal modes to activate.\n\n"
        f"AVAILABLE MODES:\n{catalogue}\n\n"
        f"USER TASK: {user_prompt}\n\n"
        "Select modes that are DIRECTLY relevant. Fewer high-value modes are "
        "better than many low-value ones. Combined budget should stay under 50000.\n\n"
        "Respond ONLY with valid JSON (no markdown, no explanation):\n"
        '{"modes": ["mode1", "mode2"], "skills": ["skill1", "skill2"], '
        '"commands": ["/cmd1"], "reasoning": "one sentence why"}'
    )


def _parse_selection(raw: str) -> Optional[ModeSelection]:
    """Parse LLM response into ModeSelection."""
    # Strip markdown code blocks if present
    cleaned = re.sub(r'```(?:json)?\s*', '', raw).strip().rstrip('`')
    # Strip thinking blocks
    if "<think>" in cleaned:
        cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL).strip()

    # Try full JSON parse first
    try:
        data = json.loads(cleaned)
        return ModeSelection(
            modes=data.get("modes", []),
            skills=data.get("skills", []),
            commands=data.get("commands", []),
            reasoning=data.get("reasoning", ""),
        )
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: find the outermost { ... } containing "modes"
    brace_depth = 0
    start_idx = None
    for i, ch in enumerate(cleaned):
        if ch == '{':
            if brace_depth == 0:
                start_idx = i
            brace_depth += 1
        elif ch == '}':
            brace_depth -= 1
            if brace_depth == 0 and start_idx is not None:
                candidate = cleaned[start_idx:i + 1]
                if '"modes"' in candidate:
                    try:
                        data = json.loads(candidate)
                        return ModeSelection(
                            modes=data.get("modes", []),
                            skills=data.get("skills", []),
                            commands=data.get("commands", []),
                            reasoning=data.get("reasoning", ""),
                        )
                    except json.JSONDecodeError:
                        pass
                start_idx = None

    return None



# ── Selection Cache (N1 — Council Proposal) ────────────────────────

def _cache_key(prompt: str) -> str:
    """Normalise prompt to cache key: lowercase, strip, collapse whitespace."""
    import hashlib
    normalised = " ".join(prompt.lower().strip().split())
    return hashlib.md5(normalised.encode()).hexdigest()[:16]


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"entries": {}, "modes_hash": ""}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _modes_content_hash() -> str:
    """Hash all mode definition files for cache invalidation."""
    import hashlib
    h = hashlib.md5()
    for f in sorted(MODES_DIR.glob("*-mode.yaml")):
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def _cache_lookup(prompt: str) -> Optional[ModeSelection]:
    """Check cache for a matching prompt. Returns None on miss."""
    cache = _load_cache()
    current_hash = _modes_content_hash()
    if cache.get("modes_hash") != current_hash:
        return None  # Mode definitions changed, invalidate all

    key = _cache_key(prompt)
    entry = cache.get("entries", {}).get(key)
    if entry:
        return ModeSelection(
            modes=entry.get("modes", []),
            skills=entry.get("skills", []),
            commands=entry.get("commands", []),
            reasoning=entry.get("reasoning", "") + " [cached]",
            budget=entry.get("budget", 0),
            model_used="cache",
        )
    return None


def _cache_store(prompt: str, selection: ModeSelection) -> None:
    """Store a mode selection in cache."""
    cache = _load_cache()
    cache["modes_hash"] = _modes_content_hash()
    cache.setdefault("entries", {})[_cache_key(prompt)] = {
        "modes": selection.modes,
        "skills": selection.skills,
        "commands": selection.commands,
        "reasoning": selection.reasoning,
        "budget": selection.budget,
    }
    # Cap cache at 100 entries (LRU-style: just trim oldest)
    entries = cache["entries"]
    if len(entries) > 100:
        keys = list(entries.keys())
        for k in keys[:len(keys) - 100]:
            del entries[k]
    _save_cache(cache)


def _record_metric(prompt: str, selection: ModeSelection, latency: float) -> None:
    """Record mode selection metrics for /evo to consume."""
    from datetime import datetime, timezone
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "prompt_len": len(prompt),
        "modes": selection.modes,
        "model": selection.model_used,
        "budget": selection.budget,
        "latency_ms": round(latency * 1000),
        "cached": selection.model_used == "cache",
    }
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _resolve_provider(prompt: str, provider: str) -> str:
    """N5+N6: Route to cheapest sufficient model."""
    if provider != "auto":
        return provider
    words = prompt.lower().split()
    connectors = {"and", "also", "plus", "then", "while", "both", "across",
                  "integrate", "synthesise", "combine", "compare", "evaluate"}
    domain_signals = sum(1 for w in words if w in connectors)
    return "pro" if domain_signals >= 3 else "gemini"


def _query_llm(provider: str, selector_prompt: str) -> str:
    """Query the appropriate LLM provider and return raw text."""
    if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    if provider in ("gemini", "pro"):
        from providers.gemini import GeminiProvider
        llm = GeminiProvider()
        if provider == "pro":
            model = _resolve_model(
                "gemini/gemini-4-carbon", "gemini-4-carbon")
        else:
            model = _resolve_model(
                "gemini/gemini-3.5-flash-lite", "gemini-3.5-flash-lite")
        return llm.generate(selector_prompt, model=model, temperature=0.0, max_tokens=2048).text
    else:
        from providers.groq import GroqProvider
        llm = GroqProvider()
        return llm.generate(selector_prompt, model="openai/gpt-oss-120b",
                            temperature=0.0, max_tokens=2048).text


_SECONDARY_CONCERNS = {
    "code": ["testing"],
    "architect": ["testing"],
}
_SECURITY_SIGNALS = {"auth", "jwt", "oauth", "credential", "password",
                     "encrypt", "vulnerability", "permission", "rbac", "acl"}

# ── Copilot Delegation Routing (free GPT-4.1 via Copilot CLI) ─────
# Token-economy: keep cheap, single-shot prompts off Claude's Premium context.
# Order matters — first match wins. Patterns are intentionally conservative
# so we never route real multi-file work to Copilot.
_COPILOT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("review",  ("review my diff", "review this diff", "review the diff",
                 "free tier review", "first-pass review")),
    ("suggest", ("what's the command for", "what is the command for",
                 "suggest a command", "shell command to", "command to ")),
    ("explain", ("explain ", "what does ", "what is ")),
    ("ask",     ("ask copilot", "delegate to gpt-4.1", "save premium tokens")),
)
# Anti-delegation guards. Any of these in the prompt forces None (stay on Claude).
_NO_DELEGATE_SIGNALS: frozenset[str] = frozenset({
    "refactor", "rewrite", "across files", "multi-file", "test for",
    "write tests", "trace the bug", "the codebase", "this repo", "this project",
})


def should_delegate_to_copilot(prompt: str) -> Optional[str]:
    """Decide whether a prompt should be delegated to Copilot CLI.

    Returns the method name ('ask' | 'explain' | 'suggest' | 'review') or
    None if the prompt should stay on Claude. CC = 6.
    """
    if not prompt or not prompt.strip():
        return None
    p = prompt.lower().strip()
    if any(sig in p for sig in _NO_DELEGATE_SIGNALS):
        return None
    for method, patterns in _COPILOT_PATTERNS:
        if any(pat in p for pat in patterns):
            return method
    return None


def _iter_always_skills(yaml_text: str):
    """Yield skill names from the `always:` block of a mode yaml."""
    in_always = False
    for line in yaml_text.split("\n"):
        stripped = line.strip()
        if stripped == "always:":
            in_always = True
            continue
        if not in_always:
            continue
        if stripped.startswith("- "):
            yield stripped.lstrip("- ").strip()
        elif stripped:
            return


def _skills_for_mode(mode: str):
    """Yield every always-skill of one mode, or nothing if the yaml is missing."""
    yaml_path = MODES_DIR / f"{mode}-mode.yaml"
    return _iter_always_skills(yaml_path.read_text()) if yaml_path.exists() else iter(())


def _deduplicate_skills(modes: list[str]) -> tuple[list[str], int]:
    """N9: Deduplicate overlapping skills across stacked modes."""
    from itertools import chain
    seen: set[str] = set()
    unique: list[str] = []
    dupes = 0
    for skill in chain.from_iterable(_skills_for_mode(m) for m in modes):
        if skill in seen:
            dupes += 1
        else:
            seen.add(skill)
            unique.append(skill)
    return unique, dupes


def _budget_for_mode(mode: str) -> int:
    """Read token_budget from a mode yaml; default 3000 when absent or invalid."""
    yaml_path = MODES_DIR / f"{mode}-mode.yaml"
    if not yaml_path.exists():
        return 0
    for line in yaml_path.read_text().split("\n"):
        if line.startswith("token_budget:"):
            try:
                return int(line.split(":", 1)[1].strip().lstrip("~"))
            except ValueError:
                return 3000
    return 0


def _add_secondary_concerns(selection: ModeSelection, prompt_lower: str) -> None:
    """Append secondary + security mode hints in place. Flat iteration."""
    additions = [sec for m in list(selection.modes)
                 for sec in _SECONDARY_CONCERNS.get(m, [])]
    for sec in additions:
        if sec not in selection.modes:
            selection.modes.append(sec)
    if any(sig in prompt_lower for sig in _SECURITY_SIGNALS) \
            and "security" not in selection.modes:
        selection.modes.append("security")


def _enrich_selection(selection: ModeSelection, prompt: str) -> ModeSelection:
    """N4: Add secondary concerns and calculate budget."""
    selection.budget += sum(_budget_for_mode(m) for m in selection.modes)
    _add_secondary_concerns(selection, prompt.lower())
    unique_skills, _dupes = _deduplicate_skills(selection.modes)
    if unique_skills:
        selection.skills = unique_skills
    return selection


def _hint_for_pattern(p: dict) -> list[str]:
    """Format one override pattern into 0-2 hint lines (no nested loop)."""
    out: list[str] = []
    if p.get("added"):
        out.append(f"Users frequently add {p['added']} for similar prompts")
    if p.get("removed"):
        out.append(f"Users frequently remove {p['removed']} for similar prompts")
    return out


def _build_override_hint() -> str:
    """Build LEARNED CORRECTIONS hint block, empty string if none."""
    patterns = get_override_patterns(min_count=3)
    if not patterns:
        return ""
    hints: list[str] = []
    for p in patterns[:3]:
        hints.extend(_hint_for_pattern(p))
    return "\n\nLEARNED CORRECTIONS:\n" + "\n".join(f"- {h}" for h in hints)


def _query_with_failover(provider: str, selector_prompt: str) -> tuple[str, str]:
    """Query LLM with N7 failover. Returns (raw_response, effective_provider).

    Raises Exception if both primary and fallback fail.
    """
    try:
        return _query_llm(provider, selector_prompt), provider
    except Exception as primary_err:
        fallback = "groq" if provider in ("gemini", "pro") else "gemini"
        try:
            return _query_llm(fallback, selector_prompt), f"{fallback} (failover)"
        except Exception as e:
            raise RuntimeError(f"{primary_err} (failover also failed: {e})") from e


def select_modes(prompt: str, provider: str = "auto") -> ModeSelection:
    """Infer optimal modes for a prompt using external LLM."""
    provider = _resolve_provider(prompt, provider)
    import time as _time
    _t0 = _time.monotonic()

    cached = _cache_lookup(prompt)
    if cached:
        _record_metric(prompt, cached, _time.monotonic() - _t0)
        return cached

    selector_prompt = _build_selector_prompt(
        prompt, _load_mode_catalogue() + _build_override_hint()
    )

    try:
        raw, effective_provider = _query_with_failover(provider, selector_prompt)
    except Exception as e:
        return ModeSelection(reasoning=f"LLM error: {e}")

    selection = _parse_selection(raw)
    if not selection:
        return ModeSelection(reasoning="Failed to parse LLM response")

    selection.model_used = effective_provider
    selection = _enrich_selection(selection, prompt)
    _cache_store(prompt, selection)
    _record_metric(prompt, selection, _time.monotonic() - _t0)
    return selection



# ── Override Learning (N2 — Council Proposal) ──────────────────────

OVERRIDES_PATH = CLAUDE_DIR / "learning" / "mode-overrides.jsonl"


def record_override(prompt: str, auto_modes: list[str], user_modes: list[str]) -> None:
    """Record when a user manually changes auto-selected modes.

    After 3 corrections with similar prompts, the pattern becomes
    a learned override that adjusts future selections.
    """
    import hashlib
    from datetime import datetime, timezone

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt_hash": hashlib.md5(prompt.lower().strip().encode()).hexdigest()[:12],
        "prompt_preview": prompt[:80],
        "auto_modes": auto_modes,
        "user_modes": user_modes,
        "added": [m for m in user_modes if m not in auto_modes],
        "removed": [m for m in auto_modes if m not in user_modes],
    }

    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OVERRIDES_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_override_patterns(min_count: int = 3) -> list[dict]:
    """Find correction patterns that have occurred >= min_count times.

    Returns patterns with: added modes, removed modes, and example prompts.
    """
    if not OVERRIDES_PATH.exists():
        return []

    pattern_counts = _group_overrides_by_pattern(OVERRIDES_PATH.read_text())
    return [
        {"added": list(a), "removed": list(r), "count": len(p), "examples": p[:3]}
        for (a, r), p in pattern_counts.items() if len(p) >= min_count
    ]


def _group_overrides_by_pattern(text: str) -> dict[tuple, list]:
    """Single-pass grouping of override entries by (added, removed) key."""
    groups: dict[tuple, list] = {}
    for line in text.strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (tuple(sorted(entry.get("added", []))),
               tuple(sorted(entry.get("removed", []))))
        groups.setdefault(key, []).append(entry.get("prompt_preview", ""))
    return groups


def propose_new_mode(prompt: str, provider: str = "gemini") -> Optional[dict]:
    """Propose a new mode when no existing modes score well for a prompt.

    Returns a mode definition dict or None if existing modes are sufficient.
    The proposed mode must be reviewed by the user before activation.
    """
    result = select_modes(prompt, provider=provider)
    if result.modes:
        return None  # Existing modes are sufficient

    if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    propose_prompt = (
        "No existing GRIP modes fit this task well. Propose a NEW mode.\n\n"
        f"TASK: {prompt}\n\n"
        "Respond with JSON:\n"
        '{"name": "mode-name", "description": "what it does", '
        '"skills": ["skill1", "skill2"], "triggers": ["trigger1"]}'
    )

    try:
        from providers.gemini import GeminiProvider
        llm = GeminiProvider()
        raw = llm.generate(
            propose_prompt,
            model=_resolve_model("gemini/gemini-3.5-flash-lite", "gemini-3.5-flash-lite"),
            temperature=0.3, max_tokens=512).text
        parsed = _parse_selection(raw)
        if parsed:
            return {"name": parsed.modes[0] if parsed.modes else "custom",
                    "description": parsed.reasoning,
                    "skills": parsed.skills}
    except Exception:
        pass
    return None


def _parse_cli_args(argv: list[str]) -> tuple[str, bool, str]:
    """Extract (prompt, use_json, provider) from a flat argv list."""
    use_json = "--json" in argv
    provider = "auto"
    clean: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--json":
            i += 1
        elif argv[i] == "--provider" and i + 1 < len(argv):
            provider = argv[i + 1]
            i += 2
        else:
            clean.append(argv[i])
            i += 1
    return " ".join(clean), use_json, provider


def _print_overrides() -> None:
    """Pretty-print learned override patterns (or a 'none yet' notice)."""
    patterns = get_override_patterns()
    if not patterns:
        print("No override patterns learned yet (need 3+ corrections)")
        return
    for p in patterns:
        print(f"  +{p['added']} -{p['removed']} ({p['count']}x)")


def _print_selection(result: ModeSelection, use_json: bool) -> None:
    """Render a ModeSelection in JSON or plain text."""
    if use_json:
        print(json.dumps({
            "modes": result.modes, "skills": result.skills,
            "commands": result.commands, "reasoning": result.reasoning,
            "budget": result.budget, "model_used": result.model_used,
        }, indent=2))
        return
    if not result.modes:
        print(f"No modes selected: {result.reasoning}")
        return
    print(f"Modes: {' + '.join(result.modes)} (~{result.budget} budget)")
    if result.skills:
        print(f"Skills: {', '.join(result.skills)}")
    if result.commands:
        print(f"Commands: {', '.join(result.commands)}")
    print(f"Reasoning: {result.reasoning}")


def main():
    """CLI entry point. Delegates to small focused helpers (KISS)."""
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("Usage: auto_mode_selector.py [--json] [--provider P] <prompt...>")
        print("       auto_mode_selector.py overrides")
        return
    if argv[0] == "overrides":
        _print_overrides()
        return
    prompt, use_json, provider = _parse_cli_args(argv)
    if not prompt:
        print("No prompt provided")
        return
    _print_selection(select_modes(prompt, provider=provider), use_json)


if __name__ == "__main__":
    main()
