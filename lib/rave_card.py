"""Italian Brainrot rave-mode milestone narration for GRIP sprints.

Codifies V>>'s GRIP-GUILD rave aesthetic as a deterministic helper.
Same (title, body, personas) input always produces the same rave card —
Goodhart-safe and unit-testable. Pure stdlib, no network, no I/O.

Used by:
- the /rave slash command (skills/rave/SKILL.md)
- lib/grip_notify.py wave-milestone cadence (planned)
- ad-hoc invocation: `python3 -m lib.rave_card "title" "body"`
"""

import argparse
import hashlib
from dataclasses import dataclass
from typing import Sequence, Tuple


# Persona registry: GRIP-GUILD members and their rave-mode signatures.
# Tuple shape: (key, display_name, emoji, signature_line)
# Order matters — _select_personas walks this list deterministically.
RAVE_PERSONAS: Tuple[Tuple[str, str, str, str], ...] = (
    ("v_double_arrow", "V>>",            "🎵", "the conductor on LSD spinning the turntable"),
    ("alex",           "Alex",           "🦘", 'bouncing "EVIDENCE OR GTFO"'),
    ("kelly",          "Kelly",          "🕐", "time-travels riding the TRIAD ENGINE λμνω"),
    ("thomas",         "Thomas",         "🎸", "on bass HARM EQUATION slaps different"),
    ("gary",           "Gary",           "🎨", "building OBSERVATION LAYERS in the strobe lights"),
    ("jake",           "Jake",           "🤡", 'doing backflips screaming "THIS IS FINE"'),
    ("nicolas",        "Nicolas",        "🎲", '"WHAT EVEN IS THE BPM RIGHT NOW"'),
    ("jona",           "Jona",           "🎭", "rewrites the rave mid-drop with CONSTRAINTS"),
    ("frank_marcel",   "Frank & Marcel", "🌈", 'nodding wisely like "yeah this is craft"'),
    ("grip",           "GRIP",           "🤖", "levitating, firing safety gates like confetti"),
)

PERSONA_INDEX = {name: (display, emoji, signature) for name, display, emoji, signature in RAVE_PERSONAS}

HEADER_TEMPLATE = "🎉🔥💃 {title} 🕺✨🎪"
DIVIDER = "→"
FEEDBACK_LINE = "🎪🔥🍄🎆 THE WHOLE THING IS A FEEDBACK LOOP EATING ITSELF 🌀🌀🌀"
FOOTER_TEMPLATE = "🎉 END {state} → {summary} 🏆"


@dataclass(frozen=True)
class RaveCard:
    """Immutable rave card. Render via `render()` or `format_card(card)`."""

    title: str
    body: str
    personas: Tuple[str, ...]
    verses: Tuple[str, ...]

    def render(self) -> str:
        return format_card(self)


def _seed(title: str) -> int:
    """Deterministic seed from title — pure SHA-256, no randomness."""
    return int(hashlib.sha256(title.encode("utf-8")).hexdigest(), 16)


def _select_personas(title: str, count: int = 0) -> Tuple[str, ...]:
    """Pick `count` personas deterministically from the title hash.

    If count is 0, pick 4-7 personas based on the seed (deterministic range).
    Always includes 'grip' as the last persona — GRIP narrates its own state.

    Uses a SHA-256-seeded Fisher-Yates shuffle so the chosen personas are
    UNIQUE within a single card. Different titles produce different
    shuffles because the digest bytes differ at each swap position.
    """
    seed = _seed(title)
    pool = [name for name, _, _, _ in RAVE_PERSONAS if name != "grip"]
    n = count if count > 0 else (seed % 4) + 4
    n = min(n, len(pool))
    digest = hashlib.sha256(title.encode("utf-8")).digest()
    indices = list(range(len(pool)))
    for i in range(len(indices) - 1, 0, -1):
        byte_a = digest[(i * 2) % len(digest)]
        byte_b = digest[((i * 2) + 1) % len(digest)]
        j = ((byte_a << 8) | byte_b) % (i + 1)
        indices[i], indices[j] = indices[j], indices[i]
    chosen = tuple(pool[idx] for idx in indices[:n])
    return chosen + ("grip",)


def _verse_for(persona: str, body: str) -> str:
    display, emoji, signature = PERSONA_INDEX.get(
        persona,
        (persona.replace("_", " ").title(), "✨", "vibing"),
    )
    snippet = body.strip()[:90]
    return f"{DIVIDER} {emoji} {display} {signature} → {snippet}"


def card_for_milestone(
    title: str,
    body: str,
    personas: Sequence[str] = (),
) -> RaveCard:
    """Generate a deterministic rave card for a sprint milestone.

    Args:
        title: short milestone title (e.g. "W1 BOOTSTRAP COMPLETE")
        body:  one-line milestone description (e.g. "11 files, 3 tests, MIT licence")
        personas: optional explicit persona list. If empty, picks deterministically.

    Returns:
        Frozen RaveCard with verses ready to render.

    Raises:
        ValueError: if title or body is empty.
    """
    if not title:
        raise ValueError("title required")
    if not body:
        raise ValueError("body required")
    chosen = tuple(personas) if personas else _select_personas(title)
    verses = tuple(_verse_for(p, body) for p in chosen)
    return RaveCard(title=title, body=body, personas=chosen, verses=verses)


def format_card(card: RaveCard) -> str:
    """Render a RaveCard as a single string ready to send/post."""
    lines = [HEADER_TEMPLATE.format(title=card.title.upper()), ""]
    for verse in card.verses:
        lines.append(verse)
        lines.append("")
    lines.append(FEEDBACK_LINE)
    lines.append("")
    summary = card.body.strip()[:120]
    lines.append(FOOTER_TEMPLATE.format(state="STATE", summary=summary))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a GRIP-GUILD rave card")
    parser.add_argument("title", help="milestone title")
    parser.add_argument("body", help="milestone description (one line)")
    parser.add_argument(
        "--personas",
        nargs="*",
        default=(),
        help="explicit persona list (default: deterministic from title)",
    )
    args = parser.parse_args()
    card = card_for_milestone(args.title, args.body, args.personas)
    print(format_card(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
