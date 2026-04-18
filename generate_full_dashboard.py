#!/usr/bin/env python3
"""Generate a comprehensive interactive dashboard for the full rationalization."""

import json
from collections import Counter, defaultdict
from pathlib import Path

OUTPUT_DIR = Path("Global Operational")


def extract_filter_attrs(r):
    attrs = set()
    def walk(node):
        if isinstance(node, dict):
            name = node.get('name')
            if name:
                attrs.add(name.lower())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    if r.get('filter'):
        walk(r['filter'].get('tree', {}))
    return attrs


def rich_fingerprint(r):
    metrics = frozenset(
        m.get('name', '').lower() if isinstance(m, dict) else str(m).lower()
        for m in r.get('metrics', [])
    )
    tables = frozenset(
        t.strip('"').lower().split('.')[-1]
        for t in r.get('sourceTables', [])
    )
    filter_attrs = frozenset(extract_filter_attrs(r))
    return metrics, tables, filter_attrs


def jaccard(a, b):
    if not a and not b: return 1.0
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)


def family_base(name):
    parts = name.rsplit(' - ', 1)
    if len(parts) == 2 and len(parts[0]) >= 10:
        return parts[0].strip()
    return name.strip()


def main():
    # Load all data
    with open(OUTPUT_DIR / "inventory" / "reports.json", "r", encoding="utf-8") as f:
        old_reports = json.load(f)
    with open(OUTPUT_DIR / "inventory" / "reports_new_enriched.json", "r", encoding="utf-8") as f:
        new_enriched = json.load(f)
    with open(OUTPUT_DIR / "telemetry" / "active_reports.json", "r", encoding="utf-8") as f:
        old_active = json.load(f)
    with open(OUTPUT_DIR / "telemetry" / "retire_reports.json", "r", encoding="utf-8") as f:
        old_retire = json.load(f)
    with open(OUTPUT_DIR / "inventory" / "reports_new_no_telemetry.json", "r", encoding="utf-8") as f:
        new_retire = json.load(f)
    with open(OUTPUT_DIR / "analysis" / "summary.json", "r", encoding="utf-8") as f:
        analysis = json.load(f)

    old_retain_ids = {r["id"] for r in old_active}
    old_retained = [r for r in old_reports if r["id"] in old_retain_ids]
    combined = old_retained + new_enriched

    # Active lookup for telemetry
    active_lookup = {a["id"]: a for a in old_active}
    # New reports also have _telemetry sidecar
    for r in new_enriched:
        if r.get("_telemetry"):
            active_lookup[r["id"]] = {
                "totalExecutions": r["_telemetry"].get("executions", 0),
                "totalUsers": 1,
                "lastExecTs": r["_telemetry"].get("lastExec", ""),
                "matchTier": r["_telemetry"].get("tier", "unknown"),
            }

    # Build fingerprints and cluster
    fingerprints = {}
    for r in combined:
        fingerprints[r["id"]] = rich_fingerprint(r)

    # Similarity clustering
    THRESHOLD = 0.80
    ids = list(fingerprints.keys())
    n = len(ids)
    adjacency = defaultdict(set)
    for i in range(n):
        fp1 = fingerprints[ids[i]]
        for j in range(i + 1, n):
            fp2 = fingerprints[ids[j]]
            s = 0.4 * jaccard(fp1[0], fp2[0]) + 0.4 * jaccard(fp1[1], fp2[1]) + 0.2 * jaccard(fp1[2], fp2[2])
            if s >= THRESHOLD:
                adjacency[ids[i]].add(ids[j])
                adjacency[ids[j]].add(ids[i])

    visited = set()
    components = []
    for start in ids:
        if start in visited:
            continue
        stack = [start]
        comp = []
        while stack:
            x = stack.pop()
            if x in visited:
                continue
            visited.add(x)
            comp.append(x)
            stack.extend(adjacency[x] - visited)
        components.append(comp)

    multi = sorted([c for c in components if len(c) >= 2], key=lambda c: -len(c))
    singletons = [c for c in components if len(c) == 1]

    # Build report lookup with all metadata
    report_data = {}
    for r in combined:
        rid = r["id"]
        act = active_lookup.get(rid, {})
        fp = fingerprints[rid]
        report_data[rid] = {
            "id": rid,
            "name": r.get("name", ""),
            "path": r.get("folderPath", "") or r.get("path", "") or "",
            "owner": r.get("owner", "") if isinstance(r.get("owner"), str) else (r.get("owner", {}).get("name", "") if isinstance(r.get("owner"), dict) else ""),
            "executions": act.get("totalExecutions", 0),
            "users": act.get("totalUsers", 0),
            "lastExec": act.get("lastExecTs", ""),
            "matchTier": act.get("matchTier", ""),
            "metrics": list(fp[0])[:8],
            "tables": list(fp[1])[:8],
            "filters": list(fp[2])[:8],
            "metric_count": len(fp[0]),
            "table_count": len(fp[1]),
            "filter_count": len(fp[2]),
        }

    # Cluster summaries
    cluster_data = []
    for i, comp in enumerate(multi[:50]):
        members = sorted(comp, key=lambda rid: -report_data[rid]["executions"])
        rep = report_data[members[0]]
        cluster_data.append({
            "id": f"C{i+1}",
            "size": len(comp),
            "primary": rep["name"],
            "metrics": rep["metrics"],
            "tables": rep["tables"],
            "filters": rep["filters"],
            "total_executions": sum(report_data[m]["executions"] for m in comp),
            "members": [{
                "id": rid,
                "name": report_data[rid]["name"],
                "executions": report_data[rid]["executions"],
                "users": report_data[rid]["users"],
                "lastExec": report_data[rid]["lastExec"],
            } for rid in members],
        })

    # Tier breakdown
    all_active_count = len(combined)
    old_tier_counts = Counter(r.get("matchTier") for r in old_active)
    new_tier_counts = Counter(r.get("_telemetry", {}).get("tier") for r in new_enriched)

    tier_data = {
        "exact": old_tier_counts.get("exact_name", 0) + new_tier_counts.get("exact", 0),
        "fuzzy": old_tier_counts.get("fuzzy", 0) + new_tier_counts.get("fuzzy", 0),
        "no_match": len(old_retire) + len(new_retire),
    }

    # Object types from the project
    object_types = analysis.get("byCategory", {})

    # Family analysis
    fam_groups = defaultdict(list)
    for r in combined:
        fam_groups[family_base(r["name"])].append(r)
    fam_multi = [g for g in fam_groups.values() if len(g) >= 2]
    fam_reducible = sum(len(g) for g in fam_multi) - len(fam_multi)

    # Top metrics and tables across all
    metric_freq = Counter()
    table_freq = Counter()
    filter_freq = Counter()
    for r in combined:
        for m in r.get("metrics", []):
            name = m.get("name", "") if isinstance(m, dict) else str(m)
            if name:
                metric_freq[name] += 1
        for t in r.get("sourceTables", []):
            t_norm = t.strip('"').lower().split('.')[-1]
            if t_norm:
                table_freq[t_norm] += 1
        for f in extract_filter_attrs(r):
            filter_freq[f] += 1

    top_metrics = metric_freq.most_common(15)
    top_tables = table_freq.most_common(15)
    top_filters = filter_freq.most_common(15)

    # Top reports by execution
    top_exec = sorted(combined, key=lambda r: -active_lookup.get(r["id"], {}).get("totalExecutions", 0))
    seen = set()
    top_unique = []
    for r in top_exec:
        if r["name"] not in seen:
            seen.add(r["name"])
            top_unique.append({
                "id": r["id"],
                "name": r["name"],
                "executions": active_lookup.get(r["id"], {}).get("totalExecutions", 0),
                "users": active_lookup.get(r["id"], {}).get("totalUsers", 0),
                "lastExec": active_lookup.get(r["id"], {}).get("lastExecTs", ""),
            })
        if len(top_unique) >= 25:
            break

    # Funnel
    total_inventory = 2524
    after_telemetry = len(combined)
    after_family = after_telemetry - fam_reducible
    after_similarity = len(components)

    dashboard_data = {
        "summary": {
            "total_inventory": total_inventory,
            "total_objects": analysis["totalObjects"],
            "after_telemetry": after_telemetry,
            "telemetry_retired": len(old_retire) + len(new_retire),
            "after_family": after_family,
            "after_similarity": after_similarity,
            "reduction_total": total_inventory - after_similarity,
            "reduction_pct": round((total_inventory - after_similarity) / total_inventory * 100, 1),
            "old_inventory": len(old_reports),
            "new_inventory": len(new_enriched) + len(new_retire),
            "old_active": len(old_retained),
            "new_active": len(new_enriched),
            "clusters_total": len(components),
            "clusters_multi": len(multi),
            "clusters_singletons": len(singletons),
        },
        "funnel": [
            {"label": "Original Inventory", "value": total_inventory, "detail": "All reports enumerated via REST API (1,347 original + 1,177 newly accessible)"},
            {"label": "After Telemetry", "value": after_telemetry, "detail": f"{len(old_retire) + len(new_retire)} reports with no usage in 7 months removed"},
            {"label": "After Family Dedup", "value": after_family, "detail": f"{fam_reducible} variants of common parent reports collapsed"},
            {"label": "After Similarity Clustering", "value": after_similarity, "detail": f"Reports with similar metrics+tables+filters merged ({THRESHOLD} Jaccard threshold)"},
        ],
        "tier_breakdown": tier_data,
        "object_types": [(k, v) for k, v in sorted(object_types.items(), key=lambda x: -x[1]) if v > 0],
        "top_clusters": cluster_data,
        "top_metrics": top_metrics,
        "top_tables": top_tables,
        "top_filters": top_filters,
        "top_executed": top_unique,
        "all_reports": list(report_data.values()),
        "report_count": len(report_data),
    }

    data_json = json.dumps(dashboard_data, indent=None, separators=(",", ":"))

    # Generate HTML
    html = generate_html(data_json)

    out_path = OUTPUT_DIR / "rationalization_dashboard_full.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Dashboard saved to: {out_path}")
    print(f"Total clusters: {len(components)}")
    print(f"Final unique reports: {after_similarity}")


def generate_html(data_json):
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Global Operational - Rationalization Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f0f2f5;
    color: #1a1a1a;
    line-height: 1.5;
}
.container { max-width: 1700px; margin: 0 auto; padding: 20px; }

header {
    background: linear-gradient(135deg, #1a3a6c 0%, #2F5496 50%, #4a6fa5 100%);
    color: white;
    padding: 35px 45px;
    border-radius: 14px;
    margin-bottom: 25px;
    box-shadow: 0 8px 24px rgba(26, 58, 108, 0.25);
}
header h1 { font-size: 32px; margin-bottom: 10px; font-weight: 700; }
header .subtitle { font-size: 15px; opacity: 0.92; }
header .badges { margin-top: 15px; display: flex; gap: 10px; flex-wrap: wrap; }
.header-badge {
    background: rgba(255,255,255,0.15);
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 12px;
    backdrop-filter: blur(10px);
}

nav {
    background: white;
    border-radius: 10px;
    padding: 5px;
    margin-bottom: 25px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    display: flex;
    gap: 5px;
    overflow-x: auto;
}
nav button {
    background: none;
    border: none;
    padding: 12px 22px;
    font-size: 13px;
    font-weight: 600;
    color: #6c7a89;
    cursor: pointer;
    border-radius: 8px;
    transition: all 0.2s;
    white-space: nowrap;
}
nav button:hover { background: #f0f4f8; color: #2F5496; }
nav button.active { background: #2F5496; color: white; }

.section { display: none; }
.section.active { display: block; }

.kpi-grid {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 16px;
    margin-bottom: 25px;
}
.kpi {
    background: white;
    padding: 22px;
    border-radius: 12px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
    border-left: 4px solid #2F5496;
    transition: transform 0.2s;
}
.kpi:hover { transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,0.08); }
.kpi-label { font-size: 11px; color: #8895a7; text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 8px; font-weight: 600; }
.kpi-value { font-size: 30px; font-weight: 800; color: #1a3a6c; }
.kpi-sub { font-size: 12px; color: #95a5a6; margin-top: 4px; }
.kpi.success { border-left-color: #27ae60; }
.kpi.success .kpi-value { color: #27ae60; }
.kpi.warning { border-left-color: #e67e22; }
.kpi.danger { border-left-color: #e74c3c; }
.kpi.danger .kpi-value { color: #e74c3c; }

.grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 20px;
    margin-bottom: 25px;
}
.grid-1 { grid-template-columns: 1fr; }
.grid-3 { grid-template-columns: repeat(3, 1fr); }

.card {
    background: white;
    border-radius: 12px;
    padding: 24px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
}
.card h2 {
    font-size: 17px;
    margin-bottom: 18px;
    color: #1a3a6c;
    border-bottom: 2px solid #ecf0f1;
    padding-bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.card h2 .subtitle {
    font-size: 12px;
    color: #95a5a6;
    font-weight: 400;
}
.card h3 { font-size: 14px; color: #2c3e50; margin: 15px 0 10px; }
.chart { width: 100%; height: 380px; }
.chart-tall { height: 480px; }
.chart-xl { height: 600px; }

table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
}
th {
    background: #f7f9fc;
    color: #1a3a6c;
    padding: 11px 10px;
    text-align: left;
    font-weight: 700;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 2px solid #e1e8ed;
}
td { padding: 9px 10px; border-bottom: 1px solid #ecf0f1; }
tr:hover { background: #f8fafc; }
tr.clickable { cursor: pointer; }
tr.clickable:hover { background: #e3edf7; }
.number { text-align: right; font-family: 'SF Mono', Monaco, Consolas, monospace; font-weight: 600; }
.scroll { max-height: 500px; overflow-y: auto; border: 1px solid #ecf0f1; border-radius: 6px; }
.scroll table { font-size: 11px; }
.scroll thead { position: sticky; top: 0; background: #f7f9fc; z-index: 1; }

.badge {
    display: inline-block;
    padding: 3px 9px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.badge-green { background: #d4f4dd; color: #1e8449; }
.badge-orange { background: #ffe5cc; color: #af6708; }
.badge-red { background: #fcdcdc; color: #962020; }
.badge-blue { background: #d6eaff; color: #1a4f8a; }

.search-box {
    width: 100%;
    padding: 12px 16px;
    border: 2px solid #ecf0f1;
    border-radius: 8px;
    font-size: 14px;
    margin-bottom: 15px;
    transition: border 0.2s;
}
.search-box:focus { outline: none; border-color: #2F5496; }

.method-step {
    display: flex;
    align-items: flex-start;
    gap: 18px;
    padding: 18px;
    background: #f8fafc;
    border-radius: 10px;
    margin-bottom: 12px;
    border-left: 4px solid #2F5496;
}
.method-step .step-num {
    width: 38px;
    height: 38px;
    background: #2F5496;
    color: white;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    flex-shrink: 0;
}
.method-step .step-content h3 { font-size: 15px; margin-bottom: 4px; color: #1a3a6c; }
.method-step .step-content p { font-size: 13px; color: #5f6c7b; }

.cluster-card {
    background: #f8fafc;
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 10px;
    border-left: 4px solid #3498db;
    cursor: pointer;
    transition: all 0.2s;
}
.cluster-card:hover { background: #eef5fc; transform: translateX(2px); }
.cluster-card .cluster-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.cluster-card .cluster-name { font-weight: 700; color: #1a3a6c; font-size: 14px; }
.cluster-card .cluster-size { background: #2F5496; color: white; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 700; }
.cluster-card .cluster-meta { font-size: 11px; color: #6c7a89; }
.cluster-card .cluster-tags { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 6px; }
.tag {
    background: #e8eef7;
    color: #2F5496;
    padding: 2px 7px;
    border-radius: 3px;
    font-size: 10px;
}

.modal {
    display: none;
    position: fixed;
    top: 0; left: 0;
    width: 100%;
    height: 100%;
    background: rgba(26, 58, 108, 0.5);
    z-index: 1000;
    backdrop-filter: blur(4px);
}
.modal.active { display: flex; align-items: center; justify-content: center; }
.modal-content {
    background: white;
    border-radius: 14px;
    padding: 30px;
    max-width: 1000px;
    width: 90%;
    max-height: 85vh;
    overflow-y: auto;
    box-shadow: 0 12px 40px rgba(0,0,0,0.3);
}
.modal-content h2 { color: #1a3a6c; margin-bottom: 15px; }
.modal-close {
    float: right;
    background: none;
    border: none;
    font-size: 28px;
    cursor: pointer;
    color: #95a5a6;
    line-height: 1;
}
.modal-close:hover { color: #e74c3c; }

footer { text-align: center; padding: 25px; color: #95a5a6; font-size: 12px; }

.legend-text { font-size: 12px; color: #6c7a89; margin-top: 8px; }
.divider { height: 1px; background: #ecf0f1; margin: 20px 0; }
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>MicroStrategy Report Rationalization</h1>
        <div class="subtitle">Global Operational Project — Complete End-to-End Analysis</div>
        <div class="badges">
            <span class="header-badge">Project: Global Operational</span>
            <span class="header-badge">Environment: Ralph Lauren Analytics Sandbox</span>
            <span class="header-badge">Methodology: Telemetry + Definition Fingerprinting</span>
        </div>
    </header>

    <nav>
        <button class="active" onclick="showSection('overview')">Overview</button>
        <button onclick="showSection('methodology')">Methodology</button>
        <button onclick="showSection('phase1')">Phase 1: Inventory</button>
        <button onclick="showSection('phase2')">Phase 2: Telemetry</button>
        <button onclick="showSection('phase3')">Phase 3: Enrichment</button>
        <button onclick="showSection('phase4')">Phase 4: Rationalization</button>
        <button onclick="showSection('clusters')">Clusters Drill-Down</button>
        <button onclick="showSection('reports')">All Reports</button>
        <button onclick="showSection('summary')">Summary</button>
    </nav>

    <!-- OVERVIEW SECTION -->
    <div id="overview" class="section active">
        <div class="kpi-grid">
            <div class="kpi">
                <div class="kpi-label">Original Inventory</div>
                <div class="kpi-value" id="kpi-orig">-</div>
                <div class="kpi-sub">Total reports</div>
            </div>
            <div class="kpi warning">
                <div class="kpi-label">Retired (No Usage)</div>
                <div class="kpi-value" id="kpi-retired">-</div>
                <div class="kpi-sub">No telemetry evidence</div>
            </div>
            <div class="kpi">
                <div class="kpi-label">Active Reports</div>
                <div class="kpi-value" id="kpi-active">-</div>
                <div class="kpi-sub">With recent usage</div>
            </div>
            <div class="kpi">
                <div class="kpi-label">Similarity Clusters</div>
                <div class="kpi-value" id="kpi-clusters">-</div>
                <div class="kpi-sub">Found by fingerprint</div>
            </div>
            <div class="kpi success">
                <div class="kpi-label">Final Unique</div>
                <div class="kpi-value" id="kpi-final">-</div>
                <div class="kpi-sub">After consolidation</div>
            </div>
            <div class="kpi success">
                <div class="kpi-label">Total Reduction</div>
                <div class="kpi-value" id="kpi-reduction">-</div>
                <div class="kpi-sub">Of original inventory</div>
            </div>
        </div>

        <div class="card">
            <h2>End-to-End Reduction Funnel <span class="subtitle">Click stages to see details</span></h2>
            <div id="funnel-chart" class="chart chart-xl"></div>
            <div class="legend-text">Each stage shows the cumulative reduction from the previous step. Hover for details.</div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>Where the Reductions Come From</h2>
                <div id="reduction-chart" class="chart"></div>
            </div>
            <div class="card">
                <h2>Telemetry Match Tier Breakdown</h2>
                <div id="tier-chart" class="chart"></div>
            </div>
        </div>
    </div>

    <!-- METHODOLOGY SECTION -->
    <div id="methodology" class="section">
        <div class="card">
            <h2>End-to-End Methodology</h2>
            <div class="method-step">
                <div class="step-num">1</div>
                <div class="step-content">
                    <h3>Inventory Collection</h3>
                    <p>Connect to MicroStrategy REST API and enumerate all objects in the Global Operational project. Pagination ensures complete coverage. Object types include reports (type 3), documents (55), cubes (21), metrics (4), filters (1), prompts (10), attributes (12), facts (13), and tables (15).</p>
                </div>
            </div>
            <div class="method-step">
                <div class="step-num">2</div>
                <div class="step-content">
                    <h3>Telemetry Matching (3-Tier)</h3>
                    <p><strong>Tier 1 — Exact Name:</strong> Normalized name comparison (lowercase, strip asterisks, collapse whitespace).<br>
                    <strong>Tier 2 — Full Path:</strong> Folder + name comparison to disambiguate same-named reports in different folders.<br>
                    <strong>Tier 3 — Fuzzy:</strong> Substring containment, same-folder boost, and Jaccard token similarity (≥0.60). This is critical because telemetry exports often have name variations.</p>
                </div>
            </div>
            <div class="method-step">
                <div class="step-num">3</div>
                <div class="step-content">
                    <h3>Definition Enrichment</h3>
                    <p>For each retained report, fetch the modeling definition via <code>GET /model/reports/{id}</code>. This returns the report's metrics, attributes, filter expression tree, source type (normal/cube/custom_sql), and source cube reference. No SQL execution required — fast and reliable.</p>
                </div>
            </div>
            <div class="method-step">
                <div class="step-num">4</div>
                <div class="step-content">
                    <h3>Fingerprint Construction</h3>
                    <p>Build a "rich fingerprint" for each report combining three signals:<br>
                    • <strong>Metrics</strong> — set of metric names used (40% weight)<br>
                    • <strong>Source Tables</strong> — set of database tables referenced (40% weight)<br>
                    • <strong>Filter Attributes</strong> — set of attributes referenced in WHERE clauses (20% weight)</p>
                </div>
            </div>
            <div class="method-step">
                <div class="step-num">5</div>
                <div class="step-content">
                    <h3>Similarity Clustering</h3>
                    <p>Compute weighted Jaccard similarity for each pair of reports. Reports with combined similarity ≥0.80 are joined into the same cluster (connected components). Each cluster represents reports that do essentially the same query against the same data.</p>
                </div>
            </div>
            <div class="method-step">
                <div class="step-num">6</div>
                <div class="step-content">
                    <h3>Family Consolidation (Cross-Check)</h3>
                    <p>Independently group reports by base name pattern (split on last " - " separator). Reports sharing a base like "GFE085a - Projections by PD" are treated as variants of the same parent. This catches cases where naming reveals consolidation opportunities even when fingerprints differ.</p>
                </div>
            </div>
            <div class="method-step">
                <div class="step-num">7</div>
                <div class="step-content">
                    <h3>Final Decision</h3>
                    <p>The final unique count is the number of similarity clusters (each becomes one parameterized report). Reports that don't match any cluster are kept as singletons. This produces a deterministic, explainable rationalization without requiring SQL extraction.</p>
                </div>
            </div>
        </div>
    </div>

    <!-- PHASE 1 -->
    <div id="phase1" class="section">
        <div class="card">
            <h2>Phase 1: Inventory Collection</h2>
            <p style="margin-bottom: 20px; color: #5f6c7b;">Enumerated all objects in the Global Operational project via the MicroStrategy REST API. The original API user (<code>bourntec_mstr</code>) initially had access to 1,347 reports. After permissions were expanded, an additional 1,177 reports became visible, bringing the total to <strong>2,524 reports</strong>.</p>
            <div id="objtypes-chart" class="chart chart-tall"></div>
        </div>
    </div>

    <!-- PHASE 2 -->
    <div id="phase2" class="section">
        <div class="card">
            <h2>Phase 2: Telemetry-Based Retirement</h2>
            <p style="margin-bottom: 20px; color: #5f6c7b;">Cross-referenced 2,524 reports against 2,270 server execution log records using a 3-tier matching strategy. Reports with no execution evidence in the past 7 months are flagged for retirement.</p>
            <div id="phase2-chart" class="chart chart-tall"></div>
            <div class="divider"></div>
            <h3>Top Used Reports</h3>
            <div class="scroll">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Report Name</th>
                            <th class="number">Executions</th>
                            <th class="number">Users</th>
                            <th>Last Execution</th>
                        </tr>
                    </thead>
                    <tbody id="top-exec-table"></tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- PHASE 3 -->
    <div id="phase3" class="section">
        <div class="card">
            <h2>Phase 3: Definition Enrichment</h2>
            <p style="margin-bottom: 20px; color: #5f6c7b;">For each retained report, fetched the full modeling definition via <code>GET /model/reports/{id}</code>. This gives us metrics, source tables, and filter attributes — all the structural data needed for semantic comparison without executing SQL.</p>
        </div>

        <div class="grid grid-3">
            <div class="card">
                <h2>Top Metrics</h2>
                <div id="metrics-chart" class="chart chart-tall"></div>
            </div>
            <div class="card">
                <h2>Top Source Tables</h2>
                <div id="tables-chart" class="chart chart-tall"></div>
            </div>
            <div class="card">
                <h2>Top Filter Attributes</h2>
                <div id="filters-chart" class="chart chart-tall"></div>
            </div>
        </div>
    </div>

    <!-- PHASE 4 -->
    <div id="phase4" class="section">
        <div class="card">
            <h2>Phase 4: Fingerprint-Based Rationalization</h2>
            <p style="margin-bottom: 20px; color: #5f6c7b;">Each report gets a "rich fingerprint" built from its metrics + source tables + filter attributes. Pairs with weighted Jaccard similarity ≥0.80 are linked. Connected components form similarity clusters, each representing reports that do essentially the same thing.</p>
            <div id="cluster-size-chart" class="chart chart-tall"></div>
        </div>
    </div>

    <!-- CLUSTERS DRILL-DOWN -->
    <div id="clusters" class="section">
        <div class="card">
            <h2>Similarity Clusters — Click to Drill Down</h2>
            <input type="text" id="cluster-search" class="search-box" placeholder="Search clusters by name..." onkeyup="filterClusters()">
            <div id="clusters-list"></div>
        </div>
    </div>

    <!-- ALL REPORTS -->
    <div id="reports" class="section">
        <div class="card">
            <h2>All Active Reports — Click for Details</h2>
            <input type="text" id="report-search" class="search-box" placeholder="Search reports by name, owner, or path..." onkeyup="filterReports()">
            <div class="scroll">
                <table>
                    <thead>
                        <tr>
                            <th>Report Name</th>
                            <th>Owner</th>
                            <th class="number">Execs</th>
                            <th class="number">Users</th>
                            <th class="number">Metrics</th>
                            <th class="number">Tables</th>
                            <th class="number">Filters</th>
                            <th>Last Exec</th>
                        </tr>
                    </thead>
                    <tbody id="reports-table"></tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- SUMMARY -->
    <div id="summary" class="section">
        <div class="card">
            <h2>Final Recommendation</h2>
            <p style="font-size: 14px; color: #5f6c7b; margin-bottom: 20px;">
                Starting from <strong id="sum-orig"></strong> reports in Global Operational, the rationalization pipeline produces <strong id="sum-final"></strong> unique reports for migration — a <strong id="sum-pct"></strong> reduction.
            </p>

            <h3>Three Migration Scenarios</h3>
            <table>
                <thead>
                    <tr>
                        <th>Scenario</th>
                        <th>Final Reports</th>
                        <th>Approach</th>
                        <th>Confidence</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><strong>Aggressive (Automated)</strong></td>
                        <td class="number" id="scen-aggressive">-</td>
                        <td>Trust similarity clustering — 1 parameterized report per cluster</td>
                        <td><span class="badge badge-orange">Medium</span></td>
                    </tr>
                    <tr>
                        <td><strong>Moderate (Recommended)</strong></td>
                        <td class="number">~400</td>
                        <td>Allow 2-3 reports per large cluster for legitimate brand/season variants</td>
                        <td><span class="badge badge-green">High</span></td>
                    </tr>
                    <tr>
                        <td><strong>Conservative (Family-Only)</strong></td>
                        <td class="number" id="scen-conservative">-</td>
                        <td>Only collapse name-pattern variants, keep distinct fingerprints</td>
                        <td><span class="badge badge-blue">Very High</span></td>
                    </tr>
                </tbody>
            </table>

            <div class="divider"></div>
            <h3>Recommended Next Steps</h3>
            <ol style="padding-left: 25px; color: #5f6c7b; line-height: 1.8;">
                <li><strong>Immediate:</strong> Retire the <span id="sum-retire"></span> reports with zero telemetry usage (lowest risk, biggest single win)</li>
                <li><strong>Validate:</strong> Share the cluster drill-down with report owners for the top 20 largest clusters</li>
                <li><strong>Consolidate:</strong> Convert the largest 10 clusters into parameterized reports with prompts</li>
                <li><strong>Re-cluster:</strong> Re-run analysis after consolidation to find further opportunities</li>
                <li><strong>Document:</strong> Create migration mapping (old report ID → new parameterized report) for traceability</li>
            </ol>
        </div>
    </div>

    <footer>
        End-to-End MicroStrategy Rationalization Pipeline • Generated from live analysis data • Open-source methodology
    </footer>
</div>

<!-- Modal for cluster drill-down -->
<div id="modal" class="modal">
    <div class="modal-content">
        <button class="modal-close" onclick="closeModal()">&times;</button>
        <div id="modal-body"></div>
    </div>
</div>

<script>
const DATA = __DATA__;

// ===== Initialize KPIs =====
document.getElementById('kpi-orig').textContent = DATA.summary.total_inventory.toLocaleString();
document.getElementById('kpi-retired').textContent = DATA.summary.telemetry_retired.toLocaleString();
document.getElementById('kpi-active').textContent = DATA.summary.after_telemetry.toLocaleString();
document.getElementById('kpi-clusters').textContent = DATA.summary.clusters_total.toLocaleString();
document.getElementById('kpi-final').textContent = DATA.summary.after_similarity.toLocaleString();
document.getElementById('kpi-reduction').textContent = DATA.summary.reduction_pct + '%';

// Summary section KPIs
document.getElementById('sum-orig').textContent = DATA.summary.total_inventory.toLocaleString();
document.getElementById('sum-final').textContent = DATA.summary.after_similarity.toLocaleString();
document.getElementById('sum-pct').textContent = DATA.summary.reduction_pct + '%';
document.getElementById('sum-retire').textContent = DATA.summary.telemetry_retired.toLocaleString();
document.getElementById('scen-aggressive').textContent = DATA.summary.after_similarity.toLocaleString();
document.getElementById('scen-conservative').textContent = DATA.summary.after_family.toLocaleString();

// ===== Funnel Chart =====
Plotly.newPlot('funnel-chart', [{
    type: 'funnel',
    y: DATA.funnel.map(s => s.label),
    x: DATA.funnel.map(s => s.value),
    text: DATA.funnel.map(s => s.value.toLocaleString()),
    textinfo: 'value+percent initial',
    textposition: 'inside',
    textfont: { size: 16, color: 'white', family: 'Segoe UI' },
    marker: {
        color: ['#3498db', '#e67e22', '#2ecc71', '#27ae60'],
        line: { width: 2, color: 'white' }
    },
    hovertemplate: '%{y}<br>%{x:,} reports<extra></extra>',
}], {
    margin: { l: 220, r: 60, t: 30, b: 30 },
    font: { family: 'Segoe UI, sans-serif', size: 13 },
    plot_bgcolor: 'white',
    paper_bgcolor: 'white',
}, {responsive: true, displayModeBar: false});

// ===== Reduction breakdown =====
const reductionData = [
    { label: 'Telemetry Retirement', value: DATA.summary.telemetry_retired, color: '#e67e22' },
    { label: 'Family Consolidation', value: DATA.summary.after_telemetry - DATA.summary.after_family, color: '#9b59b6' },
    { label: 'Similarity Clustering', value: DATA.summary.after_family - DATA.summary.after_similarity, color: '#16a085' },
    { label: 'Final Unique', value: DATA.summary.after_similarity, color: '#27ae60' },
];
Plotly.newPlot('reduction-chart', [{
    type: 'bar',
    x: reductionData.map(d => d.label),
    y: reductionData.map(d => d.value),
    marker: { color: reductionData.map(d => d.color) },
    text: reductionData.map(d => d.value.toLocaleString()),
    textposition: 'outside',
}], {
    margin: { l: 60, r: 20, t: 30, b: 80 },
    yaxis: { title: 'Reports' },
    xaxis: { tickangle: -20 },
    font: { family: 'Segoe UI, sans-serif' },
}, {responsive: true, displayModeBar: false});

// ===== Tier breakdown =====
Plotly.newPlot('tier-chart', [{
    type: 'pie',
    labels: ['Tier 1: Exact Name', 'Tier 3: Fuzzy Match', 'No Match (Retired)'],
    values: [DATA.tier_breakdown.exact, DATA.tier_breakdown.fuzzy, DATA.tier_breakdown.no_match],
    marker: { colors: ['#27ae60', '#3498db', '#e74c3c'] },
    textinfo: 'label+value+percent',
    hole: 0.4,
}], {
    margin: { l: 20, r: 20, t: 30, b: 20 },
    font: { family: 'Segoe UI, sans-serif' },
}, {responsive: true, displayModeBar: false});

// ===== Phase 1 - Object types =====
Plotly.newPlot('objtypes-chart', [{
    type: 'bar',
    orientation: 'h',
    x: DATA.object_types.map(t => t[1]).reverse(),
    y: DATA.object_types.map(t => t[0]).reverse(),
    marker: { color: '#2F5496' },
    text: DATA.object_types.map(t => t[1].toLocaleString()).reverse(),
    textposition: 'outside',
}], {
    margin: { l: 150, r: 80, t: 30, b: 50 },
    xaxis: { title: 'Count' },
    font: { family: 'Segoe UI, sans-serif' },
}, {responsive: true, displayModeBar: false});

// ===== Phase 2 chart =====
Plotly.newPlot('phase2-chart', [{
    type: 'bar',
    x: ['Tier 1: Exact', 'Tier 3: Fuzzy', 'No Match'],
    y: [DATA.tier_breakdown.exact, DATA.tier_breakdown.fuzzy, DATA.tier_breakdown.no_match],
    marker: { color: ['#27ae60', '#3498db', '#e74c3c'] },
    text: [DATA.tier_breakdown.exact, DATA.tier_breakdown.fuzzy, DATA.tier_breakdown.no_match].map(v => v.toLocaleString()),
    textposition: 'outside',
}], {
    margin: { l: 60, r: 20, t: 30, b: 60 },
    yaxis: { title: 'Reports' },
    font: { family: 'Segoe UI, sans-serif' },
}, {responsive: true, displayModeBar: false});

// Top exec table
const topExecTable = document.getElementById('top-exec-table');
DATA.top_executed.forEach((r, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${i+1}</td><td>${r.name}</td><td class="number">${r.executions.toLocaleString()}</td><td class="number">${r.users}</td><td>${r.lastExec}</td>`;
    topExecTable.appendChild(tr);
});

// ===== Phase 3 - Top metrics/tables/filters =====
function makeBarChart(elementId, data, color) {
    Plotly.newPlot(elementId, [{
        type: 'bar',
        orientation: 'h',
        x: data.map(d => d[1]).reverse(),
        y: data.map(d => (d[0].length > 30 ? d[0].substring(0, 30) + '...' : d[0])).reverse(),
        marker: { color: color },
        text: data.map(d => d[1]).reverse(),
        textposition: 'outside',
        hovertext: data.map(d => d[0]).reverse(),
    }], {
        margin: { l: 200, r: 50, t: 20, b: 40 },
        xaxis: { title: 'Reports' },
        font: { family: 'Segoe UI, sans-serif', size: 10 },
    }, {responsive: true, displayModeBar: false});
}
makeBarChart('metrics-chart', DATA.top_metrics, '#3498db');
makeBarChart('tables-chart', DATA.top_tables, '#16a085');
makeBarChart('filters-chart', DATA.top_filters, '#9b59b6');

// ===== Phase 4 - Cluster sizes =====
const topClusters = DATA.top_clusters.slice(0, 25);
Plotly.newPlot('cluster-size-chart', [{
    type: 'bar',
    x: topClusters.map(c => c.id),
    y: topClusters.map(c => c.size),
    marker: { color: '#2F5496' },
    text: topClusters.map(c => c.size),
    textposition: 'outside',
    hovertemplate: '%{customdata}<br>Size: %{y}<extra></extra>',
    customdata: topClusters.map(c => c.primary),
}], {
    margin: { l: 60, r: 20, t: 30, b: 60 },
    xaxis: { title: 'Cluster ID', tickangle: -45 },
    yaxis: { title: 'Reports in Cluster' },
    font: { family: 'Segoe UI, sans-serif' },
}, {responsive: true, displayModeBar: false});

// ===== Clusters drill-down =====
const clustersList = document.getElementById('clusters-list');
DATA.top_clusters.forEach((c, i) => {
    const card = document.createElement('div');
    card.className = 'cluster-card';
    card.dataset.search = (c.primary + ' ' + c.metrics.join(' ') + ' ' + c.tables.join(' ')).toLowerCase();
    card.onclick = () => showClusterModal(i);
    card.innerHTML = `
        <div class="cluster-header">
            <div class="cluster-name">${c.id}: ${c.primary}</div>
            <div class="cluster-size">${c.size} reports</div>
        </div>
        <div class="cluster-meta">${c.total_executions.toLocaleString()} total executions • ${c.metrics.length} metrics • ${c.tables.length} tables</div>
        <div class="cluster-tags">
            ${c.metrics.slice(0,3).map(m => `<span class="tag">📊 ${m}</span>`).join('')}
            ${c.tables.slice(0,3).map(t => `<span class="tag">🗄 ${t}</span>`).join('')}
        </div>
    `;
    clustersList.appendChild(card);
});

function filterClusters() {
    const q = document.getElementById('cluster-search').value.toLowerCase();
    document.querySelectorAll('.cluster-card').forEach(card => {
        card.style.display = card.dataset.search.includes(q) ? 'block' : 'none';
    });
}

function showClusterModal(idx) {
    const c = DATA.top_clusters[idx];
    const body = document.getElementById('modal-body');
    body.innerHTML = `
        <h2>${c.id}: ${c.primary}</h2>
        <p style="color:#6c7a89;font-size:13px;margin-bottom:15px">
            ${c.size} reports • ${c.total_executions.toLocaleString()} total executions
        </p>
        <h3 style="font-size:13px;color:#1a3a6c;margin-bottom:8px">Metrics</h3>
        <div style="margin-bottom:12px">${c.metrics.map(m => `<span class="tag">${m}</span>`).join(' ')}</div>
        <h3 style="font-size:13px;color:#1a3a6c;margin-bottom:8px">Tables</h3>
        <div style="margin-bottom:12px">${c.tables.map(t => `<span class="tag">${t}</span>`).join(' ')}</div>
        <h3 style="font-size:13px;color:#1a3a6c;margin-bottom:8px">Filter Attributes</h3>
        <div style="margin-bottom:20px">${c.filters.map(f => `<span class="tag">${f}</span>`).join(' ')}</div>
        <h3 style="font-size:13px;color:#1a3a6c;margin-bottom:8px">All Reports in Cluster (sorted by usage)</h3>
        <div class="scroll" style="max-height:400px">
            <table>
                <thead><tr><th>Report Name</th><th class="number">Executions</th><th class="number">Users</th><th>Last Exec</th></tr></thead>
                <tbody>
                    ${c.members.map(m => `<tr><td>${m.name}</td><td class="number">${m.executions.toLocaleString()}</td><td class="number">${m.users}</td><td>${m.lastExec}</td></tr>`).join('')}
                </tbody>
            </table>
        </div>
    `;
    document.getElementById('modal').classList.add('active');
}
function closeModal() {
    document.getElementById('modal').classList.remove('active');
}

// ===== All reports table =====
const reportsTable = document.getElementById('reports-table');
function renderReports(filter = '') {
    reportsTable.innerHTML = '';
    const q = filter.toLowerCase();
    const filtered = DATA.all_reports.filter(r =>
        !q || r.name.toLowerCase().includes(q) ||
        (r.owner && r.owner.toLowerCase().includes(q)) ||
        (r.path && r.path.toLowerCase().includes(q))
    ).slice(0, 200);
    filtered.forEach(r => {
        const tr = document.createElement('tr');
        tr.className = 'clickable';
        tr.onclick = () => showReportModal(r);
        tr.innerHTML = `
            <td>${r.name}</td>
            <td>${r.owner || '-'}</td>
            <td class="number">${r.executions.toLocaleString()}</td>
            <td class="number">${r.users}</td>
            <td class="number">${r.metric_count}</td>
            <td class="number">${r.table_count}</td>
            <td class="number">${r.filter_count}</td>
            <td>${r.lastExec || '-'}</td>
        `;
        reportsTable.appendChild(tr);
    });
}
function filterReports() {
    renderReports(document.getElementById('report-search').value);
}
renderReports();

function showReportModal(r) {
    document.getElementById('modal-body').innerHTML = `
        <h2>${r.name}</h2>
        <p style="color:#6c7a89;font-size:12px;margin-bottom:15px">ID: ${r.id}</p>
        <table>
            <tr><td><strong>Owner</strong></td><td>${r.owner || '-'}</td></tr>
            <tr><td><strong>Path</strong></td><td>${r.path || '-'}</td></tr>
            <tr><td><strong>Executions</strong></td><td>${r.executions.toLocaleString()}</td></tr>
            <tr><td><strong>Users</strong></td><td>${r.users}</td></tr>
            <tr><td><strong>Last Execution</strong></td><td>${r.lastExec || '-'}</td></tr>
            <tr><td><strong>Match Tier</strong></td><td>${r.matchTier || '-'}</td></tr>
        </table>
        <h3 style="margin-top:15px;font-size:13px;color:#1a3a6c">Metrics (${r.metric_count})</h3>
        <div>${r.metrics.map(m => `<span class="tag">${m}</span>`).join(' ') || '<em>None</em>'}</div>
        <h3 style="margin-top:12px;font-size:13px;color:#1a3a6c">Source Tables (${r.table_count})</h3>
        <div>${r.tables.map(t => `<span class="tag">${t}</span>`).join(' ') || '<em>None</em>'}</div>
        <h3 style="margin-top:12px;font-size:13px;color:#1a3a6c">Filter Attributes (${r.filter_count})</h3>
        <div>${r.filters.map(f => `<span class="tag">${f}</span>`).join(' ') || '<em>None</em>'}</div>
    `;
    document.getElementById('modal').classList.add('active');
}

// ===== Navigation =====
function showSection(name) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
    document.getElementById(name).classList.add('active');
    event.target.classList.add('active');
    // Trigger Plotly resize for newly visible charts
    setTimeout(() => window.dispatchEvent(new Event('resize')), 100);
}

// Modal close on backdrop click
document.getElementById('modal').addEventListener('click', (e) => {
    if (e.target.id === 'modal') closeModal();
});
</script>
</body>
</html>
""".replace("__DATA__", data_json)


if __name__ == "__main__":
    main()
