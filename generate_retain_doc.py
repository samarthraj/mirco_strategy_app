#!/usr/bin/env python3
"""Generate a Word document summarizing the rationalization process and findings."""

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


def build_report_families(active):
    """Group active reports into families by base name pattern."""
    groups = defaultdict(list)
    for r in active:
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
    output_dir = Path("mstr_rationalization_apr09")

    with open(output_dir / "telemetry" / "active_reports.json", "r", encoding="utf-8") as f:
        active = json.load(f)
    with open(output_dir / "telemetry" / "retire_reports.json", "r", encoding="utf-8") as f:
        retire = json.load(f)
    with open(output_dir / "analysis" / "summary.json", "r", encoding="utf-8") as f:
        analysis_summary = json.load(f)

    total_inventory = len(active) + len(retire)

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
    fuzzy = [r for r in active if r.get("matchTier") == "fuzzy"]

    families = build_report_families(active)
    total_in_families = sum(len(recs) for recs in families.values())
    standalone_count = len(active) - total_in_families
    reducible_variants = total_in_families - len(families)  # keep 1 per family
    consolidated_total = len(active) - reducible_variants

    # Build consolidated list for category breakdown
    standalone_groups = {b: recs for b, recs in
                         defaultdict(list, {b: [r] for b, r in
                         zip([r.get("name","").strip().rsplit(" - ",1)[0].strip()
                              if len(r.get("name","").strip().rsplit(" - ",1)) == 2
                              and len(r.get("name","").strip().rsplit(" - ",1)[0]) >= 10
                              else r.get("name","").strip()
                              for r in active], active)}).items()
                         if len(recs) == 1}
    consolidated_list = []
    for recs in standalone_groups.values():
        consolidated_list.extend(recs)
    for base, recs in families.items():
        parent = max(recs, key=lambda x: x.get("totalExecutions", 0))
        consolidated_list.append(parent)
    consol_pub = sum(1 for r in consolidated_list if classify(r) == "Public")
    consol_per = sum(1 for r in consolidated_list if classify(r) == "Personal")
    consol_oth = sum(1 for r in consolidated_list if classify(r) == "Other")

    # Top active reports by execution
    top_active = sorted(active, key=lambda x: -(x.get("totalExecutions") or 0))
    # Deduplicate by name for top list
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

    subtitle = doc.add_heading("Global Operational Project", level=1)
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("Rationalization Analysis & Recommendations")
    run.font.size = Pt(14)
    run.font.color.rgb = RGBColor(0x2F, 0x54, 0x96)

    for _ in range(2):
        doc.add_paragraph()

    info_lines = [
        f"Project: Global Operational (E77B77894C04BF0E6D244F9363CFAF64)",
        f"Environment: Ralph Lauren Analytics Sandbox",
        f"Date: {datetime.now().strftime('%B %d, %Y')}",
        f"Telemetry Window: Last 7 months (cutoff: September 11, 2025)",
    ]
    for line in info_lines:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(line)
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.add_page_break()

    # ---------------------------------------------------------------
    # Table of Contents placeholder
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
        "8. Retirement Recommendations",
        "9. Additional Findings & Next Steps",
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
        f"A comprehensive rationalization was performed on the Global Operational "
        f"project in the Ralph Lauren MicroStrategy Analytics environment. The "
        f"objective was to identify actively used reports, flag unused reports for "
        f"retirement, and provide actionable recommendations to reduce environment "
        f"complexity."
    )

    doc.add_paragraph(
        f"The project contains {analysis_summary['totalObjects']:,} total objects "
        f"across 11 categories, including {total_inventory:,} reports. Through a "
        f"three-phase process (inventory, telemetry matching, and rationalization), "
        f"we determined that {len(active):,} reports have confirmed active usage and "
        f"{len(retire):,} reports are recommended for retirement. Further analysis "
        f"identified 93 report families (324 reports that are variants of a common "
        f"parent), which can be consolidated to reduce the retained list to "
        f"{consolidated_total:,} unique reports - a {(total_inventory - consolidated_total)/total_inventory*100:.1f}% "
        f"total reduction."
    )

    doc.add_heading("Key Findings", level=2)
    key_findings = [
        ("Total reports inventoried", f"{total_inventory:,}"),
        ("", ""),
        ("Reports with active usage (telemetry matched)", f"{len(active):,}"),
        ("Reports to retire (no usage evidence)", f"{len(retire):,}"),
        ("", ""),
        ("Report families identified", f"{len(families):,}"),
        ("Variant reports reducible via consolidation", f"{reducible_variants:,}"),
        ("", ""),
        ("Final retained after consolidation", f"{consolidated_total:,}"),
        ("  - Public", f"{consol_pub:,}"),
        ("  - Personal", f"{consol_per:,}"),
        ("  - Other", f"{consol_oth:,}"),
        ("", ""),
        ("Total reduction (retire + consolidation)", f"{total_inventory - consolidated_total:,} ({(total_inventory - consolidated_total)/total_inventory*100:.1f}%)"),
    ]
    add_styled_table(doc, ["Metric", "Value"], key_findings, col_widths=[10, 4])

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 2. Process Overview
    # ---------------------------------------------------------------
    doc.add_heading("2. Process Overview", level=1)

    doc.add_paragraph(
        "The rationalization was conducted in three major phases, each building "
        "on the output of the previous:"
    )

    phases = [
        (
            "Phase 1: Inventory Collection",
            "Connected to the MicroStrategy REST API and enumerated all objects in the "
            "Global Operational project. For reports and cubes, enriched each record with "
            "its modeling definition (attributes, metrics, filters, source cube references). "
            "Resolved folder paths for all 4,134 objects via ancestor lookup."
        ),
        (
            "Phase 2: Telemetry Matching",
            "Loaded 2,270 telemetry records from server execution logs and matched them "
            "against the inventory using a three-tier strategy: exact name, full path, and "
            "fuzzy token-based matching. Reports with confirmed recent usage (within 7 months) "
            "were marked for retention; those with no usage evidence were flagged for retirement."
        ),
        (
            "Phase 3: Dependency Analysis & Rationalization",
            "Built a dependency graph across all object types (11,277 edges) to identify "
            "orphaned objects, unused metrics, stale objects, and high-impact shared components. "
            "Dependencies were inferred from report and cube definitions since the server's "
            "/dependents API endpoint was not available."
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
        f"discovered across the following categories:"
    )

    cat = analysis_summary.get("byCategory", {})
    cat_rows = [(k.replace("_", " ").title(), f"{v:,}")
                for k, v in sorted(cat.items(), key=lambda x: -x[1]) if v > 0]
    add_styled_table(doc, ["Object Category", "Count"], cat_rows, col_widths=[10, 4])

    doc.add_heading("3.3 Report Enrichment", level=2)
    doc.add_paragraph(
        f"For each of the {total_inventory:,} reports, the modeling definition was "
        f"fetched via GET /model/reports/{{id}}?showExpressionAs=tree. This provided:"
    )
    enrichment_items = [
        "Report subtype and source type (normal, cube-sourced, custom SQL)",
        "Attributes and metrics used by the report",
        "Filter expressions and their referenced objects",
        "Source cube ID for ICube-based reports",
        "Creation and modification timestamps",
    ]
    for item in enrichment_items:
        doc.add_paragraph(item, style="List Bullet")

    doc.add_paragraph(
        f"Enrichment completed successfully for {total_inventory - analysis_summary['errorCount']:,} "
        f"reports. {analysis_summary['errorCount']:,} reports encountered errors during "
        f"definition retrieval (typically due to permission restrictions or corrupted objects)."
    )

    doc.add_heading("3.4 Folder Path Resolution", level=2)
    doc.add_paragraph(
        f"Folder paths were resolved for all {analysis_summary['totalObjects']:,} objects "
        f"by fetching each object's ancestor hierarchy via GET /objects/{{id}}. This "
        f"enabled classification of reports as Public (under /Public Objects/) or "
        f"Personal (under /Profiles/{{user}}/My Reports/) and improved telemetry matching accuracy."
    )

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 4. Phase 2: Telemetry Matching
    # ---------------------------------------------------------------
    doc.add_heading("4. Phase 2: Telemetry Matching", level=1)

    doc.add_heading("4.1 Telemetry Data", level=2)
    doc.add_paragraph(
        "The telemetry dataset was exported from MicroStrategy server execution logs "
        "and contains 2,270 records representing report executions. Each record includes:"
    )
    tel_fields = [
        "Report name and folder path",
        "Executing user",
        "Object type (Grid Report, Dashboard, etc.)",
        "Total execution count and unique user count",
        "Error count and error rate",
        "Average and maximum execution time",
        "Last execution timestamp",
    ]
    for item in tel_fields:
        doc.add_paragraph(item, style="List Bullet")

    doc.add_paragraph(
        "After deduplication by report name (aggregating across users), "
        "1,102 unique report names were indexed for matching."
    )

    doc.add_heading("4.2 Matching Strategy", level=2)
    doc.add_paragraph(
        "Matching inventory reports to telemetry records required a multi-tier approach "
        "because report names can differ between the REST API and telemetry exports "
        "(e.g., leading asterisks, abbreviations, seasonal suffixes, copy variants, "
        "extra whitespace). Three tiers were applied sequentially:"
    )

    # Tier 1
    p = doc.add_paragraph()
    p.add_run("Tier 1 - Exact Name Match").bold = True
    doc.add_paragraph(
        "Both the inventory report name and telemetry report name are normalized "
        "(lowercased, whitespace collapsed, leading asterisks stripped) and compared "
        "for exact equality. This is the highest-confidence match.",
    )

    # Tier 2
    p = doc.add_paragraph()
    p.add_run("Tier 2 - Full Path Match").bold = True
    doc.add_paragraph(
        "A full path is constructed by combining the folder path and report name from "
        "both sources (e.g., \"/Global Operational/Public Objects/Reports/GFE+/ReportName\"). "
        "Normalized full paths are compared. This resolves cases where reports share names "
        "but reside in different folders.",
    )

    # Tier 3
    p = doc.add_paragraph()
    p.add_run("Tier 3 - Fuzzy Match").bold = True
    doc.add_paragraph(
        "For inventory reports still unmatched after Tiers 1 and 2, three fuzzy "
        "strategies are applied:"
    )
    fuzzy_strategies = [
        "Substring containment: if either name fully contains the other (minimum 8 characters to avoid false positives), the match is accepted with a boosted score of 0.70 or higher.",
        "Same-folder token overlap: if both reports share the same folder path, a Jaccard token similarity is computed on their names with a 0.15 score boost, recognizing that reports in the same folder are more likely to be related.",
        "Global token overlap: Jaccard similarity is computed between word tokens of the inventory name and every telemetry name. The best match scoring 0.60 or above is accepted.",
    ]
    for s in fuzzy_strategies:
        doc.add_paragraph(s, style="List Bullet")

    doc.add_paragraph(
        "Jaccard similarity is defined as |A intersection B| / |A union B| where A and B are "
        "the sets of alphanumeric tokens extracted from each name. A threshold of 0.60 ensures "
        "that at least 60% of meaningful words overlap between the names."
    )

    doc.add_heading("4.3 Matching Results", level=2)
    doc.add_paragraph(
        f"The three-tier matching process produced the following results:"
    )
    match_results = [
        ("Total inventory reports", f"{total_inventory:,}"),
        ("", ""),
        ("Tier 1: Exact name match", f"{len(exact):,}"),
        ("Tier 2: Full path match", "0"),
        ("Tier 3: Fuzzy match", f"{len(fuzzy):,}"),
        ("Total matched", f"{len(active):,}"),
        ("", ""),
        ("Unmatched (no telemetry evidence)", f"{len(retire):,}"),
    ]
    add_styled_table(doc, ["Matching Stage", "Reports"], match_results, col_widths=[10, 4])

    doc.add_paragraph()
    doc.add_paragraph(
        f"All {len(active):,} matched reports had their last execution within the past "
        f"7 months (after September 11, 2025), confirming active usage. No matched "
        f"reports fell outside the retention window."
    )

    doc.add_heading("4.4 Retention Criteria", level=2)
    doc.add_paragraph("A report is retained if:")
    doc.add_paragraph(
        "It matches a telemetry record via any of the three tiers, AND its last "
        "execution timestamp is within 7 months of the analysis date (April 9, 2026).",
        style="List Bullet",
    )
    doc.add_paragraph()
    doc.add_paragraph("A report is recommended for retirement if:")
    retire_criteria = [
        "No telemetry match was found across all three tiers (no evidence of execution)",
        "A telemetry match exists but the last execution is older than 7 months",
        "A telemetry match exists but has no valid execution timestamp",
    ]
    for c in retire_criteria:
        doc.add_paragraph(c, style="List Bullet")

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 5. Phase 3: Dependency Analysis & Rationalization
    # ---------------------------------------------------------------
    doc.add_heading("5. Phase 3: Dependency Analysis & Rationalization", level=1)

    doc.add_heading("5.1 Dependency Graph Construction", level=2)
    doc.add_paragraph(
        f"A dependency graph was constructed to understand relationships between "
        f"objects in the project. The server's /objects/{{id}}/dependents REST API "
        f"endpoint returned HTTP 404 (not supported on this server version), so "
        f"dependencies were inferred from the report and cube definitions collected "
        f"during Phase 1."
    )
    doc.add_paragraph("The inference logic extracted three types of relationships:")
    dep_types = [
        "Report/Cube uses Attribute: extracted from the attributes list in each report/cube definition",
        "Report/Cube uses Metric: extracted from the metrics list in each report/cube definition",
        "Report/Cube uses Filter: extracted by recursively walking the filter expression tree and collecting referenced object IDs",
    ]
    for d in dep_types:
        doc.add_paragraph(d, style="List Bullet")

    doc.add_paragraph(
        f"This produced {analysis_summary['totalEdges']:,} directed edges representing "
        f"\"object A depends on object B\" relationships across the entire project."
    )

    doc.add_heading("5.2 Rationalization Analysis", level=2)
    doc.add_paragraph(
        "Using the dependency graph and inventory metadata, five analyses were performed:"
    )

    analyses = [
        (
            "Orphan Detection",
            f"{analysis_summary['orphanCount']:,}",
            "Objects with zero dependents (nothing references them), excluding leaf "
            "objects like reports, documents, and cubes which are consumption endpoints "
            "by design. Orphaned metrics, filters, attributes, and prompts are candidates "
            "for cleanup."
        ),
        (
            "Stale Object Detection",
            f"{analysis_summary['staleObjectCount']:,}",
            "Objects whose last modification date is more than 365 days ago. These objects "
            "have not been updated in over a year and may no longer reflect current business logic."
        ),
        (
            "Unused Metric Detection",
            f"{analysis_summary['unusedMetricCount']:,}",
            "Metrics that are not referenced by any report or cube in the project. "
            "These represent potential dead weight that increases project complexity "
            "without serving any consumer."
        ),
        (
            "Duplicate SQL Detection",
            f"{analysis_summary['duplicateSqlGroupCount']:,}",
            "Reports and cubes are grouped by normalized SQL hash to identify objects "
            "executing identical queries. Note: SQL extraction was not included in this "
            "run, so no duplicates were detected."
        ),
        (
            "High-Impact Object Identification",
            f"{analysis_summary['highImpactCount']:,}",
            "Objects with the most dependents (highest downstream impact). Changes to "
            "these objects affect the most reports and cubes, making them critical to "
            "protect during any cleanup activity."
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
        f"Of the {total_inventory:,} reports in the Global Operational project, "
        f"{len(active):,} ({len(active)/total_inventory*100:.1f}%) are confirmed active "
        f"and should be retained. The remaining {len(retire):,} ({len(retire)/total_inventory*100:.1f}%) "
        f"have no evidence of usage in the telemetry window and are recommended for retirement."
    )

    outcome_rows = [
        ("", "Retain", "Retire", "Total"),
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
        "The following are the most heavily executed reports in the retained list, "
        "representing the core reporting workload of the project:"
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
        f"Among the {len(active):,} retained reports, many are variants of a common "
        f"parent report (e.g., the same report template applied to different brands, "
        f"seasons, or regions). By grouping reports that share a common base name "
        f"(splitting on the last \" - \" separator), we identified:"
    )

    family_summary = [
        ("Report families (2+ variants)", f"{len(families):,}"),
        ("Total reports in families", f"{total_in_families:,}"),
        ("Standalone reports (no variants)", f"{standalone_count:,}"),
        ("Avg variants per family", f"{total_in_families / len(families):.1f}" if families else "N/A"),
    ]
    add_styled_table(doc, ["Metric", "Value"], family_summary, col_widths=[10, 4])

    doc.add_paragraph()
    doc.add_paragraph(
        f"{total_in_families:,} of the {len(active):,} retained reports ({total_in_families/len(active)*100:.0f}%) "
        f"belong to a report family. These families represent consolidation opportunities "
        f"where multiple seasonal or regional variants could potentially be replaced by a "
        f"single parameterized report with appropriate prompts."
    )

    doc.add_heading("7.1 Largest Report Families", level=2)
    doc.add_paragraph(
        "The following table shows the largest report families by number of variants:"
    )

    sorted_families = sorted(families.items(), key=lambda x: -len(x[1]))
    family_rows = []
    for base, recs in sorted_families[:20]:
        max_exec = max((r.get("totalExecutions") or 0) for r in recs)
        all_users = max((r.get("totalUsers") or 0) for r in recs)
        family_rows.append((
            base[:55],
            len(recs),
            f"{max_exec:,}",
            all_users,
        ))
    add_styled_table(
        doc,
        ["Base Report Name", "Variants", "Max Executions", "Users"],
        family_rows, col_widths=[9, 2, 3, 2],
    )

    doc.add_paragraph()
    doc.add_paragraph(
        "The largest family, \"GFE085a - Projections by PD\", has 39 variants covering "
        "different brands and categories (GOLF, MENS, CPOLO, Lauren Acc, etc.). With "
        "9,986 executions and 103 users, this is the highest-traffic report in the "
        "project and the strongest candidate for consolidation into a single "
        "parameterized report."
    )

    doc.add_heading("7.2 Consolidation Impact", level=2)
    doc.add_paragraph(
        f"If each report family is consolidated into a single parameterized parent "
        f"report (keeping the variant with the highest execution count), the total "
        f"retained report count drops significantly:"
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
        f"The consolidated retain list of {consolidated_total:,} reports breaks down as follows:"
    )

    consol_breakdown = [
        ("Public", f"{consol_pub:,}"),
        ("Personal", f"{consol_per:,}"),
        ("Other", f"{consol_oth:,}"),
        ("Total", f"{consolidated_total:,}"),
    ]
    add_styled_table(doc, ["Category", "Count"], consol_breakdown, col_widths=[10, 4])

    doc.add_paragraph()
    doc.add_paragraph(
        f"This represents an overall reduction of {total_inventory - consolidated_total:,} "
        f"reports ({(total_inventory - consolidated_total)/total_inventory*100:.1f}%) from the original "
        f"inventory of {total_inventory:,} reports down to {consolidated_total:,} unique, actively "
        f"used reports."
    )

    doc.add_paragraph()
    doc.add_paragraph(
        "Note: Family consolidation requires converting variant-specific reports into "
        "a single report with appropriate prompts or parameters. This is a design "
        "activity that should be planned with report authors to ensure no functionality "
        "is lost during the transition."
    )

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 8. Retirement Recommendations
    # ---------------------------------------------------------------
    doc.add_heading("8. Retirement Recommendations", level=1)

    doc.add_heading("8.1 Reports to Retire", level=2)
    doc.add_paragraph(
        f"{len(retire):,} reports are recommended for retirement. All of these reports "
        f"had zero telemetry matches across all three matching tiers, meaning there is "
        f"no evidence they were executed during the telemetry window."
    )

    retire_breakdown = [
        ("Public reports", f"{len(pub_retire):,}",
         f"{len(pub_retire)/len(retire)*100:.1f}%"),
        ("Personal reports", f"{len(per_retire):,}",
         f"{len(per_retire)/len(retire)*100:.1f}%"),
        ("Other", f"{len(oth_retire):,}",
         f"{len(oth_retire)/len(retire)*100:.1f}%"),
        ("Total", f"{len(retire):,}", "100%"),
    ]
    add_styled_table(doc, ["Category", "Count", "% of Retired"],
                     retire_breakdown, col_widths=[7, 3, 4])

    doc.add_heading("8.2 Recommended Approach", level=2)
    doc.add_paragraph(
        "We recommend a phased approach to retirement:"
    )

    steps = [
        (
            "Phase A - Validate & Communicate",
            "Share the retirement list (retire_reports.csv) with report owners and "
            "business stakeholders. Allow a 2-week review period for any objections. "
            "Some reports may be used through scheduled jobs or external integrations "
            "not captured in the telemetry window."
        ),
        (
            "Phase B - Archive Personal Reports",
            f"Retire the {len(per_retire):,} personal reports first. These are in individual "
            f"user profile folders and have no shared impact. Move to a designated archive "
            f"folder rather than deleting outright."
        ),
        (
            "Phase C - Archive Public Reports",
            f"Retire the {len(pub_retire):,} public reports in batches, prioritizing those "
            f"with older modification dates. Move to an archive folder with a clear naming "
            f"convention (e.g., \"_Archive/YYYY-MM/\") and retain for 90 days before permanent deletion."
        ),
        (
            "Phase D - Clean Up Orphans",
            f"After report retirement, re-run the dependency analysis to identify newly "
            f"orphaned metrics ({analysis_summary['unusedMetricCount']:,} already identified), "
            f"filters, and attributes that are no longer referenced."
        ),
    ]
    for step_title, step_desc in steps:
        p = doc.add_paragraph()
        p.add_run(step_title + ": ").bold = True
        p.add_run(step_desc)

    doc.add_page_break()

    # ---------------------------------------------------------------
    # 9. Additional Findings & Next Steps
    # ---------------------------------------------------------------
    doc.add_heading("9. Additional Findings & Next Steps", level=1)

    doc.add_heading("9.1 Project Health Overview", level=2)
    doc.add_paragraph(
        "Beyond report retirement, the rationalization analysis revealed several "
        "areas for further optimization:"
    )

    health_rows = [
        ("Orphaned objects", f"{analysis_summary['orphanCount']:,}",
         "Metrics, filters, prompts, attributes, and facts not referenced by any report or cube"),
        ("Stale objects", f"{analysis_summary['staleObjectCount']:,}",
         "Objects not modified in over 1 year (across all categories)"),
        ("Unused metrics", f"{analysis_summary['unusedMetricCount']:,}",
         "Metrics not used by any report or cube"),
        ("Enrichment errors", f"{analysis_summary['errorCount']:,}",
         "Objects that could not be fully analyzed (permissions or corruption)"),
    ]
    add_styled_table(
        doc,
        ["Finding", "Count", "Description"],
        health_rows, col_widths=[4, 2, 10.5],
    )

    doc.add_heading("9.2 Recommended Next Steps", level=2)
    next_steps = [
        "Review the retirement list with business stakeholders and complete Phase A validation within 2 weeks.",
        "Begin archiving personal reports (Phase B) as these carry the lowest risk of business impact.",
        "Evaluate the 93 report families for consolidation opportunities, starting with \"GFE085a - Projections by PD\" (39 variants).",
        "Address the 393 unused metrics to simplify the project schema and improve report authoring experience.",
        "Investigate the 2,050 orphaned objects for potential cleanup, prioritizing orphaned filters and prompts.",
        "Consider enabling SQL extraction in a follow-up run to detect duplicate report queries.",
        "Schedule recurring rationalization reviews (quarterly) to prevent report sprawl from recurring.",
    ]
    for i, step in enumerate(next_steps, 1):
        doc.add_paragraph(f"{step}", style="List Number")

    doc.add_heading("9.3 Deliverables", level=2)
    doc.add_paragraph("The following files are included with this report:")
    deliverables = [
        "This document - Rationalization summary and methodology",
        "retire_reports.csv - Complete list of 608 reports recommended for retirement with IDs, names, paths, and owners",
        "active_reports.csv - Complete list of 739 reports to retain with usage statistics",
        "rationalization_report.json - Full machine-readable report including dependency graph, orphans, and all analysis results",
        "inventory/ folder - Raw inventory data by object category (JSON)",
        "analysis/ folder - Detailed analysis outputs (orphans, stale objects, unused metrics, high-impact objects)",
    ]
    for d in deliverables:
        doc.add_paragraph(d, style="List Bullet")

    # ---------------------------------------------------------------
    # Save
    # ---------------------------------------------------------------
    out_path = output_dir / "Global_Operational_Rationalization_Summary.docx"
    doc.save(str(out_path))
    print(f"Document saved to: {out_path}")


if __name__ == "__main__":
    main()
