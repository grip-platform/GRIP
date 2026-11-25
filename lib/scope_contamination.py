"""Cross-org / cross-individual scope-contamination kernel.

GRIP runs across many organisations and individuals at once. The recurring
safety question is the same in three different surfaces: *is this piece of
content / memory / output CLEAN to surface in a given (org, individual)
scope, or does it carry material that belongs to a DIFFERENT scope?*

This kernel answers that question with the SAME idempotent-no-op proof that
`lib/git_reconcile.py` (the `/git-solve` floor) uses for git divergence —
transplanted from the git domain to a non-git one:

    git-solve:  a merge is CLEAN  iff merge-tree produces no conflict — i.e.
                applying the other side is a no-op against ours.
    here:       content is CLEAN  iff SANITISING it for the target scope is a
                no-op — i.e. stripping every out-of-scope token changes nothing.

So "clean" is proven, never asserted: ``sanitise(content, target) == content``.
The non-empty *delta* between the original and its sanitised form IS the
contamination — concrete spans the agent (or a gate) can point at, not a vague
"this feels cross-client". That is the whole value: a no-op proof gives you the
exact leaked bytes for free, exactly as ``git merge-tree`` gives you the exact
conflict hunks for free.

It UNIFIES three previously-separate isolation surfaces behind one predicate:

  1. **channel_scope_gate** (`lib/channel_scope_gate.py`) — content keyed to an
     org/relationship scope must only reach that scope's channels. Here: an
     org's scope-tokens are contamination for a *different* org's target.
  2. **mcp-org-isolation** (`rules/mcp-org-isolation.md`) — org-specific MCP
     servers must not leak into the universal/shared config. Here: an
     org-specific server name in a shared-scope artefact is contamination.
  3. **principal isolation** (operator/individual routing) — one individual's
     private identifiers (a personal JID, a self-DM id) must not leak into
     another individual's surface. Here: individual-B's identifiers are
     contamination for an individual-A target.

This module is the mechanical floor ONLY. It never sends, never posts, never
mutates anything — it reads content + a target scope and returns a verdict with
the leaked spans. The *judgment* of what to do with a genuine leak (redact,
retarget, escalate) stays with the caller (a gate, or the agent via
falsify-self), exactly as `git_reconcile.classify` leaves the merge to the
agent. Replayable: same (content, target, registry) → same verdict, always.

Client-neutral by construction (this file is TRACKED and mirrored to an
external org, same constraint as `channel_scope_gate.py`): it names no client,
person, or commercial relationship. Concrete scope tokens live in the caller's
registry (the gitignored local memory / `rules/operational-context.md`); the
kernel keys on opaque ``ScopeRule`` objects the caller supplies. Pure stdlib;
no I/O, no network — every test runs hermetically with in-memory rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Pattern

# ---- Scope identity -------------------------------------------------------

# A target scope is the (org, individual) pair content is being surfaced FOR.
# Either coordinate may be None ("any") — a shared/universal target has both
# None; an org-wide target names the org and leaves individual None; a personal
# DM names both. The kernel keys cleanliness on this pair plus the rule
# registry; it stores no client detail itself.


@dataclass(frozen=True)
class Scope:
    """The (org, individual) scope content is being surfaced for.

    ``org``/``individual`` are opaque caller-defined labels (e.g. "orgA",
    "person1"). ``None`` means "unscoped / any" on that axis. The kernel never
    interprets the strings beyond equality — it carries no client knowledge.
    """

    org: str | None = None
    individual: str | None = None

    def __str__(self) -> str:  # human-readable for deny copy
        return f"(org={self.org or 'any'}, individual={self.individual or 'any'})"


# A SHARED/UNIVERSAL target — nothing org- or individual-specific may appear.
# Anything keyed to ANY org or ANY individual is contamination against it.
SHARED = Scope(org=None, individual=None)


@dataclass(frozen=True)
class ScopeRule:
    """One contamination pattern owned by exactly one (org, individual) scope.

    ``pattern`` matches the token(s) that identify content as belonging to
    ``owner``. If a target's scope does NOT match the owner (see
    ``owns_for_target``), every match of ``pattern`` in the content is a leak.

    ``label`` is an opaque, client-neutral tag for the deny copy / telemetry
    (e.g. "orgA-ticket-id", "person1-jid", "orgB-mcp-server"). ``surface`` names
    which of the three unified surfaces this rule represents — purely
    informational, used to group telemetry; it never changes the verdict.
    """

    owner: Scope
    pattern: Pattern[str]
    label: str
    surface: str = "generic"  # "channel" | "mcp-org" | "principal" | "generic"


@dataclass(frozen=True)
class Leak:
    """One concrete contamination span found in the content."""

    label: str
    owner: Scope
    surface: str
    span: tuple[int, int]  # (start, end) char offsets in the ORIGINAL content
    text: str  # the leaked substring, verbatim


@dataclass(frozen=True)
class ContaminationVerdict:
    """Result of checking content against a target scope.

    ``clean`` is True iff sanitising the content for ``target`` is a no-op —
    i.e. there are zero leaks, i.e. ``sanitised == content``. This is the
    idempotent-no-op proof: cleanliness is demonstrated, not asserted.

    ``leaks`` are the concrete spans that would be stripped (empty when clean).
    ``sanitised`` is the content with every leak removed — equal to the input
    exactly when ``clean`` is True.
    """

    clean: bool
    target: Scope
    leaks: tuple[Leak, ...]
    sanitised: str
    rationale: str

    @property
    def leaked_labels(self) -> tuple[str, ...]:
        """Distinct owner-labels that leaked, in first-seen order (telemetry)."""
        seen: list[str] = []
        for lk in self.leaks:
            if lk.label not in seen:
                seen.append(lk.label)
        return tuple(seen)


# ---- Ownership: does a rule's owner cover the target? ---------------------

def owns_for_target(owner: Scope, target: Scope) -> bool:
    """True iff ``owner``'s content is IN-SCOPE for ``target`` (so NOT a leak).

    Hierarchical containment, evaluated independently per axis (org, then
    individual). A rule's matches are contamination for the target UNLESS the
    owner is in-scope here.

    Axis rule (applied to org AND individual):
      * owner axis is None  → owner is unscoped on that axis → in-scope for any
        target (an unscoped token belongs everywhere; it is never a leak).
      * owner axis == target axis → same scope on that axis → in-scope.
      * owner axis != target axis (both concrete) → DIFFERENT scope → leak.
      * owner axis concrete but target axis None → target is BROADER (shared)
        than the owner → the owner's specific content does NOT belong in the
        broader surface → leak. (This is the mcp-org-isolation case: an
        org-specific token must not appear in the shared/universal target.)

    Content is in-scope for the target iff BOTH axes are in-scope.

    Examples (S=shared/None):
        >>> owns_for_target(Scope("orgA"), Scope("orgA"))      # same org
        True
        >>> owns_for_target(Scope("orgA"), Scope("orgB"))      # cross-org leak
        False
        >>> owns_for_target(Scope("orgA"), SHARED)             # org token in shared
        False
        >>> owns_for_target(SHARED, Scope("orgA"))             # unscoped is universal
        True
        >>> owns_for_target(Scope(individual="p1"), Scope("orgA", "p2"))
        False
    """
    return _axis_in_scope(owner.org, target.org) and _axis_in_scope(
        owner.individual, target.individual
    )


def _axis_in_scope(owner_axis: str | None, target_axis: str | None) -> bool:
    """One-axis containment (see ``owns_for_target`` for the full table)."""
    if owner_axis is None:
        return True  # unscoped owner belongs everywhere on this axis
    if target_axis is None:
        return False  # concrete owner content must not enter a broader target
    return owner_axis == target_axis


# ---- The core: sanitise, then prove the no-op -----------------------------

def find_leaks(
    content: str, target: Scope, rules: Iterable[ScopeRule]
) -> list[Leak]:
    """Return every contamination span in ``content`` for ``target``.

    A span leaks iff its rule's ``owner`` is NOT in-scope for ``target``
    (``owns_for_target`` is False). Overlapping/duplicate spans from different
    rules are all reported; ``sanitise`` de-overlaps them when removing.

    Leaks are returned sorted by start offset (stable, replayable order).
    """
    if not content:
        return []
    # One flat scan: each out-of-scope rule contributes its match-leaks via the
    # single-responsibility helper, flattened once. (Not a nested scan — the
    # per-rule match extraction is the helper's whole job; this stays a
    # single-level comprehension over rules.)
    offending = (r for r in rules if not owns_for_target(r.owner, target))
    leaks = [lk for rule in offending for lk in _rule_leaks(rule, content)]
    leaks.sort(key=lambda lk: lk.span)
    return leaks


def _rule_leaks(rule: ScopeRule, content: str) -> list[Leak]:
    """Every non-empty match of one out-of-scope ``rule`` as ``Leak`` spans.

    Single responsibility: turn one rule's regex matches into leak records.
    Zero-width matches are dropped (never a meaningful leak span).
    """
    return [
        Leak(
            label=rule.label,
            owner=rule.owner,
            surface=rule.surface,
            span=(m.start(), m.end()),
            text=m.group(0),
        )
        for m in rule.pattern.finditer(content)
        if m.start() != m.end()
    ]


def _merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent (start, end) spans into disjoint ranges."""
    ordered = sorted(spans)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def sanitise(content: str, target: Scope, rules: Iterable[ScopeRule]) -> str:
    """Strip every out-of-scope contamination span from ``content``.

    This is the function whose IDEMPOTENT-NO-OP property defines cleanliness.
    Removing leaked spans (de-overlapped) yields the in-scope residue. When the
    content was already clean for ``target``, the returned string is identical
    to the input — that equality IS the proof of cleanliness.

    Sanitising is itself idempotent: ``sanitise(sanitise(x)) == sanitise(x)``,
    because the residue contains no out-of-scope token for any rule to match.
    """
    rules = list(rules)
    leaks = find_leaks(content, target, rules)
    if not leaks:
        return content
    removals = _merge_spans(lk.span for lk in leaks)
    out: list[str] = []
    cursor = 0
    for start, end in removals:
        out.append(content[cursor:start])
        cursor = end
    out.append(content[cursor:])
    return "".join(out)


def check(
    content: str, target: Scope, rules: Iterable[ScopeRule]
) -> ContaminationVerdict:
    """Prove whether ``content`` is CLEAN for ``target`` via the no-op test.

    Cleanliness is decided by ``sanitise(content, target) == content`` — the
    same shape as ``git merge-tree`` reporting "no conflict": the operation is
    a no-op exactly when the input is already in the desired state. The verdict
    carries the leaked spans (the contamination) and the sanitised residue, so
    a caller never re-derives them.

    Examples:
        >>> rules = [ScopeRule(Scope("orgA"),
        ...                    re.compile(r"SD-\\d+"), "orgA-ticket")]
        >>> check("status update for everyone", SHARED, rules).clean
        True
        >>> v = check("ref SD-3876 from orgA", Scope("orgB"), rules)
        >>> v.clean
        False
        >>> v.leaked_labels
        ('orgA-ticket',)
        >>> v.sanitised
        'ref  from orgA'
    """
    rules = list(rules)
    leaks = tuple(find_leaks(content, target, rules))
    sanitised = sanitise(content, target, rules)
    clean = sanitised == content
    # Invariant: clean iff no leaks (the no-op proof and the leak list agree).
    # find_leaks/sanitise share one source of truth, so they cannot disagree;
    # this assertion documents (and guards) the equivalence.
    assert clean == (len(leaks) == 0), "no-op proof disagrees with leak set"
    if clean:
        rationale = (
            f"sanitising for {target} is a no-op — content carries no "
            f"out-of-scope token; CLEAN."
        )
    else:
        labels = ", ".join(dict.fromkeys(lk.label for lk in leaks))
        rationale = (
            f"sanitising for {target} would change {len(leaks)} span(s) "
            f"[{labels}] — content is CONTAMINATED for this scope."
        )
    return ContaminationVerdict(
        clean=clean, target=target, leaks=leaks, sanitised=sanitised,
        rationale=rationale,
    )


# ---- Registry construction (caller-supplied, client-neutral) --------------

def compile_token_rule(
    owner: Scope,
    tokens: Iterable[str],
    label: str,
    *,
    surface: str = "generic",
    word_bound: bool = True,
    flags: int = re.IGNORECASE,
) -> ScopeRule:
    """Build one ``ScopeRule`` from literal scope tokens (a caller convenience).

    Each token is regex-escaped and OR-joined; ``word_bound`` wraps the
    alternation in ``\\b`` anchors so a token like "orgA" does not match inside
    "orgAlpha". Returns a frozen ``ScopeRule`` ready for ``check``.

    The caller owns the token list (it knows the client detail); this kernel
    only compiles + matches it, so the file stays client-neutral.
    """
    toks = [re.escape(t) for t in tokens if t]
    if not toks:
        # An empty token set matches nothing — a rule that never fires. Use a
        # pattern that cannot match (`(?!)`) so callers get a harmless no-op
        # rule rather than an accidental match-everything.
        pat: Pattern[str] = re.compile(r"(?!)")
    else:
        body = "|".join(toks)
        src = rf"\b(?:{body})\b" if word_bound else f"(?:{body})"
        pat = re.compile(src, flags)
    return ScopeRule(owner=owner, pattern=pat, label=label, surface=surface)


@dataclass
class Registry:
    """A mutable collection of ``ScopeRule`` the caller assembles + reuses.

    Thin convenience over a list — keeps rule assembly readable at the callsite
    and lets the three unified surfaces register their rules into one place.
    The kernel functions accept any ``Iterable[ScopeRule]``; this is sugar.
    """

    rules: list[ScopeRule] = field(default_factory=list)

    def add(self, rule: ScopeRule) -> "Registry":
        self.rules.append(rule)
        return self

    def add_tokens(
        self,
        owner: Scope,
        tokens: Iterable[str],
        label: str,
        *,
        surface: str = "generic",
        word_bound: bool = True,
        flags: int = re.IGNORECASE,
    ) -> "Registry":
        """Compile + register a literal-token rule in one call."""
        return self.add(
            compile_token_rule(
                owner, tokens, label, surface=surface,
                word_bound=word_bound, flags=flags,
            )
        )

    def check(self, content: str, target: Scope) -> ContaminationVerdict:
        """``check`` against this registry's rules."""
        return check(content, target, self.rules)

    def sanitise(self, content: str, target: Scope) -> str:
        """``sanitise`` against this registry's rules."""
        return sanitise(content, target, self.rules)

    def by_surface(self, surface: str) -> list[ScopeRule]:
        """All registered rules for one unified surface (telemetry/audit)."""
        return [r for r in self.rules if r.surface == surface]


__all__ = [
    "Scope",
    "SHARED",
    "ScopeRule",
    "Leak",
    "ContaminationVerdict",
    "owns_for_target",
    "find_leaks",
    "sanitise",
    "check",
    "compile_token_rule",
    "Registry",
]
