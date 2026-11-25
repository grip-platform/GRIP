"""CLI for session_identity — declare or show this session's model."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_identity as si  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        model, source = si.resolve()
        print(f"{model}   (source: {source})")
        return 0 if source != "none" else 1
    path = si.declare(" ".join(argv))
    model, source = si.resolve()
    print(f"declared: {model}   (source: {source})\n{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
