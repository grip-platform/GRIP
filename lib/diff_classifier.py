"""Shared diff classification constants and utilities.

Used by llama_reviewer.py and pre_review_pipeline.py to avoid
duplicating extension sets across modules.
"""

import re
from pathlib import Path

# Extension sets for diff type classification
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".rb", ".sh", ".c", ".cpp", ".h", ".cs", ".swift", ".kt",
}
CONFIG_EXTENSIONS = {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
}
DOC_EXTENSIONS = {
    ".md", ".rst", ".txt", ".adoc",
}


def classify_diff(diff: str) -> str:
    """Classify a unified diff as code, config, docs, or mixed.

    Returns one of: "code", "config", "docs", "mixed".
    """
    files = re.findall(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE)
    if not files:
        return "mixed"

    total = len(files)
    code_count = sum(1 for f in files if Path(f).suffix in CODE_EXTENSIONS)
    config_count = sum(1 for f in files if Path(f).suffix in CONFIG_EXTENSIONS)
    doc_count = sum(1 for f in files if Path(f).suffix in DOC_EXTENSIONS)

    if code_count / total > 0.5:
        return "code"
    if config_count / total > 0.5:
        return "config"
    if doc_count / total > 0.5:
        return "docs"
    return "mixed"
