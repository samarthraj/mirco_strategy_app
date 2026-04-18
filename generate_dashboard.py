#!/usr/bin/env python3
"""Generate interactive HTML dashboard for rationalization analysis."""

import json
from collections import Counter
from pathlib import Path

OUTPUT_DIR = Path("Global Operational")


def main():
    # Load data
    with open(OUTPUT_DIR / "inventory" / "reports.json", "r", encoding="utf-8") as f:
        all_reports = json.load(f)
    with open(OUTPUT_DIR / "telemetry" / "active_reports.json", "r", encoding="utf-8") as f:
        active = json.load(f)
    with open(OUTPUT_DIR / "telemetry" / "retire_reports.json", "r", encoding="utf-8") as f:
        retire = json.load(f)
    with open(OUTPUT_DIR / "analysis" / "summary.json", "r", encoding="utf-8") as f:
        analysis = json.load(f)
    with open(OUTPUT_DIR / "semantic_analysis" / "semantic_summary.json", "r", encoding="utf-8") as f:
        semantic = json.load(f)
    with open(OUTPUT_DIR / "semantic_analysis" / "group_evaluations.json", "r", encoding="utf-8") as f:
        group_evals = json.load(f)
    with open(OUTPUT_DIR / "semantic_analysis" / "report_annotations.json", "r", encoding="utf-8") as f:
        annotations = json.load(f)

    retain_ids = {r["id"] for r in active}
    has_sql = [r for r in all_reports if r["id"] in retain_ids and r.get("sql")]
    no_sql = [r for r in all_reports if r["id"] in retain_ids and not r.get("sql")]
    sql_errors = [r for r in all_reports if r["id"] in retain_ids and r.get("errors", {}).get("sql")]
    prompted = [r for r in sql_errors if "prompted" in r["errors"]["sql"].lower()]

    true_duplicates = sum(1 for e in group_evals for v in e["verdicts"] if v["is_duplicate"])

    # Top reports by execution
    top_reports = sorted(active, key=lambda x: -(x.get("totalExecutions") or 0))
    seen_names = set()
    top_unique = []
    for r in top_reports:
        if r["name"] not in seen_names:
            seen_names.add(r["name"])
            top_unique.append(r)
        if len(top_unique) >= 20:
            break

    # Match tier breakdown
    tier_counts = Counter(r.get("matchTier") for r in active)

    # Object types
    obj_counts = analysis.get("byCategory", {})
    obj_types_data = [(k, v) for k, v in sorted(obj_counts.items(), key=lambda x: -x[1]) if v > 0]

    # Business categories
    cat_dist = semantic.get("category_distribution", {})
    cat_data = sorted(cat_dist.items(), key=lambda x: -x[1])

    # Cluster sizes
    cluster_sizes = []
    for e in group_evals:
        size = len(e["verdicts"])
        dups = sum(1 for v in e["verdicts"] if v["is_duplicate"])
        cluster_sizes.append({"group": e["group_id"], "size": size, "duplicates": dups})
    cluster_sizes.sort(key=lambda x: -x["size"])

    # Duplicate groups detail
    dup_groups = []
    for e in group_evals:
        dups_in_group = [v for v in e["verdicts"] if v["is_duplicate"]]
        if dups_in_group:
            dup_groups.append({
                "group": e["group_id"],
                "size": len(e["verdicts"]),
                "duplicates": len(dups_in_group),
                "canonical": next((v["report_name"] for v in e["verdicts"] if not v["is_duplicate"]), ""),
                "assessment": e["overall_assessment"][:200],
                "recommendation": e["consolidation_recommendation"][:200],
            })
    dup_groups.sort(key=lambda x: -x["duplicates"])

    # Path classification
    def classify(r):
        path = (r.get("path") or r.get("folderPath") or "").lower()
        if "/profiles/" in path or "/my reports" in path:
            return "Personal"
        elif "/public objects/" in path or "/public/" in path:
            return "Public"
        return "Other"

    path_dist_active = Counter(classify(r) for r in active)
    path_dist_retire = Counter(classify(r) for r in retire)

    # All data for dashboard
    data = {
        "funnel": {
            "stages": ["Original", "After Telemetry", "SQL Extracted", "After Dedup", "Final (+unverified)"],
            "values": [len(all_reports), len(active), len(has_sql), len(has_sql) - true_duplicates,
                       (len(has_sql) - true_duplicates) + len(no_sql)],
        },
        "summary": {
            "total_inventory": len(all_reports),
            "total_objects": analysis["totalObjects"],
            "retained": len(active),
            "retired": len(retire),
            "has_sql": len(has_sql),
            "no_sql_errors": len(no_sql),
            "prompted_fail": len(prompted),
            "true_duplicates": true_duplicates,
            "final_unique": len(has_sql) - true_duplicates,
            "final_total": (len(has_sql) - true_duplicates) + len(no_sql),
            "reduction_pct": round((len(all_reports) - ((len(has_sql) - true_duplicates) + len(no_sql))) / len(all_reports) * 100, 1),
            "semantic_groups": len(group_evals),
        },
        "object_types": obj_types_data,
        "tiers": [("Exact Name", tier_counts.get("exact_name", 0)),
                  ("Full Path", tier_counts.get("full_path", 0)),
                  ("Fuzzy", tier_counts.get("fuzzy", 0)),
                  ("Retired (no match)", len(retire))],
        "categories": cat_data,
        "path_active": [(k, v) for k, v in path_dist_active.items()],
        "path_retire": [(k, v) for k, v in path_dist_retire.items()],
        "clusters": cluster_sizes[:20],
        "dup_groups": dup_groups[:20],
        "top_reports": [{
            "name": r["name"][:60],
            "executions": r.get("totalExecutions", 0),
            "users": r.get("totalUsers", 0),
            "last_exec": r.get("lastExecTs", ""),
        } for r in top_unique],
        "sql_status": [
            ("SQL Generated", len(has_sql)),
            ("Prompted (Human Input Needed)", len(prompted)),
            ("Other Errors", len(no_sql) - len(prompted)),
        ],
    }

    data_json = json.dumps(data, indent=2)

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Global Operational - Rationalization Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f5f7fa;
    color: #2c3e50;
    padding: 20px;
}
.container { max-width: 1600px; margin: 0 auto; }
header {
    background: linear-gradient(135deg, #2F5496 0%, #1e3a6f 100%);
    color: white;
    padding: 30px 40px;
    border-radius: 12px;
    margin-bottom: 25px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}
header h1 { font-size: 28px; margin-bottom: 8px; }
header .subtitle { font-size: 14px; opacity: 0.9; }
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 15px;
    margin-bottom: 25px;
}
.kpi {
    background: white;
    padding: 20px;
    border-radius: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    border-left: 4px solid #2F5496;
}
.kpi-label { font-size: 11px; color: #7f8c8d; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
.kpi-value { font-size: 28px; font-weight: 700; color: #2c3e50; }
.kpi-sub { font-size: 12px; color: #95a5a6; margin-top: 4px; }
.kpi.highlight { border-left-color: #27ae60; background: #e8f8f0; }
.kpi.warning { border-left-color: #e67e22; }
.kpi.danger { border-left-color: #e74c3c; }

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
    border-radius: 10px;
    padding: 20px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}
.card h2 {
    font-size: 16px;
    margin-bottom: 15px;
    color: #2c3e50;
    border-bottom: 2px solid #ecf0f1;
    padding-bottom: 10px;
}
.chart { width: 100%; height: 380px; }
.chart-tall { height: 500px; }

table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    margin-top: 10px;
}
th {
    background: #2F5496;
    color: white;
    padding: 10px 8px;
    text-align: left;
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
}
td { padding: 8px; border-bottom: 1px solid #ecf0f1; }
tr:hover { background: #f8f9fa; }
.badge {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
}
.badge-green { background: #d4edda; color: #155724; }
.badge-orange { background: #fff3cd; color: #856404; }
.badge-red { background: #f8d7da; color: #721c24; }
.badge-blue { background: #cce5ff; color: #004085; }
.number { text-align: right; font-family: 'SF Mono', Monaco, monospace; }
.scroll { max-height: 450px; overflow-y: auto; }
footer { text-align: center; padding: 20px; color: #95a5a6; font-size: 12px; }

.tabs { display: flex; gap: 5px; margin-bottom: 15px; border-bottom: 2px solid #ecf0f1; }
.tab {
    padding: 10px 20px;
    cursor: pointer;
    border: none;
    background: none;
    font-size: 13px;
    color: #7f8c8d;
    border-bottom: 3px solid transparent;
    margin-bottom: -2px;
    transition: all 0.2s;
}
.tab.active { color: #2F5496; border-bottom-color: #2F5496; font-weight: 600; }
.tab-content { display: none; }
.tab-content.active { display: block; }
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>MicroStrategy Report Rationalization Dashboard</h1>
        <div class="subtitle">Global Operational Project • Ralph Lauren Analytics • Generated from live analysis data</div>
    </header>

    <div class="kpi-grid">
        <div class="kpi">
            <div class="kpi-label">Original Inventory</div>
            <div class="kpi-value" id="kpi-original">-</div>
            <div class="kpi-sub">Total reports in project</div>
        </div>
        <div class="kpi warning">
            <div class="kpi-label">Retired (No Usage)</div>
            <div class="kpi-value" id="kpi-retired">-</div>
            <div class="kpi-sub">No telemetry evidence</div>
        </div>
        <div class="kpi">
            <div class="kpi-label">SQL Analyzed</div>
            <div class="kpi-value" id="kpi-sql">-</div>
            <div class="kpi-sub">Reports with SQL extracted</div>
        </div>
        <div class="kpi danger">
            <div class="kpi-label">True Duplicates</div>
            <div class="kpi-value" id="kpi-dups">-</div>
            <div class="kpi-sub">Verified by GPT-5.4</div>
        </div>
        <div class="kpi highlight">
            <div class="kpi-label">Final to Migrate</div>
            <div class="kpi-value" id="kpi-final">-</div>
            <div class="kpi-sub">Unique + unverified</div>
        </div>
        <div class="kpi highlight">
            <div class="kpi-label">Total Reduction</div>
            <div class="kpi-value" id="kpi-reduction">-</div>
            <div class="kpi-sub">From original inventory</div>
        </div>
    </div>

    <div class="grid grid-1">
        <div class="card">
            <h2>Reduction Funnel</h2>
            <div id="funnel-chart" class="chart chart-tall"></div>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <h2>Telemetry Matching Tiers</h2>
            <div id="tiers-chart" class="chart"></div>
        </div>
        <div class="card">
            <h2>SQL Extraction Status</h2>
            <div id="sql-chart" class="chart"></div>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <h2>Business Categories (Retained Reports)</h2>
            <div id="category-chart" class="chart"></div>
        </div>
        <div class="card">
            <h2>Object Types in Project</h2>
            <div id="objtypes-chart" class="chart"></div>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <h2>Public vs Personal (Retained)</h2>
            <div id="path-active-chart" class="chart"></div>
        </div>
        <div class="card">
            <h2>Public vs Personal (Retired)</h2>
            <div id="path-retire-chart" class="chart"></div>
        </div>
    </div>

    <div class="grid grid-1">
        <div class="card">
            <h2>Top 20 Largest Semantic Clusters</h2>
            <div id="clusters-chart" class="chart chart-tall"></div>
        </div>
    </div>

    <div class="grid grid-1">
        <div class="card">
            <h2>Detailed Tables</h2>
            <div class="tabs">
                <button class="tab active" onclick="showTab('tab-dups')">Duplicate Groups</button>
                <button class="tab" onclick="showTab('tab-top')">Top Executed Reports</button>
            </div>
            <div id="tab-dups" class="tab-content active">
                <div class="scroll">
                <table>
                    <thead>
                        <tr>
                            <th>Group</th>
                            <th>Size</th>
                            <th>Duplicates</th>
                            <th>Canonical Report</th>
                            <th>Assessment</th>
                        </tr>
                    </thead>
                    <tbody id="dups-table"></tbody>
                </table>
                </div>
            </div>
            <div id="tab-top" class="tab-content">
                <div class="scroll">
                <table>
                    <thead>
                        <tr>
                            <th>Report Name</th>
                            <th class="number">Executions</th>
                            <th class="number">Users</th>
                            <th>Last Execution</th>
                        </tr>
                    </thead>
                    <tbody id="top-table"></tbody>
                </table>
                </div>
            </div>
        </div>
    </div>

    <footer>
        Generated from live analysis data • Click any chart to interact • Hover for details
    </footer>
</div>

<script>
const DATA = __DATA__;

// KPIs
document.getElementById('kpi-original').textContent = DATA.summary.total_inventory.toLocaleString();
document.getElementById('kpi-retired').textContent = DATA.summary.retired.toLocaleString();
document.getElementById('kpi-sql').textContent = DATA.summary.has_sql.toLocaleString();
document.getElementById('kpi-dups').textContent = DATA.summary.true_duplicates.toLocaleString();
document.getElementById('kpi-final').textContent = DATA.summary.final_total.toLocaleString();
document.getElementById('kpi-reduction').textContent = DATA.summary.reduction_pct + '%';

// Funnel chart
const funnelColors = ['#3498db', '#e67e22', '#f39c12', '#2ecc71', '#27ae60'];
Plotly.newPlot('funnel-chart', [{
    type: 'funnel',
    y: DATA.funnel.stages,
    x: DATA.funnel.values,
    textinfo: 'value+percent initial',
    marker: { color: funnelColors },
    textfont: { size: 14, color: 'white' },
}], {
    margin: { l: 200, r: 40, t: 20, b: 20 },
    font: { family: 'Segoe UI, sans-serif' },
}, {responsive: true, displayModeBar: false});

// Telemetry Tiers pie
Plotly.newPlot('tiers-chart', [{
    type: 'pie',
    labels: DATA.tiers.map(t => t[0]),
    values: DATA.tiers.map(t => t[1]),
    marker: { colors: ['#27ae60', '#2ecc71', '#3498db', '#e74c3c'] },
    textinfo: 'label+value',
    hole: 0.4,
}], {
    margin: { l: 20, r: 20, t: 20, b: 20 },
    showlegend: true,
    legend: { orientation: 'v', x: 1.1, y: 0.5 },
}, {responsive: true, displayModeBar: false});

// SQL extraction status
Plotly.newPlot('sql-chart', [{
    type: 'pie',
    labels: DATA.sql_status.map(s => s[0]),
    values: DATA.sql_status.map(s => s[1]),
    marker: { colors: ['#27ae60', '#e67e22', '#e74c3c'] },
    textinfo: 'label+value+percent',
    hole: 0.4,
}], {
    margin: { l: 20, r: 20, t: 20, b: 20 },
    showlegend: true,
    legend: { orientation: 'v', x: 1.1, y: 0.5 },
}, {responsive: true, displayModeBar: false});

// Business categories horizontal bar
Plotly.newPlot('category-chart', [{
    type: 'bar',
    orientation: 'h',
    x: DATA.categories.map(c => c[1]).reverse(),
    y: DATA.categories.map(c => c[0]).reverse(),
    marker: { color: '#2F5496' },
    text: DATA.categories.map(c => c[1]).reverse(),
    textposition: 'outside',
}], {
    margin: { l: 180, r: 50, t: 20, b: 40 },
    xaxis: { title: 'Reports' },
    font: { family: 'Segoe UI, sans-serif', size: 11 },
}, {responsive: true, displayModeBar: false});

// Object types bar
Plotly.newPlot('objtypes-chart', [{
    type: 'bar',
    x: DATA.object_types.map(t => t[0]),
    y: DATA.object_types.map(t => t[1]),
    marker: { color: '#3498db' },
    text: DATA.object_types.map(t => t[1]),
    textposition: 'outside',
}], {
    margin: { l: 50, r: 20, t: 20, b: 100 },
    yaxis: { title: 'Count' },
    xaxis: { tickangle: -45 },
    font: { family: 'Segoe UI, sans-serif', size: 11 },
}, {responsive: true, displayModeBar: false});

// Path distribution (active)
Plotly.newPlot('path-active-chart', [{
    type: 'pie',
    labels: DATA.path_active.map(p => p[0]),
    values: DATA.path_active.map(p => p[1]),
    marker: { colors: ['#27ae60', '#3498db', '#95a5a6'] },
    textinfo: 'label+value+percent',
}], {
    margin: { l: 20, r: 20, t: 20, b: 20 },
}, {responsive: true, displayModeBar: false});

// Path distribution (retire)
Plotly.newPlot('path-retire-chart', [{
    type: 'pie',
    labels: DATA.path_retire.map(p => p[0]),
    values: DATA.path_retire.map(p => p[1]),
    marker: { colors: ['#e67e22', '#e74c3c', '#95a5a6'] },
    textinfo: 'label+value+percent',
}], {
    margin: { l: 20, r: 20, t: 20, b: 20 },
}, {responsive: true, displayModeBar: false});

// Clusters chart
Plotly.newPlot('clusters-chart', [
    {
        type: 'bar',
        name: 'Size',
        x: DATA.clusters.map(c => c.group),
        y: DATA.clusters.map(c => c.size),
        marker: { color: '#3498db' },
    },
    {
        type: 'bar',
        name: 'Duplicates',
        x: DATA.clusters.map(c => c.group),
        y: DATA.clusters.map(c => c.duplicates),
        marker: { color: '#e74c3c' },
    },
], {
    barmode: 'overlay',
    margin: { l: 50, r: 20, t: 40, b: 100 },
    xaxis: { tickangle: -45, title: 'Semantic Group' },
    yaxis: { title: 'Reports' },
    legend: { orientation: 'h', x: 0.5, xanchor: 'center', y: 1.05 },
    font: { family: 'Segoe UI, sans-serif', size: 11 },
}, {responsive: true, displayModeBar: false});

// Populate tables
const dupsTable = document.getElementById('dups-table');
DATA.dup_groups.forEach(g => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td><strong>${g.group}</strong></td>
        <td class="number">${g.size}</td>
        <td><span class="badge badge-red">${g.duplicates}</span></td>
        <td>${g.canonical || '-'}</td>
        <td style="font-size:11px;color:#7f8c8d">${g.assessment}...</td>
    `;
    dupsTable.appendChild(tr);
});

const topTable = document.getElementById('top-table');
DATA.top_reports.forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td>${r.name}</td>
        <td class="number">${r.executions.toLocaleString()}</td>
        <td class="number">${r.users}</td>
        <td>${r.last_exec}</td>
    `;
    topTable.appendChild(tr);
});

// Tabs
function showTab(id) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    event.target.classList.add('active');
}
</script>
</body>
</html>
"""

    html = html.replace("__DATA__", data_json)

    out_path = OUTPUT_DIR / "rationalization_dashboard.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Dashboard saved to: {out_path}")
    print(f"Open in browser to view.")


if __name__ == "__main__":
    main()
