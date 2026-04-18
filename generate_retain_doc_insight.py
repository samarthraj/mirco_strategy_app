#!/usr/bin/env python3
"""Generate a Word document summarizing the INSIGHT rationalization."""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn


def set_cell_shading(cell, color_hex: str):
    shading = cell._element.get_or_add_tcPr()
    shd = shading.makeelement(qn("w:shd"), {
        qn("w:fill"): color_hex,
        qn("w:val"): "clear",
    })
    shading.append(shd)


def add_styled_table(doc, headers, rows, col_widths=None):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.LEFT

    hdr_row = table.rows[0]
    for i, h in enumerate(headers):
        cell = hdr_row.cells[i]
        cell.text = h
        for paragraph in cell.paragraphs:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for run in paragraph.runs:
                run.bold = True
                run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        set_cell_shading(cell, "2F5496")

    for r_idx, row_data in enumerate(rows):
        row = table.rows[1 + r_idx]
        for c_idx, val in enumerate(row_data):
            cell = row.cells[c_idx]
            cell.text = str(val) if val is not None else ""
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(8.5)
        if r_idx % 2 == 1:
            for cell in row.cells:
                set_cell_shading(cell, "D6E4F0")

    if col_widths:
        for row in table.rows:
            for i, width in enumerate(col_widths):
                if i < len(row.cells):
                    row.cells[i].width = Cm(width)

    return table


def classify(r):
    path = (r.get("path") or r.get("telemetryPath") or r.get("folderPath") or "").lower()
    if "/profiles/" in path or "/my reports" in path or "/my personal" in path:
        return "Personal"
    elif "/public objects/" in path or "/public/" in path:
        return "Public"
    return "Other"


def _base_name(r):
    name = r.get("name", "").strip()
    parts = name.rsplit(" - ", 1)
    if len(parts) == 2 and len(parts[0]) >= 10:
        return parts[0].strip()
    return name


def build_report_families(reports):
    groups = defaultdict(list)
    for r in reports:
        base = _base_name(r)
        groups[base].append(r)
    return {b: recs for b, recs in groups.items() if len(recs) >= 2}


def main():
    output_dir = Path("INSIGHT/rationalization")

    # Load data
    with open(output_dir / "telemetry" / "active_reports.json", "r", encoding="utf-8") as f:
        active = json.load(f)
    with open(output_dir / "telemetry" / "retire_reports.json", "r", encoding="utf-8") as f:
        retire = json.load(f)
    with open(output_dir / "analysis" / "summary.json", "r", encoding="utf-8") as f:
        analysis_summary = json.load(f)
    with open(output_dir / "analysis" / "classification.json", "r", encoding="utf-8") as f:
        classification = json.load(f)
    with open(output_dir / "run_manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    project_id = manifest.get("project", {}).get("id", "6104D29041297D66C6BD16B602F2705F")
    total_inventory = classification["totalInventoryReports"]

    # Load dedup info
    dedup_path = output_dir / "inventory" / "active_reports_dedup.json"
    dedup_info = {}
    if dedup_path.exists():
        dedup_info = json.loads(dedup_path.read_text(encoding="utf-8"))

    # Load SQL results
    sql_path = output_dir / "inventory" / "active_reports_sql.json"
    sql_reports = []
    if sql_path.exists():
        sql_reports = json.loads(sql_path.read_text(encoding="utf-8"))

    # Load duplicate SQL groups
    dupe_file = output_dir / "analysis" / "duplicate_sql.json"
    dupe_groups = []
    if dupe_file.exists():
        dupe_groups = json.loads(dupe_file.read_text(encoding="utf-8"))

    # Classify reports by location
    for r in active:
        r["_category"] = classify(r)
    for r in retire:
        r["_category"] = classify(r)

    pub_active = [r for r in active if r["_category"] == "Public"]
    per_active = [r for r in active if r["_category"] == "Personal"]
    oth_active = [r for r in active if r["_category"] == "Other"]
    pub_retire = [r for r in retire if r["_category"] == "Public"]
    per_retire = [r for r in retire if r["_category"] == "Personal"]
    oth_retire = [r for r in retire if r["_category"] == "Other"]

    exact = [r for r in active if r.get("matchTier") == "exact_name"]

    # Family analysis
    all_reports = active + retire
    all_families = build_report_families(all_reports)
    active_ids = {r["id"] for r in active}
    retire_ids = {r["id"] for r in retire}

    families_all_active = {}
    families_all_retire = {}
    families_partial = {}
    for base, recs in all_families.items():
        ids = [r["id"] for r in recs]
        a_count = sum(1 for i in ids if i in active_ids)
        if a_count == len(recs):
            families_all_active[base] = recs
        elif a_count == 0:
            families_all_retire[base] = recs
        else:
            families_partial[base] = recs

    # Active-only family analysis
    active_families = build_report_families(active)
    total_in_active_families = sum(len(recs) for recs in active_families.values())
    reducible_variants = total_in_active_families - len(active_families)
    consolidated_total = len(active) - reducible_variants

    # Dedup stats
    dedup_families = dedup_info.get("familyCount", 0)
    dedup_skipped = dedup_info.get("duplicateCount", 0)
    unique_active = classification["uniqueActiveReports"]

    # SQL stats
    has_sql = sum(1 for r in sql_reports if r.get("sql"))
    no_sql = len(sql_reports) - has_sql
    sql_prompted = sum(1 for r in sql_reports if r.get("prompted"))
    sql_cube_skip = sum(1 for r in sql_reports
                        if "cube" in str(r.get("errors", {}).get("sql", "")).lower())
    sql_err_other = no_sql - sql_cube_skip
    unique_tables = set()
    for r in sql_reports:
        unique_tables.update(r.get("sourceTables", []))

    # Duplicate SQL among active
    sql_report_ids = {r["id"] for r in sql_reports}
    active_dupe_groups = []
    for g in dupe_groups:
        members = [o for o in g["objects"] if o["id"] in sql_report_ids]
        if len(members) >= 2:
            active_dupe_groups.append(members)
    total_in_dupes = sum(len(g) for g in active_dupe_groups)
    sql_reducible = total_in_dupes - len(active_dupe_groups)

    # AST-based analysis
    ast_path = output_dir / "analysis" / "ast_duplicates.json"
    ast_groups = 0
    ast_removable = 0
    ast_parse_failures = 0
    if ast_path.exists():
        ast_data = json.loads(ast_path.read_text(encoding="utf-8"))
        ast_groups = ast_data.get("exactAstGroups", 0)
        ast_removable = ast_data.get("removableReports", 0)
        ast_parse_failures = ast_data.get("parseFailures", 0)

    structural_removable = max(sql_reducible, ast_removable)
    after_sql_dedup = unique_active - structural_removable

    # Semantic / LLM cluster review
    reviews_path = output_dir / "sql" / "cluster_reviews.json"
    semantic_removable = 0
    semantic_reviews = []
    semantic_actions = {}
    if reviews_path.exists():
        rev_data = json.loads(reviews_path.read_text(encoding="utf-8"))
        semantic_reviews = rev_data.get("reviews", [])
        for rev in semantic_reviews:
            a = rev.get("analysis", {})
            if "error" in a:
                continue
            semantic_removable += a.get("removable_count", 0)
            act = a.get("action", "?")
            semantic_actions[act] = semantic_actions.get(act, 0) + 1

    final_after_all_dedup = after_sql_dedup - semantic_removable

    # Top active reports by execution
    top_active = sorted(active, key=lambda x: -(x.get("totalExecutions") or 0))
    seen_names = set()
    top_unique = []
    for r in top_active:
        if r["name"] not in seen_names:
            seen_names.add(r["name"])
            top_unique.append(r)
        if len(top_unique) >= 15:
            break

    # ---------------------------------------------------------------
    # Build Document
    # ---------------------------------------------------------------
    doc = Document()

    # -- Title Page --
    for _ in range(4):
        doc.add_paragraph()

    title = doc.add_heading("MicroStrategy Report Rationalization", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle = doc.add_heading("INSIGHT Project", level=1)
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("Rationalization Analysis & Recommendations")
    run.font.size = Pt(14)
    run.font.color.rgb = RGBColor(0x2F, 0x54, 0x96)

    for _ in range(2):
        doc.add_paragraph()

    info_lines = [
        f"Project: INSIGHT ({project_id})",
        f"Environment: Ralph Lauren Analytics Sandbox",
        f"Date: {datetime.now().strftime('%B %d, %Y')}",
        f"Telemetry Window: Last 7 months (cutoff: September 13, 2025)",
    ]
    for line in info_lines:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(line)
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.add_page_break()

    # ---------------------------------------------------------------
    # Table of Contents
    # ---------------------------------------------------------------
    doc.add_heading("Table of Contents", level=1)
    toc_items = [
        "1. Executive Summary",
        "2. Process Overview",
        "3. Phase 1: Inventory Collection",
        "4. Phase 2: Telemetry Matching",
        "5. Phase 3: Dependency Analysis & Rationalization",
        "6. Results Summary",
        "7. Report Family Analysis",
        "8. Parent-Child & Telemetry Cross-Reference",
        "9. Definition-Based De-duplication",
        "10. SQL Extraction & Duplicate Analysis",
        "11. Retirement Recommendations",
        "12. Additional Findings & Next Steps",
    ]
    for item in toc_items:
        p = doc.add_paragraph(item)
        p.paragraph_format.space_after = Pt(2)

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 1. Executive Summary
    # ---------------------------------------------------------------
    doc.add_heading("1. Executive Summary", level=1)

    doc.add_paragraph(
        f"A comprehensive rationalization was performed on the INSIGHT "
        f"project in the Ralph Lauren MicroStrategy Analytics environment. The "
        f"objective was to identify actively used reports, flag unused reports for "
        f"retirement, and provide actionable recommendations to reduce environment "
        f"complexity."
    )

    doc.add_paragraph(
        f"The project contains {total_inventory:,} reports. Through a "
        f"multi-phase process (inventory, telemetry matching, definition-based "
        f"de-duplication, SQL extraction, and LLM-powered semantic analysis), we determined "
        f"that {len(active):,} reports have confirmed active usage and {len(retire):,} "
        f"reports are recommended for retirement. Definition fingerprinting identified "
        f"{dedup_families:,} families of identical reports, reducing to {unique_active:,}. "
        f"SQL hash dedup removed {sql_reducible:,} more. Semantic analysis (GPT-4o review of "
        f"{len(semantic_reviews):,} clusters) identified an additional {semantic_removable:,} "
        f"near-duplicates reducible via parameterization, bringing the final unique count "
        f"to {final_after_all_dedup:,} — a {(total_inventory - final_after_all_dedup)/total_inventory*100:.1f}% "
        f"total reduction."
    )

    doc.add_heading("Key Findings", level=2)
    key_findings = [
        ("Total reports inventoried", f"{total_inventory:,}"),
        ("", ""),
        ("Reports with active usage (telemetry matched)", f"{len(active):,}"),
        ("Reports to retire (no usage evidence)", f"{len(retire):,}"),
        ("", ""),
        ("Definition-based dedup families", f"{dedup_families:,}"),
        ("Duplicate copies removed (definition match)", f"{dedup_skipped:,}"),
        ("Unique active reports after dedup", f"{unique_active:,}"),
        ("", ""),
        ("SQL extracted successfully", f"{has_sql:,}"),
        ("SQL extraction gaps", f"{no_sql:,}"),
        ("Duplicate SQL groups (hash-based)", f"{len(active_dupe_groups):,}"),
        ("Further reducible via SQL hash dedup", f"{sql_reducible:,}"),
        ("", ""),
        ("Semantic clusters reviewed by LLM", f"{len(semantic_reviews):,}"),
        ("  - PARAMETERIZE (same query, diff filters)", f"{semantic_actions.get('PARAMETERIZE', 0):,}"),
        ("  - MERGE_IMMEDIATE (identical)", f"{semantic_actions.get('MERGE_IMMEDIATE', 0):,}"),
        ("  - REVIEW_WITH_OWNER", f"{semantic_actions.get('REVIEW_WITH_OWNER', 0):,}"),
        ("  - KEEP_SEPARATE", f"{semantic_actions.get('KEEP_SEPARATE', 0):,}"),
        ("Further reducible via semantic dedup", f"{semantic_removable:,}"),
        ("", ""),
        ("Final unique reports after all dedup", f"{final_after_all_dedup:,}"),
        ("Total reduction", f"{total_inventory - final_after_all_dedup:,} ({(total_inventory - final_after_all_dedup)/total_inventory*100:.1f}%)"),
        ("", ""),
        ("Orphan objects (no dependents)", f"{analysis_summary['orphanCount']:,}"),
        ("Unused metrics", f"{analysis_summary['unusedMetricCount']:,} / {classification['totalMetrics']:,}"),
        ("Stale objects (>365 days)", f"{analysis_summary['staleObjectCount']:,}"),
        ("Dependency edges", f"{analysis_summary['totalEdges']:,}"),
    ]
    add_styled_table(doc, ["Metric", "Value"], key_findings, col_widths=[10, 4])

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 2. Process Overview
    # ---------------------------------------------------------------
    doc.add_heading("2. Process Overview", level=1)

    doc.add_paragraph("The rationalization was conducted in four major phases:")

    phases = [
        (
            "Phase 1: Inventory Collection",
            f"Connected to the MicroStrategy REST API and enumerated all objects in the "
            f"INSIGHT project. Collected {total_inventory:,} reports along with "
            f"{classification['totalMetrics']:,} metrics, {classification['totalAttributes']:,} attributes, "
            f"{classification['totalFilters']:,} filters, and other supporting objects. "
            f"Fetched definitions (attributes, metrics, filters, source cube references) "
            f"and folder paths only for the {len(active):,} telemetry-active reports."
        ),
        (
            "Phase 2: Telemetry Matching",
            f"Loaded 87,274 telemetry records from server execution logs and matched "
            f"them against the inventory using exact name matching (normalized). "
            f"{len(active):,} reports had confirmed recent usage (within 7 months); "
            f"{len(retire):,} had no usage evidence and were flagged for retirement."
        ),
        (
            "Phase 3: Definition-Based De-duplication & Dependency Analysis",
            f"Created definition fingerprints (hashing attributes, metrics, filters, "
            f"source type, and cube references) for all active reports. Identified "
            f"{dedup_families:,} families of identical reports, reducing the unique set to "
            f"{unique_active:,}. Built a dependency graph with {analysis_summary['totalEdges']:,} "
            f"edges to identify orphans, unused metrics, and high-impact objects."
        ),
        (
            "Phase 4: SQL Extraction & Duplicate Analysis",
            f"Extracted SQL for {has_sql:,} unique active reports via the REST API. "
            f"Normalized and hashed all SQL to detect {len(active_dupe_groups):,} groups "
            f"of reports producing identical queries."
        ),
    ]
    for phase_title, phase_desc in phases:
        p = doc.add_paragraph()
        p.add_run(phase_title + ": ").bold = True
        p.add_run(phase_desc)

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 3. Phase 1: Inventory Collection
    # ---------------------------------------------------------------
    doc.add_heading("3. Phase 1: Inventory Collection", level=1)

    doc.add_heading("3.1 Connection & Authentication", level=2)
    doc.add_paragraph(
        "Connected to the MicroStrategy Library REST API at "
        "rlanalytics-sbx.ralphlauren.com using standard authentication "
        "(login mode 1). The session was authenticated via POST /auth/login, "
        "which returned an X-MSTR-AuthToken for subsequent API calls."
    )

    doc.add_heading("3.2 Object Enumeration", level=2)
    doc.add_paragraph(
        f"All object types were enumerated using the search API "
        f"(GET /searches/results?type={{type}}) with full pagination (1,000 objects "
        f"per page):"
    )

    cat = analysis_summary.get("byCategory", {})
    # Include full inventory counts where available
    full_inv = {}
    for cat_name in ("reports", "documents", "cubes", "metrics", "filters", "prompts",
                     "attributes", "facts", "tables", "security_filters", "custom_groups"):
        inv_file = output_dir / "inventory" / f"{cat_name}.json"
        if inv_file.exists():
            full_inv[cat_name] = len(json.loads(inv_file.read_text(encoding="utf-8")))

    cat_rows = [(k.replace("_", " ").title(), f"{v:,}")
                for k, v in sorted(full_inv.items(), key=lambda x: -x[1]) if v > 0]
    cat_rows.append(("Total", f"{sum(full_inv.values()):,}"))
    add_styled_table(doc, ["Object Category", "Count"], cat_rows, col_widths=[10, 4])

    doc.add_heading("3.3 Targeted Enrichment Strategy", level=2)
    doc.add_paragraph(
        f"Rather than fetching definitions for all {total_inventory:,} reports (which "
        f"would require 100K+ API calls), we adopted a targeted approach: first matching "
        f"against telemetry to identify active reports, then fetching definitions only for "
        f"those {len(active):,} active reports. This reduced API calls by "
        f"{(total_inventory - len(active))/total_inventory*100:.0f}% while ensuring full "
        f"coverage of reports that matter."
    )

    doc.add_paragraph(
        f"Definitions were fetched via GET /model/reports/{{id}}?showExpressionAs=tree. "
        f"Successfully enriched {classification['withDefinitions']:,} reports. "
        f"{len(active) - classification['withDefinitions']:,} reports encountered errors "
        f"during definition retrieval (connection timeouts, HTTP 400/500)."
    )

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 4. Phase 2: Telemetry Matching
    # ---------------------------------------------------------------
    doc.add_heading("4. Phase 2: Telemetry Matching", level=1)

    doc.add_heading("4.1 Telemetry Data", level=2)
    doc.add_paragraph(
        "The telemetry dataset was exported from MicroStrategy server execution logs "
        "and contains 87,274 records representing report executions across all users. "
        "Each record includes report name, folder path, executing user, execution count, "
        "error rate, execution times, and last execution timestamp."
    )

    doc.add_heading("4.2 Matching Strategy", level=2)
    doc.add_paragraph(
        "Matching was performed using exact name matching. Both the inventory report "
        "name and telemetry report name are normalized (lowercased, whitespace collapsed, "
        "leading asterisks stripped) and compared for exact equality. After deduplication "
        "by report name (aggregating across users), 16,203 unique telemetry report names "
        "were indexed for matching."
    )

    doc.add_heading("4.3 Matching Results", level=2)
    match_results = [
        ("Total inventory reports", f"{total_inventory:,}"),
        ("", ""),
        ("Exact name match", f"{len(exact):,}"),
        ("Total matched (active)", f"{len(active):,}"),
        ("", ""),
        ("Unmatched (no telemetry evidence)", f"{len(retire):,}"),
    ]
    add_styled_table(doc, ["Matching Stage", "Reports"], match_results, col_widths=[10, 4])

    doc.add_paragraph()
    doc.add_paragraph(
        f"All {len(active):,} matched reports had their last execution within the past "
        f"7 months (after September 13, 2025), confirming active usage."
    )

    doc.add_heading("4.4 Retention Criteria", level=2)
    doc.add_paragraph("A report is retained if it matches a telemetry record by exact "
                       "name and its last execution is within 7 months of the analysis date.")
    doc.add_paragraph()
    doc.add_paragraph("A report is recommended for retirement if:")
    for c in ["No telemetry match was found (no evidence of execution)",
              "A telemetry match exists but the last execution is older than 7 months"]:
        doc.add_paragraph(c, style="List Bullet")

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 5. Phase 3: Dependency Analysis
    # ---------------------------------------------------------------
    doc.add_heading("5. Phase 3: Dependency Analysis & Rationalization", level=1)

    doc.add_heading("5.1 Dependency Graph", level=2)
    doc.add_paragraph(
        f"A dependency graph was constructed with {analysis_summary['totalEdges']:,} "
        f"directed edges representing \"object A depends on object B\" relationships. "
        f"Dependencies were inferred from report definitions (attributes, metrics, "
        f"filters referenced by each active report)."
    )

    doc.add_heading("5.2 Rationalization Analysis", level=2)
    analyses = [
        ("Orphan Detection", f"{analysis_summary['orphanCount']:,}",
         "Schema objects (metrics, filters, attributes, prompts) with zero dependents - "
         "nothing references them. Candidates for cleanup."),
        ("Stale Object Detection", f"{analysis_summary['staleObjectCount']:,}",
         "Objects not modified in over 365 days. May no longer reflect current business logic."),
        ("Unused Metric Detection", f"{analysis_summary['unusedMetricCount']:,}",
         "Metrics not referenced by any active report or cube. Dead weight increasing project complexity."),
        ("Duplicate SQL Detection", f"{analysis_summary['duplicateSqlGroupCount']:,}",
         "Groups of reports producing identical normalized SQL. Consolidation candidates."),
        ("High-Impact Objects", f"{analysis_summary['highImpactCount']:,}",
         "Objects with the most dependents. Critical to protect during cleanup."),
    ]
    analysis_rows = [(a[0], a[1]) for a in analyses]
    add_styled_table(doc, ["Analysis", "Objects Found"], analysis_rows, col_widths=[10, 4])

    doc.add_paragraph()
    for name, count, desc in analyses:
        p = doc.add_paragraph()
        p.add_run(f"{name} ({count}): ").bold = True
        p.add_run(desc)

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 6. Results Summary
    # ---------------------------------------------------------------
    doc.add_heading("6. Results Summary", level=1)

    doc.add_heading("6.1 Overall Outcome", level=2)
    doc.add_paragraph(
        f"Of the {total_inventory:,} reports in the INSIGHT project, "
        f"{len(active):,} ({len(active)/total_inventory*100:.1f}%) are confirmed active "
        f"and should be retained. The remaining {len(retire):,} ({len(retire)/total_inventory*100:.1f}%) "
        f"have no evidence of usage and are recommended for retirement."
    )

    outcome_rows = [
        ("Public", f"{len(pub_active):,}", f"{len(pub_retire):,}",
         f"{len(pub_active) + len(pub_retire):,}"),
        ("Personal", f"{len(per_active):,}", f"{len(per_retire):,}",
         f"{len(per_active) + len(per_retire):,}"),
        ("Other", f"{len(oth_active):,}", f"{len(oth_retire):,}",
         f"{len(oth_active) + len(oth_retire):,}"),
        ("Total", f"{len(active):,}", f"{len(retire):,}", f"{total_inventory:,}"),
    ]
    add_styled_table(doc, ["Category", "Retain", "Retire", "Total"],
                     outcome_rows, col_widths=[5, 3, 3, 3])

    doc.add_heading("6.2 Top Active Reports", level=2)
    doc.add_paragraph("The most heavily executed reports in the retained list:")

    top_rows = []
    for i, r in enumerate(top_unique, 1):
        top_rows.append((
            i,
            r.get("name", ""),
            f"{r.get('totalExecutions', 0):,}",
            r.get("totalUsers", 0),
            r.get("lastExecTs", ""),
        ))
    add_styled_table(
        doc,
        ["#", "Report Name", "Executions", "Users", "Last Execution"],
        top_rows, col_widths=[1, 8, 2.5, 1.5, 3.5],
    )

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 7. Report Family Analysis
    # ---------------------------------------------------------------
    doc.add_heading("7. Report Family Analysis", level=1)

    doc.add_paragraph(
        f"Reports were grouped into families by base name pattern (splitting on the "
        f"last \" - \" separator, minimum 10-character base name). Across all "
        f"{total_inventory:,} reports:"
    )

    all_family_summary = [
        ("Total report families (2+ members)", f"{len(all_families):,}"),
        ("Total reports in families", f"{sum(len(r) for r in all_families.values()):,}"),
        ("Standalone reports (no variants)",
         f"{total_inventory - sum(len(r) for r in all_families.values()):,}"),
        ("Avg members per family",
         f"{sum(len(r) for r in all_families.values()) / len(all_families):.1f}" if all_families else "N/A"),
    ]
    add_styled_table(doc, ["Metric", "Value"], all_family_summary, col_widths=[10, 4])

    doc.add_heading("7.1 Largest Report Families", level=2)
    sorted_all_families = sorted(all_families.items(), key=lambda x: -len(x[1]))
    family_rows = []
    for base, recs in sorted_all_families[:20]:
        ids = [r["id"] for r in recs]
        a_count = sum(1 for i in ids if i in active_ids)
        family_rows.append((base[:55], len(recs), a_count, len(recs) - a_count))
    add_styled_table(
        doc,
        ["Base Report Name", "Family Size", "Active", "Retire"],
        family_rows, col_widths=[9, 2, 2, 2],
    )

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 8. Parent-Child & Telemetry Cross-Reference
    # ---------------------------------------------------------------
    doc.add_heading("8. Parent-Child & Telemetry Cross-Reference", level=1)

    doc.add_paragraph("Each report family was classified based on the telemetry status of its members:")

    doc.add_heading("8.1 Family Classification", level=2)
    cross_rows = [
        ("All active", f"{len(families_all_active):,}",
         f"{sum(len(r) for r in families_all_active.values()):,}"),
        ("All retire", f"{len(families_all_retire):,}",
         f"{sum(len(r) for r in families_all_retire.values()):,}"),
        ("Partially active", f"{len(families_partial):,}",
         f"{sum(len(r) for r in families_partial.values()):,}"),
    ]
    add_styled_table(doc, ["Category", "Families", "Reports"], cross_rows, col_widths=[7, 3, 4])

    doc.add_paragraph()
    doc.add_paragraph(
        f"{len(families_all_retire):,} entire families "
        f"({sum(len(r) for r in families_all_retire.values()):,} reports) "
        f"can be safely retired in bulk — zero telemetry activity across all members."
    )

    doc.add_heading("8.2 Top Families Safe to Remove Entirely", level=2)
    retire_families_sorted = sorted(families_all_retire.items(), key=lambda x: -len(x[1]))
    retire_fam_rows = [(base[:55], len(recs)) for base, recs in retire_families_sorted[:15]]
    add_styled_table(doc, ["Base Report Name", "Family Size"], retire_fam_rows, col_widths=[11, 3])

    doc.add_heading("8.3 Top Partially Active Families (Consolidation Opportunities)", level=2)
    doc.add_paragraph(
        "These families have some active and some inactive members. The inactive "
        "members can be retired, and the active members may be candidates for "
        "consolidation into a single parameterized report."
    )
    partial_sorted = sorted(families_partial.items(), key=lambda x: -len(x[1]))
    partial_rows = []
    for base, recs in partial_sorted[:15]:
        ids = [r["id"] for r in recs]
        a = sum(1 for i in ids if i in active_ids)
        pct = a / len(recs) * 100
        partial_rows.append((base[:50], len(recs), a, len(recs) - a, f"{pct:.1f}%"))
    add_styled_table(
        doc,
        ["Base Report Name", "Total", "Active", "Retire", "% Active"],
        partial_rows, col_widths=[8, 1.5, 1.5, 1.5, 2],
    )

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 9. Definition-Based De-duplication
    # ---------------------------------------------------------------
    doc.add_heading("9. Definition-Based De-duplication", level=1)

    doc.add_paragraph(
        f"A definition fingerprint was generated for each active report by hashing its "
        f"attributes, metrics, filter text, source type, and source cube ID. Reports "
        f"with identical fingerprints are true copies — regardless of name — and were "
        f"grouped into families."
    )

    dedup_rows = [
        ("Active reports analyzed", f"{len(active):,}"),
        ("Reports with definitions", f"{classification['withDefinitions']:,}"),
        ("Reports without definitions (errors)", f"{len(active) - classification['withDefinitions']:,}"),
        ("", ""),
        ("Unique definition fingerprints", f"{unique_active - (len(active) - classification['withDefinitions']):,}"),
        ("Families of identical reports", f"{dedup_families:,}"),
        ("Duplicate copies removed", f"{dedup_skipped:,}"),
        ("", ""),
        ("Unique active after dedup", f"{unique_active:,}"),
    ]
    add_styled_table(doc, ["Metric", "Count"], dedup_rows, col_widths=[10, 4])

    doc.add_paragraph()
    doc.add_paragraph(
        f"This approach is more accurate than name-based de-duplication because it "
        f"correctly identifies reports with different names but identical logic, and "
        f"preserves reports with the same name but different definitions."
    )

    # Show top families
    if dedup_info.get("families"):
        doc.add_heading("9.1 Largest Definition Families", level=2)
        fam_rows = []
        for i, fam in enumerate(dedup_info["families"][:15], 1):
            child_names = [c["name"] for c in fam.get("children", [])[:3]]
            fam_rows.append((
                i,
                fam.get("parentName", "")[:45],
                fam.get("familySize", 0),
                ", ".join(n[:25] for n in child_names) + ("..." if len(fam.get("children", [])) > 3 else ""),
            ))
        add_styled_table(
            doc,
            ["#", "Parent Report", "Family Size", "Also includes"],
            fam_rows, col_widths=[1, 6, 2, 7.5],
        )

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 10. SQL Extraction & Duplicate Analysis
    # ---------------------------------------------------------------
    doc.add_heading("10. SQL Extraction & Duplicate Analysis", level=1)

    doc.add_heading("10.1 SQL Extraction", level=2)
    doc.add_paragraph(
        f"SQL was extracted for the {unique_active:,} de-duped active reports by "
        f"creating report instances via the REST API (POST /v2/reports/{{id}}/instances) "
        f"and retrieving the generated SQL (GET /v2/reports/{{id}}/instances/{{instanceId}}/sqlView). "
        f"Cube-sourced reports were skipped as cube instances return 500 on this server."
    )

    sql_rows = [
        ("Unique active reports targeted", f"{unique_active:,}"),
        ("", ""),
        ("SQL extracted successfully", f"{has_sql:,}"),
        ("  - Including prompted (auto-resolved)", f"{sql_prompted:,}"),
        ("", ""),
        ("SQL extraction failed / skipped", f"{no_sql:,}"),
        ("  - Cube-sourced (skipped)", f"{sql_cube_skip:,}"),
        ("  - Other errors", f"{sql_err_other:,}"),
        ("", ""),
        ("Unique source tables identified", f"{len(unique_tables):,}"),
    ]
    add_styled_table(doc, ["Metric", "Count"], sql_rows, col_widths=[10, 4])

    doc.add_heading("10.2 Duplicate SQL Detection", level=2)
    doc.add_paragraph(
        f"All extracted SQL was normalized (lowercased, comments stripped, literals "
        f"replaced) and hashed using SHA-256. Reports sharing identical SQL hashes were "
        f"grouped as duplicates."
    )

    dupe_summary_rows = [
        ("Duplicate SQL groups", f"{len(active_dupe_groups):,}"),
        ("Reports with duplicate SQL", f"{total_in_dupes:,}"),
        ("Reducible (keep 1 per group)", f"{sql_reducible:,}"),
    ]
    add_styled_table(doc, ["Metric", "Count"], dupe_summary_rows, col_widths=[10, 4])

    doc.add_heading("10.3 Semantic Analysis (LLM-powered)", level=2)
    doc.add_paragraph(
        f"To catch near-duplicates that hash-based detection misses (reports with different "
        f"surface SQL but same logic), we used sentence-transformer embeddings "
        f"(all-MiniLM-L6-v2) to compute pairwise SQL similarity, then agglomerative "
        f"clustering at 85% similarity threshold. Each multi-member cluster was reviewed by "
        f"GPT-4o and classified."
    )

    sem_rows = [
        ("Clusters reviewed by LLM", f"{len(semantic_reviews):,}"),
        ("  PARAMETERIZE (same query, different filters)", f"{semantic_actions.get('PARAMETERIZE', 0):,}"),
        ("  MERGE_IMMEDIATE (identical)", f"{semantic_actions.get('MERGE_IMMEDIATE', 0):,}"),
        ("  REVIEW_WITH_OWNER", f"{semantic_actions.get('REVIEW_WITH_OWNER', 0):,}"),
        ("  KEEP_SEPARATE", f"{semantic_actions.get('KEEP_SEPARATE', 0):,}"),
        ("Total reducible (LLM-recommended)", f"{semantic_removable:,}"),
    ]
    add_styled_table(doc, ["Metric", "Count"], sem_rows, col_widths=[10, 4])

    doc.add_heading("10.4 Updated Reduction Funnel", level=2)
    funnel_rows = [
        ("Original inventory", f"{total_inventory:,}", "", ""),
        ("After telemetry retirement", f"{len(active):,}",
         f"-{len(retire):,}", f"{len(retire)/total_inventory*100:.1f}% removed"),
        ("After definition dedup", f"{unique_active:,}",
         f"-{dedup_skipped:,}", f"{dedup_skipped/len(active)*100:.1f}% further removed"),
        ("After SQL hash dedup", f"{after_sql_dedup:,}",
         f"-{sql_reducible:,}", f""),
        ("After semantic (LLM) dedup", f"{final_after_all_dedup:,}",
         f"-{semantic_removable:,}", f""),
    ]
    add_styled_table(
        doc,
        ["Stage", "Reports", "Change", "Impact"],
        funnel_rows, col_widths=[6, 2.5, 2.5, 5.5],
    )

    doc.add_paragraph()
    doc.add_paragraph(
        f"Final count after all de-duplication: {final_after_all_dedup:,} unique reports "
        f"from an original inventory of {total_inventory:,} — a total reduction of "
        f"{total_inventory - final_after_all_dedup:,} reports "
        f"({(total_inventory - final_after_all_dedup)/total_inventory*100:.1f}%)."
    )

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 11. Retirement Recommendations
    # ---------------------------------------------------------------
    doc.add_heading("11. Retirement Recommendations", level=1)

    doc.add_heading("11.1 Reports to Retire", level=2)
    doc.add_paragraph(
        f"{len(retire):,} reports are recommended for retirement. These reports "
        f"had no telemetry match, meaning there is no evidence they were executed "
        f"during the telemetry window."
    )

    retire_breakdown = [
        ("Public reports", f"{len(pub_retire):,}",
         f"{len(pub_retire)/len(retire)*100:.1f}%" if retire else "0%"),
        ("Personal reports", f"{len(per_retire):,}",
         f"{len(per_retire)/len(retire)*100:.1f}%" if retire else "0%"),
        ("Other", f"{len(oth_retire):,}",
         f"{len(oth_retire)/len(retire)*100:.1f}%" if retire else "0%"),
        ("Total", f"{len(retire):,}", "100%"),
    ]
    add_styled_table(doc, ["Category", "Count", "% of Retired"],
                     retire_breakdown, col_widths=[7, 3, 4])

    doc.add_heading("11.2 Recommended Approach", level=2)
    doc.add_paragraph("We recommend a phased approach to retirement:")

    steps = [
        ("Phase A - Validate & Communicate",
         "Share the retirement list (retire_reports.csv) with report owners and "
         "business stakeholders. Allow a 2-week review period for any objections."),
        ("Phase B - Retire Full Families",
         f"Retire the {len(families_all_retire):,} entirely inactive families "
         f"({sum(len(r) for r in families_all_retire.values()):,} reports) first. "
         f"These have zero usage across all variants and carry the lowest risk."),
        ("Phase C - Archive Personal Reports",
         f"Retire the {len(per_retire):,} personal reports. These are in individual "
         f"user profile folders and have no shared impact."),
        ("Phase D - Archive Public Reports",
         f"Retire the {len(pub_retire):,} public reports in batches, prioritizing "
         f"older modification dates. Move to an archive folder and retain for 90 days."),
        ("Phase E - Consolidate Duplicate Families",
         f"Review the {dedup_families:,} definition-identical families and "
         f"{len(active_dupe_groups):,} SQL-duplicate groups. Consolidate into "
         f"single parameterized reports where possible."),
        ("Phase F - Clean Up Orphans",
         f"Address the {analysis_summary['unusedMetricCount']:,} unused metrics and "
         f"{analysis_summary['orphanCount']:,} orphaned objects to simplify the project schema."),
    ]
    for step_title, step_desc in steps:
        p = doc.add_paragraph()
        p.add_run(step_title + ": ").bold = True
        p.add_run(step_desc)

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 12. Additional Findings & Next Steps
    # ---------------------------------------------------------------
    doc.add_heading("12. Additional Findings & Next Steps", level=1)

    doc.add_heading("12.1 Project Health Overview", level=2)
    health_rows = [
        ("Orphaned objects", f"{analysis_summary['orphanCount']:,}",
         "Schema objects (metrics, filters, attributes, prompts) with zero dependents"),
        ("Stale objects", f"{analysis_summary['staleObjectCount']:,}",
         "Objects not modified in over 1 year"),
        ("Unused metrics", f"{analysis_summary['unusedMetricCount']:,}",
         "Metrics not used by any active report or cube"),
        ("Stale but active", f"{classification['staleAndActive']:,}",
         "Reports with telemetry usage but not modified in >1 year"),
        ("Errors during enrichment", f"{analysis_summary['errorCount']:,}",
         "Objects that could not be fully analyzed"),
    ]
    add_styled_table(
        doc,
        ["Finding", "Count", "Description"],
        health_rows, col_widths=[4, 2, 10.5],
    )

    doc.add_heading("12.2 Recommended Next Steps", level=2)
    next_steps = [
        "Review the retirement list with business stakeholders and complete Phase A validation within 2 weeks.",
        f"Begin bulk retirement of {len(families_all_retire):,} entirely inactive families (Phase B) — lowest risk.",
        "Archive personal reports (Phase C) as these carry no shared impact.",
        f"Consolidate the {dedup_families:,} definition-identical families — true copies that can be merged.",
        f"Consolidate the {len(active_dupe_groups):,} SQL-duplicate groups ({sql_reducible:,} reducible reports) "
        f"into parameterized reports.",
        f"Address the {analysis_summary['unusedMetricCount']:,} unused metrics to simplify the project schema.",
        f"Investigate the {analysis_summary['orphanCount']:,} orphaned objects for potential cleanup.",
        "Schedule recurring rationalization reviews (quarterly) to prevent report sprawl.",
    ]
    for step in next_steps:
        doc.add_paragraph(step, style="List Number")

    doc.add_heading("12.3 Deliverables", level=2)
    deliverables = [
        "This document — Rationalization summary and methodology",
        f"retire_reports.csv — Complete list of {len(retire):,} reports recommended for retirement",
        f"active_reports.json — {len(active):,} reports to retain with usage statistics",
        f"active_reports_enriched.json — {classification['withDefinitions']:,} reports with full definitions",
        f"active_reports_dedup.json — Definition-based family mapping ({dedup_families:,} families)",
        f"active_reports_sql.json — SQL for {has_sql:,} unique active reports",
        "analysis/duplicate_sql.json — Duplicate SQL groups with member details",
        "analysis/orphans.json — Orphaned objects list",
        "analysis/unused_metrics.json — Unused metrics list",
        "analysis/high_impact.json — Top 50 high-impact objects",
        "rationalization_report.json — Full machine-readable report",
    ]
    for d in deliverables:
        doc.add_paragraph(d, style="List Bullet")

    # ---------------------------------------------------------------
    # Save
    # ---------------------------------------------------------------
    out_path = output_dir / "INSIGHT_Rationalization_Summary.docx"
    doc.save(str(out_path))
    print(f"Document saved to: {out_path}")


if __name__ == "__main__":
    main()
