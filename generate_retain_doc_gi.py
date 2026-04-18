#!/usr/bin/env python3
"""Generate a Word document summarizing the Global Insight rationalization."""

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
    path = (r.get("path") or r.get("telemetryPath") or "").lower()
    if "/profiles/" in path or "/my reports" in path or "/my personal" in path:
        return "Personal"
    elif "/public objects/" in path or "/public/" in path:
        return "Public"
    return "Other"


def build_report_families(reports):
    """Group reports into families by base name pattern."""
    groups = defaultdict(list)
    for r in reports:
        name = r.get("name", "").strip()
        parts = name.rsplit(" - ", 1)
        if len(parts) == 2 and len(parts[0]) >= 10:
            base = parts[0].strip()
        else:
            base = name
        groups[base].append(r)
    families = {b: recs for b, recs in groups.items() if len(recs) >= 2}
    return families


def main():
    output_dir = Path("Global Insight")

    # Load telemetry results
    with open(output_dir / "telemetry" / "active_reports.json", "r", encoding="utf-8") as f:
        active = json.load(f)
    with open(output_dir / "telemetry" / "retire_reports.json", "r", encoding="utf-8") as f:
        retire = json.load(f)
    with open(output_dir / "analysis" / "summary.json", "r", encoding="utf-8") as f:
        analysis_summary = json.load(f)
    with open(output_dir / "run_manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    project_id = manifest.get("project", {}).get("id", "07E2CE9311EB6800B59F0080EF050FB2")
    total_inventory = len(active) + len(retire)

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

    # Family analysis on ALL reports (not just active)
    all_reports_for_families = active + retire
    all_families = build_report_families(all_reports_for_families)
    standalone_all = {b: recs for b, recs in
                      defaultdict(list, {_base_name(r): [r] for r in all_reports_for_families}).items()
                      if len(recs) == 1}

    # Family x telemetry cross-reference
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

    # Active-only family analysis for consolidation
    active_families = build_report_families(active)
    total_in_active_families = sum(len(recs) for recs in active_families.values())
    standalone_active_count = len(active) - total_in_active_families
    reducible_variants = total_in_active_families - len(active_families)
    consolidated_total = len(active) - reducible_variants

    # Build de-duped active IDs (1 per family, highest executions)
    _dedup_groups = defaultdict(list)
    for r in active:
        _dedup_groups[_base_name(r)].append(r)
    deduped_ids = set()
    for base, recs in _dedup_groups.items():
        if len(recs) == 1:
            deduped_ids.add(recs[0]["id"])
        else:
            parent = max(recs, key=lambda x: x.get("totalExecutions", 0))
            deduped_ids.add(parent["id"])

    # Consolidated list for category breakdown
    consolidated_list = []
    active_standalone_groups = {b: recs for b, recs in
                                 defaultdict(list, {_base_name(r): [r] for r in active}).items()
                                 if len(recs) == 1}
    for recs in active_standalone_groups.values():
        consolidated_list.extend(recs)
    for base, recs in active_families.items():
        parent = max(recs, key=lambda x: x.get("totalExecutions", 0))
        consolidated_list.append(parent)
    consol_pub = sum(1 for r in consolidated_list if classify(r) == "Public")
    consol_per = sum(1 for r in consolidated_list if classify(r) == "Personal")
    consol_oth = sum(1 for r in consolidated_list if classify(r) == "Other")

    # SQL extraction stats — load from inventory reports.json
    sql_stats = {"has_sql": 0, "no_sql": 0, "prompted_ok": 0,
                 "err_prompted": 0, "err_cube": 0, "err_permission": 0,
                 "err_server": 0, "err_other": 0, "unique_tables": set()}
    inv_reports_path = output_dir / "inventory" / "reports.json"
    if inv_reports_path.exists():
        import json as _json
        _inv = _json.loads(inv_reports_path.read_text(encoding="utf-8"))
        _inv_lookup = {r["id"]: r for r in _inv}
        for rid in deduped_ids:
            r = _inv_lookup.get(rid, {})
            if r.get("sql"):
                sql_stats["has_sql"] += 1
                if r.get("prompted"):
                    sql_stats["prompted_ok"] += 1
                sql_stats["unique_tables"].update(r.get("sourceTables", []))
            else:
                sql_stats["no_sql"] += 1
                err = r.get("errors", {}).get("sql", "").lower()
                if "prompt" in err:
                    sql_stats["err_prompted"] += 1
                elif "cube" in err or "icube" in err:
                    sql_stats["err_cube"] += 1
                elif "403" in err or "permission" in err:
                    sql_stats["err_permission"] += 1
                elif "500" in err:
                    sql_stats["err_server"] += 1
                elif err:
                    sql_stats["err_other"] += 1
        del _inv, _inv_lookup
    sql_stats["unique_table_count"] = len(sql_stats["unique_tables"])
    del sql_stats["unique_tables"]

    # Duplicate SQL groups (from active-only analysis if available, else fall back)
    active_analysis_dir = output_dir / "analysis_active"
    if (active_analysis_dir / "duplicate_sql.json").exists():
        dupe_groups = json.loads((active_analysis_dir / "duplicate_sql.json").read_text(encoding="utf-8"))
        active_dupe_groups = [g["objects"] for g in dupe_groups if g.get("count", len(g.get("objects", []))) >= 2]
    else:
        dupe_groups = []
        dupe_file = output_dir / "analysis" / "duplicate_sql.json"
        if dupe_file.exists():
            dupe_groups = json.loads(dupe_file.read_text(encoding="utf-8"))
        active_dupe_groups = []
        for g in dupe_groups:
            members = [o for o in g["objects"] if o["id"] in deduped_ids]
            if len(members) >= 2:
                active_dupe_groups.append(members)
    total_in_dupes = sum(len(g) for g in active_dupe_groups)
    sql_reducible = total_in_dupes - len(active_dupe_groups)
    final_after_sql_dedup = consolidated_total - sql_reducible

    # Semantic clusters + LLM reviews
    semantic_clusters = []
    llm_reviews = []
    semantic_summary = {}
    sc_file = output_dir / "sql" / "semantic_clusters.json"
    if sc_file.exists():
        sc_data = json.loads(sc_file.read_text(encoding="utf-8"))
        if isinstance(sc_data, dict):
            semantic_summary = sc_data.get("summary", {})
            semantic_clusters = sc_data.get("clusters", [])
        else:
            semantic_clusters = sc_data
    review_file = output_dir / "sql" / "cluster_reviews.json"
    if review_file.exists():
        rv = json.loads(review_file.read_text(encoding="utf-8"))
        llm_reviews = rv.get("reviews", []) if isinstance(rv, dict) else rv

    # AST analysis summary (if cache exists)
    ast_summary = {}
    ast_cache = output_dir / "analysis" / "ast_cache.json"
    if ast_cache.exists():
        ast_data = json.loads(ast_cache.read_text(encoding="utf-8"))
        # cache format is id -> hashes; count groups externally
        if isinstance(ast_data, dict):
            # Group by frozenset of hashes to find exact-AST duplicates
            groups_by_ast = defaultdict(list)
            for rid, hashes in ast_data.items():
                if isinstance(hashes, list):
                    groups_by_ast[frozenset(hashes)].append(rid)
            multi = [g for g in groups_by_ast.values() if len(g) >= 2]
            ast_summary = {
                "parsed_count": len(ast_data),
                "group_count": len(multi),
                "reports_in_groups": sum(len(g) for g in multi),
                "removable": sum(len(g) - 1 for g in multi),
            }

    # LLM-based removable count
    llm_removable = sum((r.get("analysis") or {}).get("removable_count", 0) for r in llm_reviews)
    final_after_llm = final_after_sql_dedup - llm_removable

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

    subtitle = doc.add_heading("Global Insight Project", level=1)
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("Rationalization Analysis & Recommendations")
    run.font.size = Pt(14)
    run.font.color.rgb = RGBColor(0x2F, 0x54, 0x96)

    for _ in range(2):
        doc.add_paragraph()

    info_lines = [
        f"Project: Global Insight ({project_id})",
        f"Environment: Ralph Lauren Analytics Sandbox",
        f"Date: {datetime.now().strftime('%B %d, %Y')}",
        f"Telemetry Window: Last 7 months (cutoff: September 12, 2025)",
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
        "9. SQL Extraction & Duplicate Analysis",
        "10. Semantic Clustering & LLM Review",
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
        f"A comprehensive rationalization was performed on the Global Insight "
        f"project in the Ralph Lauren MicroStrategy Analytics environment. The "
        f"objective was to identify actively used reports, flag unused reports for "
        f"retirement, and provide actionable recommendations to reduce environment "
        f"complexity."
    )

    doc.add_paragraph(
        f"The project contains {analysis_summary['totalObjects']:,} total objects "
        f"across 11 categories, including {total_inventory:,} reports. Through a "
        f"multi-phase process (inventory, telemetry matching, dependency analysis, "
        f"and parent-child analysis), we determined that {len(active):,} reports have "
        f"confirmed active usage and {len(retire):,} reports are recommended for "
        f"retirement. Further analysis identified {len(active_families):,} report families "
        f"among the active reports ({total_in_active_families:,} reports that are variants "
        f"of a common parent), which can be consolidated to reduce the retained list to "
        f"{consolidated_total:,} unique reports - a "
        f"{(total_inventory - consolidated_total)/total_inventory*100:.1f}% total reduction."
    )

    doc.add_heading("Key Findings", level=2)
    key_findings = [
        ("Total reports inventoried", f"{total_inventory:,}"),
        ("", ""),
        ("Reports with active usage (telemetry matched)", f"{len(active):,}"),
        ("Reports to retire (no usage evidence)", f"{len(retire):,}"),
        ("", ""),
        ("Report families identified (all reports)", f"{len(all_families):,}"),
        ("  - Families entirely active", f"{len(families_all_active):,}"),
        ("  - Families entirely retired", f"{len(families_all_retire):,}"),
        ("  - Families partially active", f"{len(families_partial):,}"),
        ("", ""),
        ("Active report families (consolidation candidates)", f"{len(active_families):,}"),
        ("Variant reports reducible via consolidation", f"{reducible_variants:,}"),
        ("De-duped active reports (1 per family)", f"{len(deduped_ids):,}"),
        ("", ""),
        ("SQL extracted successfully", f"{sql_stats['has_sql']:,}"),
        ("SQL extraction gaps", f"{sql_stats['no_sql']:,}"),
        ("Duplicate SQL groups (exact hash)", f"{len(active_dupe_groups):,}"),
        ("Reducible via exact-SQL de-dup", f"{sql_reducible:,}"),
        ("AST-based duplicate groups", f"{ast_summary.get('group_count', 0):,}"),
        ("Reducible via AST de-dup", f"{ast_summary.get('removable', 0):,}"),
        ("Semantic clusters (multi-member)", f"{semantic_summary.get('multiMemberClusters', len([c for c in semantic_clusters if isinstance(c, dict) and len(c.get('members', c.get('objects', []))) >= 2])):,}"),
        ("LLM-reviewed clusters (top tier)", f"{len(llm_reviews):,}"),
        ("Reducible via LLM parameterization", f"{llm_removable:,}"),
        ("", ""),
        ("Final retained after exact-SQL de-dup", f"{final_after_sql_dedup:,}"),
        ("Final retained after LLM parameterization", f"{final_after_llm:,}"),
        ("  - Public", f"{consol_pub:,}"),
        ("  - Personal", f"{consol_per:,}"),
        ("  - Other", f"{consol_oth:,}"),
        ("", ""),
        ("Total reduction (full pipeline)",
         f"{total_inventory - final_after_llm:,} ({(total_inventory - final_after_llm)/total_inventory*100:.1f}%)"),
    ]
    add_styled_table(doc, ["Metric", "Value"], key_findings, col_widths=[10, 4])

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 2. Process Overview
    # ---------------------------------------------------------------
    doc.add_heading("2. Process Overview", level=1)

    doc.add_paragraph(
        "The rationalization was conducted in four major phases:"
    )

    phases = [
        (
            "Phase 1: Inventory Collection",
            f"Connected to the MicroStrategy REST API and enumerated all "
            f"{analysis_summary['totalObjects']:,} objects in the Global Insight project "
            f"across 11 categories. For each of the {total_inventory:,} reports, enriched "
            f"the record with its modeling definition (attributes, metrics, filters, "
            f"source cube references)."
        ),
        (
            "Phase 2: Telemetry Matching",
            f"Loaded 130,550 telemetry records from server execution logs and matched "
            f"them against the inventory using exact name matching (normalized). Reports "
            f"with confirmed recent usage (within 7 months) were marked for retention; "
            f"those with no usage evidence were flagged for retirement."
        ),
        (
            "Phase 3: Dependency Analysis & Rationalization",
            f"Built a dependency graph with {analysis_summary['totalEdges']:,} edges "
            f"to identify orphaned objects, unused metrics, stale objects, and high-impact "
            f"shared components."
        ),
        (
            "Phase 4: Parent-Child Analysis",
            f"Grouped all {total_inventory:,} reports into families by base name pattern, "
            f"then cross-referenced family membership with telemetry results to identify "
            f"entire families safe for retirement and partial families with consolidation "
            f"opportunities."
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
        f"per page). A total of {analysis_summary['totalObjects']:,} objects were "
        f"discovered:"
    )

    cat = analysis_summary.get("byCategory", {})
    cat_rows = [(k.replace("_", " ").title(), f"{v:,}")
                for k, v in sorted(cat.items(), key=lambda x: -x[1]) if v > 0]
    add_styled_table(doc, ["Object Category", "Count"], cat_rows, col_widths=[10, 4])

    doc.add_heading("3.3 Report Enrichment", level=2)
    doc.add_paragraph(
        f"For each of the {total_inventory:,} reports, the modeling definition was "
        f"fetched via GET /model/reports/{{id}}?showExpressionAs=tree. This provided "
        f"attributes, metrics, filters, source cube IDs, and timestamps. "
        f"Enrichment completed successfully for {total_inventory - analysis_summary['errorCount']:,} "
        f"reports. {analysis_summary['errorCount']:,} reports encountered errors during "
        f"definition retrieval."
    )

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 4. Phase 2: Telemetry Matching
    # ---------------------------------------------------------------
    doc.add_heading("4. Phase 2: Telemetry Matching", level=1)

    doc.add_heading("4.1 Telemetry Data", level=2)
    doc.add_paragraph(
        "The telemetry dataset was exported from MicroStrategy server execution logs "
        "and contains 130,550 records representing report executions across all users. "
        "Each record includes report name, folder path, executing user, execution count, "
        "error rate, execution times, and last execution timestamp."
    )

    doc.add_heading("4.2 Matching Strategy", level=2)
    doc.add_paragraph(
        "Matching was performed using exact name matching. Both the inventory report "
        "name and telemetry report name are normalized (lowercased, whitespace collapsed, "
        "leading asterisks stripped) and compared for exact equality. After deduplication "
        "by report name (aggregating across users), 34,404 unique telemetry report names "
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
        f"7 months (after September 12, 2025), confirming active usage."
    )

    doc.add_heading("4.4 Retention Criteria", level=2)
    doc.add_paragraph("A report is retained if it matches a telemetry record by exact "
                       "name and its last execution is within 7 months of the analysis date.")
    doc.add_paragraph()
    doc.add_paragraph("A report is recommended for retirement if:")
    retire_criteria = [
        "No telemetry match was found (no evidence of execution)",
        "A telemetry match exists but the last execution is older than 7 months",
    ]
    for c in retire_criteria:
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
        f"Dependencies were inferred from report and cube definitions."
    )

    doc.add_heading("5.2 Rationalization Analysis", level=2)
    analyses = [
        (
            "Orphan Detection",
            f"{analysis_summary['orphanCount']:,}",
            "Schema objects (metrics, filters, attributes, prompts) with zero dependents - "
            "nothing references them. Candidates for cleanup."
        ),
        (
            "Stale Object Detection",
            f"{analysis_summary['staleObjectCount']:,}",
            "Objects not modified in over 365 days. May no longer reflect current business logic."
        ),
        (
            "Unused Metric Detection",
            f"{analysis_summary['unusedMetricCount']:,}",
            "Metrics not referenced by any report or cube. Dead weight increasing project complexity."
        ),
        (
            "Duplicate SQL Detection",
            f"{analysis_summary['duplicateSqlGroupCount']:,}",
            "Reports grouped by normalized SQL hash. SQL extraction was not included in this run."
        ),
        (
            "High-Impact Objects",
            f"{analysis_summary['highImpactCount']:,}",
            "Objects with the most dependents. Critical to protect during cleanup."
        ),
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
        f"Of the {total_inventory:,} reports in the Global Insight project, "
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
    doc.add_paragraph(
        "The most heavily executed reports in the retained list:"
    )

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
        ("Standalone reports (no variants)", f"{total_inventory - sum(len(r) for r in all_families.values()):,}"),
        ("Avg members per family", f"{sum(len(r) for r in all_families.values()) / len(all_families):.1f}" if all_families else "N/A"),
    ]
    add_styled_table(doc, ["Metric", "Value"], all_family_summary, col_widths=[10, 4])

    doc.add_heading("7.1 Largest Report Families", level=2)

    sorted_all_families = sorted(all_families.items(), key=lambda x: -len(x[1]))
    family_rows = []
    for base, recs in sorted_all_families[:20]:
        ids = [r["id"] for r in recs]
        a_count = sum(1 for i in ids if i in active_ids)
        family_rows.append((
            base[:55],
            len(recs),
            a_count,
            len(recs) - a_count,
        ))
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

    doc.add_paragraph(
        f"Each report family was classified based on the telemetry status of its members:"
    )

    partial_active_reports = sum(
        sum(1 for r in recs if r["id"] in active_ids)
        for recs in families_partial.values()
    )
    partial_retire_reports = sum(
        sum(1 for r in recs if r["id"] in retire_ids)
        for recs in families_partial.values()
    )
    standalone_active_n = sum(1 for r in all_reports_for_families
                              if _base_name(r) not in all_families and r["id"] in active_ids)
    standalone_retire_n = sum(1 for r in all_reports_for_families
                              if _base_name(r) not in all_families and r["id"] in retire_ids)

    doc.add_heading("8.1 Family Classification", level=2)
    cross_rows = [
        ("All active", f"{len(families_all_active):,}",
         f"{sum(len(r) for r in families_all_active.values()):,}"),
        ("All retire", f"{len(families_all_retire):,}",
         f"{sum(len(r) for r in families_all_retire.values()):,}"),
        ("Partially active", f"{len(families_partial):,}",
         f"{sum(len(r) for r in families_partial.values()):,}"),
        ("  -> active members", "",  f"{partial_active_reports:,}"),
        ("  -> retire members", "",  f"{partial_retire_reports:,}"),
    ]
    add_styled_table(doc, ["Category", "Families", "Reports"], cross_rows, col_widths=[7, 3, 4])

    doc.add_paragraph()

    standalone_rows = [
        ("Active", f"{standalone_active_n:,}"),
        ("Retire", f"{standalone_retire_n:,}"),
        ("Total", f"{standalone_active_n + standalone_retire_n:,}"),
    ]
    doc.add_heading("8.2 Standalone Reports", level=2)
    add_styled_table(doc, ["Status", "Reports"], standalone_rows, col_widths=[7, 4])

    doc.add_paragraph()
    doc.add_paragraph(
        f"{len(families_all_retire):,} entire families ({sum(len(r) for r in families_all_retire.values()):,} reports) "
        f"can be safely retired in bulk - zero telemetry activity across all members."
    )

    doc.add_heading("8.3 Top Families Safe to Remove Entirely", level=2)
    retire_families_sorted = sorted(families_all_retire.items(), key=lambda x: -len(x[1]))
    retire_fam_rows = [(base[:55], len(recs)) for base, recs in retire_families_sorted[:15]]
    add_styled_table(doc, ["Base Report Name", "Family Size"], retire_fam_rows, col_widths=[11, 3])

    doc.add_heading("8.4 Top Partially Active Families (Consolidation Opportunities)", level=2)
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
    # 7.2 (continued) Consolidation Impact for Active Reports
    # ---------------------------------------------------------------
    doc.add_heading("8.5 Consolidation Impact", level=2)
    doc.add_paragraph(
        f"If each active report family is consolidated into a single parameterized "
        f"parent report (keeping the variant with the highest execution count):"
    )

    funnel_rows = [
        ("Original inventory", f"{total_inventory:,}", "", ""),
        ("After telemetry retirement", f"{len(active):,}",
         f"-{len(retire):,}", f"{len(retire)/total_inventory*100:.1f}% removed"),
        ("After family consolidation", f"{consolidated_total:,}",
         f"-{reducible_variants:,}", f"{reducible_variants/len(active)*100:.1f}% further removed"),
    ]
    add_styled_table(
        doc,
        ["Stage", "Reports", "Change", "Impact"],
        funnel_rows, col_widths=[6, 2.5, 2.5, 5.5],
    )

    doc.add_paragraph()
    doc.add_paragraph(
        f"Overall reduction: {total_inventory - final_after_sql_dedup:,} reports "
        f"({(total_inventory - final_after_sql_dedup)/total_inventory*100:.1f}%) from "
        f"{total_inventory:,} down to {final_after_sql_dedup:,} (including SQL de-dup)."
    )

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 9. SQL Extraction & Duplicate Analysis
    # ---------------------------------------------------------------
    doc.add_heading("9. SQL Extraction & Duplicate Analysis", level=1)

    doc.add_heading("9.1 SQL Extraction", level=2)
    doc.add_paragraph(
        f"SQL was extracted for the {len(deduped_ids):,} de-duped active reports by "
        f"creating report instances via the REST API (POST /v2/reports/{{id}}/instances) "
        f"and retrieving the generated SQL (GET /v2/reports/{{id}}/instances/{{instanceId}}/sqlView). "
        f"For prompted reports, prompts were auto-resolved using cached or default answers."
    )

    sql_rows = [
        ("De-duped active reports targeted", f"{len(deduped_ids):,}"),
        ("", ""),
        ("SQL extracted successfully", f"{sql_stats['has_sql']:,}"),
        ("  - Including prompted (auto-resolved)", f"{sql_stats['prompted_ok']:,}"),
        ("", ""),
        ("SQL extraction failed", f"{sql_stats['no_sql']:,}"),
        ("  - Prompted (unresolvable)", f"{sql_stats['err_prompted']:,}"),
        ("  - Cube-sourced (server 500)", f"{sql_stats['err_cube']:,}"),
        ("  - Permission denied (403)", f"{sql_stats['err_permission']:,}"),
        ("  - Server error (500)", f"{sql_stats['err_server']:,}"),
        ("  - Other", f"{sql_stats['err_other']:,}"),
        ("", ""),
        ("Unique source tables identified", f"{sql_stats['unique_table_count']:,}"),
    ]
    add_styled_table(doc, ["Metric", "Count"], sql_rows, col_widths=[10, 4])

    doc.add_paragraph()
    doc.add_paragraph(
        f"SQL was successfully extracted for {sql_stats['has_sql']:,} of "
        f"{len(deduped_ids):,} reports ({sql_stats['has_sql']/len(deduped_ids)*100:.1f}%). "
        f"The {sql_stats['err_prompted']:,} prompted reports had required prompts with no "
        f"default answers that could not be auto-resolved. The {sql_stats['err_cube']:,} "
        f"cube-sourced reports failed because Intelligent Cube instances could not be "
        f"created on the server (HTTP 500) - these cubes need to be published and loaded "
        f"in memory for SQL extraction, which requires server administrator intervention."
    )

    doc.add_heading("9.2 Duplicate SQL Detection", level=2)
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

    doc.add_paragraph()
    doc.add_paragraph(
        f"These are reports with different names but identical underlying SQL queries. "
        f"Common patterns include time-period variants (YTD/QTD/monthly copies of the "
        f"same query), account-specific copies (same query filtered for different "
        f"retailers), and fiscal year copies. Each group is a strong candidate for "
        f"consolidation into a single parameterized report."
    )

    doc.add_heading("9.3 Largest Duplicate SQL Groups", level=2)
    top_dupe_rows = []
    for i, members in enumerate(sorted(active_dupe_groups, key=len, reverse=True)[:15], 1):
        names = [m.get("name", "")[:50] for m in members]
        top_dupe_rows.append((
            i,
            len(members),
            names[0],
            ", ".join(n[:30] for n in names[1:3]) + ("..." if len(names) > 3 else ""),
        ))
    add_styled_table(
        doc,
        ["#", "Size", "Primary Report", "Also includes"],
        top_dupe_rows, col_widths=[1, 1.5, 6, 8],
    )

    doc.add_heading("9.4 Updated Reduction Funnel", level=2)
    full_funnel_rows = [
        ("Original inventory", f"{total_inventory:,}", "", ""),
        ("After telemetry retirement", f"{len(active):,}",
         f"-{len(retire):,}", f"{len(retire)/total_inventory*100:.1f}% removed"),
        ("After family de-dup", f"{len(deduped_ids):,}",
         f"-{reducible_variants:,}", f"{reducible_variants/len(active)*100:.1f}% further removed"),
        ("After SQL de-dup", f"{final_after_sql_dedup:,}",
         f"-{sql_reducible:,}", f"{sql_reducible/len(deduped_ids)*100:.1f}% further removed"),
    ]
    add_styled_table(
        doc,
        ["Stage", "Reports", "Change", "Impact"],
        full_funnel_rows, col_widths=[6, 2.5, 2.5, 5.5],
    )

    doc.add_paragraph()
    doc.add_paragraph(
        f"Final count after all de-duplication: {final_after_sql_dedup:,} unique reports "
        f"from an original inventory of {total_inventory:,} - a total reduction of "
        f"{total_inventory - final_after_sql_dedup:,} reports "
        f"({(total_inventory - final_after_sql_dedup)/total_inventory*100:.1f}%)."
    )

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 10. Semantic Clustering & LLM Review
    # ---------------------------------------------------------------
    doc.add_heading("10. Semantic Clustering & LLM Review", level=1)

    doc.add_heading("10.1 AST-Based Duplicate Detection", level=2)
    doc.add_paragraph(
        "Beyond exact SQL hash matching, we applied AST (Abstract Syntax Tree) analysis "
        "using sqlglot. Each SQL is parsed into a tree structure, canonicalized (lowercased, "
        "aliases normalized, literal values replaced), then subtrees are hashed. Reports "
        "with identical AST subtree sets are structural duplicates even if text differs "
        "(whitespace, formatting, comments)."
    )
    ast_rows = [
        ("SQLs parsed", f"{ast_summary.get('parsed_count', 0):,}"),
        ("AST-identical groups", f"{ast_summary.get('group_count', 0):,}"),
        ("Reports in AST groups", f"{ast_summary.get('reports_in_groups', 0):,}"),
        ("Removable (keep 1 per group)", f"{ast_summary.get('removable', 0):,}"),
    ]
    add_styled_table(doc, ["Metric", "Count"], ast_rows, col_widths=[10, 4])

    doc.add_heading("10.2 Semantic Clustering (ML-Based)", level=2)
    doc.add_paragraph(
        "Unique normalized SQLs were embedded using the all-MiniLM-L6-v2 sentence-transformer "
        "model, producing 384-dimensional vectors. Agglomerative clustering was applied with "
        "a 75% cosine similarity threshold to group near-duplicate SQLs that differ only in "
        "minor structural ways (column orderings, aliases, literal constants, filter values). "
        "This catches consolidation opportunities that exact hashing and AST matching miss."
    )

    cluster_rows = [
        ("Total unique SQL", f"{semantic_summary.get('totalUniqueSQL', 0):,}"),
        ("Total reports", f"{semantic_summary.get('totalReports', 0):,}"),
        ("Multi-member clusters", f"{semantic_summary.get('multiMemberClusters', 0):,}"),
        ("Singleton clusters", f"{semantic_summary.get('singletonClusters', 0):,}"),
        ("Reports in multi-member clusters", f"{semantic_summary.get('reportsInMultiMemberClusters', 0):,}"),
        ("High-similarity pairs (>90%)", f"{semantic_summary.get('highSimilarityPairs_gt90', 0):,}"),
        ("High-similarity pairs (>80%)", f"{semantic_summary.get('highSimilarityPairs_gt80', 0):,}"),
    ]
    add_styled_table(doc, ["Metric", "Count"], cluster_rows, col_widths=[10, 4])

    doc.add_heading("10.3 LLM-Based Cluster Review", level=2)
    doc.add_paragraph(
        "The top semantic clusters (highest average similarity) were submitted to GPT-4o for "
        "business labeling and consolidation recommendations. The LLM was given representative "
        "SQL samples from each cluster and asked to identify the business function, the "
        "relationship between members, and recommend an action (PARAMETERIZE, REVIEW_WITH_OWNER, "
        "or KEEP_ALL). Reports labeled PARAMETERIZE can be consolidated into a single "
        "parameterized report."
    )

    if llm_reviews:
        llm_rows = []
        for i, r in enumerate(llm_reviews, 1):
            a = r.get("analysis") or {}
            llm_rows.append((
                i,
                r.get("totalReportCount", 0),
                f"{r.get('avgSimilarity', 0):.3f}",
                a.get("action", ""),
                a.get("label", "")[:40],
                a.get("removable_count", 0),
            ))
        add_styled_table(
            doc,
            ["#", "Size", "Similarity", "Action", "Label", "Removable"],
            llm_rows, col_widths=[1, 1.5, 1.5, 3, 5.5, 1.5],
        )

        doc.add_paragraph()
        doc.add_paragraph(
            f"Across the {len(llm_reviews):,} reviewed clusters, {llm_removable:,} reports "
            f"were flagged as reducible via parameterization. The two largest clusters alone "
            f"account for over 1,600 reports that could collapse to 2 parameterized templates:"
        )
        for r in sorted(llm_reviews, key=lambda x: -x.get("totalReportCount", 0))[:3]:
            a = r.get("analysis") or {}
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"{a.get('label', 'Unnamed')}: ").bold = True
            p.add_run(f"{r.get('totalReportCount', 0)} reports, "
                       f"{a.get('removable_count', 0)} removable. "
                       f"{a.get('consolidation_detail', '')}")

    doc.add_heading("10.4 Full De-duplication Funnel", level=2)
    full_funnel_rows = [
        ("Original inventory", f"{total_inventory:,}", "", ""),
        ("After telemetry retirement", f"{len(active):,}",
         f"-{len(retire):,}", f"{len(retire)/total_inventory*100:.1f}% removed"),
        ("After family de-dup", f"{len(deduped_ids):,}",
         f"-{reducible_variants:,}", f"{reducible_variants/len(active)*100:.1f}% further removed"),
        ("After exact-SQL de-dup", f"{final_after_sql_dedup:,}",
         f"-{sql_reducible:,}", f"{sql_reducible/len(deduped_ids)*100:.1f}% further removed"),
        ("After LLM parameterization", f"{final_after_llm:,}",
         f"-{llm_removable:,}", f"{llm_removable/final_after_sql_dedup*100:.1f}% further removed" if final_after_sql_dedup else ""),
    ]
    add_styled_table(
        doc,
        ["Stage", "Reports", "Change", "Impact"],
        full_funnel_rows, col_widths=[6, 2.5, 2.5, 5.5],
    )

    doc.add_paragraph()
    doc.add_paragraph(
        f"Full pipeline reduction: {total_inventory - final_after_llm:,} reports removed "
        f"({(total_inventory - final_after_llm)/total_inventory*100:.1f}% of inventory). "
        f"Final count: {final_after_llm:,} unique parameterized reports."
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
        (
            "Phase A - Validate & Communicate",
            "Share the retirement list (retire_reports.csv) with report owners and "
            "business stakeholders. Allow a 2-week review period for any objections."
        ),
        (
            "Phase B - Retire Full Families",
            f"Retire the {len(families_all_retire):,} entirely inactive families "
            f"({sum(len(r) for r in families_all_retire.values()):,} reports) first. "
            f"These have zero usage across all variants and carry the lowest risk."
        ),
        (
            "Phase C - Archive Personal Reports",
            f"Retire the {len(per_retire):,} personal reports. These are in individual "
            f"user profile folders and have no shared impact."
        ),
        (
            "Phase D - Archive Public Reports",
            f"Retire the {len(pub_retire):,} public reports in batches, prioritizing "
            f"older modification dates. Move to an archive folder and retain for 90 days."
        ),
        (
            "Phase E - Consolidate Partial Families",
            f"Review the {len(families_partial):,} partially active families. Retire "
            f"inactive members and evaluate active members for consolidation into "
            f"parameterized reports."
        ),
        (
            "Phase F - Clean Up Orphans",
            f"Re-run dependency analysis to identify newly orphaned metrics "
            f"({analysis_summary['unusedMetricCount']:,} already identified), "
            f"filters, and attributes."
        ),
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
         "Metrics, filters, prompts, attributes not referenced by any report/cube"),
        ("Stale objects", f"{analysis_summary['staleObjectCount']:,}",
         "Objects not modified in over 1 year"),
        ("Unused metrics", f"{analysis_summary['unusedMetricCount']:,}",
         "Metrics not used by any report or cube"),
        ("Enrichment errors", f"{analysis_summary['errorCount']:,}",
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
        f"Begin bulk retirement of {len(families_all_retire):,} entirely inactive families (Phase B) - lowest risk.",
        "Archive personal reports (Phase C) as these carry no shared impact.",
        f"Evaluate the {len(families_partial):,} partially active families for consolidation, "
        f"starting with the largest ones.",
        f"Consolidate the {len(active_dupe_groups):,} duplicate SQL groups ({sql_reducible:,} reducible reports) "
        f"into parameterized reports.",
        f"Resolve the {sql_stats['err_cube']:,} cube-sourced SQL gaps by publishing the source cubes "
        f"and re-extracting SQL, or by obtaining cube SQL from server admin tools.",
        f"Address the {analysis_summary['unusedMetricCount']:,} unused metrics to simplify the project schema.",
        f"Investigate the {analysis_summary['orphanCount']:,} orphaned objects for potential cleanup.",
        "Schedule recurring rationalization reviews (quarterly) to prevent report sprawl.",
    ]
    for step in next_steps:
        doc.add_paragraph(step, style="List Number")

    doc.add_heading("12.3 Deliverables", level=2)
    deliverables = [
        "This document - Rationalization summary and methodology",
        f"retire_reports.csv - Complete list of {len(retire):,} reports recommended for retirement",
        f"active_reports.json - Complete list of {len(active):,} reports to retain with usage statistics",
        f"parent_reports.csv - Parent-child family analysis ({len(all_families):,} families)",
        f"sql/ folder - Extracted SQL for {sql_stats['has_sql']:,} de-duped active reports",
        "analysis/duplicate_sql.json - Duplicate SQL groups with member report details",
        "rationalization_report.json - Full machine-readable report",
        "inventory/ folder - Raw inventory data by object category (JSON)",
        "analysis/ folder - Detailed analysis outputs",
    ]
    for d in deliverables:
        doc.add_paragraph(d, style="List Bullet")

    # ---------------------------------------------------------------
    # Save
    # ---------------------------------------------------------------
    out_path = output_dir / "Global_Insight_Rationalization_Summary.docx"
    doc.save(str(out_path))
    print(f"Document saved to: {out_path}")


def _base_name(r):
    name = r.get("name", "").strip()
    parts = name.rsplit(" - ", 1)
    if len(parts) == 2 and len(parts[0]) >= 10:
        return parts[0].strip()
    return name


if __name__ == "__main__":
    main()
