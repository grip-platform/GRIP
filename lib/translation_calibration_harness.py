"""Native-speaker calibration harness for lib.low_resource_translate.

Built per a Fable-led review's explicit recommendation (2026-09-24): before
any sealed "verification record" is built, the automatic fidelity metric
needs to be checked against real native-speaker judgement -- sealing an
uncalibrated score is a tamper-evident record of nothing.

WHAT THIS DOES: generates a rating fixture -- a JSONL file with one row per
domain sentence, each already translated and back-translated by the shipped
pipeline, with an empty "human_adequacy" field for a native speaker to fill
in (1-5 scale: does the translation preserve the meaning?).

WHAT THIS DELIBERATELY DOES NOT DO YET (named, not silently skipped):
- Compute the Spearman correlation between human ratings and the automatic
  fidelity score -- that needs REAL ratings to exist first. `score_fixture()`
  below is ready to do this the moment a rated fixture exists; it currently
  has no caller because there is no rated data yet.
- Seal or publish anything. This is the calibration step Fable's review
  named as the prerequisite to sealing, not the sealing itself.

The sentence set below is a real starting set (18 sentences), not the full
30-50 Fable recommended -- extending it is adding lines to a list, not new
engineering. It's drawn from the domain this was actually built for:
personal/community WhatsApp-style messages, the same register as the
exchange that motivated this whole module.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from lib.low_resource_translate import SUPPORTED_LANGUAGES, translate_converging

# Real domain sentences -- personal/community register, not news or
# technical text, matching what this module is actually used for.
CALIBRATION_SENTENCES: list[str] = [
    "Thank you for your hard work.",
    "I am truly happy about this new chapter.",
    "How is your family doing?",
    "I will call you tomorrow morning.",
    "Please let me know if you need anything.",
    "We are looking forward to seeing you soon.",
    "I am sorry I could not reply sooner.",
    "Congratulations on your new job.",
    "Take care of yourself and stay safe.",
    "I hope you had a good weekend.",
    "Can we meet next week to discuss this?",
    "I really appreciate everything you have done.",
    "Let us know when you arrive.",
    "I am not able to come today.",
    "The meeting has been moved to Friday.",
    "Send my regards to your parents.",
    "I will send you the details shortly.",
    "It was wonderful to hear from you.",
]


@dataclass
class CalibrationRow:
    source: str
    target_lang: str
    translation: str
    back_translation: str
    automatic_fidelity: float
    human_adequacy: int | None  # 1-5, filled in by a native speaker; None = not yet rated


def generate_fixture(target_lang: str, out_path: Path) -> list[CalibrationRow]:
    """Translate every CALIBRATION_SENTENCE into target_lang, write one JSON
    row per sentence to out_path (JSONL), human_adequacy left None for a
    rater to fill in. Returns the rows for immediate inspection.

    Raises the same loud errors as translate_converging for an unsupported/
    unavailable language -- a fixture silently built for the wrong language
    would waste a rater's time on a language they didn't sign up to check.
    """
    if target_lang not in SUPPORTED_LANGUAGES:
        raise ValueError(f"{target_lang!r} is not in SUPPORTED_LANGUAGES {list(SUPPORTED_LANGUAGES)}.")

    # Ephemeral, per-call fixed-point registry, not the shared default --
    # CALIBRATION_SENTENCES is a fixed, small list re-translated every time
    # this fixture is regenerated, which is exactly the repeated-input
    # pattern that triggers translate_converging's documented fixed-point-
    # collision sharp edge against a shared registry that accumulates
    # across a whole session's testing (found the hard way, 2026-09-24).
    with tempfile.TemporaryDirectory(prefix="grip-calibration-") as tmp:
        fp_path, tel_path = Path(tmp) / "fixed-points.jsonl", Path(tmp) / "telemetry.jsonl"
        rows = [_translate_row(s, target_lang, fp_path, tel_path) for s in CALIBRATION_SENTENCES]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(asdict(r)) for r in rows) + "\n", encoding="utf-8")
    return rows


def _translate_row(sentence: str, target_lang: str, fixed_points_path: Path, telemetry_path: Path) -> CalibrationRow:
    result = translate_converging(
        sentence, target_lang,
        fixed_points_path=fixed_points_path,
        telemetry_path=telemetry_path,
    )
    return CalibrationRow(
        source=sentence,
        target_lang=target_lang,
        translation=result.state.translation,
        back_translation=result.state.back_translation,
        automatic_fidelity=result.metrics["fidelity"],
        human_adequacy=None,
    )


def load_fixture(path: Path) -> list[CalibrationRow]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [CalibrationRow(**json.loads(line)) for line in lines]


def score_fixture(rows: list[CalibrationRow]) -> dict:
    """Spearman correlation (scipy.stats.spearmanr) between automatic_
    fidelity and human_adequacy, over only the rows actually rated. NOT YET
    CALLED IN PRODUCTION -- there is no rated fixture yet; this exists so
    scoring is ready the moment one does, per the harness's stated purpose.

    Returns {"n_rated": int, "n_total": int, "spearman_rho": float | None,
    "p_value": float | None}. Both are None when fewer than 2 rated rows
    exist (undefined correlation).
    """
    from scipy.stats import spearmanr

    rated = [r for r in rows if r.human_adequacy is not None]
    result = {"n_rated": len(rated), "n_total": len(rows), "spearman_rho": None, "p_value": None}
    if len(rated) < 2:
        return result
    rho, p_value = spearmanr(
        [r.automatic_fidelity for r in rated],
        [r.human_adequacy for r in rated],
    )
    result["spearman_rho"] = float(rho)
    result["p_value"] = float(p_value)
    return result
