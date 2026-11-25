"""onboarding_interview — PURE testable core for the /onboarding-interview skill.

This module is the DIP-clean, ZERO-I/O heart of the conversational onboarding
interview that gets to know a new operator BEYOND the fixed activate-grip-tui
steps and then sets GRIP up optimally. It COMPOSES existing primitives and
reimplements none of them — every function here produces DATA (a descriptor, a
string, a payload dict); a thin impure runner (not in this file) executes the
descriptors via injected real shims (``output_tier.set_tier``,
``auto_mode_selector.select_modes``, ``activate_grip_state.write_onboarded`` /
``write_identity``, ``precog.idr.build_idr`` / ``append_idr``,
``context_chain.append_context``, ``idr_forest.*``, ``prove_it.*``).

Four surfaces
-------------
* ``QUESTION_BANK`` — the interview questions (data), each branch-guarded so no
  operator ever sees another persona branch's follow-ups (progressive disclosure).
* ``map_answers_to_config(answers)`` -> ``list[ConfigAction]`` — deterministic,
  pure. Turns answers into GRIP config-action DESCRIPTORS (op + args +
  plain-language provenance). It NEVER performs the action.
* ``render_profile_card(profile)`` -> ``str`` — deterministic plain-text Operator
  Profile Card. No ANSI escapes are ever emitted, so a chunk boundary can never
  fall inside an escape sequence (the decoration-in-content-stream bug class).
  Fail-closed: withholds the whole card when the provenance seal does not verify.
* ``build_interview_idr(answers, config_actions)`` -> ``dict`` — the IDR
  sub-forest PAYLOAD a caller signs via ``lib.precog.idr`` / ``lib.idr_forest``.
  Does NOT sign — pure. Decision leaves chain root->leaf by content-address;
  belief nodes cross-link each decision via ``records_idr`` (the
  cryptographic-causation moat, dogfooded on the operator's own onboarding).

Grounded against canonical GRIP APIs (verify-canonical, 2026-07-15)
------------------------------------------------------------------
* Output tiers are ``("caveman_plain", "simple", "eli5")``; the operator-facing
  labels ``super-simple`` / ``medium`` / ``detailed`` are ALIASES that resolve
  to those internals (mirrors ``lib/output_tier.TIER_ALIASES`` exactly).
* ``lib.idr_forest.empty_forest`` REJECTS any root not matching an admissible
  exogenous anchor (``human:`` / ``ci:`` / ``council:`` / ``hypo:``) — so the
  human confirmation anchor is ``"human:onboarding:<fingerprint>"``.
* Mode files carry a ``-mode`` suffix (``modes/definitions/code-mode.yaml``).
  There is NO ``design-mode.yaml`` — design work stacks the real
  ``marketing-mode`` rather than a fabricated mode (no-stubs).
* ``create-pr`` is a COMMAND (``commands/create-pr.md``), NOT a skill — it is
  surfaced as a command alias, never as a surfaced skill.
* This core NEVER emits ``grip_model.set_model`` NOR ``grip_tier.set_tier`` (the
  PLAN / context-envelope tier). ``output_tier.set_tier`` (VERBOSITY, modelled
  here as the ``set_output_tier`` descriptor op) is the only permitted tier
  setter. The interview is ADD-only: it structurally cannot undo a fixed
  activate-grip-tui choice (name / signature / plan-tier / frontier-model).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Optional

__all__ = [
    "BranchGuard",
    "Question",
    "ConfigAction",
    "QUESTION_BANK",
    "OUTPUT_TIER_ALIASES",
    "DEFAULT_OUTPUT_TIER",
    "FORBIDDEN_OPS",
    "resolve_output_tier",
    "is_visible",
    "visible_questions",
    "content_address",
    "map_answers_to_config",
    "render_profile_card",
    "build_interview_idr",
]


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BranchGuard:
    """A progressive-disclosure guard for a branch question.

    The question is shown iff ``context[key] == value``. ``key`` names either a
    prior QUESTION_BANK answer (e.g. ``"primary_work"``) or a fixed
    activate-grip-tui step already captured in ``.onboarded.json`` (e.g.
    ``"mode"`` for the solo/team boolean — composition, not duplication).
    """

    key: str
    value: str


@dataclass(frozen=True)
class Question:
    """One interview question. ``guard is None`` marks a cold seed (asked of
    everyone); a non-None guard marks a branch question shown only when the guard
    matches. ``options`` are the fixed AskUserQuestion choices (<=4 unless a
    free-text closer)."""

    id: str
    text: str
    why: str
    options: tuple[str, ...]
    guard: Optional[BranchGuard] = None


@dataclass(frozen=True)
class ConfigAction:
    """A single GRIP configuration DESCRIPTOR produced by the mapper.

    The mapper emits these; the impure runner executes them. Frozen + tuple-only
    ``args`` makes every action hashable, value-comparable, and order-deterministic
    (so replay is byte-stable and mutation tests bite).

    Attributes
    ----------
    op:
        The verb, e.g. ``stack_mode`` / ``surface_skills`` / ``set_output_tier`` /
        ``open_branch`` / ``command_alias`` / ``infer_modes`` / ``arm_ultragoal`` /
        ``enable_gates`` / ``set_autonomy`` / ``set_cc_trust`` / ``write_identity`` /
        ``surface_skills_opt_in`` / ``clarify`` / ``note``.
    args:
        Operands, ALWAYS a tuple of strings (hashable + deterministic order).
    because:
        Plain-language provenance — the operator's own words that caused this
        action ("because you said 'Ship software'"). Load-bearing: the profile
        card cites it per line and ``prove_it`` verifies it against the transcript.
    question_id:
        The QUESTION_BANK id (or the synthetic ``"cascade"`` / ``"contradiction"``)
        that produced the action.
    answer:
        The verbatim answer token that caused it.
    """

    op: str
    args: tuple[str, ...]
    because: str
    question_id: str
    answer: str


# --------------------------------------------------------------------------- #
# Canonical option strings (defined once, reused by QUESTION_BANK, the mapper
# dispatch tables, and the guards — so a rename can never silently drift a guard
# out of sync with its question's options).
# --------------------------------------------------------------------------- #
_PW_SHIP = "Ship software — code, PRs, tests, deploys"
_PW_DESIGN = "Design products & UX"
_PW_WRITE = "Write & research — content, docs, analysis"
_PW_COMPANY = "Run a company — strategy, deals, ops"

_FG_FEATURE = "Ship a specific feature or fix"
_FG_HARDEN = "Review & harden existing work"
_FG_WRITE = "Draft or polish writing"
_FG_DECIDE = "Make a decision or plan strategy"

_REG_LEAST = "Just the answer — least words"
_REG_BALANCED = "Balanced — answer plus the key why"
_REG_FULL = "Full detail with the audit trail"
_REG_AUTO = "Let GRIP pick per task"

_AUD_ME = "Just me"
_AUD_TEAM = "My team (internal)"
_AUD_CLIENTS = "Clients & stakeholders"
_AUD_PUBLIC = "Public / the internet"

_AU_ASK = "Ask me before most changes"
_AU_CLEAR = "Act on clear tasks, ask on the fuzzy ones"
_AU_AUTO = "Run autonomously, ping me when done"
_AU_FULL = "Full-send on safe work, I review PRs"

_DS_FE = "Web / frontend"
_DS_BE = "Backend / infra"
_DS_DATA = "Data / ML"
_DS_OTHER = "Something else →"

_DR_FAST = "Ship fast — light gates"
_DR_BAL = "Balanced"
_DR_STRICT = "Strict — block on quality"
_DR_SEC = "Security-critical"

_FF_FUND = "Fundraising"
_FF_LEGAL = "Legal & compliance"
_FF_HIRE = "Hiring & team"
_FF_PROD = "Product & delivery"

_DES_FIGMA = "Figma / design tools"
_DES_CODE = "Code (Tailwind / React)"
_DES_BRAND = "Brand & visual systems"
_DES_HANDOFF = "Handoff — both"

_WF_SOCIAL = "Social posts (LinkedIn)"
_WF_LONG = "Long-form articles"
_WF_COPY = "Marketing / product copy"
_WF_DOCS = "Technical docs"

_TM_FREE = "(free text — teammate names)"
_TM_SKIP = "Skip — I work solo"


# --------------------------------------------------------------------------- #
# QUESTION_BANK — 5 cold seeds + 6 branch/conditional questions.
# --------------------------------------------------------------------------- #
QUESTION_BANK: tuple[Question, ...] = (
    Question(
        id="primary_work",
        text="What will you mostly point GRIP at, day to day?",
        why="Seed 1/5, cold — the persona pivot: sets the default mode, re-ranks "
        "which skills surface first, and opens exactly one persona branch.",
        options=(_PW_SHIP, _PW_DESIGN, _PW_WRITE, _PW_COMPANY),
    ),
    Question(
        id="first_goal",
        text="What's the first real thing you'd want GRIP to help you get done? "
        "(say it in your words)",
        why="Seed 2/5, cold — the load-bearing free-text: one Pool-B inference "
        "pass stacks the mode set, arms a durable goal, seeds welcome-memory, and "
        "becomes the profile-card headline. Chips are only fast starters.",
        options=(_FG_FEATURE, _FG_HARDEN, _FG_WRITE, _FG_DECIDE),
    ),
    Question(
        id="comms_register",
        text="How do you like answers delivered?",
        why="Seed 3/5, cold — sets output verbosity only (never the plan tier or "
        "the model). Orthogonal to who-reads, so asked outright, never inferred.",
        options=(_REG_LEAST, _REG_BALANCED, _REG_FULL, _REG_AUTO),
    ),
    Question(
        id="audience",
        text="Who reads what GRIP produces?",
        why="Seed 4/5, cold — sets tone AND safety posture (redaction / public "
        "release / visual-leak sweep / no-shilling). Orthogonal to verbosity.",
        options=(_AUD_ME, _AUD_TEAM, _AUD_CLIENTS, _AUD_PUBLIC),
    ),
    Question(
        id="autonomy",
        text="How much should GRIP do before checking with you?",
        why="Seed 5/5, cold — calibrates DISCRETIONARY posture only (confidence "
        "gate, flow eligibility, /afk readiness). Never the mechanical deny-gates "
        "or the four HALT classes, which always hold.",
        options=(_AU_ASK, _AU_CLEAR, _AU_AUTO, _AU_FULL),
    ),
    Question(
        id="dev_stack",
        text="What's your main stack?",
        why="Branch — shown only when primary_work is Ship software. Surfaces the "
        "stack-specific real skill cluster and wires the matching integrations.",
        options=(_DS_FE, _DS_BE, _DS_DATA, _DS_OTHER),
        guard=BranchGuard("primary_work", _PW_SHIP),
    ),
    Question(
        id="dev_rigour",
        text="How hard should GRIP push on tests and review?",
        why="Branch — shown only for developers. Sets the CC trust posture against "
        "the real cc-enforcement thresholds (RELAXED 20/40, DEFAULT 15/30, "
        "STRICT 10/20).",
        options=(_DR_FAST, _DR_BAL, _DR_STRICT, _DR_SEC),
        guard=BranchGuard("primary_work", _PW_SHIP),
    ),
    Question(
        id="founder_front",
        text="Which front is most active for you right now?",
        why="Branch — shown only when primary_work is Run a company. SURFACES "
        "(opt-in, never auto-runs — high-stakes) the matching real cluster.",
        options=(_FF_FUND, _FF_LEGAL, _FF_HIRE, _FF_PROD),
        guard=BranchGuard("primary_work", _PW_COMPANY),
    ),
    Question(
        id="design_surface",
        text="Where does most of your design work live?",
        why="Branch — shown only when primary_work is Design products & UX. "
        "Surfaces real design skills; stacks marketing-mode for brand work (there "
        "is no design-mode.yaml, so no fake mode is stacked).",
        options=(_DES_FIGMA, _DES_CODE, _DES_BRAND, _DES_HANDOFF),
        guard=BranchGuard("primary_work", _PW_DESIGN),
    ),
    Question(
        id="writer_focus",
        text="What do you write most?",
        why="Branch — shown only when primary_work is Write & research. Stacks "
        "writing-mode + research-mode and surfaces the real skill per format.",
        options=(_WF_SOCIAL, _WF_LONG, _WF_COPY, _WF_DOCS),
        guard=BranchGuard("primary_work", _PW_WRITE),
    ),
    Question(
        id="teammates",
        text="Who else works with you? (names, comma-separated — skip if solo)",
        why="Conditional — shown only when the fixed activate-grip-tui step already "
        "set mode=team (composition, not duplication). Optional-by-design so it "
        "stays zero-friction; solo operators never see it.",
        options=(_TM_FREE, _TM_SKIP),
        guard=BranchGuard("mode", "team"),
    ),
)


# --------------------------------------------------------------------------- #
# Output-tier alias resolution (mirrors lib/output_tier.TIER_ALIASES exactly) +
# the forbidden-op invariant.
# --------------------------------------------------------------------------- #
OUTPUT_TIER_ALIASES: dict[str, str] = {
    "super-simple": "eli5",
    "medium": "simple",
    "detailed": "caveman_plain",
    # identity — accept the internal names too
    "eli5": "eli5",
    "simple": "simple",
    "caveman_plain": "caveman_plain",
}
DEFAULT_OUTPUT_TIER = "simple"

# Ops this core must NEVER emit — the PLAN/context-envelope tier (grip_tier.set_tier,
# modelled as bare "set_tier"/"set_plan_tier") and the frontier model
# (grip_model.set_model). The only permitted tier setter is "set_output_tier".
FORBIDDEN_OPS: frozenset[str] = frozenset(
    {"set_tier", "set_plan_tier", "set_model", "grip_tier", "grip_model"}
)


def resolve_output_tier(alias: str) -> str:
    """Resolve an operator-facing verbosity label to its canonical INTERNAL tier.

    ``super-simple`` -> ``eli5``, ``medium`` -> ``simple``, ``detailed`` ->
    ``caveman_plain``. Unknown/blank -> the default ``simple``. Mirrors
    ``lib/output_tier.TIER_ALIASES`` (verify-canonical 2026-07-15). Pure.
    """
    return OUTPUT_TIER_ALIASES.get(alias, DEFAULT_OUTPUT_TIER)


_REGISTER_ALIAS: dict[str, str] = {
    _REG_LEAST: "super-simple",
    _REG_BALANCED: "medium",
    _REG_FULL: "detailed",
    # _REG_AUTO deliberately absent -> no set_output_tier is emitted.
}

_PERSONA_BY_PRIMARY: dict[str, str] = {
    _PW_SHIP: "developer",
    _PW_DESIGN: "designer",
    _PW_WRITE: "writer",
    _PW_COMPANY: "founder",
}


# --------------------------------------------------------------------------- #
# Progressive disclosure (makes the branch guards non-decorative — no-stubs).
# --------------------------------------------------------------------------- #
def is_visible(question: Question, context: dict) -> bool:
    """True if ``question`` should be shown given prior answers/context.

    Cold seeds (``guard is None``) are always visible. A branch question is
    visible iff ``context[guard.key] == guard.value`` — the mechanical basis of
    progressive disclosure (no operator sees another branch's follow-ups). Pure.
    """
    if question.guard is None:
        return True
    return context.get(question.guard.key) == question.guard.value


def visible_questions(context: dict) -> tuple[Question, ...]:
    """The ordered subset of ``QUESTION_BANK`` to ask, given prior answers/context.

    Preserves QUESTION_BANK order and drops every branch whose guard does not
    match — so the asked path stays a bounded, per-persona set. Pure.
    """
    return tuple(q for q in QUESTION_BANK if is_visible(q, context))


# --------------------------------------------------------------------------- #
# Deterministic content addressing (pure mirror of lib.precog.idr.content_addr).
# --------------------------------------------------------------------------- #
_CONTENT_ADDR_EXCLUDE = ("id", "ts", "audit")


def _canonical(obj: object) -> str:
    """Canonical JSON (sorted keys, tight separators, unicode preserved) — the
    deterministic byte-image used for hashing. Pure."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_address(body: dict) -> str:
    """Content address of an IDR body — ``"sha256:<hex>"`` over the canonical JSON
    of the semantic body EXCLUDING the volatile fields (``id`` / ``ts`` /
    ``audit``).

    Mirrors ``lib.precog.idr.content_addr`` so that when the caller later signs
    the payload, the real signer re-derives the SAME linkage keys the payload
    already chained by. Two bodies differing in any decision byte address
    differently — that is what makes the belief<->decision cross-links and the
    leaf chain tamper-evident. Pure (no I/O).
    """
    addressed = {k: v for k, v in body.items() if k not in _CONTENT_ADDR_EXCLUDE}
    digest = hashlib.sha256(_canonical(addressed).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _derive_fingerprint(answers: dict) -> str:
    """Deterministic session fingerprint derived from the answers content — keeps
    ``build_interview_idr`` pure and replayable (no wall-clock / randomness in the
    signed body). A real runner may inject the true session fingerprint instead."""
    digest = hashlib.sha256(_canonical(answers).encode("utf-8")).hexdigest()
    return f"onboarding-{digest[:16]}"


# --------------------------------------------------------------------------- #
# Answer -> ConfigAction mapping (pure). One small handler per question keeps
# cyclomatic complexity low; dispatch tables hold the answer->bundle data.
# --------------------------------------------------------------------------- #
def _act(
    op: str, args: tuple[str, ...], because: str, question_id: str, answer: str
) -> ConfigAction:
    """Construct a ConfigAction, coercing ``args`` to a tuple of strings."""
    return ConfigAction(
        op=op,
        args=tuple(str(a) for a in args),
        because=because,
        question_id=question_id,
        answer=answer,
    )


# Persona bundles: every real skill/mode/command below was verify-canonical'd to
# exist on origin/main (skills/<name>/SKILL.md, modes/definitions/<name>.yaml,
# commands/create-pr.md). No fabricated support (no-stubs).
_PRIMARY_BUNDLES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    _PW_SHIP: (
        ("stack_mode", ("code-mode",)),
        ("surface_skills", ("design-principles", "feature-complete", "converge", "self-healing-ci")),
        ("command_alias", ("/pr", "create-pr")),  # create-pr is a COMMAND, not a skill
        ("open_branch", ("developer",)),
    ),
    _PW_DESIGN: (
        ("stack_mode", ("marketing-mode",)),  # nearest REAL mode; no design-mode.yaml
        ("surface_skills", ("figma-to-code", "mobile-responsive-ui", "swiss-nihilism-design", "wcag-image-accessibility")),
        ("open_branch", ("designer",)),
    ),
    _PW_WRITE: (
        ("stack_mode", ("writing-mode", "research-mode")),
        ("surface_skills", ("authentic-writing", "linkedin-post-craft", "medium-article-writer", "markdown-expert", "research-synthesis")),
        ("open_branch", ("writer",)),
    ),
    _PW_COMPANY: (
        ("stack_mode", ("business-mode", "strategy-mode")),
        ("surface_skills", ("leading-organisation", "decision-lifecycle", "competitive-battlecard", "strat-ops")),
        ("open_branch", ("founder",)),
    ),
}


def _map_primary_work(answer: str, answers: dict) -> list[ConfigAction]:
    """Persona pivot. A known persona expands its bundle; free-text ('a mix') is
    routed to Pool-B mode inference rather than forcing a speculative 5th persona
    (YAGNI)."""
    bundle = _PRIMARY_BUNDLES.get(answer)
    if bundle is None:
        return [
            _act(
                "infer_modes",
                (answer, "groq"),
                f"because you said '{answer}' — no single persona, so the mode stack is inferred",
                "primary_work",
                answer,
            )
        ]
    because = f"because you said '{answer}'"
    return [_act(op, args, because, "primary_work", answer) for op, args in bundle]


def _map_first_goal(answer: str, answers: dict) -> list[ConfigAction]:
    """The single Pool-B inference plus the durable 'win' wiring. The chosen chip
    OR the free text is treated verbatim as the goal (DRY — no separate closer)."""
    because = f"because your first goal is '{answer}'"
    return [
        _act("infer_modes", (answer, "groq"), because, "first_goal", answer),
        _act("arm_ultragoal", (answer,), because, "first_goal", answer),
        _act("seed_welcome_memory", (answer,), because, "first_goal", answer),
        _act("set_headline", (answer,), because, "first_goal", answer),
        _act("set_loop_anchor", (answer,), because, "first_goal", answer),
    ]


def _map_comms_register(answer: str, answers: dict) -> list[ConfigAction]:
    """Verbosity only. Maps through the real alias layer to the internal tier; the
    ``set_output_tier`` args carry BOTH the alias and its resolved internal so a
    test can assert the resolution. 'Let GRIP pick' emits NO tier setter."""
    because = f"because you asked for answers delivered as '{answer}'"
    alias = _REGISTER_ALIAS.get(answer)
    if alias is None:  # "Let GRIP pick per task"
        return [
            _act("note", ("output-tier:auto",), because, "comms_register", answer),
            _act("set_register", ("auto",), because, "comms_register", answer),
        ]
    internal = resolve_output_tier(alias)
    return [
        _act("set_output_tier", (alias, internal), because, "comms_register", answer),
        _act("set_register", (internal,), because, "comms_register", answer),
    ]


def _map_audience(answer: str, answers: dict) -> list[ConfigAction]:
    """Tone + safety posture (orthogonal to verbosity). Clients/Public arm the
    redaction + public-release + visual-leak gates and authentic-writing; Public
    additionally arms no-shilling. Just-me stays terse and wires no scheduler."""
    because = f"because '{answer}' read what GRIP produces"
    if answer == _AUD_ME:
        return [
            _act("set_tone", ("terse-private",), because, "audience", answer),
            _act("disable_scheduler", ("update-team",), because, "audience", answer),
        ]
    actions = [_act("set_tone", ("audience-aware",), because, "audience", answer)]
    if answer in (_AUD_CLIENTS, _AUD_PUBLIC):
        actions.append(
            _act(
                "enable_gates",
                ("redaction-shield", "sanitizing-public-releases", "visual-artifact-leak-sweep-on-render"),
                because,
                "audience",
                answer,
            )
        )
        actions.append(_act("set_flag", ("authentic-writing:always_on",), because, "audience", answer))
    actions.append(_act("surface_skills", ("update-team",), because, "audience", answer))
    if answer == _AUD_PUBLIC:
        actions.append(_act("enable_gates", ("no-shilling",), because, "audience", answer))
    return actions


_AUTONOMY_HIGH = frozenset({_AU_AUTO, _AU_FULL})


def _map_autonomy(answer: str, answers: dict) -> list[ConfigAction]:
    """Discretionary posture only — never the mechanical deny-gates or the four
    HALT classes. High autonomy raises flow/afk appetite for THIS operator (a
    later runner writes a TTL-bounded marker); 'Ask me' writes NO yolo marker."""
    because = f"because you chose autonomy: '{answer}'"
    if answer in _AUTONOMY_HIGH:
        return [
            _act("set_autonomy", ("high",), because, "autonomy", answer),
            _act("set_confidence_gate", ("balanced",), because, "autonomy", answer),
            _act("enable_flow_mode", ("eligible",), because, "autonomy", answer),
            _act("set_afk_ready", ("true",), because, "autonomy", answer),
            _act("progressive_disclose", ("notify-target",), because, "autonomy", answer),
        ]
    if answer == _AU_ASK:
        return [
            _act("set_autonomy", ("low",), because, "autonomy", answer),
            _act("set_confidence_gate", ("strict",), because, "autonomy", answer),
            _act("disable_flow_mode", ("off",), because, "autonomy", answer),
        ]
    # "Act on clear tasks, ask on the fuzzy ones" -> balanced default
    return [
        _act("set_autonomy", ("balanced",), because, "autonomy", answer),
        _act("set_confidence_gate", ("default",), because, "autonomy", answer),
    ]


_DEV_STACK_SKILLS: dict[str, tuple[str, ...]] = {
    _DS_FE: ("mobile-responsive-ui", "figma-to-code", "e2e-test-generation", "wcag-image-accessibility"),
    _DS_BE: ("gitops", "github-actions-setup", "self-healing-ci", "secure-code-review", "deployment-hardening"),
    _DS_DATA: ("research-synthesis", "optimizing-complexity"),
}


def _map_dev_stack(answer: str, answers: dict) -> list[ConfigAction]:
    """Developer branch. Each option surfaces a named real skill cluster and wires
    the matching integration; free-text 'Something else' is inferred."""
    because = f"because your stack is '{answer}'"
    skills = _DEV_STACK_SKILLS.get(answer)
    if skills is None:  # "Something else →" free text
        return [
            _act("infer_modes", (answer, "groq"), because, "dev_stack", answer),
            _act("note", ("stack:custom",), because, "dev_stack", answer),
        ]
    actions = [_act("surface_skills", skills, because, "dev_stack", answer)]
    if answer == _DS_FE:
        actions.append(_act("set_flag", ("dependency-guardian:strict",), because, "dev_stack", answer))
    elif answer == _DS_BE:
        actions.append(_act("wire", ("grip-gh-default-repo",), because, "dev_stack", answer))
    else:  # Data / ML
        actions.append(_act("note", ("stack:data-ml",), because, "dev_stack", answer))
    return actions


# CC trust tuples match rules/cc-enforcement.md exactly: RELAXED 20/40, DEFAULT
# 15/30, STRICT 10/20.
_CC_TRUST: dict[str, tuple[str, str, str]] = {
    _DR_FAST: ("RELAXED", "warn20", "block40"),
    _DR_BAL: ("DEFAULT", "warn15", "block30"),
    _DR_STRICT: ("STRICT", "warn10", "block20"),
    _DR_SEC: ("STRICT", "warn10", "block20"),
}


def _map_dev_rigour(answer: str, answers: dict) -> list[ConfigAction]:
    """Developer branch. Sets the CC trust posture; Strict/Security-critical also
    stack the real security-mode + testing-mode."""
    because = f"because on tests/review you chose '{answer}'"
    trust = _CC_TRUST.get(answer, ("DEFAULT", "warn15", "block30"))
    actions = [_act("set_cc_trust", trust, because, "dev_rigour", answer)]
    if answer in (_DR_STRICT, _DR_SEC):
        actions.append(_act("stack_mode", ("security-mode", "testing-mode"), because, "dev_rigour", answer))
    else:
        actions.append(_act("note", (f"cc-trust:{trust[0].lower()}",), because, "dev_rigour", answer))
    return actions


_FOUNDER_CLUSTERS: dict[str, tuple[str, ...]] = {
    _FF_FUND: ("decision-lifecycle", "competitive-battlecard", "red-teaming-strategic-decisions"),
    _FF_LEGAL: ("legal-mode", "legal-ops", "legal-provenance"),
    _FF_HIRE: ("delegating-team-work", "leading-organisation"),
    _FF_PROD: ("feature-complete", "sprint"),
}


def _map_founder_front(answer: str, answers: dict) -> list[ConfigAction]:
    """Founder branch. SURFACES the matching real cluster as opt-in — never
    auto-runs (high-stakes: the operator consents before any run)."""
    because = f"because your most active front is '{answer}'"
    cluster = _FOUNDER_CLUSTERS.get(answer, ("leading-organisation",))
    return [
        _act("surface_skills_opt_in", cluster, because, "founder_front", answer),
        _act("note", ("founder-front:" + answer.split()[0].lower(),), because, "founder_front", answer),
    ]


_DESIGN_CLUSTERS: dict[str, tuple[str, ...]] = {
    _DES_FIGMA: ("figma-to-code",),
    _DES_CODE: ("mobile-responsive-ui", "swiss-nihilism-design"),
    _DES_BRAND: ("swiss-nihilism-design", "wcag-image-accessibility"),
    _DES_HANDOFF: ("figma-to-code", "mobile-responsive-ui", "e2e-test-generation"),
}


def _map_design_surface(answer: str, answers: dict) -> list[ConfigAction]:
    """Designer branch. Surfaces the real skill cluster; brand/handoff work stacks
    the real marketing-mode (there is no design-mode.yaml — no fake mode)."""
    because = f"because your design work lives in '{answer}'"
    cluster = _DESIGN_CLUSTERS.get(answer, ("swiss-nihilism-design",))
    actions = [_act("surface_skills", cluster, because, "design_surface", answer)]
    if answer in (_DES_BRAND, _DES_HANDOFF):
        actions.append(_act("stack_mode", ("marketing-mode",), because, "design_surface", answer))
    else:
        actions.append(_act("note", ("design-surface:" + answer.split()[0].lower(),), because, "design_surface", answer))
    return actions


_WRITER_SKILL: dict[str, tuple[str, ...]] = {
    _WF_SOCIAL: ("linkedin-post-craft",),
    _WF_LONG: ("medium-article-writer", "authentic-writing"),
    _WF_COPY: ("authentic-writing",),
    _WF_DOCS: ("markdown-expert",),
}


def _map_writer_focus(answer: str, answers: dict) -> list[ConfigAction]:
    """Writer branch. Stacks writing-mode + research-mode and surfaces the real
    skill for the chosen format (external-facing formats arm authentic-writing)."""
    because = f"because you mostly write '{answer}'"
    skills = _WRITER_SKILL.get(answer, ("authentic-writing",))
    return [
        _act("stack_mode", ("writing-mode", "research-mode"), because, "writer_focus", answer),
        _act("surface_skills", skills, because, "writer_focus", answer),
    ]


def _map_teammates(answer: str, answers: dict) -> list[ConfigAction]:
    """Team-mode conditional. Named teammates seed identity + notify targets +
    team skills; 'skip'/blank is a composition-safe no-op."""
    if not answer or answer == _TM_SKIP:
        return []
    names = tuple(n.strip() for n in answer.split(",") if n.strip())
    if not names:
        return []
    return [
        _act("write_identity", names, f"because you work with {', '.join(names)}", "teammates", answer),
        _act("register_notify", names, "because they work with you", "teammates", answer),
        _act("surface_skills", ("update-team", "delegating-team-work", "standup"), "because you work as a team", "teammates", answer),
    ]


_HANDLERS: dict[str, Callable[[str, dict], list[ConfigAction]]] = {
    "primary_work": _map_primary_work,
    "first_goal": _map_first_goal,
    "comms_register": _map_comms_register,
    "audience": _map_audience,
    "autonomy": _map_autonomy,
    "dev_stack": _map_dev_stack,
    "dev_rigour": _map_dev_rigour,
    "founder_front": _map_founder_front,
    "design_surface": _map_design_surface,
    "writer_focus": _map_writer_focus,
    "teammates": _map_teammates,
}


def _cascade_actions(answers: dict) -> list[ConfigAction]:
    """Cross-answer inference: Ship AND (Clients | Public) folds the provability
    stack into the code path and makes the IDR footer a daily default. This is
    inference across answers, not a per-question lookup."""
    pw = answers.get("primary_work")
    aud = answers.get("audience")
    if pw == _PW_SHIP and aud in (_AUD_CLIENTS, _AUD_PUBLIC):
        because = f"because you ship software AND '{aud}' read it (cross-answer inference)"
        anchor = f"{pw}+{aud}"
        return [
            _act("stack_into_code_path", ("prove-it", "secure-code-review", "trust-review"), because, "cascade", anchor),
            _act("surface_daily_default", ("idr-cryptographic-causation-footer",), because, "cascade", anchor),
        ]
    return []


def _contradiction_actions(answers: dict) -> list[ConfigAction]:
    """Low-confidence pair: the fixed step set mode=solo but teammates were named.
    Return a CLARIFY descriptor so the runner resolves it via asking-users BEFORE
    persisting — never a silent guess."""
    mode = answers.get("mode")
    teammates = answers.get("teammates", "")
    named = bool(teammates) and teammates != _TM_SKIP and any(n.strip() for n in teammates.split(","))
    if mode == "solo" and named:
        return [
            _act(
                "clarify",
                ("mode-solo-but-teammates-named",),
                "because you set solo mode but named teammates — resolving before persisting",
                "contradiction",
                f"mode=solo;teammates={teammates}",
            )
        ]
    return []


def map_answers_to_config(answers: dict) -> list[ConfigAction]:
    """Map interview answers to an ordered list of GRIP config-action descriptors.

    PURE + DETERMINISTIC: iterates ``QUESTION_BANK`` order (not the answers dict's
    insertion order), so the output is independent of how ``answers`` was built.
    Emits descriptors only — performs NO I/O and NO real configuration. The impure
    runner executes them via injected shims.

    Invariants (mutation-tested):
      * >=2 actions per actionable answer option (minimality / anti-survey bar).
      * NEVER emits a ``FORBIDDEN_OPS`` op — the plan tier and the frontier model
        are ADD-only-untouchable; ``set_output_tier`` (verbosity) is the only tier
        setter.
      * On a solo-vs-teammates contradiction the teammate-persistence actions are
        SUPPRESSED and a single ``clarify`` is emitted instead (never silent-guess).

    Parameters
    ----------
    answers:
        ``{question_id: answer}`` plus optional fixed-step context keys already in
        ``.onboarded.json`` (e.g. ``"mode"``). Unknown keys are ignored.

    Returns
    -------
    list[ConfigAction]
    """
    contradiction = _contradiction_actions(answers)
    actions: list[ConfigAction] = []
    for question in QUESTION_BANK:
        if question.id not in answers:
            continue
        # Never persist teammate identity while a contradiction is unresolved.
        if question.id == "teammates" and contradiction:
            continue
        handler = _HANDLERS.get(question.id)
        if handler is not None:
            actions.extend(handler(answers[question.id], answers))
    actions.extend(_cascade_actions(answers))
    actions.extend(contradiction)
    return actions


# --------------------------------------------------------------------------- #
# IDR payload builder (the cryptographic-causation moat, dogfooded). Returns the
# PAYLOAD a caller signs via lib.precog.idr / lib.idr_forest — does NOT sign.
# --------------------------------------------------------------------------- #
def _belief_snapshot(answers: dict) -> dict:
    """The operator-model snapshot GRIP believed at decision time — the WHAT-
    GRIP-BELIEVED-WHEN half of the moat. Deterministic from the answers."""
    return {
        "persona": _PERSONA_BY_PRIMARY.get(answers.get("primary_work", ""), "unknown"),
        "primary_goal": answers.get("first_goal", ""),
        "register": resolve_output_tier(_REGISTER_ALIAS.get(answers.get("comms_register", ""), DEFAULT_OUTPUT_TIER)),
        "audience": answers.get("audience", ""),
        "autonomy": answers.get("autonomy", ""),
    }


def build_interview_idr(
    answers: dict,
    config_actions: list[ConfigAction],
    *,
    fingerprint: Optional[str] = None,
    addr_fn: Optional[Callable[[dict], str]] = None,
) -> dict:
    """Build the onboarding IDR sub-forest PAYLOAD (unsigned, pure).

    Produces one DECISION leaf per ``(question -> answer -> config_action)`` triple
    and one cross-linked BELIEF node per leaf, rooted at the operator's exogenous
    confirmation anchor ``"human:onboarding:<fingerprint>"`` (the only admissible
    ``human:`` root ``lib.idr_forest.empty_forest`` will accept). Leaves chain
    root->leaf by ``predecessor_idr`` = the content-address of the prior leaf;
    each belief cross-links its decision by ``records_idr``. A caller signs the
    returned payload via ``lib.precog.idr.build_idr`` / ``append_context`` — this
    function performs NO signing and NO I/O.

    The returned leaf dicts are shaped to the real ``build_idr`` keyword contract
    (``prompt`` / ``fingerprint`` / ``decision`` / ``predecessor_idr`` / ``depth``
    / ``kind``) so a caller can splat them straight into the signer.

    Parameters
    ----------
    answers:
        The interview answers (used for the belief snapshot + the deterministic
        fingerprint default).
    config_actions:
        The output of :func:`map_answers_to_config` — one leaf is emitted per
        action.
    fingerprint:
        Optional real session fingerprint; when omitted a deterministic content
        hash of ``answers`` is used so the payload is replayable byte-for-byte.
    addr_fn:
        Optional content-address function (DIP seam). Defaults to
        :func:`content_address`; a runner may inject ``lib.precog.idr.content_addr``
        so the payload's linkage keys match what real signing will produce.

    Returns
    -------
    dict
        ``{"kind", "anchor", "fingerprint", "leaves": [...], "beliefs": [...]}``.
    """
    fp = fingerprint or _derive_fingerprint(answers)
    addr = addr_fn or content_address
    anchor = f"human:onboarding:{fp}"  # admissible exogenous root (idr_forest.empty_forest)
    prompt_by_id = {q.id: q.text for q in QUESTION_BANK}
    # Belief is a UNIFORM snapshot across all leaves BY DESIGN: config_actions are
    # batch-computed from the COMPLETE answer set (the interview finishes before
    # map_answers_to_config runs), so GRIP genuinely believed this full operator
    # model at EVERY decision. An incremental per-leaf snapshot would fabricate a
    # false "GRIP knew less at decision i" story — uniform is the honest record.
    believed = _belief_snapshot(answers)

    leaves: list[dict] = []
    beliefs: list[dict] = []
    predecessor: Optional[str] = None
    for depth, action in enumerate(config_actions):
        decision = {
            "kind": "onboarding-config",
            "question_id": action.question_id,
            "answer": action.answer,
            "config_action": {"op": action.op, "args": list(action.args)},
            "why": action.because,
        }
        leaf = {
            "prompt": prompt_by_id.get(action.question_id, action.question_id),
            "fingerprint": fp,
            "decision": decision,
            "predecessor_idr": predecessor,
            "depth": depth,
            "kind": "onboarding-interview",
        }
        leaf_addr = addr(leaf)
        leaves.append(leaf)
        beliefs.append(
            {
                "decision": {"kind": "onboarding-belief", "believed": believed},
                "fingerprint": fp,
                "kind": "onboarding-belief",
                "records_idr": leaf_addr,  # belief -> decision cross-reference
            }
        )
        predecessor = leaf_addr

    return {
        "kind": "onboarding-interview",
        "anchor": anchor,
        "fingerprint": fp,
        "leaves": leaves,
        "beliefs": beliefs,
    }


# --------------------------------------------------------------------------- #
# Operator Profile Card (pure, deterministic, no ANSI, fail-closed).
# --------------------------------------------------------------------------- #
_WITHHELD_CARD = (
    "┌─ OPERATOR PROFILE ─────────────────────────\n"
    "│ CARD WITHHELD — provenance seal did not verify.\n"
    "│ A profile card that cannot prove its own provenance is worse than none.\n"
    "└─────────────────────────────────────────────────"
)

# Provenance statuses that are allowed to render as a normal (traceable) line.
_TRACEABLE = frozenset({"VERIFIED", "FUZZY"})


def render_profile_card(
    profile: dict, *, verify_fn: Optional[Callable[[dict], bool]] = None
) -> str:
    """Render the signed Operator Profile Card as deterministic plain text.

    The card reflects the operator back to themselves WITH RECEIPTS: their
    signature as AUTHOR of the setup, the inferred persona, the register chosen,
    the live goal headline, the mode stack + top-3 surfaced skills, a per-line
    provenance trail, and a GRASP Merkle-root footer.

    NO ANSI escape sequences are emitted — decoration is drawn with plain Unicode
    box characters that are part of the content stream, so a chunk boundary can
    never fall inside an escape sequence (the ANSI-mid-chunk bug class).

    Fail-closed (the falsifiable-delight hard invariant): if ``verify_fn`` is
    supplied and returns falsey (or ``profile["verified"]`` is ``False``), the
    WHOLE card is withheld — a delight that lies about its own provenance is worse
    than none. Per-line: any provenance entry whose ``status`` is not traceable
    (VERIFIED / FUZZY) renders a ``[NOT-FOUND]`` marker rather than a clean line.

    Parameters
    ----------
    profile:
        ``{"signature", "persona", "stack"?, "register", "headline", "modes",
        "skills", "provenance"? , "merkle_root"?, "verified"?}``.
    verify_fn:
        Optional whole-card seal verifier (e.g. wrapping
        ``idr_forest.verify_chain_integrity`` / forest-inclusion). Pure default:
        trust ``profile.get("verified", True)``.

    Returns
    -------
    str
    """
    sealed = bool(profile.get("verified", True))
    if verify_fn is not None:
        sealed = bool(verify_fn(profile))
    if not sealed:
        return _WITHHELD_CARD

    signature = str(profile.get("signature", "??>>"))
    persona = str(profile.get("persona", "operator"))
    stack = str(profile.get("stack", ""))
    register = str(profile.get("register", DEFAULT_OUTPUT_TIER))
    headline = str(profile.get("headline", ""))
    modes = tuple(profile.get("modes", ()))
    skills = tuple(profile.get("skills", ()))
    provenance = tuple(profile.get("provenance", ()))
    merkle_root = str(profile.get("merkle_root", ""))

    lines: list[str] = [
        "┌─ OPERATOR PROFILE ─────────────────────────",
        f"│ {signature}  — author of this setup (you configured GRIP)",
        f"│ persona   : {persona}",
    ]
    if stack:
        lines.append(f"│ stack     : {stack}")
    lines.append(f"│ register  : {register}")
    if headline:
        lines.append(f"│ goal      : {headline}")
    lines.append(f"│ modes     : {', '.join(modes) if modes else '(none)'}")
    lines.append(f"│ skills    : {', '.join(skills[:3]) if skills else '(none)'}")
    lines.append(f"│ GRIP surfaced {len(skills)} skills + stacked {len(modes)} modes FOR YOU")

    if provenance:
        lines.append("├─ provenance ──────────────────────────────")
        for entry in provenance:
            line_text = str(entry.get("line", ""))
            because = str(entry.get("because", ""))
            status = str(entry.get("status", "VERIFIED"))
            mark = "" if status in _TRACEABLE else "  [NOT-FOUND]"
            lines.append(f"│ {line_text} · {because}{mark}")

    lines.append("├─ GRASP provenance ───────────────────────")
    if merkle_root:
        lines.append(f"│ merkle-root: {merkle_root}")
        lines.append(f"│ verify: grip prove-it {merkle_root}")
    else:
        lines.append("│ merkle-root: (unsealed)")
    lines.append("└─────────────────────────────────────────────────")
    return "\n".join(lines)
