"""Redact what a review sends to model providers outside GRIP.

AGORA (lib/agora_pr_review.py) sends a pull request's diff, the full text of
small touched files and its own working text to outside providers, and
lib/external_reviewer.py does the same for single documents. Every such text
passes through the redaction shield's public profile first: the denylist's
names and matter terms, then the shield's floor (keys, message IDs, emails,
identity numbers, phone numbers and IP addresses). The public profile is used,
not llm_egress, because llm_egress treats any run of capitalised words as a
person, which would mangle code under review.

The shared outside-model transports pass their request text through
``shield_payload`` before it leaves: lib/hal_llm_adapter.py, lib/hal_client.py,
lib/sovereign_council.py, lib/roundtable.py and lib/providers/. So a council,
a classifier or a report that never calls the shield itself is still covered,
and text a caller already scrubbed passes through unchanged. A body bound for a
decision model (a ``state`` and typed ``questions``) is scrubbed at every depth
by ``shield_decision_payload``, and ``shield_payload`` finds one at any depth.

Where the denylist comes from, in order:
  1. REVIEW_REDACTION_DENYLIST_B64, base64 of the denylist JSON. CI reads the
     git-crypt-encrypted file as ciphertext, so a runner gets the list this way,
     from a repository secret.
  2. config/redaction-denylist.json in this checkout, when it is decrypted.
  3. The same file under CLAUDE_DIR.
With none of these, only the floor runs, and the footer says so, so a verdict
never implies protection that did not happen.

Fails closed: if redaction itself raises, the whole text is replaced by
WITHHELD and nothing of it is sent.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import redaction_shield as rs  # noqa: E402

DENYLIST_ENV = "REVIEW_REDACTION_DENYLIST_B64"
WITHHELD = "[text withheld: the redaction shield failed, so none of it was sent]"
SOURCE_SECRET, SOURCE_CHECKOUT, SOURCE_NONE = "secret", "checkout", "none"
SOURCE_OVERLAY = "overlay"   # no shared list, but the operator's private one supplied names

_state: dict = {}


def reset() -> None:
    """Forget the cached shield and the tally (tests, or a new review run)."""
    _state.clear()
    _state.update(shield=None, source="", counts=Counter(), seen=set(), sends=0)


reset()


def _with_names(denylist: rs.Denylist | None) -> rs.Denylist | None:
    """``denylist`` if it lists anyone, else None.

    A list with no entries redacts no name, so it counts as no list at all.
    Accepting it would let the footer say names were redacted with it.
    """
    return denylist if denylist is not None and denylist.entries else None


def _from_env(env: dict) -> rs.Denylist | None:
    """The denylist carried base64-encoded in the environment, or None."""
    raw = (env.get(DENYLIST_ENV) or "").strip()
    if not raw:
        return None
    try:
        data = base64.b64decode(raw)
    except (binascii.Error, ValueError):
        return None
    with tempfile.NamedTemporaryFile(suffix=".json") as handle:
        handle.write(data)
        handle.flush()
        try:
            return _with_names(rs.load_denylist(Path(handle.name)))
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            return None  # a malformed secret falls through to the next source


def _from_files() -> rs.Denylist | None:
    """The first decrypted denylist file that lists anyone: this checkout's, then CLAUDE_DIR's."""
    candidates = (_LIB.parent / "config" / "redaction-denylist.json", rs.DEFAULT_CONFIG)
    for path in dict.fromkeys(candidates):
        found = _with_names(rs.load_denylist(path)) if rs.denylist_state(path) == "ok" else None
        if found is not None:
            return found
    return None


def _shield() -> rs.Shield:
    if _state["shield"] is None:
        denylist, source = _from_env(dict(os.environ)), SOURCE_SECRET
        if denylist is None:
            denylist, source = _from_files(), SOURCE_CHECKOUT
        if denylist is None:
            denylist, source = rs.Denylist.empty(), SOURCE_NONE
        # The operator's private overlay goes first wherever it exists (never on
        # CI). One that is configured but unreadable raises here, and redact()
        # then withholds the whole text rather than send it without its entries.
        # When it is the only list with names, the footer says so (Codex, P2,
        # on GRIP#7579) instead of claiming that no names were redacted.
        overlay = rs.load_overlay()
        if source == SOURCE_NONE and overlay.entries:
            source = SOURCE_OVERLAY
        denylist = rs.Denylist.merge(overlay, denylist)
        _state["shield"] = rs.Shield(denylist, profile=rs.PROFILE_PUBLIC)
        _state["source"] = source
    return _state["shield"]


def redact(text: str) -> str:
    """``text`` as the shield's public profile rewrites it; WITHHELD if that fails.

    Pure: no tally. The quote gate uses this to compare a speaker's quote, which
    was written against redacted text, with the real file line.
    """
    try:
        return _shield().redact(text or "").text
    except Exception:  # noqa: BLE001 -- a failed redaction must never send the original
        return WITHHELD


def redact_outbound(text: str) -> str:
    """``redact(text)``, counted for the verdict footer. Call it on text that leaves.

    The same text sent twice (one prompt to several judge rungs) counts once.
    """
    digest = hashlib.sha256((text or "").encode("utf-8", "surrogatepass")).hexdigest()
    try:
        result = _shield().redact(text or "")
    except Exception:  # noqa: BLE001 -- a failed redaction must never send the original
        _state["counts"]["withheld"] += 1
        return WITHHELD
    if digest not in _state["seen"]:
        _state["seen"].add(digest)
        _state["sends"] += 1
        _state["counts"].update(result.counts)
    return result.text


#: Keys whose string values are prose for a model: prompts, system text, chat
#: content, content-part text, the input and instructions some APIs take, and
#: tool or schema descriptions. Every other value (model ids, names, schema
#: types, image data, flags) passes through byte-identical.
_TEXT_KEYS = frozenset({"prompt", "system", "content", "text", "input", "instructions",
                        "description"})
#: The question types of the System One decision format (``is_decision_payload``).
_DECISION_KINDS = frozenset({"noul", "choice", "score"})


class ShieldFailed(OSError):
    """Text bound for an outside model could not be scrubbed, so none of it is sent.

    An OSError on purpose: every transport already treats one as a failed send,
    so a shield failure fails closed through the handling each one has, and a
    fallback route reaches the same shield and fails the same way.
    """


def shield_text(text: str) -> str:
    """``text`` scrubbed whole for an outside model; raises ShieldFailed when it cannot be."""
    scrubbed = redact(text)
    if scrubbed == WITHHELD:
        raise ShieldFailed("the redaction shield failed, so nothing was sent")
    return scrubbed


def shield_payload(value, *, to=None, env=None):
    """A prompt, request body or message list with every prose string scrubbed whole.

    A bare string is prose. In dicts and lists, the strings under the keys in
    ``_TEXT_KEYS`` are prose, including a list of them (an embeddings ``input``),
    so a prompt, a system message, chat content and a content part's text are
    each scrubbed in one piece. Entering a dict resets this, so an image part's
    data and a tool schema pass through untouched. A decision body, found at any
    depth, is scrubbed as ``shield_decision_payload`` scrubs it. Returns a new
    structure; raises ShieldFailed, sending nothing, on failure.
    """
    if to is not None:
        _check_billing(to, value, env)
    return _shield_walk(value, isinstance(value, str))


def is_decision_payload(value) -> bool:
    """True for a body in the System One decision format: a ``state`` and a map of typed ``questions``.

    TypeSafe's /v1/systemone takes this shape, and so does HAL's decide route.
    Every question must be an object whose ``type`` is noul, choice or score. A
    looser test would mistake a tool schema whose ``properties`` name ``state``
    and ``questions`` for a decision body, and scrubbing that schema's ``enum``
    or ``const`` values would break the tool's contract (Codex, P2, on
    GRIP#7933). A caller that knows it holds a decision body calls
    ``shield_decision_payload``, which needs no detection at all.
    """
    if not isinstance(value, dict) or "state" not in value:
        return False
    questions = value.get("questions")
    return isinstance(questions, dict) and bool(questions) and all(
        isinstance(question, dict) and question.get("type") in _DECISION_KINDS
        for question in questions.values())


def shield_decision_payload(value, *, to=None, env=None):
    """A decision body with every string value scrubbed, at any depth; keys are never rewritten.

    A decision model reads more than prose keys. ``state`` may be a string, an
    object or an array, and a question's ``instructions`` and ``criteria`` may
    nest objects, so the key-based walk in ``shield_payload`` would let their
    strings out unscrubbed. Here every string value is scrubbed whole.

    Keys are the exception, because the reply depends on them: question ids
    come back as answer ids, and a Choice's option keys come back as its
    answer. A key the shield would change therefore refuses the whole send
    (ShieldFailed) rather than go out unscrubbed or come back unrecognisable.
    HAL's decision providers refuse to send when this function is missing.
    """
    if to is not None:
        _check_billing(to, value, env)
    return _decision_walk(value)


def _check_billing(to, value, env) -> None:
    try:
        from openai_billing import check
    except ImportError:
        try:
            from lib.openai_billing import check
        except ImportError as exc:
            raise OSError("the billing policy is unavailable, so nothing was sent") from exc
    check(to, value, env=env)


def _shield_walk(value, prose: bool):
    if isinstance(value, str):
        return shield_text(value) if prose else value
    if isinstance(value, list):
        return [_shield_walk(item, prose) for item in value]
    if is_decision_payload(value):
        return _decision_walk(value)
    if isinstance(value, dict):
        return {key: _shield_walk(item, key in _TEXT_KEYS) for key, item in value.items()}
    return value


def _decision_walk(value):
    if isinstance(value, str):
        return shield_text(value)
    if isinstance(value, list):
        return [_decision_walk(item) for item in value]
    if isinstance(value, dict):
        return {_unchanged_key(key): _decision_walk(item) for key, item in value.items()}
    return value


def _unchanged_key(key):
    """``key`` itself, or ShieldFailed when the shield would rewrite it."""
    if isinstance(key, str) and shield_text(key) != key:
        raise ShieldFailed("a key in the decision body holds text the shield would change, "
                           "so nothing was sent")
    return key


def summary() -> dict:
    """What the shield did this run: where its names came from, and what it replaced."""
    return {"names_list": _state["source"], "sends": _state["sends"],
            "replaced": dict(_state["counts"])}


def render_footer() -> str:
    """One italic line for the review comment; "" when nothing was sent."""
    if not _state["sends"] and not _state["counts"]:
        return ""
    counts = dict(_state["counts"])
    total = sum(n for k, n in counts.items() if k != "withheld")
    detail = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()) if k != "withheld")
    replaced = f"{total} item{'s' if total != 1 else ''} replaced" + (f" ({detail})" if detail else "")
    where = {SOURCE_SECRET: "the repository secret", SOURCE_CHECKOUT: "this checkout",
             SOURCE_OVERLAY: "the private overlay"}
    if _state["source"] in where:
        names = f"names were redacted using the list from {where[_state['source']]}"
    else:
        names = ("the names list was not available on this runner, so only identifiers "
                 "(keys, message IDs, emails, ID numbers, phone numbers, IPs) were redacted")
    withheld = counts.get("withheld", 0)
    tail = f"; {withheld} text(s) withheld because redaction failed" if withheld else ""
    return f"\n\n*Outbound text passed the redaction shield: {replaced}; {names}{tail}.*\n"
