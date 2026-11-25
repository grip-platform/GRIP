"""Billing policy at the send boundary; no transport or secret retrieval here."""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

PAID_OPT_IN = "GRIP_CODEX_ALLOW_PAID"
_CATALOG = Path(__file__).resolve().parents[1] / "config/model_catalog.yaml"
# Account logins are machine-local and deliberately not tracked in a checkout.
# None means "resolve at call time" (see _accounts_path); a test may pin a path here.
_ACCOUNTS: Path | None = None
_REMEDIES = {
    "PAID_OPENAI_DISABLED": "set GRIP_CODEX_ALLOW_PAID=1 to permit",
    "CODEX_INVOCATION_UNSAFE": "use the subscription seat: flags after exec, both keys blank, a home from config/codex-accounts.json",
}


class BillingRefused(OSError):
    """A refused send, with a stable reason and a remedy that contains no secret."""

    def __init__(self, reason: str):
        self.reason = reason
        self.remedy = _REMEDIES[reason]
        super().__init__(f"{reason}: {self.remedy}")


def paid_allowed(env=None) -> bool:
    """Only the literal opt-in permits per-token OpenAI billing."""
    return (os.environ if env is None else env).get(PAID_OPT_IN) == "1"


def paid_openai_auth() -> dict:
    """Return a secret reference only after opt-in; never read the secret itself."""
    if not paid_allowed():
        return {}
    token = "env:OPENAI_API_KEY" if os.environ.get("OPENAI_API_KEY") else "keychain:grip-openai"
    return {"scheme": "apikey", "token": token}


@lru_cache(maxsize=1)
def _addresses() -> tuple[str, ...]:
    """Read only catalogue address lines; no YAML dependency or model guesses."""
    return tuple(line.strip().split(":", 1)[1].strip().strip("\"'")
                 for line in _CATALOG.read_text().splitlines()
                 if line.strip().startswith("- address:"))


def _accounts_path() -> Path:
    """The ladder registry: GRIP_CODEX_ACCOUNTS, else <CLAUDE_DIR>/config/codex-accounts.json.

    The same resolution bin/grip-codex-ladder and lib/subscription_resets.py use. Until
    2026-10-15 this was hardcoded to ~/.grip, so an install living only at ~/.claude
    (the starter kit's default) found no accounts file and refused every Codex seat.
    """
    if _ACCOUNTS is not None:
        return Path(_ACCOUNTS)
    override = os.environ.get("GRIP_CODEX_ACCOUNTS")
    if override:
        return Path(override).expanduser()
    return get_claude_dir() / "config" / "codex-accounts.json"


def subscription_homes() -> tuple[Path, ...]:
    """Configured subscription homes, resolved so aliases cannot evade the check."""
    try:
        accounts = json.loads(_accounts_path().read_text())["accounts"]
        return tuple(Path(a["codex_home"]).expanduser().resolve() for a in accounts)
    except (OSError, ValueError, KeyError, TypeError):
        return ()


# HAL's own family table (~/.hal/lib/hal/model_route.py _FAMILY_ROUTES): a bare id
# in one of these families goes to HAL's OpenAI provider, whatever the catalogue says.
_OPENAI_FAMILIES = ("gpt-", "chatgpt", "o1", "o3", "o4")


def _resolves_to_openai(model: str) -> bool:
    """Mirror HAL's routing. An explicit provider prefix decides (``openai/...`` is
    OpenAI; ``groq/openai/gpt-oss-120b`` is Groq's). A bare id is OpenAI when HAL's
    family table says so, or when the catalogue maps it only to ``openai/``. A bare
    provider name is OpenAI only when it is ``openai``. Anything else passes: the
    catalogue is not a complete list of what HAL serves (measured 2026-10-06:
    ``llama-3.1-8b-instant``, ``qwen/qwen3-32b`` and a fallback test's fake models
    were all refused as uncatalogued), and a HAL-side alias is HAL's guard to catch."""
    if "/" in model:
        return model.split("/", 1)[0] == "openai"
    if model == "openai" or model.startswith(_OPENAI_FAMILIES):
        return True
    try:
        matches = [a for a in _addresses() if a.endswith("/" + model) or a.startswith(model + "/")]
    except OSError:
        return False
    return bool(matches) and all(a.startswith("openai/") for a in matches)


def _check_model(model, env) -> None:
    """``model`` is a catalogue address, a bare model id, or a bare provider name;
    it is refused when it would bill OpenAI per token and the opt-in is unset."""
    if model and not paid_allowed(env) and _resolves_to_openai(model):
        raise BillingRefused("PAID_OPENAI_DISABLED")


def _codex_safe(argv, env) -> bool:
    if "exec" not in argv:
        return False
    indices = [i for i, arg in enumerate(argv) if arg == "-c"]
    if any(i < argv.index("exec") or i + 1 == len(argv) for i in indices):
        return False
    values = {argv[i + 1] for i in indices}
    if not {"model_provider=openai", "forced_login_method=chatgpt"} <= values:
        return False
    # Refuse conflicting overrides too: a later -c must not undo the seat lock.
    if any(v.startswith(("model_provider=", "forced_login_method=")) and
           v not in {"model_provider=openai", "forced_login_method=chatgpt"} for v in values):
        return False
    if env.get("OPENAI_API_KEY") or env.get("CODEX_API_KEY"):
        return False
    home = env.get("CODEX_HOME")
    return bool(home) and Path(home).expanduser().resolve() in subscription_homes()


def check(to, payload=None, env=None) -> None:
    """Refuse a paid route or an unsafe Codex invocation before anything is sent.

    ``to`` is a URL or an argv. A Codex argv passes only in the subscription
    form: ``exec`` present, every ``-c`` AFTER it (Codex drops root-level ones
    once the subcommand has its own), ``model_provider=openai`` and
    ``forced_login_method=chatgpt`` both set and never contradicted, both API
    keys empty in ``env``, and ``CODEX_HOME`` one of config/codex-accounts.json's
    homes (rules/gpt-on-the-subscription-never-credits.md)."""
    env = os.environ if env is None else env
    body = payload if isinstance(payload, dict) else {}
    if isinstance(to, (list, tuple)):
        program = Path(to[0]).name if to else ""
        if program == "codex" and not _codex_safe(to, env):
            raise BillingRefused("CODEX_INVOCATION_UNSAFE")
        if program == "hal":
            _check_model((body.get("flags") or {}).get("provider") or body.get("model"), env)
        return
    host = urlsplit(to).hostname
    if host == "api.openai.com" and not paid_allowed(env):
        raise BillingRefused("PAID_OPENAI_DISABLED")
    if host in {"127.0.0.1", "localhost"}:
        _check_model(body.get("model") or body.get("provider"), env)
