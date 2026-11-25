"""RISC Challenge Gate (nu layer) — non-punitive reflection prompts.

Phase 3 of the RISC Semantic Encoder. When a message scores below 50 on
any axis, generates a gentle reflection question. Signal, not silence.

Rules:
  - Only fire if score < 50 on any single axis (not aggregate)
  - Non-punitive: "Your message scored low on X. What would Y?"
  - Flagged != deleted — the response still goes through
  - Respect the RISC principles IN the challenge itself

Usage:
    from lib.risc_challenge_gate import generate_challenge
    note = generate_challenge(score_dict, validation_result)
"""

import random

# Axis-specific reflection questions (non-punitive, curiosity-framed)
_CHALLENGES: dict[str, list[str]] = {
    "low-respect": [
        "How might you reframe this to acknowledge the other person's perspective?",
        "What would a charitable interpretation of their position look like?",
        "Could you express the same disagreement with evidence instead of dismissal?",
    ],
    "low-wellbeing": [
        "Is this creating pressure that could be expressed as an invitation instead?",
        "How would this land if the reader is already stressed?",
        "Could you offer the same guidance while leaving room for 'no'?",
    ],
    "low-community": [
        "How might this include others rather than drawing a boundary?",
        "Could 'I' become 'we' here without losing the point?",
        "What would make someone reading this feel they belong?",
    ],
    "low-critical-thinking": [
        "What would falsify this claim? What evidence would change your mind?",
        "Is there a simpler explanation you haven't considered?",
        "Could you state what you're uncertain about alongside what you assert?",
    ],
}

# Contradiction-specific challenges
_CONTRADICTION_CHALLENGES = [
    "You mentioned something different earlier. Which position do you hold now?",
    "This seems to conflict with your earlier statement. What changed?",
]

# Tone shift challenges
_TONE_CHALLENGES = [
    "Your tone shifted noticeably from your recent messages. Everything OK?",
]


def generate_challenge(
    score_dict: dict,
    validation_dict: dict,
) -> str:
    """Generate a non-punitive reflection note for flagged messages.

    Returns empty string if no challenge needed.
    """
    parts: list[str] = []
    flags = score_dict.get("flags", [])
    val_flags = validation_dict.get("flags", [])

    # Phase 1 flags: axis-specific reflection
    for flag in flags:
        questions = _CHALLENGES.get(flag)
        if questions:
            parts.append(random.choice(questions))

    # Phase 2 flags: pattern-specific
    if "contradiction" in val_flags:
        parts.append(random.choice(_CONTRADICTION_CHALLENGES))
    if "inconsistent-tone" in val_flags:
        parts.append(random.choice(_TONE_CHALLENGES))

    if not parts:
        return ""

    # Max 2 challenges per message (avoid overwhelming)
    selected = parts[:2]
    return "> " + "\n> ".join(selected)


if __name__ == "__main__":
    # Test cases
    tests = [
        ({"R": 30, "flags": ["low-respect"]}, {"flags": []}),
        ({"C": 40, "flags": ["low-critical-thinking"]}, {"flags": ["contradiction"]}),
        ({"flags": []}, {"flags": []}),
    ]
    for score, val in tests:
        result = generate_challenge(score, val)
        print(f"Score flags: {score.get('flags', [])}, Val flags: {val.get('flags', [])}")
        print(f"  Challenge: {result or '(none)'}")
        print()
