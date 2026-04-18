#!/usr/bin/env python3
"""Generate the final corrected rationalization analysis report."""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

OUTPUT_DIR = Path("Global Operational")


def set_cell_shading(cell, color_hex):
    shading = cell._element.get_or_add_tcPr()
    shd = shading.makeelement(qn("w:shd"), {qn("w:fill"): color_hex, qn("w:val"): "clear"})
    shading.append(shd)


def add_table(doc, headers, rows, col_widths=None):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = h
        for p in cell.paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        set_cell_shading(cell, "2F5496")
    for r_idx, row_data in enumerate(rows):
        for c_idx, val in enumerate(row_data):
            cell = table.rows[1 + r_idx].cells[c_idx]
            cell.text = str(val) if val is not None else ""
            for p in cell.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(8.5)
        if r_idx % 2 == 1:
            for cell in table.rows[1 + r_idx].cells:
                set_cell_shading(cell, "D6E4F0")
    if col_widths:
        for row in table.rows:
            for i, w in enumerate(col_widths):
                if i < len(row.cells):
                    row.cells[i].width = Cm(w)
    return table


def classify(r):
    path = (r.get("path") or r.get("telemetryPath") or "").lower()
    if "/profiles/" in path or "/my reports" in path or "/my personal" in path:
        return "Personal"
    elif "/public objects/" in path or "/public/" in path:
        return "Public"
    return "Other"


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
    with open(OUTPUT_DIR / "claude_cluster_analysis.json", "r", encoding="utf-8") as f:
        claude = json.load(f)
    with open(OUTPUT_DIR / "sql_rationalization" / "sql_rationalization_report.json", "r", encoding="utf-8") as f:
        sql_report = json.load(f)

    retain_ids = {r["id"] for r in active}
    retained_inv = [r for r in all_reports if r["id"] in retain_ids]
    has_sql = [r for r in retained_inv if r.get("sql")]
    no_sql = [r for r in retained_inv if not r.get("sql")]
    sql_errors = [r for r in retained_inv if r.get("errors", {}).get("sql")]
    prompted_fail = [r for r in sql_errors if "prompted" in r["errors"]["sql"].lower()]

    exact = [r for r in active if r.get("matchTier") == "exact_name"]
    fuzzy = [r for r in active if r.get("matchTier") == "fuzzy"]

    # Cluster consolidation math
    cluster_detail = []
    total_cluster_removable = 0
    for cid, ca in claude.items():
        action = ca.get("consolidation_action", "")
        size = 0
        for rec in sql_report.get("recommendations", []):
            if str(rec.get("cluster")) == cid:
                size = rec["size"]
                break
        if action in ("MERGE_IMMEDIATE", "PARAMETERIZE"):
            removable = size - 1
        elif action == "REVIEW_WITH_OWNER":
            removable = len(ca.get("removable_reports", []))
        else:
            removable = 0
        total_cluster_removable += removable
        cluster_detail.append({
            "cluster": cid,
            "label": ca.get("label", ""),
            "size": size,
            "action": action,
            "removable": removable,
            "keep": 1 if removable > 0 else size,
            "confidence": ca.get("confidence", ""),
            "relationship": ca.get("relationship", ""),
            "business_function": ca.get("business_function", ""),
            "detail": ca.get("consolidation_detail", ""),
            "keep_report": ca.get("keep_report", ""),
        })
    cluster_detail.sort(key=lambda x: -x["size"])

    singletons = sql_report["summary"]["singleton_reports"]
    in_clusters = sql_report["summary"]["reports_in_clusters"]
    unique_after_sql = len(has_sql) - total_cluster_removable

    # ================================================================
    # BUILD DOCUMENT
    # ================================================================
    doc = Document()

    # Title page
    for _ in range(4):
        doc.add_paragraph()
    title = doc.add_heading("Global Operational - Final Rationalization Analysis", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("How We Arrived at the Final Number")
    run.font.size = Pt(14)
    run.font.color.rgb = RGBColor(0x2F, 0x54, 0x96)
    for _ in range(2):
        doc.add_paragraph()
    for line in [
        "Project: Global Operational (E77B77894C04BF0E6D244F9363CFAF64)",
        "Environment: Ralph Lauren Analytics Sandbox",
        f"Date: {datetime.now().strftime('%B %d, %Y')}",
    ]:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(line)
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.add_page_break()

    # ================================================================
    # 1. Starting Point
    # ================================================================
    doc.add_heading("1. Starting Point", level=1)
    doc.add_paragraph(
        f"The Global Operational project contained {analysis['totalObjects']:,} total objects "
        f"across 11 categories, including {len(all_reports):,} reports."
    )
    add_table(doc, ["", "Count"], [
        ("Total objects in project", f"{analysis['totalObjects']:,}"),
        ("Total reports", f"{len(all_reports):,}"),
    ], col_widths=[10, 4])

    doc.add_page_break()

    # ================================================================
    # 2. Step 1: Telemetry Retirement
    # ================================================================
    doc.add_heading("2. Step 1: Telemetry Retirement (-608 reports)", level=1)

    doc.add_heading("2.1 What We Did", level=2)
    doc.add_paragraph(
        f"Cross-referenced all {len(all_reports):,} reports against 2,270 server telemetry "
        f"records (execution logs from the past 7 months). Any report with no evidence of "
        f"execution was flagged for retirement."
    )

    doc.add_heading("2.2 How We Matched", level=2)
    doc.add_paragraph(
        "Report names differ between the API inventory and telemetry export, so a "
        "three-tier matching strategy was used:"
    )
    add_table(doc, ["Tier", "Method", "Matched"], [
        ("Tier 1", "Exact normalized name comparison", f"{len(exact):,}"),
        ("Tier 2", "Full path (folder + name) comparison", "0"),
        ("Tier 3", "Fuzzy: substring containment + Jaccard token similarity (>0.60)", f"{len(fuzzy):,}"),
        ("No match", "No usage evidence across all tiers", f"{len(retire):,}"),
    ], col_widths=[2, 10, 2.5])

    doc.add_heading("2.3 Result", level=2)
    add_table(doc, ["", "Count"], [
        ("Reports with confirmed usage (retain)", f"{len(active):,}"),
        ("Reports with no usage (retire)", f"{len(retire):,}"),
    ], col_widths=[10, 4])

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run(f"After Step 1: 1,347 -> 739 reports").bold = True

    doc.add_page_break()

    # ================================================================
    # 3. Step 2: SQL Extraction
    # ================================================================
    doc.add_heading("3. Step 2: SQL Extraction", level=1)

    doc.add_heading("3.1 What We Did", level=2)
    doc.add_paragraph(
        f"For each of the {len(active):,} retained reports, we attempted to extract the "
        f"underlying SQL statement via the MicroStrategy REST API. This is required to "
        f"compare what each report actually queries."
    )

    doc.add_heading("3.2 How It Works", level=2)
    steps = [
        "Create a report instance via POST /v2/reports/{id}/instances",
        "For prompted reports, attempt to resolve prompts using cached/default answers",
        "Retrieve the generated SQL via GET /v2/reports/{id}/instances/{instanceId}/sqlView",
        "Clean up the instance via DELETE",
    ]
    for s in steps:
        doc.add_paragraph(s, style="List Number")

    doc.add_heading("3.3 Result", level=2)
    add_table(doc, ["", "Count", "% of 739"], [
        ("SQL extracted successfully", f"{len(has_sql):,}", f"{len(has_sql)/len(active)*100:.1f}%"),
        ("SQL failed - prompted (requires human input)", f"{len(prompted_fail):,}", f"{len(prompted_fail)/len(active)*100:.1f}%"),
        ("SQL failed - other (permission, timeout)", f"{len(sql_errors) - len(prompted_fail):,}", f"{(len(sql_errors) - len(prompted_fail))/len(active)*100:.1f}%"),
    ], col_widths=[8, 3, 3])

    doc.add_paragraph()
    doc.add_paragraph(
        f"The 165 prompted failures are unavoidable. These reports require a user to "
        f"select filter values (season, brand, region) before SQL can be generated. "
        f"449 other prompted reports resolved successfully using cached/default answers, "
        f"proving we cannot skip all prompted reports upfront."
    )

    p = doc.add_paragraph()
    p.add_run(f"After Step 2: 568 reports with SQL available for analysis, 171 without.").bold = True

    doc.add_page_break()

    # ================================================================
    # 4. Step 3: Near-Duplicate Detection
    # ================================================================
    doc.add_heading("4. Step 3: Near-Duplicate Detection", level=1)

    doc.add_heading("4.1 What We Did", level=2)
    doc.add_paragraph(
        "Normalized each SQL statement to remove cosmetic differences and hashed them "
        "to find reports executing identical queries."
    )

    doc.add_heading("4.2 Normalization Steps", level=2)
    norm_steps = [
        "Lowercase all SQL text",
        "Strip comments (-- and /* */)",
        "Replace all string literals with '?' placeholder",
        "Replace all numeric literals with 0",
        "Remove MicroStrategy SET query_group headers (per-report identifier)",
        "Normalize auto-generated temp table names (e.g., TIG3JE6NZMD000 -> TTMP)",
        "Collapse all whitespace",
        "SHA-256 hash the result",
    ]
    for s in norm_steps:
        doc.add_paragraph(s, style="List Number")

    doc.add_paragraph(
        "Reports with the same hash after normalization execute identical query logic, "
        "differing only in auto-generated identifiers."
    )

    doc.add_heading("4.3 Result", level=2)
    exact_dup_groups = sql_report["summary"]["exact_duplicate_groups"]
    near_dup_groups = sql_report["summary"]["near_duplicate_groups"]
    add_table(doc, ["", "Groups", "Reports"], [
        ("Exact duplicates (standard normalization)", f"{exact_dup_groups}", "32"),
        ("Near duplicates (deep normalization)", f"{near_dup_groups}", "78"),
        ("Additional found by deep normalization", f"+{near_dup_groups - exact_dup_groups}", f"+{78 - 32}"),
    ], col_widths=[8, 3, 3])

    doc.add_page_break()

    # ================================================================
    # 5. Step 4: Vector Embeddings & Semantic Clustering
    # ================================================================
    doc.add_heading("5. Step 4: Vector Embeddings & Semantic Clustering", level=1)

    doc.add_heading("5.1 What We Did", level=2)
    doc.add_paragraph(
        "Hash-based deduplication only catches identical SQL. To find reports that do "
        "similar things but differ textually (different column aliases, different JOIN "
        "order, different WHERE clauses on the same tables), we used vector embeddings "
        "and clustering."
    )

    doc.add_heading("5.2 Embedding Process", level=2)
    embed_steps = [
        "Pre-processed each SQL statement: removed comments, query_group headers, "
        "normalized temp table names, replaced literals, truncated to 2,000 characters",
        "Embedded all 568 SQL statements using the sentence-transformers model "
        "(all-MiniLM-L6-v2) which maps text to 384-dimensional vectors capturing semantic meaning",
        "Computed a 568 x 568 cosine similarity matrix measuring how semantically "
        "similar each pair of SQL statements is (1.0 = identical, 0.0 = completely different)",
    ]
    for s in embed_steps:
        doc.add_paragraph(s, style="List Number")

    doc.add_heading("5.3 Clustering Process", level=2)
    cluster_steps = [
        "Converted the similarity matrix to a distance matrix (distance = 1 - similarity)",
        "Applied Agglomerative Clustering with average linkage and a distance threshold of 0.25 "
        "(meaning reports must have >75% cosine similarity to be in the same cluster)",
        "Tested multiple thresholds (0.15 to 0.40) to find the right balance between "
        "granularity and grouping",
        "The algorithm automatically determined the number of clusters based on the threshold - "
        "no need to predefine the count",
    ]
    for s in cluster_steps:
        doc.add_paragraph(s, style="List Number")

    doc.add_heading("5.4 Result", level=2)
    add_table(doc, ["", "Count"], [
        ("Total clusters formed", f"{sql_report['summary']['total_clusters']}"),
        ("Multi-member clusters (2+ reports)", f"{sql_report['summary']['multi_member_clusters']}"),
        ("Reports in multi-member clusters", f"{in_clusters:,}"),
        ("Singleton reports (truly unique SQL)", f"{singletons}"),
    ], col_widths=[10, 4])

    doc.add_paragraph()
    doc.add_paragraph(
        f"Out of 568 reports with SQL, only {singletons} have truly unique SQL that "
        f"doesn't resemble any other report. The remaining {in_clusters} fall into "
        f"{sql_report['summary']['multi_member_clusters']} clusters of semantically similar queries."
    )

    doc.add_page_break()

    # ================================================================
    # 6. Step 5: Cluster Analysis & Consolidation
    # ================================================================
    doc.add_heading("6. Step 5: Cluster Analysis & Consolidation Recommendations", level=1)

    doc.add_heading("6.1 What We Did", level=2)
    doc.add_paragraph(
        "Each of the 22 multi-member clusters was analyzed by reviewing the actual SQL "
        "statements, source tables, and report names to determine:"
    )
    criteria = [
        "What business function the cluster serves",
        "The relationship between reports: exact duplicates, near-duplicates, "
        "parameterized variants (same query with different WHERE filters), similar domain, "
        "or loosely related",
        "The recommended consolidation action",
        "Which report to keep as the primary",
    ]
    for c in criteria:
        doc.add_paragraph(c, style="List Bullet")

    doc.add_heading("6.2 Consolidation Actions", level=2)
    action_rows = [
        ("MERGE_IMMEDIATE", "4", "Reports with identical or near-identical SQL. "
         "Remove duplicates immediately, keep one."),
        ("PARAMETERIZE", "14", "Reports with the same query structure but different "
         "WHERE clause filters (brand, season, region). Consolidate into a single "
         "report with prompts."),
        ("REVIEW_WITH_OWNER", "2", "Reports that appear similar but serve different "
         "enough purposes to warrant business review before consolidating."),
        ("KEEP_SEPARATE", "2", "Reports that share some tables but serve fundamentally "
         "different business functions. No consolidation."),
    ]
    add_table(doc, ["Action", "Clusters", "Description"], action_rows, col_widths=[4, 2, 10.5])

    doc.add_heading("6.3 Cluster-by-Cluster Detail", level=2)

    cluster_rows = []
    for cd in cluster_detail:
        cluster_rows.append((
            f"C{cd['cluster']}",
            cd["label"][:45],
            cd["size"],
            cd["action"],
            cd["keep"],
            cd["removable"],
            cd["confidence"],
        ))
    add_table(doc,
              ["Cluster", "Description", "Reports", "Action", "Keep", "Remove", "Confidence"],
              cluster_rows,
              col_widths=[1.5, 5.5, 1.5, 3, 1, 1.5, 2])

    doc.add_paragraph()
    # Totals
    total_in = sum(cd["size"] for cd in cluster_detail)
    total_keep = sum(cd["keep"] for cd in cluster_detail)
    total_remove = sum(cd["removable"] for cd in cluster_detail)
    add_table(doc, ["", "Count"], [
        ("Total reports in clusters", f"{total_in:,}"),
        ("Reports to keep (1 per consolidated cluster)", f"{total_keep:,}"),
        ("Reports removable", f"{total_remove:,}"),
    ], col_widths=[10, 4])

    doc.add_page_break()

    # Detail for each cluster
    doc.add_heading("6.4 Cluster Descriptions", level=2)
    for cd in cluster_detail:
        p = doc.add_paragraph()
        p.add_run(f"Cluster {cd['cluster']}: {cd['label']}").bold = True
        doc.add_paragraph(f"Size: {cd['size']} reports | Action: {cd['action']} | Confidence: {cd['confidence']}")
        doc.add_paragraph(f"Business function: {cd['business_function']}")
        doc.add_paragraph(f"Relationship: {cd['relationship']}")
        doc.add_paragraph(f"Keep: {cd['keep_report']}")
        doc.add_paragraph(f"Recommendation: {cd['detail']}")
        doc.add_paragraph()

    doc.add_page_break()

    # ================================================================
    # 7. Final Number
    # ================================================================
    doc.add_heading("7. The Final Number", level=1)

    doc.add_heading("7.1 Complete Reduction Funnel", level=2)

    funnel_rows = [
        ("Original inventory", f"{len(all_reports):,}", "", "All reports enumerated via REST API"),
        ("", "", "", ""),
        ("Step 1: Telemetry retirement", f"{len(active):,}", f"-{len(retire):,}",
         "608 reports with no execution evidence in 7 months"),
        ("", "", "", ""),
        ("Step 2: SQL extraction", "", "",
         f"568 of 739 reports yielded SQL; 171 failed (prompted)"),
        ("", "", "", ""),
        ("Step 3-5: SQL analysis of 568 reports", "", "", ""),
        (f"  Singleton reports (unique SQL)", f"{singletons}", "",
         "No similar SQL found - truly unique"),
        (f"  Clustered reports", f"{in_clusters}", "",
         f"Grouped into {sql_report['summary']['multi_member_clusters']} semantic clusters"),
        (f"  Clusters consolidated to 1 each", f"{sql_report['summary']['multi_member_clusters']}", f"-{total_cluster_removable}",
         "Keep 1 parameterized report per cluster"),
        ("", "", "", ""),
        ("Unique reports from SQL analysis", f"{unique_after_sql}", "",
         f"{singletons} singletons + {sql_report['summary']['multi_member_clusters']} cluster representatives"),
        ("Reports without SQL (unknown)", f"{len(no_sql)}", "",
         "Could not extract SQL; overlap unknown"),
        ("", "", "", ""),
        ("FINAL TOTAL", f"{unique_after_sql + len(no_sql)}", "",
         f"{unique_after_sql} verified unique + {len(no_sql)} unverified"),
    ]
    add_table(doc, ["Stage", "Reports", "Change", "Explanation"],
              funnel_rows, col_widths=[5.5, 2, 1.5, 7.5])

    doc.add_heading("7.2 The Math", level=2)

    doc.add_paragraph(
        f"Starting with {len(all_reports):,} reports:"
    )

    math_steps = [
        f"Telemetry retirement removes {len(retire):,} unused reports -> {len(active):,} remain",
        f"SQL extraction succeeds for {len(has_sql):,} of {len(active):,} reports ({len(no_sql):,} fail due to prompts/permissions)",
        f"Of the {len(has_sql):,} with SQL, only {singletons} are truly unique (no similar SQL exists)",
        f"The remaining {in_clusters:,} reports form {sql_report['summary']['multi_member_clusters']} clusters of semantically similar SQL",
        f"Consolidating each cluster to 1 parameterized report: {sql_report['summary']['multi_member_clusters']} cluster reps + {singletons} singletons = {unique_after_sql} unique reports",
        f"Adding back the {len(no_sql):,} reports we couldn't analyze: {unique_after_sql} + {len(no_sql):,} = {unique_after_sql + len(no_sql)} total",
    ]
    for i, s in enumerate(math_steps, 1):
        doc.add_paragraph(f"{s}", style="List Number")

    doc.add_heading("7.3 Summary", level=2)

    add_table(doc, ["Category", "Count", "Status"], [
        ("Verified unique (SQL singletons)", f"{singletons}", "Keep - confirmed unique"),
        ("Cluster representatives", f"{sql_report['summary']['multi_member_clusters']}", "Keep - 1 per cluster, parameterize"),
        ("Unverified (no SQL extracted)", f"{len(no_sql):,}", "Keep - cannot confirm overlap"),
        ("", "", ""),
        ("TOTAL TO RETAIN", f"{unique_after_sql + len(no_sql)}", ""),
        ("TOTAL TO RETIRE/CONSOLIDATE", f"{len(all_reports) - unique_after_sql - len(no_sql):,}",
         f"{(len(all_reports) - unique_after_sql - len(no_sql))/len(all_reports)*100:.1f}% reduction"),
    ], col_widths=[6, 3, 7.5])

    doc.add_page_break()

    # ================================================================
    # 8. Caveats & Recommendations
    # ================================================================
    doc.add_heading("8. Caveats & Recommendations", level=1)

    doc.add_heading("8.1 Caveats", level=2)
    caveats = [
        f"The {len(no_sql):,} reports without SQL could not be analyzed for overlap. "
        f"Some may be duplicates of existing reports, further reducing the unique count. "
        f"To resolve these, a human must run each report interactively to select prompt values.",
        "Semantic clustering uses a 0.25 distance threshold (75% similarity minimum). "
        "Adjusting this threshold changes the number of clusters: looser grouping (0.35) "
        "produces fewer, larger clusters; tighter grouping (0.15) produces more, smaller clusters.",
        "The 'PARAMETERIZE' action requires report redesign - the current variant reports "
        "must be replaced with a single report containing appropriate prompts. This is a "
        "development effort, not a simple deletion.",
        "The 'REVIEW_WITH_OWNER' clusters (2 clusters, 4 reports) need business confirmation "
        "before acting. These reports appear similar but may serve distinct purposes.",
    ]
    for c in caveats:
        doc.add_paragraph(c, style="List Bullet")

    doc.add_heading("8.2 Recommended Action Plan", level=2)
    actions = [
        ("Immediate (Week 1)", "Retire the 608 telemetry-excluded reports. Move to archive folder. "
         "No business impact since they have zero execution evidence."),
        ("Quick wins (Week 2)", "Merge the 4 MERGE_IMMEDIATE clusters (12 reports -> 4). "
         "These have identical or near-identical SQL and can be consolidated today."),
        ("Parameterization (Weeks 3-8)", "Work with report authors to consolidate the 14 PARAMETERIZE "
         "clusters. Biggest wins: GFE085a Projections by PD (120 -> 1), GFE106 Buysheet Downloads "
         "(100 -> 3-4), Change Memo Reports (29 -> 1)."),
        ("Investigation (Ongoing)", f"Attempt to resolve the {len(no_sql):,} prompted reports manually "
         "to extract SQL and check for additional overlap."),
    ]
    for title, desc in actions:
        p = doc.add_paragraph()
        p.add_run(f"{title}: ").bold = True
        p.add_run(desc)

    doc.add_heading("8.3 Deliverables", level=2)
    deliverables = [
        "This document - Complete analysis walkthrough",
        "retire_reports.csv - 608 reports to retire immediately",
        "active_reports.csv - 739 reports with confirmed usage",
        "sql_rationalization/report_clusters.csv - All 568 reports with cluster assignments",
        "sql_rationalization/consolidation_recommendations.csv - 22 cluster recommendations",
        "sql_rationalization/near_duplicates.csv - 78 near-duplicate reports",
        "sql_rationalization_executed.ipynb - Full Jupyter notebook with code and results",
        "sql_clusters_pca.png - Visual cluster map",
    ]
    for d in deliverables:
        doc.add_paragraph(d, style="List Bullet")

    # Save
    out_path = OUTPUT_DIR / "Global_Operational_Final_Analysis.docx"
    doc.save(str(out_path))
    print(f"Document saved to: {out_path}")


if __name__ == "__main__":
    main()
