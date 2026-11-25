#!/usr/bin/env python3
"""One-shot migration: Opus 4.6 -> Opus 4.7 across live GRIP code/config/docs.
Skips historical files (CHANGELOG, data/**, drafts/**, archives). Idempotent.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path.home() / ".claude"

LITELLM_CONFIG = """model_list:
  - model_name: claude-opus-4-7
    litellm_params:
      model: gemini/gemini-2.5-flash
      api_key: os.environ/GEMINI_API_KEY
  - model_name: claude-sonnet-4-6
    litellm_params:
      model: gemini/gemini-2.5-flash
      api_key: os.environ/GEMINI_API_KEY
  - model_name: claude-haiku-4-5-20251001
    litellm_params:
      model: gemini/gemini-2.5-flash
      api_key: os.environ/GEMINI_API_KEY

litellm_settings:
  drop_params: true
  num_retries: 2
"""

OPUS_4_7_RELEASE = {
    "version": "opus-4-7",
    "model_family": "anthropic",
    "release_type": "model",
    "date": "2026-04-16",
    "source": "https://www.anthropic.com/claude/opus",
    "announcement_urls": [
        "https://www.cnbc.com/2026/04/16/anthropic-claude-opus-4-7-model-mythos.html",
        "https://9to5mac.com/2026/04/16/anthropic-reveals-new-opus-4-7-model-with-focus-on-advanced-software-engineering/",
        "https://www.axios.com/2026/04/16/anthropic-claude-opus-model-mythos",
    ],
    "summary": (
        "Claude Opus 4.7, released 2026-04-16, is Anthropic's most powerful "
        "generally available model. Notable improvement on Opus 4.6 in advanced "
        "software engineering. Priced the same as Opus 4.6."
    ),
    "features": [
        {"title": "Advanced software engineering, particular gains on hardest tasks",
         "description": "Users can hand off their hardest coding work with confidence.",
         "category": "capability"},
        {"title": "Rigor on long-running tasks + self-verification",
         "description": "Handles complex, long-running work; devises ways to verify outputs before reporting back.",
         "category": "capability"},
        {"title": "Better instruction following, multimodal, memory",
         "description": "Instruction-following precision, multimodal input, persistent-memory improvements.",
         "category": "capability"},
        {"title": "New 'xhigh' effort level between high and max",
         "description": "Opus 4.7 introduces xhigh for finer control over reasoning/latency tradeoff. GRIP standard stays at max.",
         "category": "config"},
        {"title": "Cybersecurity safeguards",
         "description": "Auto-detect and block requests indicating prohibited or high-risk cybersecurity uses.",
         "category": "safety"},
        {"title": "Same price as Opus 4.6",
         "description": "$5/MTok input, $25/MTok output. Fast variant: $30/$150.",
         "category": "pricing"},
        {"title": "Available via Claude products, Anthropic API, Azure, Vertex, Bedrock",
         "description": "Standard availability matrix, same as 4.6.",
         "category": "availability"},
    ],
    "grip_integration": {
        "default_model_id": "claude-opus-4-7",
        "context_window": 1_000_000,
        "effort_default": "max",
        "effort_notes": "GRIP always uses reasoning_effort=max. xhigh is available but not GRIP default.",
        "cost_per_mtok": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25},
        "cost_per_mtok_fast": {"input": 30.0, "output": 150.0, "cache_read": 3.0, "cache_write": 37.5},
    },
}

EXACT_OPS = [
    ("lib/config_validator.py",
     'VALID_MODELS = {\n    "claude-sonnet-4-20250514",\n    "claude-opus-4-20250514",\n    "claude-opus-4-6",\n    "claude-sonnet-4-6",\n    "claude-haiku-3-5",\n    "claude-3-5-sonnet-20241022",\n    "claude-3-5-haiku-20241022",\n    "claude-3-opus-20240229",\n}',
     'VALID_MODELS = {\n    "claude-sonnet-4-20250514",\n    "claude-opus-4-20250514",\n    "claude-opus-4-7",\n    "claude-opus-4-6",\n    "claude-sonnet-4-6",\n    "claude-haiku-4-5-20251001",\n    "claude-haiku-3-5",\n    "claude-3-5-sonnet-20241022",\n    "claude-3-5-haiku-20241022",\n    "claude-3-opus-20240229",\n}'),
    ("lib/hal/cost_tracker.py",
     '    tracker = CostTracker()\n    cost = tracker.calculate("claude-opus-4-6", input_tokens=1000, output_tokens=500)\n    tracker.record("claude-opus-4-6", input_tokens=1000, output_tokens=500)\n    print(tracker.total_usd)\n"""',
     '    tracker = CostTracker()\n    cost = tracker.calculate("claude-opus-4-7", input_tokens=1000, output_tokens=500)\n    tracker.record("claude-opus-4-7", input_tokens=1000, output_tokens=500)\n    print(tracker.total_usd)\n"""'),
    ("lib/hal/cost_tracker.py",
     '    # Anthropic (from TF++ src/utils/modelCost.ts)\n    "claude-opus-4-6":      (5.0,   25.0,  0.5,   6.25),\n    "claude-opus-4-6-fast": (30.0,  150.0, 3.0,   37.5),\n    "claude-opus-4-5":      (5.0,   25.0,  0.5,   6.25),\n',
     '    # Anthropic (from TF++ src/utils/modelCost.ts)\n    # Opus 4.7 launched 2026-04-16 at the same price as 4.6 (per Anthropic announcement).\n    "claude-opus-4-7":      (5.0,   25.0,  0.5,   6.25),\n    "claude-opus-4-7-fast": (30.0,  150.0, 3.0,   37.5),\n    "claude-opus-4-6":      (5.0,   25.0,  0.5,   6.25),\n    "claude-opus-4-6-fast": (30.0,  150.0, 3.0,   37.5),\n    "claude-opus-4-5":      (5.0,   25.0,  0.5,   6.25),\n'),
    ("lib/hal/cost_tracker.py",
     '    # Try without version suffix (e.g. "claude-opus-4-6-20250514" -> "claude-opus-4-6")',
     '    # Try without version suffix (e.g. "claude-opus-4-7-20260416" -> "claude-opus-4-7")'),
    ("lib/hal/providers/anthropic_native.py",
     '    def list_models(self) -> list[ModelInfo]:\n        return [\n            ModelInfo(id="claude-opus-4-6-20250514", provider="anthropic",\n                      display_name="Opus 4.6", context_window=1_000_000),\n            ModelInfo(id="claude-sonnet-4-6-20250514", provider="anthropic",\n                      display_name="Sonnet 4.6", context_window=1_000_000),\n            ModelInfo(id="claude-haiku-4-5-20251001", provider="anthropic",\n                      display_name="Haiku 4.5", context_window=200_000),\n        ]',
     '    def list_models(self) -> list[ModelInfo]:\n        return [\n            ModelInfo(id="claude-opus-4-7", provider="anthropic",\n                      display_name="Opus 4.7", context_window=1_000_000),\n            ModelInfo(id="claude-opus-4-6-20250514", provider="anthropic",\n                      display_name="Opus 4.6", context_window=1_000_000),\n            ModelInfo(id="claude-sonnet-4-6-20250514", provider="anthropic",\n                      display_name="Sonnet 4.6", context_window=1_000_000),\n            ModelInfo(id="claude-haiku-4-5-20251001", provider="anthropic",\n                      display_name="Haiku 4.5", context_window=200_000),\n        ]'),
    ("lib/hal/council_profiles.py",
     '- Opus 4.6 (1M) — structural / SOLID / GRASP (Anthropic family)',
     '- Opus 4.7 (1M) — structural / SOLID / GRASP (Anthropic family)'),
    ("lib/hal/council_profiles.py",
     '    models=(\n        "anthropic/claude-opus-4-6",\n        "anthropic/claude-sonnet-4-6",',
     '    models=(\n        "anthropic/claude-opus-4-7",\n        "anthropic/claude-sonnet-4-6",'),
    ("lib/hal/sentinel.py",
     '    response = sentinel.dispatch("anthropic/claude-opus-4-6", "Hello")',
     '    response = sentinel.dispatch("anthropic/claude-opus-4-7", "Hello")'),
    ("lib/hal/demo.py",
     '        ("claude-opus-4-6", 10000, 5000),',
     '        ("claude-opus-4-7", 10000, 5000),'),
    ("lib/hal/web_demo.py",
     "{ name: 'Claude Opus 4.6', cost: 0.175, color: '#e94560' },",
     "{ name: 'Claude Opus 4.7', cost: 0.175, color: '#e94560' },"),
    ("lib/context_optimizer.py",
     'OPUS_1M = "opus-1m"       # Default since Opus 4.6 (March 2026)',
     'OPUS_1M = "opus-1m"       # Default since Opus 4.7 (April 2026); Opus 4.6 also 1M'),
    ("lib/model_state_manager.py",
     'Note: 1M context is GA for both Opus 4.6 and Sonnet 4.6 at standard pricing (March 2026).',
     'Note: 1M context is GA for Opus 4.7 (Apr 2026), Opus 4.6 and Sonnet 4.6 (Mar 2026) at standard pricing.'),
    ("lib/model_state_manager.py",
     '    "opus-fast": {"context_window": 1000000, "alias": "opus-fast"},  # Opus 4.6 fast mode (CC 2.1.50+)',
     '    "opus-fast": {"context_window": 1000000, "alias": "opus-fast"},  # Opus 4.7 fast mode (CC 2.1.50+)'),
    ("lib/model_state_manager.py",
     '# opus-fast = Opus 4.6 fast mode with 1M context (CC 2.1.50+, toggled via /fast).',
     '# opus-fast = Opus 4.7 fast mode with 1M context (CC 2.1.50+, toggled via /fast).'),
    ("lib/model_state_manager.py",
     '    Opus 4.6 fast mode (CC 2.1.50+, toggled via /fast) uses the same model',
     '    Opus 4.7 fast mode (CC 2.1.50+, toggled via /fast) uses the same model'),
    ("lib/model_state_manager.py",
     '    """Switch back to opus with 1M context window (default since Opus 4.6)."""',
     '    """Switch back to opus with 1M context window (default since Opus 4.7)."""'),
    ("lib/grip_config.py",
     '        Since March 2026, both Opus 4.6 and Sonnet 4.6 default to 1M context.',
     '        Since April 2026, Opus 4.7 (default), Opus 4.6, and Sonnet 4.6 all run at 1M context.'),
    ("lib/wiki_generator.py",
     '"- Token budget: 1M per session (Opus 4.6 / Sonnet 4.6 default since March 2026).",',
     '"- Token budget: 1M per session (Opus 4.7 default since April 2026; Opus 4.6 / Sonnet 4.6 since March 2026).",'),
    ("lib/grip-splash.sh",
     '    local model_info="Opus 4.6"\n    if [[ -f "$claude_dir/.model-state.json" ]]; then\n        local active_model\n        active_model=$(python3 -c "import json; print(json.load(open(\'$claude_dir/.model-state.json\')).get(\'active_model\',\'opus\'))" 2>/dev/null || echo "opus")\n        case "$active_model" in\n            "opus[1m]") model_info="Opus 4.6 1M" ;;\n            "opus-fast") model_info="Opus 4.6 Fast 1M" ;;\n            *) model_info="Opus 4.6" ;;\n        esac\n    fi',
     '    local model_info="Opus 4.7"\n    if [[ -f "$claude_dir/.model-state.json" ]]; then\n        local active_model\n        active_model=$(python3 -c "import json; print(json.load(open(\'$claude_dir/.model-state.json\')).get(\'active_model\',\'opus\'))" 2>/dev/null || echo "opus")\n        case "$active_model" in\n            "opus[1m]") model_info="Opus 4.7 1M" ;;\n            "opus-fast") model_info="Opus 4.7 Fast 1M" ;;\n            *) model_info="Opus 4.7" ;;\n        esac\n    fi'),
    ("statusline-command.sh",
     '    # Context size from model-state (1M default for Opus 4.6)',
     '    # Context size from model-state (1M default for Opus 4.7)'),
    ("config/statusline.yaml",
     '  - model             # Opus 1M (default since Opus 4.6)',
     '  - model             # Opus 1M (default since Opus 4.7)'),
    (".claude/CLAUDE.md",
     '- Default: Opus 4.6 1M / Sonnet 4.6 1M (thresholds: 70% = 700K, 80% = 800K)',
     '- Default: Opus 4.7 1M / Sonnet 4.6 1M (thresholds: 70% = 700K, 80% = 800K)'),
    ("agents/broly.md",
     '- PIPELINE/MESH: Requires 1M (default for Opus 4.6)',
     '- PIPELINE/MESH: Requires 1M (default for Opus 4.7)'),
    ("agents/broly.md",
     '- CLUSTER: Recommends 1M (now default for Opus 4.6)',
     '- CLUSTER: Recommends 1M (now default for Opus 4.7)'),
    ("README.md",
     'GRIP runs on Opus 4.6 with 1M context (default since March 2026):',
     'GRIP runs on Opus 4.7 with 1M context (default since April 2026):'),
    ("skills/INDEX.md",
     'Adaptive Context Extension (ACE) - legacy model switching (Opus 4.6 now defaults to 1M)',
     'Adaptive Context Extension (ACE) - legacy model switching (Opus 4.7 now defaults to 1M)'),
    ("skills/context-extension/SKILL.md",
     'description: Adaptive Context Extension (ACE) - legacy model switching (Opus 4.6 now defaults to 1M)',
     'description: Adaptive Context Extension (ACE) - legacy model switching (Opus 4.7 now defaults to 1M)'),
    ("skills/guild-communication/reference.md",
     'Claude Opus 4.6. All checks are deterministic — no LLM judgment involved.',
     'Claude Opus 4.7. All checks are deterministic — no LLM judgment involved.'),
    ("skills/rsi-aliases/SKILL.md",
     '`/rsi` uses delegate mode by default. Opus 4.6 plans each wave; Sonnet 4.6 executes.',
     '`/rsi` uses delegate mode by default. Opus 4.7 plans each wave; Sonnet 4.6 executes.'),
    ("commands/extend-context.md",
     'Opus 4.6 defaults to 1M context since March 2026. This command is retained for switching to Sonnet 1M or reverting to Opus 1M.',
     'Opus 4.7 defaults to 1M context since April 2026. This command is retained for switching to Sonnet 1M or reverting to Opus 1M.'),
    ("public/guild-invite/dashboard.html",
     '<span class="header-meta">Model <strong>Opus 4.6</strong></span>',
     '<span class="header-meta">Model <strong>Opus 4.7</strong></span>'),
    ("public/guild-invite/index.html",
     '<div class="meta-block"><span class="label">Model</span><span class="meta-value">Claude Opus 4.6</span></div>',
     '<div class="meta-block"><span class="label">Model</span><span class="meta-value">Claude Opus 4.7</span></div>'),
    ("mcp-servers/grip-channel/src/discord.ts",
     "      // Commander routes ALL responses through Opus 4.6 at $0 — model switching is informational only\n      return 'All responses route through **Commander (Opus 4.6)** at $0 cost via subscription. Model switching is no longer needed — you always get the best model for free.';",
     "      // Commander routes ALL responses through Opus 4.7 at $0 — model switching is informational only\n      return 'All responses route through **Commander (Opus 4.7)** at $0 cost via subscription. Model switching is no longer needed — you always get the best model for free.';"),
]

SIMPLE_OPS = [
    ("tests/test_hal_integration.py", '"claude-opus-4-6"', '"claude-opus-4-7"', 1),
    ("tests/test_hal_cost_tracker.py", '"claude-opus-4-6"', '"claude-opus-4-7"', 6),
    ("tests/test_hal_cost_tracker.py", '"claude-opus-4-6-20250514"', '"claude-opus-4-7-20260416"', 1),
    ("tests/test_hal_cost_tracker.py", 'Matches "claude-opus-4-6" prefix', 'Matches "claude-opus-4-7" prefix', 1),
    ("tests/test_hal_cost_tracker.py", "# Opus 4.6: $5/MTok input, $25/MTok output", "# Opus 4.7: $5/MTok input, $25/MTok output", 1),
    ("tests/test_config_validator.py", '{"model": "claude-opus-4-6"}', '{"model": "claude-opus-4-7"}', 1),
    ("tests/test_model_state_manager.py", "default since Opus 4.6", "default since Opus 4.7", 1),
    ("tests/test_council_profiles.py", "anthropic/claude-opus-4-6", "anthropic/claude-opus-4-7", 10),
    ("skills/context-extension/SKILL.md", "Opus 4.6", "Opus 4.7", 8),
    ("skills/context-gate/SKILL.md", "Opus 4.6", "Opus 4.7", 3),
    ("public/guild-invite/index.html", "Claude Opus 4.6", "Claude Opus 4.7", 2),
    ("mcp-servers/grip-channel/src/guild-knowledge.ts", "Opus 4.6", "Opus 4.7", 2),
    ("mcp-servers/grip-channel/src/autonomous-responder.ts", "Opus 4.6", "Opus 4.7", 1),
]

def apply_exact(path, old, new):
    text = path.read_text()
    if old not in text:
        return (False, "already-applied" if new in text else "old-not-found")
    if text.count(old) > 1:
        return (False, f"ambiguous-{text.count(old)}-matches")
    path.write_text(text.replace(old, new, 1))
    return (True, "ok")

def apply_simple(path, old, new, expected_min):
    text = path.read_text()
    count = text.count(old)
    if count == 0:
        return (False, "already-applied" if new in text else "not-found", 0)
    if count < expected_min:
        return (False, f"expected-{expected_min}-found-{count}", count)
    path.write_text(text.replace(old, new))
    return (True, "ok", count)

def main():
    applied, skipped, errors = [], [], []
    cfg = ROOT / "config" / "litellm-config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if cfg.exists() and cfg.read_text() == LITELLM_CONFIG:
        skipped.append(f"[litellm] {cfg.relative_to(ROOT)} already correct")
    else:
        cfg.write_text(LITELLM_CONFIG)
        applied.append(f"[litellm] wrote {cfg.relative_to(ROOT)}")
    rel_path = ROOT / "data" / "cc-releases" / "model-opus-4-7.json"
    rel_path.parent.mkdir(parents=True, exist_ok=True)
    rjson = json.dumps(OPUS_4_7_RELEASE, indent=2)
    if rel_path.exists() and rel_path.read_text() == rjson:
        skipped.append(f"[release] {rel_path.relative_to(ROOT)} already correct")
    else:
        rel_path.write_text(rjson)
        applied.append(f"[release] wrote {rel_path.relative_to(ROOT)}")
    for rel, old, new in EXACT_OPS:
        p = ROOT / rel
        if not p.exists():
            errors.append(f"[exact] MISSING: {rel}")
            continue
        ok, status = apply_exact(p, old, new)
        if ok: applied.append(f"[exact] {rel}")
        elif status == "already-applied": skipped.append(f"[exact] {rel}: idempotent")
        else: errors.append(f"[exact] {rel}: {status}")
    for rel, old, new, expected in SIMPLE_OPS:
        p = ROOT / rel
        if not p.exists():
            errors.append(f"[simple] MISSING: {rel}")
            continue
        ok, status, count = apply_simple(p, old, new, expected)
        if ok: applied.append(f"[simple] {rel}: x{count}")
        elif status == "already-applied": skipped.append(f"[simple] {rel}: idempotent")
        elif status == "not-found": skipped.append(f"[simple] {rel}: not-found")
        else: errors.append(f"[simple] {rel}: {status}")
    print("=" * 72)
    print(f"APPLIED ({len(applied)}):")
    for line in applied: print("  +", line)
    print(f"\nSKIPPED (already correct) ({len(skipped)}):")
    for line in skipped: print("  .", line)
    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for line in errors: print("  !", line)
    print("=" * 72)
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main())
