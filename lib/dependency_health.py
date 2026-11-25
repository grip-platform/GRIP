"""Dependency health monitor.

Checks for outdated packages, known vulnerabilities, and dependency
complexity across Python (pip) and Node.js (npm) projects.

Honesty contract (2026-08-17, provenance-perimeter sweep — four
adversarially-upheld findings fixed):

- A scan that measured NOTHING has no health score (``health_score=None``,
  rendered ``n/a``) — never a perfect ``100.0 (Excellent)`` for a
  nonexistent directory.
- A missing scanner binary is reported as an UNAUDITED surface
  (``audits_unavailable``), never rendered as ``Vulnerable: 0`` — empty
  output from an absent tool is not a clean audit.
- ``requirements.txt`` pin syntax is measured as ``loose-pin``, its own
  bucket; it is not staleness, joins no ``Outdated:`` list and deducts no
  staleness points (no version was ever compared against any index).
- "All dependencies are up to date and secure" is emitted only when
  something was actually scanned and every attempted audit ran.

Known residual blind spot: ``run_cmd`` also returns "" on a TIMEOUT with the
binary present, which still reads as no-findings; availability is gated on
``shutil.which`` only.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from subprocess_helpers import run_cmd as _run_cmd


# === Data Models ===

@dataclass
class PackageInfo:
    """Information about a single package."""
    name: str
    current_version: str = ""
    latest_version: str = ""
    status: str = "ok"  # ok, loose-pin, outdated, missing, vulnerable
    severity: str = ""  # low, moderate, high, critical
    advisory: str = ""
    ecosystem: str = ""  # pip, npm


@dataclass
class DependencyReport:
    """Complete dependency health report."""
    total_packages: int = 0
    outdated_count: int = 0
    vulnerable_count: int = 0
    loose_pin_count: int = 0
    packages: list[PackageInfo] = field(default_factory=list)
    health_score: Optional[float] = None  # 0-100; None = nothing measured
    ecosystems: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    audits_run: list[str] = field(default_factory=list)
    audits_unavailable: list[str] = field(default_factory=list)


_SEVERITY_ORDER = {"ok": 0, "loose-pin": 1, "outdated": 2, "missing": 3, "vulnerable": 4}


def _tool_available(binary: str) -> bool:
    """Whether a scanner binary is reachable on PATH — the deterministic
    line between 'ran and found nothing' and 'never ran at all'."""
    return shutil.which(binary) is not None


# === Scanners ===

def scan_pip_outdated(path: str = ".") -> list[PackageInfo]:
    """Scan for outdated pip packages."""
    output = _run_cmd("pip list --outdated --format=json")
    if not output:
        return []

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []

    packages = []
    for pkg in data:
        packages.append(PackageInfo(
            name=pkg.get("name", ""),
            current_version=pkg.get("version", ""),
            latest_version=pkg.get("latest_version", ""),
            status="outdated",
            ecosystem="pip",
        ))
    return packages


def scan_pip_audit(path: str = ".") -> list[PackageInfo]:
    """Scan for pip vulnerabilities using pip-audit."""
    output = _run_cmd("pip-audit --format=json")
    if not output:
        return []

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []

    packages = []
    vulns = data if isinstance(data, list) else data.get("dependencies", [])
    for item in vulns:
        for vuln in item.get("vulns", []):
            packages.append(PackageInfo(
                name=item.get("name", ""),
                current_version=item.get("version", ""),
                status="vulnerable",
                severity=vuln.get("fix_versions", [""])[0] if vuln.get("fix_versions") else "unknown",
                advisory=vuln.get("id", ""),
                ecosystem="pip",
            ))
    return packages


def scan_npm_outdated(path: str = ".") -> list[PackageInfo]:
    """Scan for outdated npm packages."""
    pkg_json = Path(path) / "package.json"
    if not pkg_json.exists():
        return []

    output = _run_cmd("npm outdated --json", cwd=path)
    if not output:
        return []

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []

    packages = []
    for name, info in data.items():
        packages.append(PackageInfo(
            name=name,
            current_version=info.get("current", ""),
            latest_version=info.get("latest", ""),
            status="outdated",
            ecosystem="npm",
        ))
    return packages


def scan_npm_audit(path: str = ".") -> list[PackageInfo]:
    """Scan for npm vulnerabilities."""
    pkg_json = Path(path) / "package.json"
    if not pkg_json.exists():
        return []

    output = _run_cmd("npm audit --json", cwd=path)
    if not output:
        return []

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []

    packages = []
    vulns = data.get("vulnerabilities", {})
    for name, info in vulns.items():
        packages.append(PackageInfo(
            name=name,
            current_version=info.get("range", ""),
            status="vulnerable",
            severity=info.get("severity", "unknown"),
            advisory=info.get("via", [{}])[0].get("url", "") if isinstance(info.get("via", [{}])[0], dict) else "",
            ecosystem="npm",
        ))
    return packages


def scan_requirements_txt(path: str = ".") -> list[PackageInfo]:
    """Parse requirements.txt for unpinned or loosely pinned packages.

    Pin SYNTAX is measured as status ``loose-pin`` — it is a reproducibility
    property of the file, not staleness: no version is ever compared against
    an index here.
    """
    req_file = Path(path) / "requirements.txt"
    if not req_file.exists():
        return []

    packages = []
    for line in req_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue

        # Parse package name and version
        for op in [">=", "<=", "==", "~=", "!="]:
            if op in line:
                name, version = line.split(op, 1)
                packages.append(PackageInfo(
                    name=name.strip(),
                    current_version=version.strip(),
                    status="ok" if op == "==" else "loose-pin",
                    ecosystem="pip",
                ))
                break
        else:
            # No version pin
            packages.append(PackageInfo(
                name=line.strip(),
                status="loose-pin",
                ecosystem="pip",
                advisory="No version pin — builds may break",
            ))

    return packages


# === Aggregation ===

def _scan_pip_ecosystem(path: str, audits_run: list[str],
                        audits_unavailable: list[str]) -> list[PackageInfo]:
    """All pip-side packages; records which audits actually ran."""
    packages: list[PackageInfo] = []
    if _tool_available("pip"):
        audits_run.append("pip-outdated")
        packages.extend(scan_pip_outdated(path))
    else:
        audits_unavailable.append("pip-outdated")
    if _tool_available("pip-audit"):
        audits_run.append("pip-audit")
        packages.extend(scan_pip_audit(path))
    else:
        audits_unavailable.append("pip-audit")
    req_pkgs = scan_requirements_txt(path)
    if req_pkgs:
        audits_run.append("requirements-txt")
        packages.extend(req_pkgs)
    return packages


def _scan_npm_ecosystem(path: str, audits_run: list[str],
                        audits_unavailable: list[str]) -> list[PackageInfo]:
    """All npm-side packages; not applicable (silent) without package.json."""
    if not (Path(path) / "package.json").exists():
        return []
    if not _tool_available("npm"):
        audits_unavailable.extend(["npm-outdated", "npm-audit"])
        return []
    audits_run.extend(["npm-outdated", "npm-audit"])
    return scan_npm_outdated(path) + scan_npm_audit(path)


def _dedupe_worst(all_packages: list[PackageInfo]) -> list[PackageInfo]:
    """Deduplicate by ecosystem:name, keeping the worst status."""
    seen: dict[str, PackageInfo] = {}
    for pkg in all_packages:
        key = f"{pkg.ecosystem}:{pkg.name}"
        if key not in seen or _SEVERITY_ORDER.get(pkg.status, 0) > _SEVERITY_ORDER.get(seen[key].status, 0):
            seen[key] = pkg
    return list(seen.values())


def analyze_dependencies(
    path: str = ".",
    include_pip: bool = True,
    include_npm: bool = True,
) -> DependencyReport:
    """Run full dependency health analysis."""
    audits_run: list[str] = []
    audits_unavailable: list[str] = []
    all_packages: list[PackageInfo] = []
    ecosystems = []

    if include_pip:
        pip_pkgs = _scan_pip_ecosystem(path, audits_run, audits_unavailable)
        all_packages.extend(pip_pkgs)
        if pip_pkgs:
            ecosystems.append("pip")

    if include_npm:
        npm_pkgs = _scan_npm_ecosystem(path, audits_run, audits_unavailable)
        all_packages.extend(npm_pkgs)
        if npm_pkgs:
            ecosystems.append("npm")

    return _build_report(_dedupe_worst(all_packages), ecosystems,
                         audits_run, audits_unavailable)


def analyze_from_data(
    packages: list[PackageInfo],
    ecosystems: Optional[list[str]] = None,
) -> DependencyReport:
    """Build report from pre-scanned package data (useful for testing)."""
    return _build_report(packages, ecosystems or [], [], [])


def _build_report(packages: list[PackageInfo], ecosystems: list[str],
                  audits_run: list[str],
                  audits_unavailable: list[str]) -> DependencyReport:
    outdated_count = sum(1 for p in packages if p.status == "outdated")
    vulnerable_count = sum(1 for p in packages if p.status == "vulnerable")
    loose_pin_count = sum(1 for p in packages if p.status == "loose-pin")
    total = len(packages)
    return DependencyReport(
        total_packages=total,
        outdated_count=outdated_count,
        vulnerable_count=vulnerable_count,
        loose_pin_count=loose_pin_count,
        packages=sorted(packages,
                        key=lambda p: _SEVERITY_ORDER.get(p.status, 0),
                        reverse=True),
        health_score=_calculate_health(total, outdated_count, vulnerable_count),
        ecosystems=ecosystems,
        recommendations=_generate_recommendations(
            packages, outdated_count, vulnerable_count,
            total=total, audits_unavailable=audits_unavailable),
        audits_run=audits_run,
        audits_unavailable=audits_unavailable,
    )


def _calculate_health(total: int, outdated: int, vulnerable: int) -> Optional[float]:
    """Health score 0-100 from MEASURED counts, or None when nothing was
    measured — zero packages is no grade, never a perfect one."""
    if total == 0:
        return None

    score = 100.0
    # Outdated packages reduce score
    outdated_ratio = outdated / total
    score -= outdated_ratio * 30  # Max -30 for all outdated

    # Vulnerabilities are worse
    vuln_ratio = vulnerable / total
    score -= vuln_ratio * 60  # Max -60 for all vulnerable

    return max(0, min(100, round(score, 1)))


def _generate_recommendations(
    packages: list[PackageInfo],
    outdated_count: int,
    vulnerable_count: int,
    total: Optional[int] = None,
    audits_unavailable: tuple | list = (),
) -> list[str]:
    """Generate actionable recommendations. Never asserts security that was
    not measured: unavailable audits are surfaced as UNVERIFIED, and the
    all-clear line requires real data with every attempted audit run."""
    total = len(packages) if total is None else total
    recs = []

    if vulnerable_count > 0:
        critical = [p for p in packages if p.status == "vulnerable" and p.severity == "critical"]
        if critical:
            names = ", ".join(p.name for p in critical[:3])
            recs.append(f"URGENT: {len(critical)} critical vulnerabilities ({names}) — update immediately")
        else:
            recs.append(f"{vulnerable_count} vulnerable package(s) — review and update")

    if outdated_count > 5:
        recs.append(f"{outdated_count} outdated packages — schedule a dependency update sprint")
    elif outdated_count > 0:
        recs.append(f"{outdated_count} outdated package(s) — update when convenient")

    loose = [p for p in packages if p.status == "loose-pin"]
    if loose:
        recs.append(f"{len(loose)} loosely-pinned dependencies — pin exact versions (==) for reproducible builds")

    if audits_unavailable:
        recs.append(
            f"UNVERIFIED: {', '.join(audits_unavailable)} did not run (tool "
            "unavailable) — the counts above cover only what was actually scanned"
        )

    if not recs:
        recs.append(
            "All dependencies are up to date and secure" if total
            else "No packages found — nothing was scanned"
        )

    return recs


# === Formatters ===

HEALTH_LABELS = {
    (90, 101): "Excellent",
    (70, 90): "Good",
    (50, 70): "Fair",
    (30, 50): "Needs Attention",
    (0, 30): "Critical",
}


def _health_label(score: Optional[float]) -> str:
    if score is None:
        return "No Data"
    for (low, high), label in HEALTH_LABELS.items():
        if low <= score < high:
            return label
    return "Unknown"


def _score_display(report: DependencyReport) -> str:
    """The score fragment, honest about missing data and partial audits."""
    if report.health_score is None:
        base = "n/a (nothing measured)"
    else:
        base = f"{report.health_score}/100 ({_health_label(report.health_score)})"
    if report.audits_unavailable:
        base += f" — PARTIAL: {', '.join(report.audits_unavailable)} did not run"
    return base


def _vulnerable_display(report: DependencyReport) -> str:
    """Never renders a bare 0 when no vulnerability audit executed."""
    missing_vuln_audits = [a for a in report.audits_unavailable if a.endswith("audit")]
    if missing_vuln_audits and report.vulnerable_count == 0:
        return f"unaudited ({', '.join(missing_vuln_audits)} unavailable)"
    if missing_vuln_audits:
        return f"{report.vulnerable_count} ({', '.join(missing_vuln_audits)} unavailable)"
    return str(report.vulnerable_count)


def _counts_line(report: DependencyReport) -> str:
    counts = f"Outdated: {report.outdated_count} | Vulnerable: {_vulnerable_display(report)}"
    if report.loose_pin_count:
        counts += f" | Loose pins: {report.loose_pin_count}"
    return counts


def _package_sections(report: DependencyReport) -> list[str]:
    """Vulnerability / outdated / loose-pin listings for the text format."""
    lines: list[str] = []
    vulns = [p for p in report.packages if p.status == "vulnerable"]
    if vulns:
        lines += ["", "Vulnerabilities:"]
        for p in vulns[:10]:
            sev = f" [{p.severity}]" if p.severity else ""
            lines.append(f"  ! {p.name} {p.current_version}{sev}")
            if p.advisory:
                lines.append(f"    {p.advisory}")

    outdated = [p for p in report.packages if p.status == "outdated"]
    if outdated:
        lines += ["", "Outdated:"]
        for p in outdated[:10]:
            upgrade = f" -> {p.latest_version}" if p.latest_version else ""
            lines.append(f"  ~ {p.name} {p.current_version}{upgrade}")

    loose = [p for p in report.packages if p.status == "loose-pin"]
    if loose:
        lines += ["", "Loose pins (reproducibility, not staleness):"]
        for p in loose[:10]:
            ver = f" {p.current_version}" if p.current_version else " (no pin)"
            lines.append(f"  ~ {p.name}{ver}")
    return lines


def format_report_text(report: DependencyReport) -> str:
    """Format as terminal text."""
    lines = [
        "Dependency Health Report",
        "=" * 50,
        f"Health Score: {_score_display(report)}",
        f"Total Packages: {report.total_packages}",
        _counts_line(report),
    ]
    if report.ecosystems:
        lines.append(f"Ecosystems: {', '.join(report.ecosystems)}")
    lines += _package_sections(report)
    if report.recommendations:
        lines += ["", "Recommendations:"]
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"  {i}. {rec}")
    return "\n".join(lines)


def format_report_slack(report: DependencyReport) -> str:
    """Format as Slack mrkdwn."""
    lines = []
    lines.append("*Dependency Health Report*")
    lines.append(f"Score: *{_score_display(report)}*")
    lines.append(
        f"Packages: {report.total_packages} | Outdated: {report.outdated_count} "
        f"| Vulnerable: {_vulnerable_display(report)}"
    )

    vulns = [p for p in report.packages if p.status == "vulnerable"]
    if vulns:
        lines.append("")
        lines.append("_Vulnerabilities:_")
        for p in vulns[:5]:
            sev = f" [{p.severity}]" if p.severity else ""
            lines.append(f"  - `{p.name}` {p.current_version}{sev}")

    if report.recommendations:
        lines.append("")
        lines.append("_Recommendations:_")
        for rec in report.recommendations:
            lines.append(f"  - {rec}")

    return "\n".join(lines)


def format_report_json(report: DependencyReport) -> str:
    """Format as JSON."""
    data = {
        "health_score": report.health_score,
        "health_label": _health_label(report.health_score),
        "total_packages": report.total_packages,
        "outdated_count": report.outdated_count,
        "vulnerable_count": report.vulnerable_count,
        "loose_pin_count": report.loose_pin_count,
        "ecosystems": report.ecosystems,
        "audits_run": report.audits_run,
        "audits_unavailable": report.audits_unavailable,
        "packages": [
            {
                "name": p.name,
                "current_version": p.current_version,
                "latest_version": p.latest_version,
                "status": p.status,
                "severity": p.severity,
                "ecosystem": p.ecosystem,
            }
            for p in report.packages
        ],
        "recommendations": report.recommendations,
    }
    return json.dumps(data, indent=2)


# === CLI ===

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Dependency Health Monitor")
    parser.add_argument("path", nargs="?", default=".", help="Project path")
    parser.add_argument("--format", choices=["text", "slack", "json"], default="text")
    parser.add_argument("--pip-only", action="store_true")
    parser.add_argument("--npm-only", action="store_true")
    args = parser.parse_args()

    report = analyze_dependencies(
        args.path,
        include_pip=not args.npm_only,
        include_npm=not args.pip_only,
    )

    if args.format == "slack":
        print(format_report_slack(report))
    elif args.format == "json":
        print(format_report_json(report))
    else:
        print(format_report_text(report))


if __name__ == "__main__":
    main()
