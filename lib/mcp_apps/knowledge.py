"""Knowledge-related MCP app generators: concept maps, gaps, ideas, complexity, collaboration."""

from .common import _run_git, CLAUDE_DIR


def generate_knowledge_concept_map(graph_data: dict = None) -> dict:
    """Generate grip_concept_map from developer knowledge graph.

    Args:
        graph_data: Dict with developers, expertise, collaborations.
                    If None, runs knowledge_graph.py against current repo.

    Returns:
        Dict for grip_concept_map: {title, nodes, edges}
    """
    if graph_data is None:
        try:
            from knowledge_graph import scan_git_log, build_knowledge_graph, format_graph_concept_map
            file_authors, author_stats = scan_git_log(".")
            graph = build_knowledge_graph(file_authors, author_stats)
            return format_graph_concept_map(graph)
        except (ImportError, Exception):
            graph_data = {"developers": [], "expertise": [], "collaborations": []}

    nodes = []
    edges = []

    for dev in graph_data.get("developers", []):
        name = dev.get("name", dev) if isinstance(dev, dict) else dev
        nodes.append({"id": f"dev-{name}", "label": name, "type": "developer"})

    for exp in graph_data.get("expertise", []):
        area = exp.get("area", "")
        dev = exp.get("developer", "")
        area_id = f"area-{area}"
        if not any(n["id"] == area_id for n in nodes):
            nodes.append({"id": area_id, "label": area, "type": "area"})
        depth = exp.get("depth", 0.5)
        style = "solid" if depth > 0.6 else "dashed"
        edges.append({"from": f"dev-{dev}", "to": area_id, "label": f"{int(depth*100)}%", "style": style})

    for collab in graph_data.get("collaborations", []):
        edges.append({
            "from": f"dev-{collab.get('dev_a', '')}",
            "to": f"dev-{collab.get('dev_b', '')}",
            "label": f"{collab.get('shared_files', 0)} files",
            "style": "dotted",
        })

    return {"title": "Developer Knowledge Graph", "nodes": nodes, "edges": edges}


def generate_gap_visualiser(skills_analysis: dict) -> dict:
    """Generate grip_gap_visualiser for skill coverage analysis.

    Args:
        skills_analysis: Dict with keys: current_skills, missing_skills, coverage_by_domain

    Returns:
        Dict with structure: {title, gaps: [{id, label, current, target, status}]}
    """
    gaps = []

    # Domain coverage gaps
    if "coverage_by_domain" in skills_analysis:
        for domain, data in skills_analysis["coverage_by_domain"].items():
            current = data.get("current", 0)
            target = data.get("target", 10)
            gap_pct = ((target - current) / max(target, 1)) * 100

            if gap_pct > 0:
                status = "critical" if gap_pct > 50 else "warning" if gap_pct > 25 else "minor"
                gaps.append({
                    "id": domain.lower().replace(" ", "_"),
                    "label": domain,
                    "current": current,
                    "target": target,
                    "status": status
                })

    # Missing skills
    if "missing_skills" in skills_analysis:
        for skill in skills_analysis["missing_skills"][:10]:
            gaps.append({
                "id": skill.lower().replace(" ", "_"),
                "label": f"Missing: {skill}",
                "current": 0,
                "target": 1,
                "status": "warning"
            })

    return {
        "title": "Skill Coverage Gaps",
        "gaps": gaps
    }


def generate_idea_canvas(ideas: list) -> dict:
    """Generate grip_idea_canvas from brainstorming ideas.

    Args:
        ideas: List of idea dicts with keys: title, description, category, status

    Returns:
        Dict with structure: {title, quadrants: [{id, label, ideas}]}
    """
    # Classify ideas into quadrants
    high_impact_easy = []
    high_impact_hard = []
    low_impact_easy = []
    low_impact_hard = []

    for idea in ideas:
        effort = idea.get("effort", "medium")
        impact = idea.get("impact", "medium")

        idea_item = {
            "id": idea.get("id", ""),
            "title": idea.get("title", "")[:40],
            "description": idea.get("description", "")[:60],
            "status": idea.get("status", "proposed")
        }

        if impact in ("high", "critical"):
            if effort in ("low", "small"):
                high_impact_easy.append(idea_item)
            else:
                high_impact_hard.append(idea_item)
        else:
            if effort in ("low", "small"):
                low_impact_easy.append(idea_item)
            else:
                low_impact_hard.append(idea_item)

    return {
        "title": "Idea Canvas",
        "quadrants": [
            {"id": "quick-wins", "label": "Quick Wins (High Impact, Easy)", "ideas": high_impact_easy},
            {"id": "strategic", "label": "Strategic (High Impact, Hard)", "ideas": high_impact_hard},
            {"id": "fill-in", "label": "Fill-In (Low Impact, Easy)", "ideas": low_impact_easy},
            {"id": "avoid", "label": "Avoid (Low Impact, Hard)", "ideas": low_impact_hard},
        ]
    }


def generate_complexity_concept_map(complexity_data: dict = None) -> dict:
    """Generate grip_concept_map from complexity_profiler output.

    Args:
        complexity_data: Dict with hotspots and file metrics. If None, scans current repo.

    Returns:
        Dict for grip_concept_map: {title, nodes, edges}
    """
    if complexity_data is None:
        try:
            from complexity_profiler import analyze_directory
            import json as _json
            report = analyze_directory(".")
            hotspots = report.hotspots[:15]
            complexity_data = {
                "hotspots": [
                    {"name": h.name, "file": h.file_path, "complexity": h.complexity, "grade": h.grade}
                    for h in hotspots
                ],
                "avg_complexity": report.avg_complexity,
            }
        except (ImportError, Exception):
            complexity_data = {"hotspots": [], "avg_complexity": 0}

    nodes = []
    edges = []
    hotspots = complexity_data.get("hotspots", [])

    # Central node
    avg = complexity_data.get("avg_complexity", 0)
    nodes.append({
        "id": "center",
        "label": f"Project (avg: {avg:.1f})",
        "type": "core",
    })

    # File grouping
    files_seen = {}
    for i, h in enumerate(hotspots[:15]):
        func_id = f"func-{i}"
        file_path = h.get("file", "unknown")
        short_file = file_path.split("/")[-1] if "/" in file_path else file_path
        grade = h.get("grade", "?")
        cc = h.get("complexity", 0)

        node_type = "critical" if cc > 20 else "warning" if cc > 10 else "normal"
        nodes.append({
            "id": func_id,
            "label": f"{h.get('name', '?')} ({grade}, cc={cc})",
            "type": node_type,
        })

        # Group by file
        if short_file not in files_seen:
            file_id = f"file-{len(files_seen)}"
            files_seen[short_file] = file_id
            nodes.append({"id": file_id, "label": short_file, "type": "file"})
            edges.append({"from": "center", "to": file_id, "style": "dashed"})

        edges.append({
            "from": files_seen[short_file],
            "to": func_id,
            "label": f"cc={cc}",
            "style": "solid" if cc > 10 else "dotted",
        })

    return {"title": "Code Complexity Hotspot Map", "nodes": nodes, "edges": edges}


def generate_collaboration_map(collab_data: dict = None) -> dict:
    """Generate grip_concept_map from collaboration heatmap.

    Args:
        collab_data: Dict with collaboration edges. If None, analyzes git.

    Returns:
        Dict for grip_concept_map MCP app.
    """
    if collab_data is None:
        # Get contributors from git
        output = _run_git(["shortlog", "-sn", "--since=30 days ago", "--no-merges"])
        if not output:
            collab_data = {"developers": [], "edges": []}
        else:
            devs = []
            for line in output.strip().split("\n")[:10]:
                parts = line.strip().split("\t", 1)
                if len(parts) == 2:
                    devs.append({"name": parts[1].strip(), "commits": int(parts[0].strip())})
            collab_data = {"developers": devs, "edges": []}

    nodes = []
    edges = []

    for i, dev in enumerate(collab_data.get("developers", [])[:10]):
        commits = dev.get("commits", 0)
        node_type = "primary" if commits > 20 else "secondary" if commits > 5 else "tertiary"
        nodes.append({
            "id": f"dev_{i}",
            "label": dev.get("name", f"Dev {i}"),
            "type": node_type,
        })

    for edge in collab_data.get("edges", []):
        edges.append({
            "from": edge.get("from", ""),
            "to": edge.get("to", ""),
            "label": edge.get("shared_files", "collaborates"),
            "style": "solid" if edge.get("strength", 0) > 5 else "dashed",
        })

    # If no edges but multiple devs, create implicit connections
    if not edges and len(nodes) > 1:
        for i in range(len(nodes) - 1):
            edges.append({
                "from": nodes[i]["id"],
                "to": nodes[i + 1]["id"],
                "label": "team",
                "style": "dotted",
            })

    return {
        "title": "Team Collaboration Map (30 days)",
        "nodes": nodes,
        "edges": edges,
    }


def generate_doc_coverage_gap(coverage_data: dict = None) -> dict:
    """Generate grip_gap_visualiser from doc_coverage output.

    Args:
        coverage_data: Dict with items grouped by kind. If None, scans current repo.

    Returns:
        Dict for grip_gap_visualiser: {title, dimensions, mode, scale}
    """
    if coverage_data is None:
        try:
            from doc_coverage import analyze_coverage
            report = analyze_coverage(".")
            # Group by kind
            by_kind = {}
            for item in report.items:
                by_kind.setdefault(item.kind, {"total": 0, "documented": 0})
                by_kind[item.kind]["total"] += 1
                if item.has_docstring:
                    by_kind[item.kind]["documented"] += 1
            coverage_data = {"by_kind": by_kind, "coverage_pct": report.coverage_pct}
        except (ImportError, Exception):
            coverage_data = {"by_kind": {}, "coverage_pct": 0}

    dimensions = []
    by_kind = coverage_data.get("by_kind", {})
    for kind, counts in sorted(by_kind.items()):
        total = counts.get("total", 0)
        documented = counts.get("documented", 0)
        current = round((documented / total * 10) if total > 0 else 0, 1)
        dimensions.append({
            "id": kind,
            "label": kind.title() + "s",
            "current": current,
            "desired": 8.0,
            "currentText": f"{documented}/{total} documented",
            "desiredText": "80% coverage target",
        })

    if not dimensions:
        dimensions.append({
            "id": "overall",
            "label": "Overall",
            "current": coverage_data.get("coverage_pct", 0) / 10,
            "desired": 8.0,
        })

    return {
        "title": "Documentation Coverage Gap Analysis",
        "dimensions": dimensions,
        "mode": "quantitative",
        "scale": {"min": 0, "max": 10},
    }
