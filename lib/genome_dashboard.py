#!/usr/bin/env python3
"""Genome DNA Dashboard — generates a self-contained HTML dashboard from cache files."""

import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
CACHE_DIR = CLAUDE_DIR / "cache"

GENOME_PATH = CACHE_DIR / "genome.json"
HISTORY_PATH = CACHE_DIR / "genome-history.jsonl"
OUTCOMES_PATH = CACHE_DIR / "genome-outcomes.jsonl"
VERDICTS_PATH = CACHE_DIR / "review-verdicts.jsonl"
OUTPUT_PATH = CLAUDE_DIR / "genome-dashboard.html"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def load_genome(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def classify_genes(genome: dict) -> dict[str, list]:
    """Classify genes into tiers by fitness threshold."""
    genes = genome.get("genes", {})
    tiers = {"essential": [], "active": [], "dormant": [], "dead": []}
    for name, gene in genes.items():
        f = gene.get("fitness", 0)
        inv = gene.get("invocation_count", 0)
        if f <= 0:
            tiers["dead"].append((name, f))
        elif f >= 0.1 or inv > 0 or gene.get("protected", False):
            tiers["essential"].append((name, f))
        elif f >= 0.01:
            tiers["active"].append((name, f))
        else:
            tiers["dormant"].append((name, f))
    return tiers


def svg_line_chart(points: list[tuple[str, float]], width: int = 700, height: int = 220,
                   color: str = "#4ade80", label: str = "") -> str:
    """SVG line chart from (label, value) pairs."""
    if not points:
        return '<text x="50%" y="50%" text-anchor="middle" fill="#888">No data</text>'
    vals = [v for _, v in points]
    mn, mx = min(vals), max(vals)
    rng = mx - mn if mx != mn else 1
    pad_x, pad_y, pad_r, pad_b = 55, 15, 15, 40
    chart_w = width - pad_x - pad_r
    chart_h = height - pad_y - pad_b
    n = len(points)
    step = chart_w / max(n - 1, 1)

    # Build polyline
    coords = []
    for i, (_, v) in enumerate(points):
        x = pad_x + i * step
        y = pad_y + chart_h - ((v - mn) / rng) * chart_h
        coords.append(f"{x:.1f},{y:.1f}")

    lines = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']
    # Grid lines
    for i in range(5):
        gy = pad_y + chart_h * i / 4
        gv = mx - (mx - mn) * i / 4
        lines.append(f'<line x1="{pad_x}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" stroke="#333" stroke-dasharray="3"/>')
        lines.append(f'<text x="{pad_x - 8}" y="{gy + 4:.1f}" text-anchor="end" fill="#888" font-size="11">{gv:.3f}</text>')
    # X labels
    max_labels = 8
    label_step = max(1, n // max_labels)
    for i in range(0, n, label_step):
        lbl, _ = points[i]
        x = pad_x + i * step
        lines.append(f'<text x="{x:.1f}" y="{height - 5}" text-anchor="middle" fill="#888" font-size="10">{lbl}</text>')
    # Line + dots
    lines.append(f'<polyline points="{" ".join(coords)}" fill="none" stroke="{color}" stroke-width="2.5"/>')
    for i, (_, v) in enumerate(points):
        x = pad_x + i * step
        y = pad_y + chart_h - ((v - mn) / rng) * chart_h
        lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')
    if label:
        lines.append(f'<text x="{width // 2}" y="{height - 2}" text-anchor="middle" fill="#aaa" font-size="10">{label}</text>')
    lines.append('</svg>')
    return "\n".join(lines)


def svg_bar_chart(data: list[tuple[str, int, str]], width: int = 400, height: int = 200) -> str:
    """SVG bar chart from (label, value, color) triples."""
    if not data:
        return '<text x="50%" y="50%" text-anchor="middle" fill="#888">No data</text>'
    mx = max(v for _, v, _ in data)
    if mx == 0:
        mx = 1
    pad_x, pad_y, pad_r, pad_b = 80, 15, 15, 10
    chart_w = width - pad_x - pad_r
    chart_h = height - pad_y - pad_b
    n = len(data)
    bar_h = min(30, chart_h / n - 5)
    gap = (chart_h - bar_h * n) / max(n, 1)

    lines = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']
    for i, (lbl, val, col) in enumerate(data):
        y = pad_y + i * (bar_h + gap) + gap / 2
        bw = (val / mx) * chart_w
        lines.append(f'<text x="{pad_x - 8}" y="{y + bar_h / 2 + 4:.1f}" text-anchor="end" fill="#ccc" font-size="12">{lbl}</text>')
        lines.append(f'<rect x="{pad_x}" y="{y:.1f}" width="{bw:.1f}" height="{bar_h}" rx="3" fill="{col}"/>')
        lines.append(f'<text x="{pad_x + bw + 6:.1f}" y="{y + bar_h / 2 + 4:.1f}" fill="#ccc" font-size="12">{val}</text>')
    lines.append('</svg>')
    return "\n".join(lines)


def svg_scatter(outcomes: list[dict], width: int = 700, height: int = 250) -> str:
    """SVG scatter of improvement per run, color-coded by satisfied."""
    if not outcomes:
        return '<text x="50%" y="50%" text-anchor="middle" fill="#888">No data</text>'
    improvements = [o.get("improvement", 0) for o in outcomes]
    mn, mx = min(improvements), max(improvements)
    rng = mx - mn if mx != mn else 1
    pad_x, pad_y, pad_r, pad_b = 55, 15, 15, 30
    chart_w = width - pad_x - pad_r
    chart_h = height - pad_y - pad_b
    n = len(outcomes)
    step = chart_w / max(n - 1, 1)

    lines = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']
    # Zero line
    if mn < 0 < mx:
        zy = pad_y + chart_h - ((0 - mn) / rng) * chart_h
        lines.append(f'<line x1="{pad_x}" y1="{zy:.1f}" x2="{width - pad_r}" y2="{zy:.1f}" stroke="#555" stroke-width="1"/>')
        lines.append(f'<text x="{pad_x - 8}" y="{zy + 4:.1f}" text-anchor="end" fill="#888" font-size="11">0</text>')
    # Grid
    for i in range(5):
        gy = pad_y + chart_h * i / 4
        gv = mx - (mx - mn) * i / 4
        lines.append(f'<line x1="{pad_x}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" stroke="#333" stroke-dasharray="3"/>')
        lines.append(f'<text x="{pad_x - 8}" y="{gy + 4:.1f}" text-anchor="end" fill="#888" font-size="11">{gv:.1f}</text>')
    # Dots
    for i, o in enumerate(outcomes):
        imp = o.get("improvement", 0)
        sat = o.get("satisfied", False)
        x = pad_x + i * step
        y = pad_y + chart_h - ((imp - mn) / rng) * chart_h
        col = "#4ade80" if sat else "#f87171"
        lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{col}" opacity="0.8"/>')
    # Legend
    lx = width - pad_r - 140
    lines.append(f'<circle cx="{lx}" cy="{pad_y + 5}" r="5" fill="#4ade80"/>')
    lines.append(f'<text x="{lx + 10}" y="{pad_y + 9}" fill="#aaa" font-size="11">Satisfied</text>')
    lines.append(f'<circle cx="{lx + 80}" cy="{pad_y + 5}" r="5" fill="#f87171"/>')
    lines.append(f'<text x="{lx + 90}" y="{pad_y + 9}" fill="#aaa" font-size="11">Failed</text>')
    lines.append('</svg>')
    return "\n".join(lines)


def svg_cube_bars(outcomes: list[dict], width: int = 400, height: int = 200) -> str:
    """SVG horizontal bars for CUBE domain averages from outcomes."""
    domain_sums: dict[str, list[float]] = {}
    for o in outcomes:
        cubes = o.get("cube_scores", {})
        for domain, score in cubes.items():
            domain_sums.setdefault(domain, []).append(score)
    if not domain_sums:
        return '<text x="50%" y="50%" text-anchor="middle" fill="#888">No CUBE data</text>'
    avgs = sorted(
        [(d, sum(vals) / len(vals)) for d, vals in domain_sums.items()],
        key=lambda x: -x[1]
    )
    colors = {"DRY": "#60a5fa", "SOLID": "#a78bfa", "KISS": "#34d399", "YAGNI": "#fbbf24", "GRASP": "#f472b6"}
    pad_x, pad_y, pad_r, pad_b = 65, 15, 40, 10
    chart_w = width - pad_x - pad_r
    chart_h = height - pad_y - pad_b
    n = len(avgs)
    bar_h = min(28, chart_h / n - 5)
    gap = (chart_h - bar_h * n) / max(n, 1)

    lines = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']
    for i, (domain, avg) in enumerate(avgs):
        y = pad_y + i * (bar_h + gap) + gap / 2
        bw = avg * chart_w  # scores are 0-1
        col = colors.get(domain, "#888")
        lines.append(f'<text x="{pad_x - 8}" y="{y + bar_h / 2 + 4:.1f}" text-anchor="end" fill="#ccc" font-size="12">{domain}</text>')
        lines.append(f'<rect x="{pad_x}" y="{y:.1f}" width="{bw:.1f}" height="{bar_h}" rx="3" fill="{col}" opacity="0.85"/>')
        lines.append(f'<text x="{pad_x + bw + 6:.1f}" y="{y + bar_h / 2 + 4:.1f}" fill="#ccc" font-size="12">{avg:.2f}</text>')
    lines.append('</svg>')
    return "\n".join(lines)


def count_by_component_type(genome: dict) -> dict[str, int]:
    """Count genes by component_type."""
    counts: dict[str, int] = {}
    for gene in genome.get("genes", {}).values():
        ct = gene.get("component_type", "unknown")
        counts[ct] = counts.get(ct, 0) + 1
    return counts


def get_genesis_genes(genome: dict) -> list[tuple[str, dict]]:
    """Return genes created via genesis (lineage starts with 'genesis:')."""
    return [
        (name, gene) for name, gene in genome.get("genes", {}).items()
        if (gene.get("lineage") or "").startswith("genesis:")
    ]


def render_summary_cards(genome: dict, history: list[dict]) -> str:
    gen = genome.get("generation", "?")
    genes = genome.get("genes", {})
    total = len(genes)
    fh = genome.get("fitness_history", [])
    avg_fit = fh[-1] if fh else 0
    sr = history[-1].get("success_rate", 0) if history else 0
    mr = genome.get("mutation_rate", 0)
    living = history[-1].get("living_count", total) if history else total
    dead = history[-1].get("dead_count", 0) if history else 0
    genesis_count = len(get_genesis_genes(genome))

    cards = [
        ("Generation", str(gen), "#60a5fa"),
        ("Total Genes", str(total), "#a78bfa"),
        ("Living / Dead", f"{living} / {dead}", "#34d399"),
        ("Avg Fitness", f"{avg_fit:.4f}", "#4ade80"),
        ("Success Rate", f"{sr * 100:.1f}%", "#fbbf24"),
        ("Mutation Rate", f"{mr:.4f}", "#f472b6"),
        ("Genesis Skills", str(genesis_count), "#c084fc"),
    ]
    html = '<div class="cards">\n'
    for label, value, color in cards:
        html += f'''  <div class="card">
    <div class="card-value" style="color:{color}">{value}</div>
    <div class="card-label">{label}</div>
  </div>\n'''
    html += '</div>'
    return html


def render_gene_tables(genome: dict) -> str:
    """Tables for top 10 and bottom 10 fitness genes."""
    genes = genome.get("genes", {})
    if not genes:
        return '<p class="muted">No gene data</p>'
    sorted_genes = sorted(genes.items(), key=lambda x: x[1].get("fitness", 0), reverse=True)
    top10 = sorted_genes[:10]
    bottom10 = sorted_genes[-10:]

    def gene_table(title: str, gene_list: list) -> str:
        rows = ""
        for name, gene in gene_list:
            f = gene.get("fitness", 0)
            inv = gene.get("invocation_count", 0)
            gen_born = gene.get("generation_born", 0)
            ctype = gene.get("component_type", "?")
            bar_w = min(f * 200, 200)
            bar_col = "#4ade80" if f > 0.05 else "#fbbf24" if f > 0 else "#f87171"
            rows += f'''<tr>
  <td class="gene-name">{name}</td>
  <td>{ctype}</td>
  <td><div class="fitness-bar"><div style="width:{bar_w:.0f}px;background:{bar_col}"></div></div> {f:.4f}</td>
  <td>{inv}</td>
  <td>Gen {gen_born}</td>
</tr>\n'''
        return f'''<h3>{title}</h3>
<table>
<thead><tr><th>Gene</th><th>Type</th><th>Fitness</th><th>Invocations</th><th>Born</th></tr></thead>
<tbody>{rows}</tbody>
</table>'''

    return gene_table("Top 10 Genes (Highest Fitness)", top10) + gene_table("Bottom 10 Genes (Lowest Fitness)", bottom10)


def render_component_type_chart(genome: dict) -> str:
    """Bar chart breaking down genes by component_type."""
    counts = count_by_component_type(genome)
    type_colors = {
        "skill": "#60a5fa", "mode": "#a78bfa", "rule": "#fbbf24",
        "agent": "#34d399", "unknown": "#888",
    }
    data = sorted(counts.items(), key=lambda x: -x[1])
    bar_data = [(ct, n, type_colors.get(ct, "#888")) for ct, n in data]
    return svg_bar_chart(bar_data, width=400, height=200)


def render_genesis_panel(genome: dict) -> str:
    """Panel showing genesis (micro-skill) genes created by LLM review."""
    genesis = get_genesis_genes(genome)
    if not genesis:
        return '''<div class="genesis-empty">
  <span class="muted">No genesis micro-skills yet. The LLM attribution layer proposes micro-skills
  when it detects behaviors not captured by existing genes. They appear here once created.</span>
</div>'''
    rows = ""
    for name, gene in sorted(genesis, key=lambda x: x[1].get("fitness", 0), reverse=True):
        f = gene.get("fitness", 0)
        lineage = (gene.get("lineage") or "").replace("genesis:", "")
        gen_born = gene.get("generation_born", 0)
        inv = gene.get("invocation_count", 0)
        bar_w = min(f * 200, 200)
        rows += f'''<tr>
  <td class="gene-name">{name}</td>
  <td class="genesis-source">{lineage}</td>
  <td><div class="fitness-bar"><div style="width:{bar_w:.0f}px;background:#c084fc"></div></div> {f:.4f}</td>
  <td>{inv}</td>
  <td>Gen {gen_born}</td>
</tr>\n'''
    return f'''<table>
<thead><tr><th>Micro-Skill</th><th>Source Gene</th><th>Fitness</th><th>Invocations</th><th>Born</th></tr></thead>
<tbody>{rows}</tbody>
</table>'''


def render_full_catalog(genome: dict) -> str:
    """Full searchable gene catalog grouped by component_type."""
    genes = genome.get("genes", {})
    if not genes:
        return '<p class="muted">No gene data</p>'

    # Group by component_type
    groups: dict[str, list[tuple[str, dict]]] = {}
    for name, gene in genes.items():
        ct = gene.get("component_type", "unknown")
        groups.setdefault(ct, []).append((name, gene))

    type_order = ["skill", "mode", "agent", "rule"]
    type_colors = {
        "skill": "#60a5fa", "mode": "#a78bfa", "rule": "#fbbf24",
        "agent": "#34d399", "unknown": "#888",
    }

    html = ""
    for ct in type_order:
        if ct not in groups:
            continue
        gene_list = sorted(groups[ct], key=lambda x: x[1].get("fitness", 0), reverse=True)
        color = type_colors.get(ct, "#888")
        rows = ""
        for name, gene in gene_list:
            f = gene.get("fitness", 0)
            inv = gene.get("invocation_count", 0)
            gen_born = gene.get("generation_born", 0)
            lineage = gene.get("lineage") or ""
            is_genesis = lineage.startswith("genesis:")
            bar_w = min(f * 200, 200)
            bar_col = "#c084fc" if is_genesis else ("#4ade80" if f > 0.05 else "#fbbf24" if f > 0 else "#f87171")
            origin_badge = f'<span class="badge genesis">genesis</span>' if is_genesis else ""
            rows += f'''<tr>
  <td class="gene-name">{name} {origin_badge}</td>
  <td><div class="fitness-bar"><div style="width:{bar_w:.0f}px;background:{bar_col}"></div></div> {f:.4f}</td>
  <td>{inv}</td>
  <td>Gen {gen_born}</td>
</tr>\n'''
        html += f'''<h3 style="color:{color}">{ct.upper()} ({len(gene_list)})</h3>
<table>
<thead><tr><th>Gene</th><th>Fitness</th><th>Invocations</th><th>Born</th></tr></thead>
<tbody>{rows}</tbody>
</table>\n'''
    return html


def render_verdicts_table(verdicts: list[dict]) -> str:
    """Table of last 5 LLM attribution verdicts."""
    recent = [v for v in verdicts if v.get("reasoning")][-5:]
    if not recent:
        return '<p class="muted">No verdict data with reasoning</p>'
    rows = ""
    for v in reversed(recent):
        run = v.get("run_id", "?")
        ts = v.get("timestamp", "")[:16].replace("T", " ")
        attribs = v.get("attributions", {})
        top_attribs = ", ".join(f"{k} ({v2:.1f})" for k, v2 in sorted(attribs.items(), key=lambda x: -x[1])[:3])
        neg = v.get("negative_attributions", {})
        neg_str = ", ".join(neg.keys()) if neg else "none"
        reason = v.get("reasoning", "")[:120]
        genesis = len(v.get("proposed_skills", []))
        rows += f'''<tr>
  <td>{run}</td>
  <td class="ts">{ts}</td>
  <td>{top_attribs}</td>
  <td>{neg_str}</td>
  <td>{genesis}</td>
  <td class="reason">{reason}</td>
</tr>\n'''
    return f'''<table>
<thead><tr><th>Run</th><th>Timestamp</th><th>Top Attributions</th><th>Negative</th><th>Genesis</th><th>Reasoning</th></tr></thead>
<tbody>{rows}</tbody>
</table>'''


def render_dashboard(genome: dict, history: list[dict], outcomes: list[dict], verdicts: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Fitness trend from history
    fitness_points = []
    for h in history:
        ts = h.get("timestamp", "")[:10]
        gen = h.get("generation", 0)
        af = h.get("avg_fitness", 0)
        fitness_points.append((f"G{gen} {ts[5:]}", af))

    # Tier distribution
    tiers = classify_genes(genome)
    tier_data = [
        ("Essential", len(tiers["essential"]), "#4ade80"),
        ("Active", len(tiers["active"]), "#60a5fa"),
        ("Dormant", len(tiers["dormant"]), "#fbbf24"),
        ("Dead", len(tiers["dead"]), "#f87171"),
    ]

    # Deduplicate outcomes by run_id+timestamp for scatter
    seen = set()
    unique_outcomes = []
    for o in outcomes:
        key = (o.get("run_id", ""), o.get("timestamp", ""))
        if key not in seen:
            seen.add(key)
            unique_outcomes.append(o)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Genome DNA Dashboard</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0a0a0a; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; padding: 24px; }}
h1 {{ font-size: 1.5rem; color: #fff; margin-bottom: 4px; }}
h2 {{ font-size: 1.1rem; color: #aaa; margin: 28px 0 12px; border-bottom: 1px solid #222; padding-bottom: 6px; }}
h3 {{ font-size: 0.95rem; color: #888; margin: 18px 0 8px; }}
.header {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 20px; }}
.header .ts {{ color: #555; font-size: 0.85rem; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 8px; }}
.card {{ background: #141414; border: 1px solid #222; border-radius: 8px; padding: 16px 14px; text-align: center; }}
.card-value {{ font-size: 1.4rem; font-weight: 700; }}
.card-label {{ font-size: 0.75rem; color: #666; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
.chart-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
.chart-box {{ background: #111; border: 1px solid #1a1a1a; border-radius: 8px; padding: 16px; }}
.chart-box.full {{ grid-column: 1 / -1; }}
svg {{ width: 100%; height: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
th {{ text-align: left; color: #666; font-weight: 600; padding: 6px 10px; border-bottom: 1px solid #222; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.5px; }}
td {{ padding: 5px 10px; border-bottom: 1px solid #1a1a1a; }}
tr:hover {{ background: #151515; }}
.gene-name {{ color: #60a5fa; font-family: monospace; font-size: 0.8rem; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.fitness-bar {{ display: inline-block; width: 80px; height: 8px; background: #222; border-radius: 4px; vertical-align: middle; margin-right: 6px; overflow: hidden; }}
.fitness-bar div {{ height: 100%; border-radius: 4px; }}
.reason {{ color: #888; font-size: 0.75rem; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.ts {{ white-space: nowrap; }}
.muted {{ color: #555; font-style: italic; padding: 12px 0; }}
.badge {{ display: inline-block; font-size: 0.6rem; padding: 1px 6px; border-radius: 3px; vertical-align: middle; margin-left: 4px; font-weight: 600; letter-spacing: 0.3px; }}
.badge.genesis {{ background: #c084fc33; color: #c084fc; border: 1px solid #c084fc55; }}
.genesis-source {{ color: #a78bfa; font-family: monospace; font-size: 0.8rem; }}
.genesis-empty {{ background: #141414; border: 1px solid #222; border-radius: 8px; padding: 20px; }}
@media (max-width: 768px) {{
  .chart-grid {{ grid-template-columns: 1fr; }}
  .cards {{ grid-template-columns: repeat(2, 1fr); }}
}}
</style>
</head>
<body>
<div class="header">
  <h1>Genome DNA Dashboard</h1>
  <span class="ts">Generated {now}</span>
</div>

{render_summary_cards(genome, history)}

<div class="chart-grid">
  <div class="chart-box full">
    <h2>Fitness Trend</h2>
    {svg_line_chart(fitness_points)}
  </div>

  <div class="chart-box">
    <h2>Gene Tier Distribution</h2>
    {svg_bar_chart(tier_data)}
  </div>

  <div class="chart-box">
    <h2>Component Types</h2>
    {render_component_type_chart(genome)}
  </div>

  <div class="chart-box">
    <h2>CUBE Domain Scores</h2>
    {svg_cube_bars(unique_outcomes)}
  </div>

  <div class="chart-box">
    <h2>Genesis Micro-Skills</h2>
    {render_genesis_panel(genome)}
  </div>

  <div class="chart-box full">
    <h2>Converge Outcomes</h2>
    {svg_scatter(unique_outcomes)}
  </div>
</div>

<h2>Recent LLM Attribution Verdicts</h2>
{render_verdicts_table(verdicts)}

<h2>Gene Fitness Rankings</h2>
{render_gene_tables(genome)}

<h2>Full Gene Catalog</h2>
{render_full_catalog(genome)}

</body>
</html>'''


def main():
    genome = load_genome(GENOME_PATH)
    history = load_jsonl(HISTORY_PATH)
    outcomes = load_jsonl(OUTCOMES_PATH)
    verdicts = load_jsonl(VERDICTS_PATH)

    if not genome:
        print(f"No genome found at {GENOME_PATH}", file=sys.stderr)
        sys.exit(1)

    html = render_dashboard(genome, history, outcomes, verdicts)
    OUTPUT_PATH.write_text(html)
    print(f"Dashboard written to {OUTPUT_PATH}")

    if platform.system() == "Darwin":
        subprocess.run(["open", str(OUTPUT_PATH)])
    elif platform.system() == "Linux":
        subprocess.run(["xdg-open", str(OUTPUT_PATH)])


if __name__ == "__main__":
    main()
