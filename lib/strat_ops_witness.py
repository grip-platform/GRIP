"""strat_ops_witness — the V>>-only internal strat-ops witness page (encrypted at rest).

Renders the /ultralearn witness ledger (sealed exogenous RSI deliberations) into a single
static HTML page for a private GitHub Pages surface. The page ships **ciphertext only**:
the strat-ops payload is AES-256-GCM encrypted at build time with a key derived from the
`V>>--<<V` passphrase (PBKDF2-HMAC-SHA256, random salt, 310_000 iterations). The passphrase
decrypts client-side via Web Crypto on entry; a wrong passphrase fails on the GCM auth tag.

## Hard leak invariant (enforced in `build()` AND `tests/`)

The output HTML contains the ciphertext, salt, IV, and iteration count — and NOTHING ELSE
sensitive. The plaintext payload NEVER appears in the page; the passphrase NEVER appears in
this source, in the page, or in the committed repo (it is read from `STRAT_OPS_PASSPHRASE`
at build time and lives only in V>>'s head). This is the visual-artifact-leak-prevention /
no-sensitive-info rule applied to a public-static-hosting surface: "password-gated GitHub
Pages" for strategy content is only safe as encrypted-payload, never a JS `if (pw === ...)`.

## Feature-complete over the input domain

An empty ledger (no /ultralearn runs yet) still builds a valid, honest page ("no sealed
deliberations yet"). A malformed ledger line is skipped, not fatal. No partial-domain stub.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
DEFAULT_LEDGER = CLAUDE_DIR / "state" / "ultralearn" / "witness-ledger.jsonl"
DEFAULT_OUT = CLAUDE_DIR / "state" / "strat-ops-witness" / "index.html"
PASSPHRASE_ENV = "STRAT_OPS_PASSPHRASE"
_ITERATIONS = 310_000
_KEYLEN = 32  # AES-256


# ------------------------------------------------------------------------------- crypto
def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, _KEYLEN)


def encrypt_payload(plaintext: str, passphrase: str, iterations: int = _ITERATIONS) -> dict:
    """PBKDF2-SHA256 -> AES-256-GCM. Returns a JSON-safe dict of base64 fields.

    Interoperable with Web Crypto: `deriveKey({name:'PBKDF2',salt,iterations,hash:'SHA-256'})`
    then `decrypt({name:'AES-GCM',iv}, key, ct)` where `ct` is the GCM ciphertext||tag.
    """
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = _derive_key(passphrase, salt, iterations)
    ct = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None)  # ciphertext || 16-byte tag
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iterations": iterations, "cipher": "AES-256-GCM",
            "salt": _b64(salt), "iv": _b64(iv), "ct": _b64(ct)}


def decrypt_payload(payload: dict, passphrase: str) -> str:
    """Python-side inverse of `encrypt_payload` (used by tests to prove the round-trip)."""
    salt = base64.b64decode(payload["salt"])
    iv = base64.b64decode(payload["iv"])
    ct = base64.b64decode(payload["ct"])
    key = _derive_key(passphrase, salt, int(payload["iterations"]))
    return AESGCM(key).decrypt(iv, ct, None).decode("utf-8")


# --------------------------------------------------------------------------- payload build
def payload_from_ledger(ledger_path: Path, now: Optional[float] = None) -> dict:
    """Assemble the strat-ops payload from the /ultralearn witness ledger.

    Handles the whole input domain: absent ledger -> empty records; malformed line -> skipped.
    """
    records = []
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # a corrupt line is skipped, never fatal
            records.append({
                "ts": rec.get("ts"),
                "verdict": rec.get("verdict"),
                "seal": rec.get("seal"),
                "idr_leaf": rec.get("idr_leaf"),
                "snapshot": rec.get("snapshot"),
            })
    records.sort(key=lambda r: (r.get("ts") or 0), reverse=True)
    return {
        "title": "STRAT-OPS WITNESS",
        "generated_at": (time.time() if now is None else now),
        "count": len(records),
        "records": records,
    }


# ------------------------------------------------------------------------------ page render
def _render_html(enc_payload: dict) -> str:
    """The GRASP-NIHILISM page: paper-grey / black / orange, mono labels, ciphertext-only.

    `enc_payload` is the ENCRYPTED dict — no plaintext is ever interpolated here.
    """
    # Raw JSON — NOT html-escaped: a <script type="application/json"> block is read as raw
    # text (entities are NOT decoded there), so escaping would break JSON.parse. Instead
    # neutralise '<' '>' '&' with JSON-level \u escapes — valid JSON, parses identically, and
    # no payload byte can forge a '</script>' close. The blob is the ENCRYPTED dict only.
    blob = (json.dumps(enc_payload, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    return _TEMPLATE.replace("{{PAYLOAD}}", blob)


# The plaintext payload is NEVER interpolated into this template — only the encrypted blob.
_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>STRAT-OPS WITNESS</title>
<style>
:root{--paper:#EAEAEA;--ink:#000;--muted:#525252;--line:#c9c9c9;--accent:#ea580c;
--accent-ink:#9a3412;--panel:#111}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--paper);color:var(--ink)}
body{font:400 16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
-webkit-font-smoothing:antialiased;min-height:100vh}
.mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
.label{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;font-size:11px;
letter-spacing:.2em;text-transform:uppercase;color:var(--muted)}
.wrap{max-width:840px;margin:0 auto;padding:72px 24px 112px}
header{border-bottom:2px solid var(--ink);padding-bottom:28px;margin-bottom:44px}
h1{font-weight:700;letter-spacing:-.035em;line-height:.9;font-size:clamp(2.2rem,7vw,4rem)}
header .label{margin-bottom:16px}
header .sub{margin-top:14px;color:var(--muted);font-size:14px}
.gate{max-width:440px;margin:8vh auto 0;text-align:left}
.gate h2{font-weight:700;letter-spacing:-.02em;font-size:1.4rem;margin-bottom:6px}
.gate p{color:var(--muted);font-size:14px;margin-bottom:26px}
.row{display:flex;gap:0;border:2px solid var(--ink)}
input{flex:1;border:0;padding:16px 18px;font-size:16px;background:#fff;color:var(--ink);outline:none}
input::placeholder{color:#9ca3af}
button{border:0;background:var(--ink);color:#fff;padding:0 26px;font:600 13px/1 ui-monospace,monospace;
letter-spacing:.14em;text-transform:uppercase;cursor:pointer}
button:hover{background:var(--accent)}
.err{color:var(--accent-ink);font-size:13px;margin-top:14px;min-height:18px;font-weight:600}
.meta{display:flex;gap:28px;flex-wrap:wrap;margin-bottom:36px}
.meta div{display:flex;flex-direction:column;gap:4px}
.meta .n{font-weight:700;font-size:1.6rem;letter-spacing:-.02em}
.meta .n.accent{color:var(--accent)}
.card{border:1px solid var(--line);background:#fff;padding:28px 30px;margin-bottom:20px}
.card .top{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-bottom:16px}
.verdict{font-weight:700;letter-spacing:-.01em;font-size:1.05rem}
.ts{white-space:nowrap}
.seal{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--accent-ink);
word-break:break-all;line-height:1.6}
.kv{margin-top:14px;display:grid;grid-template-columns:auto 1fr;gap:6px 18px;font-size:13px}
.kv .label{align-self:center}
.snap{font-family:ui-monospace,monospace;font-size:12px;color:#374151;white-space:pre-wrap;
background:var(--paper);padding:12px 14px;margin-top:14px;border-left:2px solid var(--accent)}
.empty{color:var(--muted);border:1px dashed var(--line);padding:40px;text-align:center}
footer{margin-top:64px;border-top:1px solid var(--line);padding-top:20px;color:var(--muted);font-size:12px}
footer .mono{color:var(--muted)}
.hide{display:none}
</style></head>
<body><div class="wrap">
<header>
<div class="label">GRIP &middot; internal &middot; V&gt;&gt;--&lt;&lt;V</div>
<h1>STRAT&#8209;OPS<br>WITNESS</h1>
<div class="sub">Sealed exogenous RSI deliberations. Encrypted at rest &middot; ciphertext only.</div>
</header>

<section id="gate" class="gate">
<h2>Locked</h2>
<p>This surface is encrypted. Enter the passphrase to decrypt it in your browser &mdash;
nothing is sent anywhere.</p>
<div class="row">
<input id="pw" type="password" placeholder="passphrase" autocomplete="off" autofocus>
<button id="go">Unlock</button>
</div>
<div id="err" class="err"></div>
</section>

<main id="board" class="hide"></main>

<footer class="hide" id="foot">
<span class="mono">AES-256-GCM &middot; PBKDF2-SHA256 &middot; decrypted locally</span> &middot;
re-verify any seal: <span class="mono">grip-sovereign-council --verify &lt;seal&gt;</span>
</footer>

<script id="payload" type="application/json">{{PAYLOAD}}</script>
<script>
const P = JSON.parse(document.getElementById("payload").textContent);
const $ = id => document.getElementById(id);
const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
const esc = s => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };
const fmtTs = t => { if(!t) return "&mdash;"; try{ return new Date(t*1000).toISOString().replace("T"," ").slice(0,16)+" UTC"; }catch(e){ return esc(t); } };

async function decrypt(pw){
  const km = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey(
    {name:"PBKDF2", salt:b64(P.salt), iterations:P.iterations, hash:"SHA-256"},
    km, {name:"AES-GCM", length:256}, false, ["decrypt"]);
  const pt = await crypto.subtle.decrypt({name:"AES-GCM", iv:b64(P.iv)}, key, b64(P.ct));
  return JSON.parse(new TextDecoder().decode(pt));
}

function render(d){
  const g = fmtTs(d.generated_at);
  let h = `<div class="meta">
    <div><span class="label">Sealed runs</span><span class="n accent">${d.count}</span></div>
    <div><span class="label">Rendered</span><span class="n mono" style="font-size:1rem">${g}</span></div>
  </div>`;
  if(!d.records.length){
    h += `<div class="empty"><span class="label">No sealed deliberations yet</span><br><br>
      Run <span class="mono">/ultralearn</span> to seal the first exogenous RSI deliberation.</div>`;
  } else {
    for(const r of d.records){
      const snap = r.snapshot ? esc(JSON.stringify(r.snapshot, null, 2)) : "";
      h += `<article class="card">
        <div class="top"><span class="verdict">${esc(r.verdict) || "&mdash;"}</span>
          <span class="ts label">${fmtTs(r.ts)}</span></div>
        <div class="seal">${esc(r.seal) || "no seal"}</div>
        <div class="kv"><span class="label">IDR leaf</span><span class="mono" style="font-size:12px">${esc(r.idr_leaf) || "&mdash;"}</span></div>
        ${snap ? `<div class="snap">${snap}</div>` : ""}
      </article>`;
    }
  }
  $("board").innerHTML = h;
  $("gate").classList.add("hide");
  $("board").classList.remove("hide");
  $("foot").classList.remove("hide");
}

async function attempt(){
  const pw = $("pw").value;
  if(!pw){ $("err").textContent = "Enter the passphrase."; return; }
  $("err").textContent = "Decrypting…";
  try{ render(await decrypt(pw)); }
  catch(e){ $("err").textContent = "Access denied — wrong passphrase."; $("pw").select(); }
}
$("go").addEventListener("click", attempt);
$("pw").addEventListener("keydown", e => { if(e.key === "Enter") attempt(); });
</script>
</div></body></html>
"""


# ------------------------------------------------------------------------ leak invariant
# A leaf shorter than this is not scanned per-value. Two reasons the floor is 8, not lower:
# (a) "" is a substring of every page; (b) a <8-char token risks a COINCIDENTAL hit inside the
# base64 ciphertext blob (a 64-symbol alphabet), which would be a false positive. Genuinely
# sensitive strat-ops content — seals (64 hex), idr leaves, snapshot notes — is always longer;
# a short verdict never leaks in isolation (only inside the whole payload, caught exact above).
_LEAK_MIN_TOKEN = 8


def _leaf_strings(obj):
    """Yield every STRING leaf of a JSON-shaped value (dict / list / scalar), recursively.

    Only strings are yielded on purpose: JSON null / bool / number leaves carry no strat-ops
    secret (they are structural), and their literals ("null", "true", "false") occur in the
    page's own client-side JS — scanning for them would false-positive on every build."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _leaf_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _leaf_strings(v)


def _assert_no_plaintext_leak(plaintext: str, page: str) -> None:
    """Fail closed if any decrypted strat-ops content reached the emitted HTML.

    `plaintext` is the exact JSON that was encrypted, so this is a PRECISE assertion, not the
    old ``'"records":'`` substring guess (which both false-positived on any template literal
    and missed a re-serialized leak):
    (1) the exact serialized payload must be absent;
    (2) its base64 must be absent — a transform-leak (payload embedded base64-encoded, the
        same encoding the ciphertext uses) that the exact-string check alone would miss;
    (3) no individual decrypted string leaf (verdict / seal / idr_leaf / any snapshot value)
        may appear verbatim — catching a partial or reformatted leak an exact-match misses."""
    if plaintext in page:
        raise AssertionError("leak: exact plaintext payload present in output HTML")
    if _b64(plaintext.encode("utf-8")) in page:
        raise AssertionError("leak: base64 of the plaintext payload present in output HTML")
    try:
        payload = json.loads(plaintext)
    except ValueError as exc:  # our own dumps is always valid; guard misuse -> a clear leak-failure
        raise AssertionError(f"leak check aborted: plaintext is not valid JSON ({exc})") from exc
    for value in _leaf_strings(payload.get("records", []) if isinstance(payload, dict) else payload):
        if len(value) >= _LEAK_MIN_TOKEN and value in page:
            raise AssertionError("leak: decrypted record value present in output HTML")


def build(passphrase: str, ledger_path: Optional[Path] = None,
          out_path: Optional[Path] = None, now: Optional[float] = None) -> Path:
    """Encrypt the ledger payload and write the static witness page. Returns the output path.

    Enforces the hard leak invariant defensively: the plaintext payload and the passphrase
    must NOT appear anywhere in the emitted HTML.
    """
    ledger_path = DEFAULT_LEDGER if ledger_path is None else ledger_path
    out_path = DEFAULT_OUT if out_path is None else out_path
    if not passphrase:
        raise ValueError("a non-empty passphrase is required")

    plaintext = json.dumps(payload_from_ledger(ledger_path, now=now), sort_keys=True)
    enc = encrypt_payload(plaintext, passphrase)
    page = _render_html(enc)

    # Defensive leak check — never ship plaintext strat-ops content or the passphrase.
    if passphrase in page:
        raise AssertionError("leak: passphrase present in output HTML")
    _assert_no_plaintext_leak(plaintext, page)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path


def _read_passphrase() -> str:
    """Secure passphrase input, feature-complete over methods: env (CI) -> piped stdin ->
    interactive no-echo prompt. The passphrase never lands in argv or a committed literal."""
    pw = os.environ.get(PASSPHRASE_ENV, "")
    if pw:
        return pw
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    import getpass
    return getpass.getpass("V>>--<<V passphrase: ")


def main() -> None:
    passphrase = _read_passphrase()
    if not passphrase:
        print(f"error: no passphrase. Provide it via the {PASSPHRASE_ENV} env var, a pipe "
              f"(printf %s '<passphrase>' | ...), or the interactive prompt.", file=sys.stderr)
        sys.exit(2)
    out = build(passphrase)
    payload = payload_from_ledger(DEFAULT_LEDGER)
    print(f"[strat-ops-witness] built {out}  ({payload['count']} sealed run(s), ciphertext only)")


if __name__ == "__main__":
    main()
