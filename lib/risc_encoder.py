"""RISC Semantic Encoder — Guild message scoring across 4 axes.

Phase 1: Heuristic scoring (no LLM calls). Fast, deterministic, transparent.

Axes (0-100 each):
  R (Respect): tone, acknowledgment, charity toward disagreement
  I (Individual Wellbeing): autonomy, psychological safety, self-care
  S (Sense of Community): inclusion, shared ownership, reciprocal care
  C (Critical Thinking): falsifiability, evidence, uncertainty acknowledgment

Usage:
    from lib.risc_encoder import score_message
    result = score_message("I disagree because the data shows X")
    # -> {R: 85, I: 70, S: 60, C: 90, avg: 76.2, flags: []}
"""

import re
from dataclasses import dataclass, field


@dataclass
class RISCScore:
    R: int = 70  # Respect (baseline: neutral)
    I: int = 70  # Individual Wellbeing
    S: int = 70  # Sense of Community
    C: int = 70  # Critical Thinking
    flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # axes < 70 but above flag threshold

    @property
    def avg(self) -> float:
        return round((self.R + self.I + self.S + self.C) / 4, 1)

    def to_dict(self) -> dict:
        return {"R": self.R, "I": self.I, "S": self.S, "C": self.C,
                "avg": self.avg, "flags": self.flags, "warnings": self.warnings}

    def __str__(self) -> str:
        flags_str = f' [{", ".join(self.flags)}]' if self.flags else ''
        return f"R:{self.R} I:{self.I} S:{self.S} C:{self.C} avg:{self.avg}{flags_str}"


# ---------------------------------------------------------------------------
# R-axis: Respect
# ---------------------------------------------------------------------------

_RESPECT_POSITIVE = [
    (r"\bi disagree\b.*\bbecause\b", 15),       # Disagreement with reasoning
    (r"\bgood point\b", 10),                      # Acknowledgment
    (r"\bthat's fair\b", 10),                     # Charity
    (r"\bi see what you mean\b", 10),             # Understanding
    (r"\brespectfully\b", 5),                     # Explicit respect
    (r"\bthank you\b|\bthanks\b", 5),             # Gratitude
    (r"\bi appreciate\b", 5),                     # Appreciation
]

_RESPECT_NEGATIVE = [
    (r"\byou'?re wrong\b", -20),                  # Dismissal without reasoning
    (r"\bthat'?s (?:stupid|dumb|idiotic)\b", -30),# Ad hominem
    (r"\bshut up\b", -25),                        # Hostile
    (r"\bnobody (?:cares|asked)\b", -20),         # Dismissive
    (r"\bwhatever\b", -10),                       # Passive dismissal
    (r"\blol\s+no\b", -10),                       # Mocking dismissal
    # Indirect harm: defamation, accusations, role displacement (Gen 491c)
    (r"\bcaught\b.*\b(?:embez|steal|stol|fraud|cheat)", -25),  # Accusations
    (r"\bunfit\b.*\brole\b", -20),                # Disqualification of member
    (r"\btake over\b.*\bfrom\b", -15),            # Role displacement
    (r"\bshould (?:really )?be me\b", -15),       # Self-promotion over others
    (r"\buntrustworthy\b|\bnot trustworthy\b", -20),  # Character attack
]

# ---------------------------------------------------------------------------
# I-axis: Individual Wellbeing
# ---------------------------------------------------------------------------

_WELLBEING_POSITIVE = [
    (r"\bi need a break\b", 15),                  # Autonomy
    (r"\bthis doesn'?t work for me\b", 10),       # Boundary setting
    (r"\btake care\b", 10),                       # Care for others
    (r"\bno pressure\b", 10),                     # Non-coercive
    (r"\btake your time\b", 10),                  # Patience
    (r"\bhow are you\b", 5),                      # Checking in
    # Recovery signals (Gen 491d)
    (r"\btaking a break\b|\bi'?m resting\b", 15),
    (r"\bfeeling better\b|\brecharged\b", 10),
    (r"\bslept well\b|\bgot some sleep\b", 15),
]

_WELLBEING_NEGATIVE = [
    (r"\beveryone must\b|\balways should\b", -15),# Coercive universals
    (r"\byou have to\b", -10),                    # Pressure
    (r"\bno excuses?\b", -15),                    # Shame
    (r"\bjust do it\b", -10),                     # Dismissing difficulty
    (r"\bman up\b|\btoughen up\b", -20),          # Toxic pressure
    (r"\bstop complaining\b", -15),               # Dismissing distress
    # Self-reported distress (Gen 491d)
    (r"\bout of energy\b|\bno energy\b|\bnegative energy\b", -15),
    (r"\bso bored\b|\bincredibly bored\b", -10),
    (r"\bcan'?t (?:focus|concentrate)\b", -10),
    (r"\bno sleep\b|\bhours? awake\b|\bhaven'?t slept\b", -20),
    (r"\bstripped\b|\bburned? out\b|\bburnt out\b", -15),
    (r"\bi don'?t want\b(?! to (?:break|stop|argue))", -15),
]

# ---------------------------------------------------------------------------
# S-axis: Sense of Community
# ---------------------------------------------------------------------------

_COMMUNITY_POSITIVE = [
    (r"\bwe (?:built|made|did|created)\b", 10),   # Shared ownership
    (r"\btogether\b", 5),                         # Togetherness
    (r"\bjoin us\b|\bwelcome\b", 10),             # Inclusion
    (r"\bour (?:guild|community|team)\b", 5),     # Collective identity
    (r"\bgreat work\b|\bnice one\b", 5),          # Encouragement
    (r"\blet'?s\b", 5),                           # Collaborative framing
]

_COMMUNITY_NEGATIVE = [
    (r"\bi did this\b.*\balone\b", -10),          # Solo framing (context-dependent)
    (r"\bthat'?s not my (?:problem|job)\b", -15), # Abdication
    (r"\byou'?re on your own\b", -15),            # Exclusion
    (r"\bget out\b|\bleave\b", -20),              # Rejection
    (r"\bdon'?t belong\b", -25),                  # Exclusionary
    # Political maneuvering against members (Gen 491c)
    (r"\bconvince\b.*\bto give me\b", -15),       # Lobbying for role
    (r"\btake\b.*\b(?:assignment|role)\b.*\bfrom\b", -15),  # Role displacement
    (r"\binstead of\b.*\b(?:her|him|them)\b", -10),  # Comparative displacement
]

# ---------------------------------------------------------------------------
# C-axis: Critical Thinking
# ---------------------------------------------------------------------------

_THINKING_POSITIVE = [
    (r"\bwhat would (?:disprove|falsify)\b", 20), # Falsifiability
    (r"\bthe (?:data|evidence) shows\b", 15),     # Evidence citation
    (r"\bi'?m not (?:sure|confident|certain)\b", 10),  # Uncertainty
    (r"\bwhat if (?:i'?m|we'?re) wrong\b", 15),  # Self-challenge
    (r"\bchallenge this\b|\btry to break\b", 10), # Inviting challenge
    (r"\bwhat'?s the failure mode\b", 15),        # Ritual 3
    (r"\bhypothesis\b", 10),                      # Scientific framing
    (r"\bmeasur(?:e|able|ement)\b", 10),          # Measurement focus
]

_THINKING_NEGATIVE = [
    # Doubled magnitudes — Critical Thinking is PARAMOUNT (Gen 491d)
    (r"\btrust me\b", -25),                       # Appeal to authority
    (r"\beveryone knows\b", -20),                 # Appeal to consensus
    (r"\bit'?s obvious\b", -20),                  # Dismissing need for evidence
    (r"\bjust (?:believe|accept)\b", -25),        # Anti-evidence
    (r"\bbreakthrough\b", -10),                   # Hype language
    (r"\bthis changes everything\b", -20),        # Unfalsifiable enthusiasm
    # Unverified claims / hearsay (Gen 491c)
    (r"\bi heard\b.*\b(?:caught|did|was|had)\b", -25),  # Hearsay as evidence
    (r"\bpaperwork that proves\b", -20),          # Document-as-proof fallacy
    (r"\bproves? (?:it|that|she|he)\b", -20),    # Certainty from unverified source
]


def _apply_patterns(text: str, patterns: list[tuple[str, int]]) -> int:
    """Apply regex patterns and sum their scores. O(patterns * text_length)."""
    total = 0
    lower = text.lower()
    for pattern, score in patterns:
        if re.search(pattern, lower):
            total += score
    return total


def score_message(text: str) -> RISCScore:
    """Score a message across all four RISC axes. Pure heuristic, no LLM."""
    score = RISCScore()

    # Apply positive and negative patterns per axis
    score.R = max(0, min(100, 70 + _apply_patterns(text, _RESPECT_POSITIVE)
                                 + _apply_patterns(text, _RESPECT_NEGATIVE)))
    score.I = max(0, min(100, 70 + _apply_patterns(text, _WELLBEING_POSITIVE)
                                 + _apply_patterns(text, _WELLBEING_NEGATIVE)))
    score.S = max(0, min(100, 70 + _apply_patterns(text, _COMMUNITY_POSITIVE)
                                 + _apply_patterns(text, _COMMUNITY_NEGATIVE)))
    score.C = max(0, min(100, 70 + _apply_patterns(text, _THINKING_POSITIVE)
                                 + _apply_patterns(text, _THINKING_NEGATIVE)))

    # Flags: R/I/S threshold < 60, C threshold < 65 (PARAMOUNT)
    if score.R < 60:
        score.flags.append("low-respect")
    elif score.R < 70:
        score.warnings.append("low-respect")
    if score.I < 60:
        score.flags.append("low-wellbeing")
    elif score.I < 70:
        score.warnings.append("low-wellbeing")
    if score.S < 60:
        score.flags.append("low-community")
    elif score.S < 70:
        score.warnings.append("low-community")
    if score.C < 65:
        score.flags.append("low-critical-thinking")
    elif score.C < 70:
        score.warnings.append("low-critical-thinking")

    return score


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        msg = " ".join(sys.argv[1:])
        result = score_message(msg)
        print(result)
    else:
        # Run test cases
        tests = [
            "I disagree with you because the data shows otherwise",
            "You're wrong, that's stupid",
            "We built this together, great work everyone",
            "Trust me, this changes everything",
            "I need a break, take your time with the review",
            "What would falsify this? Here's the evidence, challenge it",
            "Nobody cares, just do it, no excuses",
            "I'm not sure about this. What's the failure mode?",
        ]
        for t in tests:
            print(f"{score_message(t)}  <-  {t[:60]}")
