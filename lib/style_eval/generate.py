"""Generate the responses of the style evaluation: three conditions, one isolated runner.

Every condition answers the same cases under the same runner, with its style body
injected as a response-style block. The rows go to a JSONL file, one row per
(case, trial, condition, runner), and a run resumes by skipping the rows that exist.

  baseline     the body of output-styles/grip-canonical.md, the live voice
  candidate    an amended body (Wave 2 of the plan supplies it; --candidate-body)
  comparator   the body of ayghri/i-have-adhd v0.4.1 SKILL.md (--comparator-body,
               a local clone; the body is never vendored, its sha256 is recorded)

Isolation, so no live session and none of GRIP's own instructions touch a row:

  - a scratch CLAUDE_CONFIG_DIR, seeded with a copy of the account state file
    (~/.claude.json) and nothing else, so the runner loads no CLAUDE.md, no
    settings.json and no hook. Measured 2026-10-18 on Claude Code 2.1.301: an empty
    scratch directory is signed out, and so is one seeded with that file alone,
    because the sign-in lives in the keychain under the real directory. The live
    run therefore signs the scratch directory in once, by hand, before it starts
    (`CLAUDE_CONFIG_DIR=<dir> claude auth login`), and --check-login refuses to
    spend a cent until `claude auth status` there says loggedIn;
  - --setting-sources "" and a neutral temporary working directory, so no settings
    file and no project memory file loads;
  - --tools "", --no-session-persistence, --disable-slash-commands, a pinned model
    and --max-budget-usd on every call.

A multi-turn case (one with "turns") runs as one conversation: the first turn with
--session-id, the later turns with --resume, inside the same scratch directory, as
the source's scenario runner did. The row's response is the JSON transcript.

Codex and OpenCode runners are declared for the per-harness measurement of Wave 3.
The Codex command carries the subscription flags after the subcommand and the
runner blanks both API keys (rules/gpt-on-the-subscription-never-credits.md).
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import CONDITIONS, READER_FACTS

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEFAULT_CASES = HERE / "cases.jsonl"
#: The voice before Wave 2, frozen. GRIP#8999 replaced output-styles/grip-canonical.md,
#: so a run against the live file would compare the change with itself. The
#: registration's amendment of 2026-11-08 records this file's sha256.
DEFAULT_BASELINE = HERE / "baseline-before-wave-2.md"
PINNED_MODEL = "claude-sonnet-5-5"
MAX_BUDGET_USD = 25.0
CALL_TIMEOUT_S = 300
#: The variables a runner must not inherit from the session that launched it: its
#: settings and its identity. A gateway URL (ANTHROPIC_BASE_URL) is not one of them.
SESSION_ENV_PREFIXES = ("CLAUDE", "ANTHROPIC_DEFAULT_", "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL")
#: Credentials reach a row only under the paid opt-in that
#: rules/claude-cap-moves-to-the-next-subscription.md names. A gateway's own token
#: travels with the gateway URL, because the gateway cannot authenticate without it.
CREDENTIAL_VARIABLES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
GATEWAY_URL = "ANTHROPIC_BASE_URL"
GATEWAY_TOKEN = "ANTHROPIC_AUTH_TOKEN"
PAID_OPT_IN = "GRIP_CLAUDE_ALLOW_PAID"

RUNNERS = {
    "claude": {
        "command": ["claude", "--disable-slash-commands", "--print", "--output-format", "json",
                    "--setting-sources", "", "--tools", "", "--model", PINNED_MODEL],
        "single_turn_flags": ["--no-session-persistence"],
        "response_format": "claude-json",
        "budget_flag": "--max-budget-usd",
        "scenario": True,
    },
    "codex": {
        "command": ["codex", "exec", "--ephemeral", "--ignore-user-config", "--sandbox", "read-only",
                    "--skip-git-repo-check", "--json",
                    "-c", "model_provider=openai", "-c", "forced_login_method=chatgpt"],
        "response_format": "codex-jsonl",
        "env": {"OPENAI_API_KEY": "", "CODEX_API_KEY": ""},
    },
    "opencode": {
        "command": ["opencode", "run", "--format", "json", "--pure"],
        "response_format": "opencode-json",
    },
}

REQUIRED_CASE_KEYS = ("id", "category", "prompt", "risk", "criteria", "probes")
RISKS = ("low", "medium", "high")


# ---- cases ------------------------------------------------------------------------------------

def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_cases(path: Path = DEFAULT_CASES) -> list:
    return read_jsonl(path)


def _validate_case(case: dict, where: str) -> list:
    missing = [key for key in REQUIRED_CASE_KEYS if key not in case]
    if missing:
        return [f"{where}: missing {', '.join(missing)}"]
    errors = []
    if case["risk"] not in RISKS:
        errors.append(f"{where}: risk must be one of {RISKS}")
    criteria = case["criteria"]
    if not criteria or not all(isinstance(c, str) and c.strip() for c in criteria):
        errors.append(f"{where}: criteria must be non-empty strings")
    unknown = [p for p in case["probes"] if p not in READER_FACTS]
    if unknown or not case["probes"]:
        errors.append(f"{where}: probes must name reader facts {sorted(READER_FACTS)}, got {case['probes']}")
    return errors + _validate_turns(case, where)


def _validate_turns(case: dict, where: str) -> list:
    turns = case.get("turns")
    if turns is None:
        return []
    if not isinstance(turns, list) or len(turns) < 2:
        return [f"{where}: turns needs at least two entries"]
    shaped = [isinstance(t, dict) and isinstance(t.get("id"), str) and isinstance(t.get("prompt"), str)
              and bool(t["prompt"].strip()) for t in turns]
    if not all(shaped):
        return [f"{where}: each turn needs an id and a non-empty prompt"]
    ids = [t["id"] for t in turns]
    return [f"{where}: duplicate turn id"] if len(set(ids)) != len(ids) else []


def validate_cases(cases: list) -> list:
    """Every defect in the case set, as sentences; empty when the set is sound."""
    errors = []
    for index, case in enumerate(cases):
        errors += _validate_case(case, f"case {index} ({case.get('id', '?')})")
    ids = [case.get("id") for case in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1 and i is not None})
    return errors + [f"duplicate case id: {i}" for i in duplicates]


def cases_digest(cases: list) -> str:
    """The sha256 of the case set as shown to the runner: ids, prompts, criteria, turns."""
    shown = [[c["id"], c["prompt"], c["criteria"], c.get("turns", [])] for c in cases]
    return hashlib.sha256(json.dumps(shown, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


# ---- bodies and prompts --------------------------------------------------------------------------

def strip_frontmatter(text: str) -> str:
    """The text without a leading YAML frontmatter block. An unclosed block is left alone."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    closers = [i for i in range(1, len(lines)) if lines[i].strip() == "---"]
    return "\n".join(lines[closers[0] + 1:]).lstrip("\n") if closers else text


def body_of(path: Path) -> str:
    body = strip_frontmatter(path.read_text(encoding="utf-8")).strip()
    if not body:
        raise ValueError(f"{path}: the style body is empty")
    return body


def body_digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def condition_prompt(task: str, body: str) -> str:
    """The task wrapped with the condition's style body. Every condition has a body:
    GRIP's baseline is its live voice, not a bare prompt."""
    return ("Follow the response-style instructions below while completing the task. "
            "Do not discuss or quote them.\n\n"
            f"<response_style>\n{body}\n</response_style>\n\n"
            f"<task>\n{task}\n</task>")


# ---- the isolated runner -------------------------------------------------------------------------

def seeded_config_dir(parent: Path, home: Path | None = None) -> Path:
    """A scratch Claude Code configuration directory under ``parent``, holding a copy
    of the account state file and nothing else: no CLAUDE.md, no settings, no hook.
    The sign-in is the operator's one-time step there (see the module docstring)."""
    home = Path.home() if home is None else Path(home)
    scratch = parent / "claude-config"
    scratch.mkdir(parents=True, exist_ok=True)
    state = home / ".claude.json"
    if state.exists():
        shutil.copy(state, scratch / ".claude.json")
    return scratch


def isolated_env() -> dict:
    """This process's environment without what a live Claude Code session exports:
    its effort level, model aliases, session ids and messaging token. An Anthropic
    credential stays only when GRIP_CLAUDE_ALLOW_PAID=1, so by default a row bills
    the scratch directory's subscription sign-in and never the paid key. A gateway
    URL stays, and so does its token."""
    if os.environ.get(PAID_OPT_IN) == "1":
        kept = set(CREDENTIAL_VARIABLES)
    else:
        kept = {GATEWAY_TOKEN} if os.environ.get(GATEWAY_URL) else set()
    return {key: value for key, value in os.environ.items()
            if not key.startswith(SESSION_ENV_PREFIXES) and (key not in CREDENTIAL_VARIABLES or key in kept)}


def logged_in(config_dir: Path, run=subprocess.run) -> bool:
    """Whether `claude auth status` under ``config_dir`` reports a sign-in."""
    try:
        done = run(["claude", "auth", "status"], capture_output=True, text=True, timeout=30,
                   env={**isolated_env(), "CLAUDE_CONFIG_DIR": str(config_dir)}, check=False)
        return bool(json.loads(done.stdout).get("loggedIn"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _parse_text(output: str) -> tuple:
    return output.strip(), {}, None


def _parse_claude_json(output: str) -> tuple:
    payload, _ = json.JSONDecoder().raw_decode(output.lstrip())
    if payload.get("is_error") or str(payload.get("subtype", "")).startswith("error"):
        raise RuntimeError(f"runner error: {str(payload.get('result', ''))[:200]}")
    return str(payload.get("result", "")).strip(), payload.get("usage") or {}, payload.get("total_cost_usd")


def _events(output: str) -> list:
    events = []
    for line in output.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return [e for e in events if isinstance(e, dict)]


def _parse_codex_jsonl(output: str) -> tuple:
    events = _events(output)
    messages = [e["item"].get("text", "") for e in events
                if e.get("type") == "item.completed" and (e.get("item") or {}).get("type") == "agent_message"]
    usages = [e.get("usage") for e in events if e.get("type") == "turn.completed" and e.get("usage")]
    return str(messages[-1] if messages else "").strip(), (usages[-1] if usages else {}), None


def _parse_opencode_json(output: str) -> tuple:
    parts = [(e.get("part") or e) for e in _events(output)]
    texts = [p["text"] for p in parts if p.get("type") == "text" and isinstance(p.get("text"), str)]
    return "\n".join(texts).strip(), {}, None


_PARSERS = {"text": _parse_text, "claude-json": _parse_claude_json,
            "codex-jsonl": _parse_codex_jsonl, "opencode-json": _parse_opencode_json}


def parse_response(output: str, response_format: str) -> tuple:
    """(text, usage, cost_usd or None) from one runner's stdout."""
    parser = _PARSERS.get(response_format)
    if parser is None:
        raise ValueError(f"unsupported response format: {response_format}")
    return parser(output)


def _invoke(command: list, prompt: str, cwd: Path, env: dict, retries: int) -> str:
    """Run the runner once, with the prompt on stdin, retrying a failed process."""
    done = None
    for attempt in range(retries + 1):
        done = subprocess.run(command, input=prompt, capture_output=True, text=True, cwd=str(cwd),
                              env=env, timeout=CALL_TIMEOUT_S, check=False)
        if done.returncode == 0:
            return done.stdout
        time.sleep(min(2 ** attempt, 5) if attempt < retries else 0)
    detail = (done.stderr or done.stdout).strip()[:400]
    raise RuntimeError(f"runner failed after {retries + 1} attempts ({shlex.join(command[:3])} ...): {detail}")


class Runner:
    """One configured runner. ``invoke`` is injectable so tests run with no process."""

    def __init__(self, name: str, spec: dict | None = None, scratch: Path | None = None, invoke=None,
                 home: Path | None = None):
        self.name = name
        self.spec = spec or RUNNERS[name]
        self.scratch = Path(scratch) if scratch else Path(tempfile.mkdtemp(prefix="style-eval-"))
        self.invoke = invoke or _invoke
        self.config_dir = seeded_config_dir(self.scratch, home) if name == "claude" else None

    def env(self) -> dict:
        env = isolated_env()
        env.update(self.spec.get("env", {}))
        if self.config_dir is not None:
            env["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        return env

    def model(self):
        command = self.spec["command"]
        return command[command.index("--model") + 1] if "--model" in command else None

    def command(self, remaining_usd: float | None, extra: list) -> list:
        command = [*self.spec["command"], *extra]
        if self.spec.get("budget_flag") and remaining_usd is not None:
            command += [self.spec["budget_flag"], f"{remaining_usd:.4f}"]
        return command

    def answer(self, prompt: str, remaining_usd: float | None, retries: int = 2, extra: list | None = None) -> tuple:
        """(text, usage, cost) for one prompt. The prompt goes in on stdin: the
        command ends in an option that takes a value, so a trailing argument would be
        swallowed as that option's value."""
        cwd = self.scratch / "cwd"
        cwd.mkdir(exist_ok=True)
        output = self.invoke(self.command(remaining_usd, extra or []), prompt, cwd, self.env(), retries)
        return parse_response(output, self.spec.get("response_format", "text"))


# ---- rows ---------------------------------------------------------------------------------------------

def row_key(row: dict) -> tuple:
    return (row["case_id"], int(row["trial"]), row["condition"], row["runner"])


def completed_keys(rows: list) -> set:
    return {row_key(row) for row in rows}


def _single_turn(runner: Runner, case: dict, body: str, remaining: float | None) -> tuple:
    extra = list(runner.spec.get("single_turn_flags", []))
    return runner.answer(condition_prompt(case["prompt"], body), remaining, extra=extra)


def _conversation(runner: Runner, case: dict, body: str, remaining: float | None) -> tuple:
    """A multi-turn case as one resumed conversation. The response is the JSON
    transcript; cost is the sum over turns."""
    if not runner.spec.get("scenario"):
        raise RuntimeError(f"runner {runner.name} cannot run a multi-turn case")
    session = str(uuid.uuid4())
    transcript, usage, spent = [], {}, 0.0
    for index, turn in enumerate(case["turns"]):
        prompt = condition_prompt(turn["prompt"], body) if index == 0 else turn["prompt"]
        extra = ["--resume", session] if index else ["--session-id", session]
        left = None if remaining is None else max(remaining - spent, 0.0)
        text, turn_usage, cost = runner.answer(prompt, left, extra=extra)
        spent += float(cost or 0.0)
        usage = turn_usage or usage
        transcript.append({"turn_id": turn["id"], "prompt": turn["prompt"], "response": text})
    return json.dumps(transcript, ensure_ascii=False), usage, spent


def _row(runner: Runner, case: dict, trial: int, condition: str, body: str, remaining: float | None) -> dict:
    run = _conversation if case.get("turns") else _single_turn
    text, usage, cost = run(runner, case, body, remaining)
    return {"case_id": case["id"], "trial": trial, "condition": condition, "runner": runner.name,
            "model": runner.model(), "body_sha256": body_digest(body), "response": text, "usage": usage,
            "cost_usd": cost, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _check_generate_inputs(cases: list, bodies: dict, budget_usd: float) -> None:
    errors = validate_cases(cases)
    if errors:
        raise ValueError("\n".join(errors))
    if not 0 < budget_usd <= MAX_BUDGET_USD:
        raise ValueError(f"budget_usd must be in (0, {MAX_BUDGET_USD}]")
    unknown = sorted(set(bodies) - set(CONDITIONS))
    if unknown:
        raise ValueError(f"unknown conditions: {unknown}")


def generate(cases: list, bodies: dict, runner: Runner, trials: int, out: Path, *,
             budget_usd: float = 5.0, only: set | None = None, log=print) -> dict:
    """Write one row per (case, trial, condition). Resumes: rows already in ``out``
    are skipped. Stops when the reported cost reaches ``budget_usd``. Returns the
    run's counts and spend."""
    _check_generate_inputs(cases, bodies, budget_usd)
    prior = read_jsonl(out)
    done = completed_keys(prior)
    spent = sum(float(r.get("cost_usd") or 0.0) for r in prior if r.get("runner") == runner.name)
    chosen = [c for c in cases if not only or c["id"] in only]
    work = itertools.product(range(1, trials + 1), chosen, list(bodies.items()))
    written, skipped = 0, 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        for trial, case, (condition, body) in work:
            if (case["id"], trial, condition, runner.name) in done:
                skipped += 1
                continue
            remaining = budget_usd - spent
            if remaining <= 0:
                log(f"budget of ${budget_usd:.2f} reached after ${spent:.4f}; stopping")
                return {"written": written, "skipped": skipped, "spent_usd": round(spent, 6), "stopped": "budget"}
            row = _row(runner, case, trial, condition, body, remaining)
            spent += float(row["cost_usd"] or 0.0)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            written += 1
            log(f"{condition} trial {trial}: {case['id']} (${spent:.4f} so far)")
    return {"written": written, "skipped": skipped, "spent_usd": round(spent, 6), "stopped": "done"}


# ---- command line ----------------------------------------------------------------------------------------

def _parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the responses of GRIP's style evaluation.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, help="the JSONL file of rows (resumed when it exists)")
    parser.add_argument("--runner", choices=sorted(RUNNERS), default="claude")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--budget-usd", type=float, default=5.0)
    parser.add_argument("--conditions", nargs="+", choices=CONDITIONS, default=list(CONDITIONS))
    parser.add_argument("--baseline-body", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--candidate-body", type=Path, help="the amended style body (required for candidate)")
    parser.add_argument("--comparator-body", type=Path, help="the source skill's SKILL.md (required for comparator)")
    parser.add_argument("--case", action="append", default=[], help="run only this case id (repeatable)")
    parser.add_argument("--scratch", type=Path, help="the scratch directory (signed in by hand for a live run)")
    parser.add_argument("--validate", action="store_true", help="validate the cases and exit")
    parser.add_argument("--check-login", action="store_true",
                        help="refuse to run unless the scratch configuration directory is signed in")
    return parser.parse_args(argv)


def bodies_from(args: argparse.Namespace) -> dict:
    paths = {"baseline": args.baseline_body, "candidate": args.candidate_body, "comparator": args.comparator_body}
    missing = [c for c in args.conditions if paths[c] is None]
    if missing:
        raise ValueError("a body path is required for: " + ", ".join(f"--{c}-body" for c in missing))
    return {condition: body_of(paths[condition]) for condition in args.conditions}


def main(argv=None) -> int:
    args = _parse(argv)
    cases = load_cases(args.cases)
    errors = validate_cases(cases)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    if args.validate:
        print(f"{len(cases)} cases valid; sha256 {cases_digest(cases)}")
        return 0
    if args.out is None:
        print("--out is required to generate", file=sys.stderr)
        return 2
    runner = Runner(args.runner, scratch=args.scratch)
    if args.check_login and runner.config_dir is not None and not logged_in(runner.config_dir):
        print(f"not signed in under {runner.config_dir}; run: CLAUDE_CONFIG_DIR={runner.config_dir} claude auth login",
              file=sys.stderr)
        return 4
    result = generate(cases, bodies_from(args), runner, args.trials, args.out, budget_usd=args.budget_usd,
                      only=set(args.case) or None)
    print(json.dumps(result))
    return 0 if result["stopped"] == "done" else 3


if __name__ == "__main__":
    sys.exit(main())
