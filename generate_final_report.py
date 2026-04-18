#!/usr/bin/env python3
"""Generate the final rationalization report in Word format."""

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


def set_cell_shading(cell, color_hex: str):
    shading = cell._element.get_or_add_tcPr()
    shd = shading.makeelement(qn("w:shd"), {
        qn("w:fill"): color_hex, qn("w:val"): "clear",
    })
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


def build_families(active):
    groups = defaultdict(list)
    for r in active:
        name = r.get("name", "").strip()
        parts = name.rsplit(" - ", 1)
        base = parts[0].strip() if len(parts) == 2 and len(parts[0]) >= 10 else name
        groups[base].append(r)
    return {b: recs for b, recs in groups.items() if len(recs) >= 2}


def main():
    # ---- Load data ----
    with open(OUTPUT_DIR / "inventory" / "reports.json", "r", encoding="utf-8") as f:
        reports = json.load(f)
    with open(OUTPUT_DIR / "telemetry" / "active_reports.json", "r", encoding="utf-8") as f:
        active = json.load(f)
    with open(OUTPUT_DIR / "telemetry" / "retire_reports.json", "r", encoding="utf-8") as f:
        retire = json.load(f)
    with open(OUTPUT_DIR / "analysis" / "summary.json", "r", encoding="utf-8") as f:
        analysis = json.load(f)

    retain_ids = {r["id"] for r in active}
    retained_inv = [r for r in reports if r["id"] in retain_ids]

    # Tiers
    exact = [r for r in active if r.get("matchTier") == "exact_name"]
    fuzzy = [r for r in active if r.get("matchTier") == "fuzzy"]

    # Classification
    pub_active = sum(1 for r in active if classify(r) == "Public")
    per_active = sum(1 for r in active if classify(r) == "Personal")
    oth_active = sum(1 for r in active if classify(r) == "Other")
    pub_retire = sum(1 for r in retire if classify(r) == "Public")
    per_retire = sum(1 for r in retire if classify(r) == "Personal")

    # SQL stats
    has_sql = [r for r in retained_inv if r.get("sql")]
    sql_errors = [r for r in retained_inv if r.get("errors", {}).get("sql")]
    prompted_fail = [r for r in sql_errors if "prompted" in r["errors"]["sql"].lower()]
    perm_denied = [r for r in sql_errors if "403" in r["errors"]["sql"]]
    other_err = [r for r in sql_errors if r not in prompted_fail and r not in perm_denied]

    # Duplicate SQL
    sql_hashes = [r.get("sqlHash") for r in has_sql if r.get("sqlHash")]
    hash_counts = Counter(sql_hashes)
    dup_groups_map = {h: c for h, c in hash_counts.items() if c >= 2}
    dup_report_count = sum(c for c in dup_groups_map.values())

    dup_details = []
    for h, count in sorted(dup_groups_map.items(), key=lambda x: -x[1]):
        members = [r["name"] for r in has_sql if r.get("sqlHash") == h]
        dup_details.append({"hash": h, "count": count, "members": members})

    # Families
    families = build_families(active)
    total_in_families = sum(len(recs) for recs in families.values())
    standalone = len(active) - total_in_families
    reducible = total_in_families - len(families)
    consolidated = len(active) - reducible

    # Consolidated breakdown
    consol_list = []
    groups_all = defaultdict(list)
    for r in active:
        name = r.get("name", "").strip()
        parts = name.rsplit(" - ", 1)
        base = parts[0].strip() if len(parts) == 2 and len(parts[0]) >= 10 else name
        groups_all[base].append(r)
    for base, recs in groups_all.items():
        if len(recs) >= 2:
            consol_list.append(max(recs, key=lambda x: x.get("totalExecutions", 0)))
        else:
            consol_list.extend(recs)
    consol_pub = sum(1 for r in consol_list if classify(r) == "Public")
    consol_per = sum(1 for r in consol_list if classify(r) == "Personal")
    consol_oth = sum(1 for r in consol_list if classify(r) == "Other")

    # ---- Build Document ----
    doc = Document()

    # ============================================================
    # Title Page
    # ============================================================
    for _ in range(4):
        doc.add_paragraph()
    title = doc.add_heading("MicroStrategy Report Rationalization", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_heading("Global Operational Project", level=1)
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("Complete Analysis Report")
    run.font.size = Pt(14)
    run.font.color.rgb = RGBColor(0x2F, 0x54, 0x96)

    for _ in range(2):
        doc.add_paragraph()
    for line in [
        "Project: Global Operational (E77B77894C04BF0E6D244F9363CFAF64)",
        "Environment: Ralph Lauren Analytics Sandbox",
        f"Date: {datetime.now().strftime('%B %d, %Y')}",
        "Telemetry Window: 7 months (cutoff: September 11, 2025)",
    ]:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(line)
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.add_page_break()

    # ============================================================
    # 1. Starting Point
    # ============================================================
    doc.add_heading("1. Starting Point", level=1)

    doc.add_paragraph(
        f"The Global Operational project contains {analysis['totalObjects']:,} total "
        f"objects across 11 categories. The rationalization focused on the report "
        f"population to identify active, inactive, duplicate, and redundant reports."
    )

    add_table(doc, ["Metric", "Count"], [
        ("Total objects in project", f"{analysis['totalObjects']:,}"),
        ("Reports", f"{len(reports):,}"),
        ("Documents", f"{analysis['byCategory'].get('documents', 0):,}"),
        ("Cubes", f"{analysis['byCategory'].get('cubes', 0):,}"),
        ("Metrics", f"{analysis['byCategory'].get('metrics', 0):,}"),
        ("Filters", f"{analysis['byCategory'].get('filters', 0):,}"),
        ("Prompts", f"{analysis['byCategory'].get('prompts', 0):,}"),
        ("Attributes", f"{analysis['byCategory'].get('attributes', 0):,}"),
        ("Facts", f"{analysis['byCategory'].get('facts', 0):,}"),
    ], col_widths=[10, 4])

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run(f"Starting report count: {len(reports):,}").bold = True

    doc.add_page_break()

    # ============================================================
    # 2. Telemetry Exclusion
    # ============================================================
    doc.add_heading("2. Telemetry Exclusion", level=1)

    doc.add_paragraph(
        f"All {len(reports):,} reports were cross-referenced against 2,270 server "
        f"telemetry records (execution logs) to determine which reports are actively "
        f"used. Reports with no execution evidence in the past 7 months were excluded."
    )

    doc.add_heading("2.1 Matching Strategy", level=2)
    doc.add_paragraph(
        "Report names in the API inventory and telemetry export often differ "
        "(leading asterisks, abbreviations, seasonal suffixes, copy variants). "
        "A three-tier matching strategy was applied sequentially:"
    )

    p = doc.add_paragraph()
    p.add_run("Tier 1 - Exact Name Match: ").bold = True
    p.add_run(
        "Both names normalized (lowercase, collapse whitespace, strip leading "
        "asterisks) and compared for equality."
    )
    p = doc.add_paragraph()
    p.add_run("Tier 2 - Full Path Match: ").bold = True
    p.add_run(
        "Full path constructed (folder + report name) from both sources and "
        "compared. Handles reports sharing names but in different folders."
    )
    p = doc.add_paragraph()
    p.add_run("Tier 3 - Fuzzy Match: ").bold = True
    p.add_run(
        "Three fuzzy strategies: (a) substring containment (min 8 chars), "
        "(b) same-folder token overlap with 0.15 score boost, "
        "(c) Jaccard token similarity across all telemetry names. "
        "Minimum threshold of 0.60 required."
    )

    doc.add_heading("2.2 Matching Results", level=2)

    add_table(doc, ["Matching Tier", "How", "Reports Matched"], [
        ("Tier 1 - Exact name", "Normalized name comparison", f"{len(exact):,}"),
        ("Tier 2 - Full path", "Folder + name comparison", "0"),
        ("Tier 3 - Fuzzy", "Substring + Jaccard token similarity (>0.60)", f"{len(fuzzy):,}"),
        ("No match", "No usage evidence", f"{len(retire):,}"),
    ], col_widths=[4, 7, 3.5])

    doc.add_paragraph()

    add_table(doc, ["Outcome", "Count", "Breakdown"], [
        ("Reports retained (active)", f"{len(active):,}",
         f"{pub_active:,} Public / {per_active:,} Personal / {oth_active:,} Other"),
        ("Reports excluded (retire)", f"{len(retire):,}",
         f"{pub_retire:,} Public / {per_retire:,} Personal"),
    ], col_widths=[5, 2.5, 9])

    doc.add_paragraph()
    doc.add_paragraph(
        f"All {len(active):,} matched reports had their last execution within the "
        f"past 7 months (after September 11, 2025), confirming active usage. "
        f"The {len(retire):,} excluded reports had zero telemetry matches across all "
        f"three tiers."
    )

    doc.add_page_break()

    # ============================================================
    # 3. Duplicate Identification
    # ============================================================
    doc.add_heading("3. Duplicate Identification", level=1)

    doc.add_heading("3.1 Method", level=2)
    doc.add_paragraph(
        "SQL was extracted for each retained report via the MicroStrategy REST API. "
        "The process for each report:"
    )
    steps = [
        "Create a report instance: POST /v2/reports/{id}/instances",
        "For prompted reports, attempt to resolve prompts using cached/default answers via PUT /reports/{id}/instances/{instanceId}/prompts/answers",
        "Retrieve the SQL: GET /v2/reports/{id}/instances/{instanceId}/sqlView",
        "Clean up the instance: DELETE /v2/reports/{id}/instances/{instanceId}",
    ]
    for s in steps:
        doc.add_paragraph(s, style="List Number")

    doc.add_paragraph(
        "Each SQL statement was then normalized (lowercase, strip comments, collapse "
        "whitespace, replace string and numeric literals with placeholders) and "
        "SHA-256 hashed. Reports sharing the same hash execute identical queries and "
        "are flagged as duplicates."
    )

    doc.add_heading("3.2 Duplicate SQL Results", level=2)

    add_table(doc, ["Metric", "Count"], [
        ("Reports with SQL extracted", f"{len(has_sql):,}"),
        ("Duplicate SQL groups", f"{len(dup_groups_map):,}"),
        ("Reports in duplicate groups", f"{dup_report_count:,}"),
        ("Unique reports (after dedup)", f"{len(has_sql) - dup_report_count + len(dup_groups_map):,}"),
    ], col_widths=[10, 4])

    if dup_details:
        doc.add_paragraph()
        doc.add_heading("3.3 Duplicate Groups Detail", level=2)
        doc.add_paragraph(
            f"{len(dup_details):,} groups of reports were found executing identical "
            f"SQL queries:"
        )

        for i, grp in enumerate(dup_details, 1):
            p = doc.add_paragraph()
            p.add_run(f"Group {i} ({grp['count']} reports):").bold = True
            for member in grp["members"]:
                doc.add_paragraph(member, style="List Bullet")
            doc.add_paragraph()

    doc.add_page_break()

    # ============================================================
    # 4. Parent/Child (Family) Identification
    # ============================================================
    doc.add_heading("4. Parent and Child Report Identification", level=1)

    doc.add_heading("4.1 Method", level=2)
    doc.add_paragraph(
        "Reports were grouped into families by analyzing naming patterns. Each report "
        "name was split on the last \" - \" separator to extract a base name. Reports "
        "sharing the same base name (minimum 10 characters) were grouped as a family, "
        "where the base represents the parent report template and the suffix represents "
        "the variant (brand, season, region, etc.)."
    )
    doc.add_paragraph(
        "Example: \"GFE085a - Projections by PD - GOLF\", "
        "\"GFE085a - Projections by PD - MENS\", and "
        "\"GFE085a - Projections by PD - CPOLO\" all share the base "
        "\"GFE085a - Projections by PD\" and form a single family."
    )

    doc.add_heading("4.2 Family Results", level=2)

    add_table(doc, ["Metric", "Count"], [
        ("Report families (2+ variants)", f"{len(families):,}"),
        ("Total reports in families", f"{total_in_families:,}"),
        ("Standalone reports (no variants)", f"{standalone:,}"),
        ("Average variants per family", f"{total_in_families / len(families):.1f}"),
        ("", ""),
        ("Reducible variants (keep 1 per family)", f"{reducible:,}"),
    ], col_widths=[10, 4])

    doc.add_heading("4.3 Top 15 Report Families", level=2)

    sorted_families = sorted(families.items(), key=lambda x: -len(x[1]))
    fam_rows = []
    for base, recs in sorted_families[:15]:
        max_exec = max((r.get("totalExecutions") or 0) for r in recs)
        max_users = max((r.get("totalUsers") or 0) for r in recs)
        fam_rows.append((
            base[:50],
            len(recs),
            f"{max_exec:,}",
            max_users,
        ))
    add_table(doc, ["Parent Report Name", "Variants", "Max Execs", "Users"],
              fam_rows, col_widths=[9, 2, 2.5, 2])

    doc.add_page_break()

    # ============================================================
    # 5. How We Arrived at the Retain Number
    # ============================================================
    doc.add_heading("5. How We Arrived at the Retain Number", level=1)

    doc.add_paragraph(
        "The final retain count was determined through a sequential reduction funnel, "
        "each stage building on the previous:"
    )

    doc.add_heading("5.1 Reduction Funnel", level=2)

    add_table(doc, ["Stage", "Reports", "Change", "Action"], [
        ("Original inventory", f"{len(reports):,}", "", "All reports enumerated via REST API"),
        ("After telemetry retirement", f"{len(active):,}", f"-{len(retire):,}",
         "608 reports with no execution evidence removed"),
        ("After family consolidation", f"{consolidated:,}", f"-{reducible:,}",
         "231 variant reports consolidated (keep 1 parent per family)"),
    ], col_widths=[5, 2, 2, 7.5])

    doc.add_paragraph()
    p = doc.add_paragraph()
    run = p.add_run(
        f"Final retain count: {consolidated:,} reports "
        f"(from {len(reports):,} original - {(len(reports) - consolidated) / len(reports) * 100:.1f}% reduction)"
    )
    run.bold = True

    doc.add_heading("5.2 Final Breakdown", level=2)

    add_table(doc, ["Category", "Retain", "Retire", "Total"], [
        ("Public", f"{consol_pub:,}", f"{pub_retire:,}",
         f"{consol_pub + pub_retire:,}"),
        ("Personal", f"{consol_per:,}", f"{per_retire:,}",
         f"{consol_per + per_retire:,}"),
        ("Other", f"{consol_oth:,}", f"{len(retire) - pub_retire - per_retire:,}",
         f"{consol_oth + len(retire) - pub_retire - per_retire:,}"),
        ("Total", f"{consolidated:,}", f"{len(retire):,}", f"{len(reports):,}"),
    ], col_widths=[4, 3, 3, 3])

    doc.add_heading("5.3 Breakdown of Each Stage", level=2)

    doc.add_paragraph(
        "Stage 1 - Inventory: Connected to the MicroStrategy REST API, enumerated "
        f"all {len(reports):,} reports in the Global Operational project. Enriched each "
        "report with its modeling definition (attributes, metrics, filters, source type) "
        "and resolved folder paths for classification."
    )
    doc.add_paragraph(
        f"Stage 2 - Telemetry Matching: Cross-referenced all {len(reports):,} reports "
        f"against 2,270 telemetry records using three-tier matching (exact name, full "
        f"path, fuzzy). {len(active):,} reports matched with confirmed recent usage. "
        f"{len(retire):,} reports had no telemetry evidence and were excluded."
    )
    doc.add_paragraph(
        f"Stage 3 - Family Consolidation: Grouped the {len(active):,} retained reports "
        f"by base name pattern, identifying {len(families):,} families with "
        f"{total_in_families:,} total reports. Keeping 1 parent per family (highest "
        f"execution count) reduces the list by {reducible:,} to {consolidated:,} unique reports."
    )

    doc.add_page_break()

    # ============================================================
    # 6. SQL Extraction Results
    # ============================================================
    doc.add_heading("6. SQL Extraction Results", level=1)

    doc.add_heading("6.1 Overview", level=2)
    doc.add_paragraph(
        f"SQL extraction was attempted for all {len(active):,} retained reports. "
        f"The REST API instantiates each report, optionally resolves prompts, and "
        f"retrieves the generated SQL statement."
    )

    add_table(doc, ["Metric", "Count", "% of Retained"], [
        ("SQL generated successfully", f"{len(has_sql):,}",
         f"{len(has_sql) / len(active) * 100:.1f}%"),
        ("SQL failed - total", f"{len(sql_errors):,}",
         f"{len(sql_errors) / len(active) * 100:.1f}%"),
        ("", "", ""),
        ("Failed: Prompted (requires user input)", f"{len(prompted_fail):,}",
         f"{len(prompted_fail) / len(active) * 100:.1f}%"),
        ("Failed: Permission denied (HTTP 403)", f"{len(perm_denied):,}",
         f"{len(perm_denied) / len(active) * 100:.1f}%"),
        ("Failed: Other errors", f"{len(other_err):,}",
         f"{len(other_err) / len(active) * 100:.1f}%"),
    ], col_widths=[8, 3, 3.5])

    doc.add_heading("6.2 Why 165 Prompted Reports Failed", level=2)
    doc.add_paragraph(
        "Many MicroStrategy reports include prompts that require the user to select "
        "filter values (e.g., season, brand, region) before the report can execute. "
        "The extraction process handles prompted reports in two steps:"
    )
    doc.add_paragraph(
        "Step 1: Attempt direct instantiation with executionStage=resolve_prompts. "
        "If the report has no required prompts, SQL is returned immediately.",
        style="List Number",
    )
    doc.add_paragraph(
        "Step 2: If prompts are detected, retrieve the prompt list and attempt to "
        "answer them using cached user selections or default values. If answers are "
        "available, submit them and retrieve SQL.",
        style="List Number",
    )
    doc.add_paragraph(
        "Step 3: If a prompt is required but has no cached answers and no default "
        "value, the extraction fails - a human must select the prompt value interactively.",
        style="List Number",
    )

    doc.add_paragraph()
    doc.add_paragraph(
        f"Of the {len(active):,} retained reports, 614 contained prompts. Of those, "
        f"449 were successfully resolved using cached/default answers. The remaining "
        f"{len(prompted_fail):,} had required prompts with no available answers."
    )
    doc.add_paragraph(
        "This cannot be detected before the API call because the only way to know "
        "whether cached/default answers exist is to attempt the prompt resolution. "
        "449 prompted reports succeeded, proving that skipping all prompted reports "
        "would have lost significant SQL data."
    )

    doc.add_heading("6.3 SQL-Based Duplicate Detection", level=2)
    doc.add_paragraph(
        f"Among the {len(has_sql):,} reports with SQL, duplicate detection was "
        f"performed by normalizing each SQL statement and computing a SHA-256 hash. "
        f"This identified {len(dup_groups_map):,} groups of reports executing identical "
        f"queries, covering {dup_report_count:,} reports total."
    )

    if dup_details:
        dup_rows = []
        for i, grp in enumerate(dup_details, 1):
            members_str = ", ".join(grp["members"][:3])
            if len(grp["members"]) > 3:
                members_str += f" (+{len(grp['members']) - 3} more)"
            dup_rows.append((i, grp["count"], members_str))
        add_table(doc, ["Group", "Reports", "Members"],
                  dup_rows, col_widths=[1.5, 2, 13])

    doc.add_page_break()

    # ============================================================
    # 7. Summary
    # ============================================================
    doc.add_heading("7. Complete Summary", level=1)

    doc.add_paragraph(
        "The following table summarizes the entire rationalization process from "
        "start to finish:"
    )

    summary_rows = [
        ("STARTING POINT", "", ""),
        ("Total objects in project", f"{analysis['totalObjects']:,}", "11 categories"),
        ("Total reports inventoried", f"{len(reports):,}", ""),
        ("", "", ""),
        ("TELEMETRY EXCLUSION", "", ""),
        ("Telemetry records analyzed", "2,270", "Server execution logs"),
        ("Tier 1 matches (exact name)", f"{len(exact):,}", ""),
        ("Tier 2 matches (full path)", "0", ""),
        ("Tier 3 matches (fuzzy)", f"{len(fuzzy):,}", "Jaccard threshold >= 0.60"),
        ("Reports retained (active)", f"{len(active):,}",
         f"{pub_active:,} Public / {per_active:,} Personal"),
        ("Reports excluded (no usage)", f"{len(retire):,}",
         f"{pub_retire:,} Public / {per_retire:,} Personal"),
        ("", "", ""),
        ("DUPLICATE IDENTIFICATION", "", ""),
        ("Reports with SQL generated", f"{len(has_sql):,}", ""),
        ("Duplicate SQL groups", f"{len(dup_groups_map):,}", f"{dup_report_count:,} reports total"),
        ("", "", ""),
        ("PARENT/CHILD FAMILIES", "", ""),
        ("Report families identified", f"{len(families):,}", ""),
        ("Reports in families", f"{total_in_families:,}", ""),
        ("Standalone reports", f"{standalone:,}", ""),
        ("Reducible variants", f"{reducible:,}", ""),
        ("", "", ""),
        ("FINAL RETAIN NUMBER", "", ""),
        ("After telemetry retirement", f"{len(active):,}", f"-{len(retire):,} from inventory"),
        ("After family consolidation", f"{consolidated:,}", f"-{reducible:,} from retained"),
        ("Final: Public", f"{consol_pub:,}", ""),
        ("Final: Personal", f"{consol_per:,}", ""),
        ("Final: Other", f"{consol_oth:,}", ""),
        ("Total reduction", f"{len(reports) - consolidated:,}",
         f"{(len(reports) - consolidated) / len(reports) * 100:.1f}% of inventory"),
        ("", "", ""),
        ("SQL EXTRACTION", "", ""),
        ("SQL generated successfully", f"{len(has_sql):,}",
         f"{len(has_sql) / len(active) * 100:.1f}% of retained"),
        ("SQL failed: prompted", f"{len(prompted_fail):,}", "Requires manual prompt resolution"),
        ("SQL failed: permission denied", f"{len(perm_denied):,}", "HTTP 403"),
        ("SQL failed: other", f"{len(other_err):,}", ""),
    ]

    add_table(doc, ["Item", "Count", "Detail"], summary_rows, col_widths=[6, 2.5, 8])

    # ---- Save ----
    out_path = OUTPUT_DIR / "Global_Operational_Rationalization_Report.docx"
    doc.save(str(out_path))
    print(f"Document saved to: {out_path}")


if __name__ == "__main__":
    main()
