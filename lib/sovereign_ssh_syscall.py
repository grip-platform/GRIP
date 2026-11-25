#!/usr/bin/env python3
"""sovereign_ssh_syscall.py — "AI is a syscall": the InnerspeakerGo kernel.

A harness-agnostic syscall shim over ``bin/grip-sovereign-council``. You call
``sovereign_call("should we X?")`` and get back a :class:`SealedVerdict`: a
tamper-evident, sha256-sealed, exogenously-judged, ZERO-Anthropic answer — the
moat delivered as a single function call.

Doctrines embodied (GRIP / HAL / HAPPI):

* ``syscall-doctrine.md`` — ONE stable API; a swappable backend (``local`` |
  ``vps`` over ssh); a mockable transport (the ``runner`` seam, DIP); audit-ready
  (the council's sha256 *is* the provenance); plain-language errors.
* ``exogenous-anchor-law.md`` — the cognition is a council of N non-Anthropic
  speakers judged by a *different* non-Anthropic model. No model grades its own
  homework; the seal is the exogenous anchor. This shim NEVER evaluates its own
  answer.
* Stromme / Banach idempotence (``lean/StrommeConvergence/Idempotency.lean``) —
  the shim is a projection onto the "already-answered" set. The FIRST call runs
  the council and persists the seal; a REPEAT call with the same request-address
  is a no-op that returns the cached fixed point. ``ℱ∘ℱ = ℱ``. This is the
  "idempotent persistence of the RSI core kernel InnerspeakerGo": ``/go`` is a
  single-shot fixed point, not an 11-deep self-referential loop (which has no
  exogenous edge and is therefore theatre — ``loop-priority-external-over-theatre.md``).
* ``cryptographic-causation.md`` — every call's decision is sealed (sha256 +, when
  the council's IDR lib is available, an IDR leaf). A skeptic re-runs
  ``grip-sovereign-council --verify <sha>`` to confirm PROVEN or catch TAMPERED.

Pure stdlib (subprocess / hashlib / json / pathlib / shlex / re). No GRIP-internal
imports — the shim runs on ANY harness that can reach the council bin locally or
over ssh. The council itself is the only dependency, and it is invoked as a
black-box syscall target, never imported.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

# --- constants ---------------------------------------------------------------

# Tailscale host for the VPS (rules/operational-context.md "GRIP VPS"). The VPS
# runs HAL + has provider keys, so the council runs headless there.
DEFAULT_VPS_HOST = "grip@100.64.0.10"
DEFAULT_COUNCIL_REL = "bin/grip-sovereign-council"
DEFAULT_LEDGER_REL = "state/sovereign-syscall"
DEFAULT_TIMEOUT_S = 300
_BACKENDS = ("local", "vps")

# The council prints these two lines; we parse them back out of stdout so the
# shim works identically over a pipe or an ssh channel.
_SHA_RE = re.compile(r"PROVENANCE sha256\s*:\s*([0-9a-f]{64})")
_ANTHROPIC_RE = re.compile(r"Anthropic calls\s*:\s*(\d+)")
# A safe ssh destination: user@host / host / host:port — letters, digits and
# . _ - @ : only. Anything else (space, ;, &, |, $, backtick) is refused before
# it can reach the ssh argv — defence-in-depth beyond list-form subprocess.
_VPS_HOST_RE = re.compile(r"^[A-Za-z0-9._@:-]+$")


class SovereignSyscallError(RuntimeError):
    """Raised when the syscall cannot produce a trustworthy sealed verdict.

    The message is plain-language (syscall-doctrine rule 5): it names the backend
    and a concrete next step, never a raw errno.
    """


@dataclass(frozen=True)
class SealedVerdict:
    """The return value of the AI syscall — a provable answer, not just text."""

    request_addr: str  # sha256 of {prompt, speakers, judge, backend} — idempotence key
    sha256: str  # the council's content-seal (the moat receipt); the --verify handle
    backend: str  # "local" | "vps"
    cached: bool  # True → returned from the ledger without re-spawning (the fixed point)
    anthropic_calls: int  # sovereignty invariant — MUST be 0
    sealed_at: str  # ISO-8601 UTC
    synthesis: str  # the judge's consensus tail (may be "" if not captured)

    def verify_hint(self) -> str:
        return f"grip-sovereign-council --verify {self.sha256[:16]}"


# --- transport seam (DIP — tests inject a mock runner, zero network) ----------

RunResult = tuple[int, str, str]  # (returncode, stdout, stderr)
Runner = Callable[[Sequence[str], int], RunResult]


def _subprocess_runner(argv: Sequence[str], timeout: int) -> RunResult:
    """Default transport: run argv, capture output. The ONLY place a process spawns."""
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # ssh or python not on PATH
        raise SovereignSyscallError(
            f"transport binary not found ({exc.filename}) — is ssh (for vps) or "
            f"python3 (for local) on PATH?"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SovereignSyscallError(
            f"council did not answer within {timeout}s — the models may be slow or "
            f"(vps) the host unreachable; retry, or use backend='local'."
        ) from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# --- path + address helpers ---------------------------------------------------

def _grip_root(explicit: Optional[Path] = None) -> Path:
    """Resolve the GRIP root harness-agnostically: arg → env → ~/.claude → lib/ parent."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    for env in ("CLAUDE_DIR", "GRIP_DIR"):
        val = os.environ.get(env, "").strip()
        if val:
            return Path(val).expanduser().resolve()
    home_claude = Path.home() / ".claude"
    if (home_claude / DEFAULT_COUNCIL_REL).exists():
        return home_claude.resolve()
    # Fall back to this file's repo root (lib/ -> repo).
    return Path(__file__).resolve().parent.parent


def request_address(
    prompt: str,
    speakers: Optional[Sequence[str]],
    judge: Optional[str],
    backend: str,
) -> str:
    """Content-address the request. Same inputs → same key → same fixed point.

    Speakers are sorted so ``[a,b]`` and ``[b,a]`` address identically (set-valued
    roster). This is what makes ``sovereign_call∘sovereign_call`` idempotent.
    """
    payload = {
        "prompt": prompt,
        "speakers": sorted(speakers) if speakers else [],
        "judge": judge or "",
        "backend": backend,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(canonical).hexdigest()


def _build_argv(
    prompt: str,
    speakers: Optional[Sequence[str]],
    judge: Optional[str],
    *,
    backend: str,
    council_bin: str,
    vps_host: str,
    python_exe: str,
) -> list[str]:
    """Construct the transport argv for the chosen backend.

    The prompt is passed as a single positional ARGUMENT (never interpolated into a
    shell string); for the vps backend it is quoted via ``shlex.join`` before being
    handed to the remote shell, so a prompt containing spaces/quotes cannot inject.
    """
    council_args: list[str] = []
    if speakers:
        council_args += ["--speakers", ",".join(speakers)]
    if judge:
        council_args += ["--judge", judge]
    council_args.append(prompt)

    if backend == "local":
        return [python_exe, council_bin, *council_args]
    if backend == "vps":
        if not _VPS_HOST_RE.match(vps_host):
            raise SovereignSyscallError(
                f"refusing ssh: vps_host {vps_host!r} has characters outside "
                f"[A-Za-z0-9._@:-] — a shell-metacharacter host is an injection risk. "
                f"Set GRIP_VPS_HOST to a bare user@host."
            )
        # Run the council on the VPS from ~/.claude. shlex.join quotes every token,
        # so the prompt rides as a single argument and cannot inject. Source the
        # operator's VPS env first — ``~/.claude/.env`` then ``~/.hal/.env`` (where
        # HAL keeps its provider keys, e.g. GROQ_API_KEY) — so the council resolves
        # its keys. The keys STAY on the VPS; only the sealed sha returns over ssh
        # (more sovereign, not less). Fail-open: a missing/broken .env still attempts
        # the council.
        council = shlex.join(["python3", DEFAULT_COUNCIL_REL, *council_args])
        remote = (
            "cd ~/.claude && "
            '{ set -a; for _f in ./.env "$HOME/.hal/.env"; do [ -f "$_f" ] && . "$_f"; done; set +a; } 2>/dev/null; '
            + council
        )
        return ["ssh", vps_host, remote]
    raise SovereignSyscallError(f"unknown backend {backend!r} — expected one of {_BACKENDS}.")


def _parse_seal(stdout: str) -> tuple[str, int]:
    """Extract (sha256, anthropic_calls) from council stdout. Enforces sovereignty."""
    sha_m = _SHA_RE.search(stdout)
    if not sha_m:
        raise SovereignSyscallError(
            "council produced no 'PROVENANCE sha256' line — the run failed or the "
            "council output format changed; inspect the raw output before trusting it."
        )
    ant_m = _ANTHROPIC_RE.search(stdout)
    # Absent line is treated as unknown (>0) so a silent format change cannot smuggle
    # in an Anthropic call under the sovereignty invariant.
    anthropic_calls = int(ant_m.group(1)) if ant_m else 1
    if anthropic_calls != 0:
        raise SovereignSyscallError(
            f"sovereignty invariant violated: council reported {anthropic_calls} "
            f"Anthropic call(s) — this shim only seals ZERO-Anthropic verdicts."
        )
    return sha_m.group(1), anthropic_calls


def _synthesis_tail(stdout: str, max_lines: int = 4) -> str:
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    return " ".join(lines[-max_lines:])[:600]


# --- config + cache + persist helpers (keep the syscall itself low-CC) --------

@dataclass(frozen=True)
class _Config:
    backend: str
    council_bin: str
    ledger: Path
    vps_host: str
    python_exe: str
    runner: Runner


def _resolve_config(
    backend, runner, grip_root, ledger_dir, council_bin, vps_host, python_exe
) -> _Config:
    """Resolve every default (arg -> env -> constant) in one place."""
    backend = (backend or os.environ.get("GRIP_SOVEREIGN_BACKEND") or "local").strip()
    if backend not in _BACKENDS:
        raise SovereignSyscallError(f"unknown backend {backend!r} — expected one of {_BACKENDS}.")
    root = _grip_root(grip_root)
    return _Config(
        backend=backend,
        council_bin=council_bin or str(root / DEFAULT_COUNCIL_REL),
        ledger=Path(ledger_dir) if ledger_dir else (root / DEFAULT_LEDGER_REL),
        vps_host=vps_host or os.environ.get("GRIP_VPS_HOST") or DEFAULT_VPS_HOST,
        python_exe=python_exe or os.environ.get("GRIP_PYTHON") or "python3",
        runner=runner or _subprocess_runner,
    )


def _load_cached(ledger_file: Path) -> Optional[SealedVerdict]:
    """Return the persisted fixed point for this request, or None (miss / corrupt)."""
    if not ledger_file.exists():
        return None
    try:
        rec = json.loads(ledger_file.read_text())
        return SealedVerdict(
            request_addr=rec["request_addr"],
            sha256=rec["sha256"],
            backend=rec["backend"],
            cached=True,
            anthropic_calls=rec.get("anthropic_calls", 0),
            sealed_at=rec["sealed_at"],
            synthesis=rec.get("synthesis", ""),
        )
    except (json.JSONDecodeError, KeyError):
        return None  # corrupt entry → caller re-runs (self-heal)


def _run_error(backend: str, rc: int, stderr: str) -> str:
    hint = "VPS unreachable? try backend=local. " if backend == "vps" else ""
    return f"council exited {rc} on backend={backend}. {hint}stderr tail: {stderr.strip()[-300:] or '(empty)'}"


def _persist(ledger_file: Path, ledger: Path, verdict: SealedVerdict, prompt: str) -> None:
    """Persist the fixed point (idempotent RSI-kernel persistence)."""
    ledger.mkdir(parents=True, exist_ok=True)
    ledger_file.write_text(
        json.dumps({**asdict(verdict), "prompt_preview": prompt[:200]}, indent=2, ensure_ascii=False)
    )


# --- the syscall -------------------------------------------------------------

def sovereign_call(
    prompt: str,
    *,
    backend: Optional[str] = None,
    speakers: Optional[Sequence[str]] = None,
    judge: Optional[str] = None,
    fresh: bool = False,
    runner: Optional[Runner] = None,
    grip_root: Optional[Path] = None,
    ledger_dir: Optional[Path] = None,
    council_bin: Optional[str] = None,
    vps_host: Optional[str] = None,
    python_exe: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> SealedVerdict:
    """Spawn (or recall) a sealed, non-Anthropic council verdict — AI as a syscall.

    Idempotent: the first call with a given request-address runs the council and
    persists the seal; a later call with the same address returns the cached
    :class:`SealedVerdict` (``cached=True``) WITHOUT re-spawning — the Banach fixed
    point ``ℱ∘ℱ=ℱ``. Pass ``fresh=True`` to force a new council run.

    Args:
        prompt: the question for the council. Operator-supplied (trusted console).
        backend: ``"local"`` (default; runs the council on this machine) or ``"vps"``
            (over ssh to the VPS). Env ``GRIP_SOVEREIGN_BACKEND`` supplies the default.
        speakers: non-Anthropic speaker roster (e.g. ``["deepseek","gemini","groq"]``);
            None → the council's own default roster.
        judge: exogenous non-Anthropic judge (e.g. ``"gemini-pro"``); None → default.
        fresh: bypass the idempotence cache and re-run.
        runner: transport injection seam (tests pass a mock; default subprocess).

    Raises:
        SovereignSyscallError: with a plain-language message on any failure.
    """
    if not prompt or not prompt.strip():
        raise SovereignSyscallError("empty prompt — the council needs a question to answer.")

    cfg = _resolve_config(backend, runner, grip_root, ledger_dir, council_bin, vps_host, python_exe)
    addr = request_address(prompt, speakers, judge, cfg.backend)
    ledger_file = cfg.ledger / f"{addr[:16]}.json"

    if not fresh:
        cached = _load_cached(ledger_file)
        if cached is not None:  # the fixed point — no re-spawn
            return cached

    argv = _build_argv(
        prompt, speakers, judge,
        backend=cfg.backend, council_bin=cfg.council_bin,
        vps_host=cfg.vps_host, python_exe=cfg.python_exe,
    )
    rc, stdout, stderr = cfg.runner(argv, timeout)
    if rc != 0:
        raise SovereignSyscallError(_run_error(cfg.backend, rc, stderr))

    sha, anthropic_calls = _parse_seal(stdout)
    verdict = SealedVerdict(
        request_addr=addr,
        sha256=sha,
        backend=cfg.backend,
        cached=False,
        anthropic_calls=anthropic_calls,
        sealed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        synthesis=_synthesis_tail(stdout),
    )
    _persist(ledger_file, cfg.ledger, verdict, prompt)
    return verdict
