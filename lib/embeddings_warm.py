"""Always-warm ollama embeddings — opt-in setup + round-trip verification.

GRIP's semantic memory (KONO recall, skill search, plan-similarity) runs on a
local ollama daemon serving the ``nomic-embed-text`` model. This module makes
that backend robustly always-on and always-warm, and proves it end to end.

Public API (stable):
    ensure_ready(interactive=False) -> dict   # idempotent setup; NEVER raises on missing ollama
    verify_roundtrip() -> dict                # store -> retrieve proof (isolated temp DB)
    warm_model() -> bool                      # pin the tiny embed model resident

Design notes:
- ``ensure_ready`` is safe to call during onboarding: a missing/absent ollama
  yields ``ok=False`` + a plain-English ``skipped_reason`` and never raises, so
  activation is never blocked.
- ``verify_roundtrip`` runs against a throwaway temporary database, so it proves
  the save + load + retrieve path without touching the real ``embeddings.db``.
- The per-call ``keep_alive=-1`` (always-warm) already lives in the HAL shim
  ``lib/hal/embeddings.py`` (PR #4022); this module ensures the daemon + model
  are present and the model is pinned resident.

Rule 17: every user-facing string is plain English.
"""
from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

MODEL = "nomic-embed-text"
ENDPOINT = "http://localhost:11434"
PROBE_TYPE = "roundtrip_probe"
_PLIST = Path.home() / "Library" / "LaunchAgents" / "homebrew.mxcl.ollama.plist"


# ---------------------------------------------------------------------------
# Probes (each swallows its own failure and returns a plain answer)
# ---------------------------------------------------------------------------

def _ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def _get_tags(timeout_s: float = 3.0) -> list[str] | None:
    """Return pulled model names, or None if the daemon is unreachable."""
    try:
        req = urllib.request.Request(f"{ENDPOINT}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return [m.get("name", "") for m in tags.get("models", []) if m.get("name")]


def _daemon_reachable() -> bool:
    return _get_tags() is not None


def _model_pulled() -> bool:
    pulled = _get_tags() or []
    base = MODEL.split(":")[0]
    return any(n.split(":")[0] == base for n in pulled)


def _is_always_on() -> bool:
    """True if ollama is a loaded Homebrew launchd service (RunAtLoad+KeepAlive)."""
    if _PLIST.exists():
        return True
    try:
        out = subprocess.run(
            ["brew", "services", "list"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "ollama" in out.stdout and "started" in out.stdout


def _enable_always_on() -> tuple[bool, str]:
    """Best-effort: register ollama as an always-on Homebrew service."""
    if _is_always_on():
        return True, "ollama is already set to start automatically"
    try:
        r = subprocess.run(
            ["brew", "services", "start", "ollama"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not start ollama automatically: {exc}"
    if r.returncode == 0:
        return True, "set ollama to start automatically (always-on)"
    return False, f"could not start ollama automatically: {(r.stderr or r.stdout).strip()[:160]}"


def _pull_model(timeout_s: float = 600.0) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["ollama", "pull", MODEL],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not download the {MODEL} model: {exc}"
    if r.returncode == 0:
        return True, f"downloaded the {MODEL} model"
    return False, f"could not download the {MODEL} model: {(r.stderr or '').strip()[:160]}"


def warm_model() -> bool:
    """Send one embed (keep_alive=-1, via the HAL shim) to pin the model resident."""
    try:
        from lib.hal.embeddings import embed  # already sends keep_alive=-1 (PR #4022)
        return bool(embed("warm"))
    except Exception:  # noqa: BLE001 — warming is best-effort, never fatal
        return False


# ---------------------------------------------------------------------------
# ensure_ready — thin coordinator + focused steps (idempotent, onboarding-safe)
# ---------------------------------------------------------------------------

def _fresh_status() -> dict:
    return {
        "ok": False, "ollama_installed": False, "always_on": False,
        "model_pulled": False, "warmed": False, "messages": [], "skipped_reason": None,
    }


def _ensure_daemon(result: dict) -> bool:
    """Set always_on + confirm the daemon answers. False (with skipped_reason) if unusable."""
    on, on_msg = _enable_always_on()
    result["always_on"] = on
    result["messages"].append(on_msg)
    if _daemon_reachable():
        return True
    result["skipped_reason"] = (
        "ollama is installed but not responding on localhost:11434. Start it with "
        "`brew services start ollama` or `ollama serve`, then re-run."
    )
    result["messages"].append(result["skipped_reason"])
    return False


def _ensure_model_present(result: dict) -> bool:
    """Ensure the embed model is pulled. Sets model_pulled + skipped_reason on failure."""
    if _model_pulled():
        result["model_pulled"] = True
        result["messages"].append(f"the {MODEL} embedding model is already available")
        return True
    pulled, pull_msg = _pull_model()
    result["model_pulled"] = pulled
    result["messages"].append(pull_msg)
    if not pulled:
        result["skipped_reason"] = pull_msg
    return pulled


def ensure_ready(interactive: bool = False) -> dict:  # noqa: ARG001 — reserved for callers
    """Make the always-warm embeddings backend ready, idempotently.

    Returns a structured status dict. NEVER raises when ollama is missing or
    unreachable — it reports ``ok=False`` with a plain-English ``skipped_reason``
    so onboarding is never blocked.
    """
    result = _fresh_status()
    if not _ollama_installed():
        result["skipped_reason"] = (
            "ollama is not installed, so local semantic memory is off. Install it "
            "with `brew install ollama` (macOS) and re-run to enable it. GRIP works "
            "fine without it."
        )
        result["messages"].append(result["skipped_reason"])
        return result
    result["ollama_installed"] = True
    if not _ensure_daemon(result):
        return result
    if not _ensure_model_present(result):
        return result
    result["warmed"] = warm_model()
    result["messages"].append(
        "warmed the embedding model and pinned it resident (always-warm)"
        if result["warmed"]
        else "the model is available but could not be warmed just now"
    )
    result["ok"] = result["model_pulled"] and result["warmed"]
    return result


# ---------------------------------------------------------------------------
# verify_roundtrip — store -> retrieve proof against an isolated temp DB
# ---------------------------------------------------------------------------

def _cosine(a_bytes: bytes, b_bytes: bytes) -> float:
    """Cosine similarity of two packed float32 vectors. 0.0 on any degeneracy."""
    n = len(a_bytes) // 4
    if n == 0 or len(b_bytes) // 4 != n:
        return 0.0
    a = struct.unpack(f"{n}f", a_bytes)
    b = struct.unpack(f"{n}f", b_bytes)
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _run_roundtrip(engine, probe: str) -> dict:
    """Store the probe, retrieve it by similarity, and score the result."""
    from lib.embeddings.search import search_similar

    engine.init_schema()
    vec1 = engine.embed_text(probe, use_cache=False)
    engine.store_embedding(probe, embed_type=PROBE_TYPE)
    results = search_similar(engine, probe, embed_type=PROBE_TYPE, limit=1)
    # search results key the stored text as "content" (lib/embeddings/search.py)
    retrieved = (results[0] if results else {}).get("content", "")
    cos = _cosine(vec1, engine.embed_text(probe, use_cache=False))
    ok = retrieved == probe
    message = (
        "Semantic memory verified: the stored text was retrieved as the top match "
        f"and the embedding is stable (cosine {cos:.4f})."
        if ok else
        "Round-trip FAILED: the stored text was not returned as the top match — the "
        "semantic index may be misconfigured."
    )
    return {"ok": ok, "cosine": round(cos, 4), "retrieved_top1": retrieved, "message": message}


def verify_roundtrip() -> dict:
    """Prove embeddings are correctly SAVED and LOADED/RETRIEVED.

    Embeds a unique probe, stores it in a throwaway temporary database, then
    retrieves it by similarity search and asserts the stored probe comes back as
    the top match. Also reports the embedding's self-similarity (cosine) as a
    stability signal. Returns ``{ok, cosine, retrieved_top1, message}``. Never
    raises — a down/absent ollama yields ``ok=False`` + plain remediation.
    """
    probe = f"grip-embeddings-roundtrip-probe-{uuid.uuid4().hex}"
    fail: dict = {"ok": False, "cosine": 0.0, "retrieved_top1": "", "message": ""}
    try:
        from lib.embeddings import get_engine
    except Exception as exc:  # noqa: BLE001
        fail["message"] = f"the embeddings package failed to import: {exc}"
        return fail
    tmpdir = Path(tempfile.mkdtemp(prefix="grip-embed-roundtrip-"))
    engine = None
    try:
        engine = get_engine(db_path=tmpdir / "roundtrip.db")
        return _run_roundtrip(engine, probe)
    except Exception as exc:  # noqa: BLE001 — graceful: ollama down / model missing
        fail["message"] = (
            f"the round-trip could not run: {exc}. Make sure ollama is running with "
            f"the {MODEL} model — run `grip embeddings-warm` or `ollama pull {MODEL}`."
        )
        return fail
    finally:
        if engine is not None:
            try:
                engine.close()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI — convenience for the wizard and /verify-embeddings
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    action = args[0] if args else "verify"
    if action == "ensure":
        print(json.dumps(ensure_ready(interactive=False), indent=2))
        return 0
    if action == "verify":
        result = verify_roundtrip()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    print(f"usage: {Path(__file__).name} [ensure|verify]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
