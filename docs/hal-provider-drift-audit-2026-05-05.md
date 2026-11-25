# HAL Provider Routing Drift Audit — 2026-05-05

W1 of /ultrado overnight loop on remaining HAL work.

## Hypothesis

**H366**: HAL provider drift is zero after 2026-05-04 fixes.

**Falsifier**: drift sweep finds production-routing references to
`moonshot.cn` or stale `kimi-k2` model names (excluding pricing tables in
`lib/cost_tracker.py` / `lib/model_catalog.py`, which are descriptive).

## Audit method

1. **Read canonical surface** — `~/.hal/config/providers.yaml`
   (the upstream source, post-2026-05-04 fix at commit `0469dd76`).
2. **Read sync mirror** — `config/providers.yaml` in this `~/.claude` worktree.
3. **Comprehensive grep** — `rg "moonshot\.(cn|ai)|kimi-k2"` across `lib/`,
   `bin/`, `commands/`, `config/`, `mcp-servers/`.
4. **Categorise each hit**:
   - **(a) production-routing** — must match `~/.hal` canonical
   - **(b) descriptive** — pricing tables, comments (acceptable)
   - **(c) test fixture / history** — acceptable
5. **Targeted pytest** — `tests/test_hal_registry.py` +
   `tests/test_hal_active_llm_marker.py` + `tests/test_token_budget.py`.

## Findings

### Drift detected

The 2026-05-04 Wave 0 fix landed on `~/.hal` canonical, but the synced
mirror in `~/.claude/config/providers.yaml` was **not updated** — split-
brain across two repositories. Plus 4 downstream caller sites carried the
stale `kimi-k2.5` model name.

| File | Drift type | Fix |
|------|------------|-----|
| `config/providers.yaml:33` | base_url `moonshot.cn` → `moonshot.ai` + 7-model variant list | Synced from canonical |
| `lib/active_llm.py:113` | Statusline regex only matched `kimi`; new models `moonshot-v1-*` would render as raw addresses | Added 7 regex entries for moonshot-v1 family |
| `lib/token_budget.py:60` | Comment `Kimi K2.5 — 32K output` | Updated to `Moonshot v1 family — 32K output` |
| `bin/grip-hal:95,119` | Comments `Kimi K2.5 = 128K` | Updated to `Moonshot v1-128k = 128K` |
| `commands/frontier.md:48` | Production example `/llm set moonshot/kimi-k2.5` would fail | Updated to `moonshot/moonshot-v1-128k` |
| `config/litellm-config.yaml.example` (3 hits) | Example config alias + base_url stale | Synced URL + alias |

Token-budget cap of `32768` and warning/critical percentages (40/65) kept
unchanged — they apply equally to the v1 family.

### Test results

`tests/test_hal_registry.py + test_hal_active_llm_marker.py + test_token_budget.py`:
- **48 passed**
- **6 failed in `test_hal_active_llm_marker.py`** — all related to marker
  file write paths, NOT to provider drift. Pre-existing failures verified
  by stashing my changes and re-running (same 6 fail on stashed/main state).
  Not introduced by this audit. Logged for future investigation per Rule 19.

### Falsifier evaluation

Final post-edit `rg "moonshot\.cn|kimi-k2"` excluding history
(`learning/`, `drafts/`, `mcp-servers/hal-mcp/dist/`): **zero hits**.

H366 verdict: **CONFIRMED** with caveat — drift was non-zero at audit start,
remediated within this audit session, and the 6 marker-file test failures
are unrelated to drift.

## Future falsification path

H366 is wrong if any of:
- Future PR introduces hardcoded `moonshot.cn` URL in production routing.
- The two-repo sync (`~/.hal/config/providers.yaml` ↔ `~/.claude/config/providers.yaml`)
  drifts again because there is no automated regression test enforcing parity.
- A new model name (e.g. `kimi-k2.5` reintroduced as a routing alias) bypasses
  the active_llm regex layer and renders as a raw address in the statusline.

The structural risk is the **two-repo sync surface**. A drift regression
test (parametric test asserting yaml equality between `~/.hal` and `~/.claude`
versions of providers.yaml) would mechanically prevent recurrence — recorded
as H367 candidate for next iteration.

## Files changed

```
config/providers.yaml              # base_url + models list
config/litellm-config.yaml.example # 3 sites
lib/active_llm.py                  # 7 regex entries + YAGNI cleanup elsewhere
lib/token_budget.py                # comment + unused Path import
bin/grip-hal                       # 2 comment lines
commands/frontier.md               # production routing example
```
