"""Legal-mode matter frame — deterministic backend for the activation onboarding.

The onboarding itself is conversational (AskUserQuestion, driven by the
assistant from ``skills/legal-mode/SKILL.md``). Everything that does NOT need a
model lives here so it is testable and cannot drift: which questions are
load-bearing, which workflow tier the answers imply, where matter frames are
stored, and how a frame renders for eyeballing.

Design decisions locked with the operator (2026-07-25, HITL rounds 1-2):

1. Adaptive depth — two mandatory questions (jurisdiction, task), further
   questions only when earlier answers make them load-bearing.
2. Jurisdiction is NEVER pre-filled or defaulted. Pre-selecting it recreates
   the default removed from ``modes/definitions/legal-mode.yaml`` by anchoring
   instead of by configuration.
3. Frames persist as named matters in a dedicated private repo, never in the
   GRIP repo (``rules/no-sensitive-info-in-grip.md`` — GRIP is mirrored to a
   client org). ``$GRIP_LEGAL_MATTER_STORE`` names another private repository,
   so a personal matter's frame saves beside that matter's own folder; a store
   inside a GRIP checkout is refused.
4. Jurisdiction shortcuts come from local operator config that is not shipped,
   so a public release does not disclose where the operator does business.
5. Topic is optional (added 2026-10-26). It is offered only when a fact-free
   playbook skill for some topic is installed, it only selects that playbook,
   and it never changes the required questions or the workflow tier. Unknown
   topics are valid. Whether the playbook applies to the frame's jurisdiction
   is checked inside the playbook, never inferred here.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
from grip_checkout import git, grip_checkout_root  # noqa: E402

CLAUDE_DIR = get_claude_dir()

# --- Vocabulary -------------------------------------------------------------

TASKS = ("draft", "review", "explain", "research")
POSTURES = ("our_side", "their_paper", "neutral")
DESTINATIONS = ("internal", "counterparty", "filed")

TASK_HELP = {
    "draft": "new paper going out from you",
    "review": "their paper landed on you",
    "explain": "interpret existing text, no document produced",
    "research": "find and cite authority",
}

# Topic: an optional, open label for what the matter is about.
KNOWN_TOPICS = ("divorce", "contract", "privacy", "employment")
TOPIC_HELP = {
    "divorce": "ending a marriage, with its children, maintenance and property",
    "contract": "an agreement being drafted, reviewed or enforced",
    "privacy": "personal information and data-protection duties",
    "employment": "hiring, terms, discipline or dismissal",
}
# A topic's fact-free playbook skill, used only when it is installed.
TOPIC_PLAYBOOKS = {"divorce": "navigating-sa-divorce-procedure"}
_TOPIC_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
SKILLS_DIR = CLAUDE_DIR / "skills"
MATTER_STORE_ENV = "GRIP_LEGAL_MATTER_STORE"

# Jurisdiction shortcuts live outside the shipped tree. The example file is
# committed with generic placeholders; the real list is gitignored.
JURISDICTION_CONFIG = CLAUDE_DIR / "config" / "legal-jurisdictions.local.json"
JURISDICTION_EXAMPLE = CLAUDE_DIR / "config" / "legal-jurisdictions.example.json"

# Matter frames never live in the GRIP repo.
DEFAULT_MATTER_STORE = Path.home() / "CodeTonight" / "legal-matters"
BOOTSTRAP_HINT = (
    "bash {claude}/scripts/sensitive-repo-bootstrap.sh "
    "--dir {store} --repo legal-matters "
    '--desc "Legal matter frames - private, symmetric git-crypt, no client remote"'
)

_MATTER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


# --- Frame ------------------------------------------------------------------


@dataclass
class MatterFrame:
    """The answers that frame a piece of legal work.

    ``skipped`` records honestly that the operator bypassed the onboarding, so
    nothing downstream can mistake an absent frame for a confirmed one.
    """

    matter: str
    jurisdiction: str = ""
    task: str = ""
    posture: str = ""
    destination: str = ""
    sensitive_data: bool | None = None
    skipped: bool = False
    created: str = ""
    updated: str = ""
    notes: str = ""
    history: list[str] = field(default_factory=list)
    # Last, so every existing positional construction keeps its meaning.
    topic: str = ""

    def touch(self) -> None:
        """Stamp creation time on first save, and update time on every save."""
        now = _utc_now()
        if not self.created:
            self.created = now
        self.updated = now

    def validate(self) -> list[str]:
        """Return a list of problems; empty means the frame is usable."""
        problems: list[str] = []
        if not _MATTER_NAME_RE.match(self.matter or ""):
            problems.append(
                f"matter name {self.matter!r} must be lowercase alphanumeric "
                "with . _ - separators, 1-64 chars"
            )
        if self.skipped:
            return problems
        if not self.jurisdiction.strip():
            problems.append("jurisdiction is required and has no default")
        if self.task not in TASKS:
            problems.append(f"task must be one of {', '.join(TASKS)}")
        if self.posture and self.posture not in POSTURES:
            problems.append(f"posture must be one of {', '.join(POSTURES)}")
        if self.destination and self.destination not in DESTINATIONS:
            problems.append(f"destination must be one of {', '.join(DESTINATIONS)}")
        if self.topic and not _TOPIC_RE.match(self.topic):
            problems.append(
                f"topic {self.topic!r} must be lowercase letters, digits and hyphens, "
                "starting with a letter, 1-32 chars"
            )
        for name in required_questions(asdict(self)):
            if not _answered(getattr(self, name, None)):
                problems.append(f"{name} is required for this frame but is unanswered")
        return problems


def _answered(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


# --- Branch engine ----------------------------------------------------------


def required_questions(answers: dict[str, Any] | None = None) -> list[str]:
    """Questions that are load-bearing given the answers so far.

    Always jurisdiction and task. Everything else appears only when an earlier
    answer makes it matter, so an "explain" question stays two questions while
    a filed draft deepens to five.
    """
    answers = answers or {}
    if answers.get("skipped"):
        return []

    questions = ["jurisdiction", "task"]
    task = answers.get("task", "")
    if task not in TASKS:
        return questions

    if task in ("draft", "review"):
        questions.append("posture")
    if task in ("draft", "review", "research"):
        questions.append("destination")
    if answers.get("destination") in ("counterparty", "filed"):
        questions.append("sensitive_data")
    return questions


def next_question(answers: dict[str, Any] | None = None) -> str | None:
    """The first required question still unanswered, or None when complete."""
    answers = answers or {}
    for name in required_questions(answers):
        if not _answered(answers.get(name)):
            return name
    return None


def workflow_tier(answers: dict[str, Any] | None = None) -> dict[str, Any]:
    """Map a frame to workflow tier, Broly level, and receipt obligation.

    Announced before work starts so the cost is visible before it is spent.
    """
    answers = answers or {}
    task = answers.get("task", "")
    destination = answers.get("destination", "")

    if answers.get("skipped"):
        tier, broly = "quick", "thread"
    elif destination == "filed":
        tier, broly = "deep", "cluster"
    elif task == "research" or destination == "counterparty":
        tier, broly = "standard", "fork"
    else:
        tier, broly = "quick", "thread"

    receipt_required = task == "research" or destination in ("counterparty", "filed")
    return {
        "tier": tier,
        "broly": broly,
        "receipt_required": receipt_required,
        "redaction_required": bool(answers.get("sensitive_data")),
    }


# --- Topic and playbooks ----------------------------------------------------


def normalise_topic(value: Any) -> str:
    """Trimmed and lowercased, with spaces and underscores turned into hyphens.

    No hyphen is left at either end, so ``divorce_`` and ``_divorce`` both name
    the topic ``divorce``: a trailing one would otherwise make a valid topic that
    matches no playbook. ``None`` and blank values become ``""``, which means no
    topic. The result is not validated here; ``MatterFrame.validate`` reports a
    malformed one.
    """
    if value is None:
        return ""
    return re.sub(r"[\s_]+", "-", str(value).strip().lower()).strip("-")


def available_playbooks(skills_root: Path | None = None) -> dict[str, str]:
    """The topic playbooks whose skill is installed, as ``{topic: skill}``.

    Reads ``skills_root`` (default: the GRIP install's skills folder). A mapping
    whose ``SKILL.md`` is missing is left out, so a topic never points at a
    playbook nobody can read.
    """
    root = Path(skills_root) if skills_root is not None else SKILLS_DIR
    return {
        topic: skill
        for topic, skill in TOPIC_PLAYBOOKS.items()
        if (root / skill / "SKILL.md").is_file()
    }


def optional_questions(
    answers: dict[str, Any] | None = None, playbooks: dict[str, str] | None = None
) -> list[str]:
    """Questions worth offering but never required.

    Topic is offered only while it is unanswered and at least one topic playbook
    is installed, because without one the answer changes nothing. A skipped
    frame is offered nothing. ``playbooks=None`` asks ``available_playbooks()``.
    """
    answers = answers or {}
    if answers.get("skipped"):
        return []
    if playbooks is None:
        playbooks = available_playbooks()
    if playbooks and not _answered(answers.get("topic")):
        return ["topic"]
    return []


def playbook_for(
    answers: dict[str, Any] | None = None, playbooks: dict[str, str] | None = None
) -> str | None:
    """The installed playbook skill for the frame's topic, or None.

    None for a skipped frame, a frame with no topic, or a topic with no
    installed playbook. ``playbooks=None`` asks ``available_playbooks()``.
    """
    answers = answers or {}
    topic = normalise_topic(answers.get("topic"))
    if answers.get("skipped") or not topic:
        return None
    if playbooks is None:
        playbooks = available_playbooks()
    return playbooks.get(topic)


def render_frame_line(answers: dict[str, Any] | None = None) -> str:
    """One compact line, so a stale frame is obvious rather than silent."""
    answers = answers or {}
    if answers.get("skipped"):
        return f"[{answers.get('matter', 'unnamed')} · NO FRAME SET (skipped)]"

    parts = [
        str(answers.get("matter") or "unnamed"),
        str(answers.get("jurisdiction") or "jurisdiction?"),
        str(answers.get("task") or "task?"),
    ]
    topic = normalise_topic(answers.get("topic"))
    if topic:
        parts.append(topic)
    for key in ("posture", "destination"):
        value = answers.get(key)
        if value:
            parts.append(str(value).replace("_", "-"))
    if answers.get("sensitive_data"):
        parts.append("real-data")

    plan = workflow_tier(answers)
    parts.append(f"{plan['tier']}/{plan['broly']}")
    if plan["receipt_required"]:
        parts.append("receipt")
    return "[" + " · ".join(parts) + "]"


# --- Jurisdiction shortcuts -------------------------------------------------


def load_jurisdictions() -> list[str]:
    """Operator-configured one-click options, or empty when unconfigured.

    Empty is a valid and safe state: the onboarding then asks for jurisdiction
    as free text, which anchors nothing at all.
    """
    for path in (JURISDICTION_CONFIG, JURISDICTION_EXAMPLE):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        entries = data.get("jurisdictions") if isinstance(data, dict) else data
        if isinstance(entries, list):
            names = [str(e).strip() for e in entries if str(e).strip()]
            if names:
                return names
    return []


# --- Matter store -----------------------------------------------------------


def default_matter_store() -> Path:
    """``$GRIP_LEGAL_MATTER_STORE`` when set and not blank, else the default store.

    A personal matter points the variable at the operator's private repository,
    so its frame saves beside the matter's own folder and never in GRIP.
    """
    override = os.environ.get(MATTER_STORE_ENV, "").strip()
    return Path(override).expanduser() if override else DEFAULT_MATTER_STORE


def grip_root_holding(path: Path | str) -> Path | None:
    """The GRIP root whose tree holds ``path``, or None.

    Compares the resolved path with the configured install (``get_claude_dir()``,
    read at call time) and with the checkout this module ships in, then asks git
    whether the nearest existing ancestor sits in any other GRIP clone or
    worktree (``grip_checkout.grip_checkout_root``). Works for a path not created
    yet, so a writer can refuse before it writes anything. Matter data belongs
    in none of them.
    """
    target = Path(path).expanduser().resolve()
    for root in (get_claude_dir().resolve(), _LIB.parent.resolve()):
        if target == root or root in target.parents:
            return root
    return grip_checkout_root(_nearest_existing(target))


def _nearest_existing(path: Path) -> Path:
    """``path`` or its closest ancestor that exists, so git can be asked about it."""
    return next((candidate for candidate in (path, *path.parents) if candidate.exists()), path)


def git_crypt_covers(root: Path | str, relative: str) -> bool:
    """True when git in ``root`` routes ``relative`` through ``filter=git-crypt``.

    False when ``root`` is not a git repository, when the attribute is unset, or
    when git cannot be run, so a caller that needs encryption fails closed.
    """
    code, out = git(Path(root), "check-attr", "filter", "--", relative)
    return code == 0 and out.endswith("filter: git-crypt")


def git_crypt_ready(root: Path | str) -> bool:
    """True when git-crypt is set up in the repository at ``root``.

    The attribute alone is not enough: until git-crypt is initialised or
    unlocked, the repository has no clean filter and no key, and ``git add``
    stores a file as plaintext whatever ``.gitattributes`` says. So this needs
    the clean filter configured and marked required (a failing required filter
    stops ``git add`` rather than storing plaintext) and the key that filter
    loads: the file ``git-crypt/keys/default`` in the git directory of this
    checkout. For a linked worktree that is its own folder under
    ``.git/worktrees``, not the shared ``.git``: git-crypt looks there, so a key
    only in the shared ``.git`` leaves ``git add`` failing in the worktree. Any
    other entry in that folder is not the key, so it does not count. False on
    any doubt.
    """
    root = Path(root)
    _, clean = git(root, "config", "--get", "filter.git-crypt.clean")
    _, required = git(root, "config", "--get", "filter.git-crypt.required")
    if "git-crypt" not in clean or required != "true":
        return False
    code, git_dir = git(root, "rev-parse", "--git-dir")
    if code != 0 or not git_dir:
        return False
    return (root / git_dir / "git-crypt" / "keys" / "default").is_file()


class MatterStore:
    """Named matter frames on disk, outside the GRIP repo.

    When the store has not been bootstrapped the reads return empty and the
    writes refuse loudly. It never silently falls back to a writable location,
    because the only nearby writable location is the GRIP repo itself. It
    refuses a store inside any GRIP checkout, and a frame that git would not
    route through git-crypt, so a frame is never written as plaintext.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else default_matter_store()

    @property
    def available(self) -> bool:
        return self.root.is_dir()

    def bootstrap_command(self) -> str:
        return BOOTSTRAP_HINT.format(claude=CLAUDE_DIR, store=self.root)

    def _path(self, matter: str) -> Path:
        return self.root / "matters" / f"{matter}.json"

    def list_matters(self) -> list[dict[str, Any]]:
        """Saved frames, most recently updated first."""
        if not self.available:
            return []
        found: list[dict[str, Any]] = []
        for path in sorted((self.root / "matters").glob("*.json")):
            try:
                found.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        found.sort(key=lambda f: str(f.get("updated", "")), reverse=True)
        return found

    def most_recent(self) -> dict[str, Any] | None:
        matters = self.list_matters()
        return matters[0] if matters else None

    def load(self, matter: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        path = self._path(matter)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, frame: MatterFrame) -> Path:
        """Persist a frame. Raises when the frame is invalid, or the store is
        missing, inside GRIP, or would hold the frame unencrypted."""
        problems = frame.validate()
        if problems:
            raise ValueError("cannot save an incomplete frame: " + "; ".join(problems))
        inside = grip_root_holding(self.root)
        if inside is not None:
            raise ValueError(
                f"matter store {self.root} is inside the GRIP checkout at {inside}. "
                f"Point {MATTER_STORE_ENV} at a private repository instead."
            )
        if not self.available:
            raise FileNotFoundError(
                f"matter store {self.root} does not exist. Frames hold client "
                "detail and must not land in the GRIP repo. Create it with:\n  "
                + self.bootstrap_command()
            )
        relative = f"matters/{frame.matter}.json"
        if not (git_crypt_covers(self.root, relative) and git_crypt_ready(self.root)):
            raise ValueError(
                f"{self.root / relative} would be committed as plaintext: the store must be "
                "a git-crypt repository, initialised or unlocked, whose .gitattributes "
                "routes this path through filter=git-crypt. Create one with:\n  "
                + self.bootstrap_command()
            )
        frame.touch()
        path = self._path(frame.matter)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(frame), indent=2) + "\n", encoding="utf-8")
        return path


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- CLI --------------------------------------------------------------------


_FLAG_KEYS = ("sensitive_data", "skipped")


def _parse_answers(argv: list[str]) -> dict[str, Any] | None:
    """``key=value`` pairs as answers; None, after a message, on a malformed pair."""
    answers: dict[str, Any] = {}
    for pair in argv:
        if "=" not in pair:
            print(f"expected key=value, got {pair!r}", file=sys.stderr)
            return None
        key, _, value = pair.partition("=")
        if key in _FLAG_KEYS:
            answers[key] = value.lower() in ("1", "true", "yes", "y")
        elif key == "topic":
            answers[key] = normalise_topic(value)
        else:
            answers[key] = value
    return answers


def _cmd_plan(argv: list[str]) -> int:
    """Print required and optional questions, tier, playbook and frame line."""
    answers = _parse_answers(argv)
    if answers is None:
        return 2
    playbooks = available_playbooks()
    payload = {
        "required": required_questions(answers),
        "optional": optional_questions(answers, playbooks),
        "next": next_question(answers),
        "plan": workflow_tier(answers),
        "frame_line": render_frame_line(answers),
        "playbook": playbook_for(answers, playbooks),
        "topic_playbooks": playbooks,
        "known_topics": list(KNOWN_TOPICS),
        "jurisdiction_shortcuts": load_jurisdictions(),
    }
    print(json.dumps(payload, indent=2))
    return 0


def _cmd_status(_argv: list[str]) -> int:
    store = MatterStore()
    recent = store.most_recent()
    payload = {
        "store": str(store.root),
        "store_available": store.available,
        "bootstrap_command": None if store.available else store.bootstrap_command(),
        "matter_count": len(store.list_matters()),
        "most_recent": render_frame_line(recent) if recent else None,
        "topic_playbooks": available_playbooks(),
        "jurisdiction_shortcuts": load_jurisdictions(),
    }
    print(json.dumps(payload, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "status"
    handlers = {"plan": _cmd_plan, "status": _cmd_status}
    handler = handlers.get(command)
    if handler is None:
        print(
            "usage: legal_frame.py {status|plan} [key=value ...]\n"
            "  status  store availability, matter count, most recent frame\n"
            "  plan    required and optional questions, workflow tier and topic\n"
            "          playbook for given answers",
            file=sys.stderr,
        )
        return 2
    return handler(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
