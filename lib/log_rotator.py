#!/usr/bin/env python3
"""Log/cache rotation tool for GRIP.

Rotate and archive old metrics/cache files to prevent bloat.
Pure Python, no external dependencies.
"""

import argparse
import gzip
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class RotationConfig:
    """Configuration for rotation behaviour."""

    max_size_mb: float = 10.0
    max_age_days: int = 30
    keep_last_n: int = 100
    archive_dir: str = "archive"
    compress: bool = True


@dataclass
class RotationTarget:
    """A file identified as needing rotation."""

    path: Path
    size_bytes: int
    age_days: float
    file_type: str  # "jsonl", "json", "other"
    reason: str  # human-readable reason for rotation


@dataclass
class RotationAction:
    """Record of a rotation action taken."""

    target: RotationTarget
    action_type: str  # "archive", "compress", "truncate"
    archive_path: Optional[Path]
    bytes_freed: int


@dataclass
class RotationReport:
    """Summary of a rotation run."""

    actions: List[RotationAction] = field(default_factory=list)
    total_freed_bytes: int = 0
    files_rotated: int = 0
    files_skipped: int = 0
    dry_run: bool = False


def human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable string (KB, MB, GB)."""
    if size_bytes < 0:
        return f"-{human_size(-size_bytes)}"
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024.0 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    if i == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[i]}"


def _detect_file_type(path: Path) -> str:
    """Detect file type from extension."""
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".json":
        return "json"
    if suffix == ".gz":
        return "compressed"
    return "other"


def _file_age_days(path: Path) -> float:
    """Return file age in days based on modification time."""
    mtime = path.stat().st_mtime
    return (time.time() - mtime) / 86400.0


def _ensure_archive_dir(base_dir: Path, archive_dir_name: str) -> Path:
    """Create archive directory under base_dir if it does not exist."""
    archive = base_dir / archive_dir_name
    archive.mkdir(parents=True, exist_ok=True)
    return archive


def _timestamp_suffix() -> str:
    """Generate a timestamp suffix for archive filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def scan_targets(
    dirs: List[Path], config: RotationConfig
) -> List[RotationTarget]:
    """Find files needing rotation across given directories.

    A file is a target if:
    - Its size exceeds max_size_mb, OR
    - Its age exceeds max_age_days

    Already-compressed files (.gz) are skipped.
    Directories named after archive_dir are skipped.
    """
    targets: List[RotationTarget] = []
    max_size_bytes = int(config.max_size_mb * 1024 * 1024)

    for directory in dirs:
        if not directory.is_dir():
            continue
        for item in sorted(directory.rglob("*")):
            if not item.is_file():
                continue
            # Skip files inside archive directories
            try:
                item.relative_to(directory / config.archive_dir)
                continue
            except ValueError:
                pass
            # Also skip if any parent is named archive_dir
            if config.archive_dir in item.parts:
                continue

            file_type = _detect_file_type(item)
            if file_type == "compressed":
                continue

            try:
                stat = item.stat()
            except OSError:
                continue

            size = stat.st_size
            age = (time.time() - stat.st_mtime) / 86400.0
            reasons: List[str] = []

            if size > max_size_bytes:
                reasons.append(
                    f"size {human_size(size)} > {human_size(max_size_bytes)}"
                )
            if age > config.max_age_days:
                reasons.append(
                    f"age {age:.0f}d > {config.max_age_days}d"
                )

            if reasons:
                targets.append(
                    RotationTarget(
                        path=item,
                        size_bytes=size,
                        age_days=age,
                        file_type=file_type,
                        reason="; ".join(reasons),
                    )
                )

    return targets


def rotate_jsonl(target: RotationTarget, config: RotationConfig) -> RotationAction:
    """Rotate a JSONL file: archive old entries, keep last N in active file.

    Reads all lines, writes older lines to archive, keeps the most recent
    keep_last_n lines in the original file.
    """
    path = target.path
    archive_dir = _ensure_archive_dir(path.parent, config.archive_dir)
    timestamp = _timestamp_suffix()
    archive_name = f"{path.stem}_{timestamp}{path.suffix}"
    archive_path = archive_dir / archive_name

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    if len(lines) <= config.keep_last_n:
        # Nothing to rotate -- file is small enough by entry count
        return RotationAction(
            target=target,
            action_type="archive",
            archive_path=None,
            bytes_freed=0,
        )

    keep_lines = lines[-config.keep_last_n:]
    archive_lines = lines[:-config.keep_last_n]

    # Write archived lines
    with open(archive_path, "w", encoding="utf-8") as f:
        f.writelines(archive_lines)

    # Compress archive if configured
    final_archive = archive_path
    if config.compress:
        final_archive = Path(compress_file(archive_path))

    # Rewrite active file with only recent entries
    original_size = path.stat().st_size
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(keep_lines)
    new_size = path.stat().st_size

    bytes_freed = original_size - new_size
    if config.compress and final_archive != archive_path:
        # Account for compressed archive being smaller than raw
        archive_size = final_archive.stat().st_size
        bytes_freed = original_size - new_size - archive_size

    return RotationAction(
        target=target,
        action_type="archive",
        archive_path=final_archive,
        bytes_freed=max(0, bytes_freed),
    )


def rotate_json(target: RotationTarget, config: RotationConfig) -> RotationAction:
    """Archive a JSON file with a timestamp suffix.

    Moves the file to archive_dir with a timestamp, optionally compressing it.
    Leaves the original path empty (removed).
    """
    path = target.path
    archive_dir = _ensure_archive_dir(path.parent, config.archive_dir)
    timestamp = _timestamp_suffix()
    archive_name = f"{path.stem}_{timestamp}{path.suffix}"
    archive_path = archive_dir / archive_name

    original_size = path.stat().st_size
    shutil.copy2(str(path), str(archive_path))

    final_archive = archive_path
    if config.compress:
        final_archive = Path(compress_file(archive_path))

    # Remove original
    path.unlink()

    archive_size = final_archive.stat().st_size
    bytes_freed = original_size - archive_size

    return RotationAction(
        target=target,
        action_type="archive",
        archive_path=final_archive,
        bytes_freed=max(0, bytes_freed),
    )


def rotate_other(target: RotationTarget, config: RotationConfig) -> RotationAction:
    """Archive a non-JSON/JSONL file with a timestamp suffix.

    Same as rotate_json but for generic file types.
    """
    path = target.path
    archive_dir = _ensure_archive_dir(path.parent, config.archive_dir)
    timestamp = _timestamp_suffix()
    archive_name = f"{path.stem}_{timestamp}{path.suffix}"
    archive_path = archive_dir / archive_name

    original_size = path.stat().st_size
    shutil.copy2(str(path), str(archive_path))

    final_archive = archive_path
    if config.compress:
        final_archive = Path(compress_file(archive_path))

    path.unlink()

    archive_size = final_archive.stat().st_size
    bytes_freed = original_size - archive_size

    return RotationAction(
        target=target,
        action_type="archive",
        archive_path=final_archive,
        bytes_freed=max(0, bytes_freed),
    )


def compress_file(path: Path | str) -> str:
    """Gzip compress a file, removing the original. Returns archive path."""
    path = Path(path)
    gz_path = path.with_suffix(path.suffix + ".gz")
    with open(path, "rb") as f_in:
        with gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
    path.unlink()
    return str(gz_path)


def execute_rotation(
    targets: List[RotationTarget],
    config: RotationConfig,
    dry_run: bool = False,
) -> RotationReport:
    """Execute rotation on all targets. In dry_run mode, no files are modified."""
    report = RotationReport(dry_run=dry_run)

    for target in targets:
        if dry_run:
            # Estimate freed bytes as the full file size (conservative)
            action = RotationAction(
                target=target,
                action_type="archive",
                archive_path=None,
                bytes_freed=target.size_bytes,
            )
            report.actions.append(action)
            report.total_freed_bytes += action.bytes_freed
            report.files_rotated += 1
            continue

        try:
            if target.file_type == "jsonl":
                action = rotate_jsonl(target, config)
            elif target.file_type == "json":
                action = rotate_json(target, config)
            else:
                action = rotate_other(target, config)

            report.actions.append(action)
            report.total_freed_bytes += action.bytes_freed
            if action.bytes_freed > 0 or action.archive_path is not None:
                report.files_rotated += 1
            else:
                report.files_skipped += 1
        except Exception as e:
            # Log but don't crash on individual file failures
            print(f"Warning: failed to rotate {target.path}: {e}", file=sys.stderr)
            report.files_skipped += 1

    return report


def format_report(report: RotationReport) -> str:
    """Format a rotation report as a human-readable ASCII table."""
    lines: List[str] = []
    mode = "DRY RUN" if report.dry_run else "ROTATION"
    lines.append(f"=== Log Rotation Report ({mode}) ===")
    lines.append("")

    if not report.actions:
        lines.append("No files needed rotation.")
        return "\n".join(lines)

    # Header
    lines.append(f"{'File':<50} {'Size':<10} {'Action':<10} {'Freed':<10}")
    lines.append("-" * 80)

    for action in report.actions:
        name = str(action.target.path)
        # Truncate long paths
        if len(name) > 48:
            name = "..." + name[-45:]
        lines.append(
            f"{name:<50} "
            f"{human_size(action.target.size_bytes):<10} "
            f"{action.action_type:<10} "
            f"{human_size(action.bytes_freed):<10}"
        )

    lines.append("-" * 80)
    lines.append(
        f"Total: {report.files_rotated} rotated, "
        f"{report.files_skipped} skipped, "
        f"{human_size(report.total_freed_bytes)} freed"
    )
    return "\n".join(lines)


def format_json(report: RotationReport) -> str:
    """Format a rotation report as JSON."""
    data = {
        "dry_run": report.dry_run,
        "files_rotated": report.files_rotated,
        "files_skipped": report.files_skipped,
        "total_freed_bytes": report.total_freed_bytes,
        "total_freed_human": human_size(report.total_freed_bytes),
        "actions": [
            {
                "file": str(a.target.path),
                "size_bytes": a.target.size_bytes,
                "age_days": round(a.target.age_days, 1),
                "file_type": a.target.file_type,
                "reason": a.target.reason,
                "action_type": a.action_type,
                "archive_path": str(a.archive_path) if a.archive_path else None,
                "bytes_freed": a.bytes_freed,
            }
            for a in report.actions
        ],
    }
    return json.dumps(data, indent=2)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="GRIP Log/Cache Rotation Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --dry-run data/ cache/\n"
            "  %(prog)s --max-size 5 --max-age 14 --keep-last 50 data/\n"
            "  %(prog)s --json data/ cache/\n"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be rotated without making changes",
    )
    parser.add_argument(
        "--max-size",
        type=float,
        default=10.0,
        metavar="MB",
        help="Maximum file size in MB before rotation (default: 10)",
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=30,
        metavar="DAYS",
        help="Maximum file age in days before rotation (default: 30)",
    )
    parser.add_argument(
        "--keep-last",
        type=int,
        default=100,
        metavar="N",
        help="Number of recent JSONL entries to keep (default: 100)",
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="Disable gzip compression of archives",
    )
    parser.add_argument(
        "--archive-dir",
        default="archive",
        metavar="DIR",
        help="Name of archive subdirectory (default: archive)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output report as JSON",
    )
    parser.add_argument(
        "dirs",
        nargs="*",
        default=["data", "cache"],
        metavar="DIR",
        help="Directories to scan (default: data cache)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    config = RotationConfig(
        max_size_mb=args.max_size,
        max_age_days=args.max_age,
        keep_last_n=args.keep_last,
        archive_dir=args.archive_dir,
        compress=not args.no_compress,
    )

    scan_dirs = [Path(d) for d in args.dirs]
    targets = scan_targets(scan_dirs, config)
    report = execute_rotation(targets, config, dry_run=args.dry_run)

    if args.json_output:
        print(format_json(report))
    else:
        print(format_report(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
