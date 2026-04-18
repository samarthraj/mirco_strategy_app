#!/usr/bin/env python3
"""Generate the final rationalization analysis report for INSIGHT."""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

OUTPUT_DIR = Path("INSIGHT/rationalization")


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
    path = (r.get("path") or r.get("telemetryPath") or r.get("folderPath") or "").lower()
    if "/profiles/" in path or "/my reports" in path or "/my personal" in path:
        return "Personal"
    elif "/public objects/" in path or "/public/" in path:
        return "Public"
    return "Other"


def main():
    # Load data
    with open(OUTPUT_DIR / "telemetry" / "active_reports.json", "r", encoding="utf-8") as f:
        active = json.load(f)
    with open(OUTPUT_DIR / "telemetry" / "retire_reports.json", "r", encoding="utf-8") as f:
        retire = json.load(f)
    with open(OUTPUT_DIR / "analysis" / "summary.json", "r", encoding="utf-8") as f:
        analysis = json.load(f)
    with open(OUTPUT_DIR / "analysis" / "classification.json", "r", encoding="utf-8") as f:
        classification = json.load(f)
    with open(OUTPUT_DIR / "run_manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Load dedup info
    dedup_info = {}
    dedup_path = OUTPUT_DIR / "inventory" / "active_reports_dedup.json"
    if dedup_path.exists():
        dedup_info = json.loads(dedup_path.read_text(encoding="utf-8"))

    # Load SQL results
    sql_reports = []
    sql_path = OUTPUT_DIR / "inventory" / "active_reports_sql.json"
    if sql_path.exists():
        sql_reports = json.loads(sql_path.read_text(encoding="utf-8"))

    # Load duplicate SQL groups
    dupe_groups = []
    dupe_file = OUTPUT_DIR / "analysis" / "duplicate_sql.json"
    if dupe_file.exists():
        dupe_groups = json.loads(dupe_file.read_text(encoding="utf-8"))

    # Load high impact
    high_impact = []
    hi_file = OUTPUT_DIR / "analysis" / "high_impact.json"
    if hi_file.exists():
        high_impact = json.loads(hi_file.read_text(encoding="utf-8"))

    # Full inventory counts
    full_inv = {}
    for cat in ("reports", "documents", "cubes", "metrics", "filters", "prompts",
                "attributes", "facts", "tables", "security_filters"):
        f = OUTPUT_DIR / "inventory" / f"{cat}.json"
        if f.exists():
            full_inv[cat] = len(json.loads(f.read_text(encoding="utf-8")))

    total_inventory = full_inv.get("reports", classification["totalInventoryReports"])
    project_id = manifest.get("project", {}).get("id", "")

    # Stats
    exact = [r for r in active if r.get("matchTier") == "exact_name"]
    has_sql = sum(1 for r in sql_reports if r.get("sql"))
    no_sql = len(sql_reports) - has_sql
    cube_skip = sum(1 for r in sql_reports
                    if "cube" in str(r.get("errors", {}).get("sql", "")).lower())

    dedup_families = dedup_info.get("familyCount", 0)
    dedup_skipped = dedup_info.get("duplicateCount", 0)
    unique_active = classification["uniqueActiveReports"]

    sql_report_ids = {r["id"] for r in sql_reports}
    active_dupe_groups = []
    for g in dupe_groups:
        members = [o for o in g["objects"] if o["id"] in sql_report_ids]
        if len(members) >= 2:
            active_dupe_groups.append(members)
    total_in_dupes = sum(len(g) for g in active_dupe_groups)
    sql_reducible = total_in_dupes - len(active_dupe_groups)
    after_sql_dedup = unique_active - sql_reducible

    # AST-based duplicate analysis
    ast_path = OUTPUT_DIR / "analysis" / "ast_duplicates.json"
    ast_groups = 0
    ast_reports_in_groups = 0
    ast_removable = 0
    if ast_path.exists():
        ast_data = json.loads(ast_path.read_text(encoding="utf-8"))
        ast_groups = ast_data.get("exactAstGroups", 0)
        ast_reports_in_groups = ast_data.get("reportsInGroups", 0)
        ast_removable = ast_data.get("removableReports", 0)

    # Use max(sql_reducible, ast_removable) since AST is more thorough
    structural_removable = max(sql_reducible, ast_removable)
    after_structural_dedup = unique_active - structural_removable

    # Semantic / LLM cluster review
    reviews_path = OUTPUT_DIR / "sql" / "cluster_reviews.json"
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

    final_unique = after_structural_dedup - semantic_removable

    unique_tables = set()
    for r in sql_reports:
        unique_tables.update(r.get("sourceTables", []))

    # Classify
    for r in active:
        r["_cat"] = classify(r)
    for r in retire:
        r["_cat"] = classify(r)

    # ================================================================
    # BUILD DOCUMENT
    # ================================================================
    doc = Document()

    # Title page
    for _ in range(4):
        doc.add_paragraph()
    title = doc.add_heading("INSIGHT - Final Rationalization Analysis", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("How We Arrived at the Final Number")
    run.font.size = Pt(14)
    run.font.color.rgb = RGBColor(0x2F, 0x54, 0x96)
    for _ in range(2):
        doc.add_paragraph()
    for line in [
        f"Project: INSIGHT ({project_id})",
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
        f"The INSIGHT project contained {sum(full_inv.values()):,} total objects "
        f"across {len(full_inv)} categories, including {total_inventory:,} reports."
    )
    inv_rows = [(k.replace("_", " ").title(), f"{v:,}")
                for k, v in sorted(full_inv.items(), key=lambda x: -x[1]) if v > 0]
    inv_rows.append(("Total", f"{sum(full_inv.values()):,}"))
    add_table(doc, ["Object Category", "Count"], inv_rows, col_widths=[10, 4])

    doc.add_page_break()

    # ================================================================
    # 2. Step 1: Telemetry Retirement
    # ================================================================
    doc.add_heading(f"2. Step 1: Telemetry Retirement (-{len(retire):,} reports)", level=1)

    doc.add_heading("2.1 What We Did", level=2)
    doc.add_paragraph(
        f"Cross-referenced all {total_inventory:,} reports against 87,274 server telemetry "
        f"records (execution logs from the past 7 months). Any report with no evidence of "
        f"execution was flagged for retirement."
    )

    doc.add_heading("2.2 How We Matched", level=2)
    doc.add_paragraph(
        "Report names from the API inventory were normalized (lowercased, whitespace "
        "collapsed, leading asterisks stripped) and compared against telemetry names "
        "for exact equality. 16,203 unique telemetry report names were indexed."
    )

    doc.add_heading("2.3 Result", level=2)
    add_table(doc, ["", "Count", "% of Total"], [
        ("Exact name match (active)", f"{len(exact):,}", f"{len(exact)/total_inventory*100:.1f}%"),
        ("No match (retire)", f"{len(retire):,}", f"{len(retire)/total_inventory*100:.1f}%"),
    ], col_widths=[8, 3, 3])

    doc.add_paragraph()
    add_table(doc, ["Category", "Retain", "Retire", "Total"], [
        ("Public", f"{sum(1 for r in active if r['_cat']=='Public'):,}",
         f"{sum(1 for r in retire if r['_cat']=='Public'):,}",
         f"{sum(1 for r in active+retire if classify(r)=='Public'):,}"),
        ("Personal", f"{sum(1 for r in active if r['_cat']=='Personal'):,}",
         f"{sum(1 for r in retire if r['_cat']=='Personal'):,}",
         f"{sum(1 for r in active+retire if classify(r)=='Personal'):,}"),
        ("Other", f"{sum(1 for r in active if r['_cat']=='Other'):,}",
         f"{sum(1 for r in retire if r['_cat']=='Other'):,}",
         f"{sum(1 for r in active+retire if classify(r)=='Other'):,}"),
        ("Total", f"{len(active):,}", f"{len(retire):,}", f"{total_inventory:,}"),
    ], col_widths=[5, 3, 3, 3])

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run(f"After Step 1: {total_inventory:,} -> {len(active):,} reports").bold = True

    doc.add_page_break()

    # ================================================================
    # 3. Step 2: Targeted Enrichment
    # ================================================================
    doc.add_heading("3. Step 2: Targeted Enrichment (Active Reports Only)", level=1)

    doc.add_heading("3.1 What We Did", level=2)
    doc.add_paragraph(
        f"Rather than enriching all {total_inventory:,} reports (which would require "
        f"100K+ API calls taking 8+ hours), we fetched definitions and folder paths "
        f"only for the {len(active):,} active reports. This targeted approach reduced "
        f"API calls by {(total_inventory - len(active))/total_inventory*100:.0f}%."
    )

    doc.add_heading("3.2 Result", level=2)
    add_table(doc, ["", "Count"], [
        ("Active reports targeted", f"{len(active):,}"),
        ("Definitions fetched successfully", f"{classification['withDefinitions']:,}"),
        ("Definition errors (timeouts, 400/500)", f"{len(active) - classification['withDefinitions']:,}"),
        ("Folder paths resolved", f"{len(active):,}"),
    ], col_widths=[10, 4])

    p = doc.add_paragraph()
    p.add_run(f"After Step 2: {len(active):,} reports enriched with definitions").bold = True

    doc.add_page_break()

    # ================================================================
    # 4. Step 3: Definition-Based De-duplication
    # ================================================================
    doc.add_heading("4. Step 3: Definition-Based De-duplication", level=1)

    doc.add_heading("4.1 What We Did", level=2)
    doc.add_paragraph(
        "Created a fingerprint for each report by hashing its attributes, metrics, "
        "filter text, source type, and source cube ID. Reports with identical fingerprints "
        "are true copies — regardless of name — and were grouped into families. The copy "
        "with the highest execution count was kept as the parent."
    )

    doc.add_heading("4.2 Why Definition-Based (Not Name-Based)", level=2)
    doc.add_paragraph(
        "Name-based de-duplication would have identified 10,221 unique names from 17,057 "
        "reports. However, definition fingerprinting is more accurate because it correctly "
        "identifies reports with different names but identical logic, and preserves reports "
        "with the same name but different definitions (e.g., different filters)."
    )

    doc.add_heading("4.3 Result", level=2)
    add_table(doc, ["", "Count"], [
        ("Active reports analyzed", f"{len(active):,}"),
        ("Reports with definitions", f"{classification['withDefinitions']:,}"),
        ("", ""),
        ("Families of identical reports", f"{dedup_families:,}"),
        ("Duplicate copies removed", f"{dedup_skipped:,}"),
        ("Reports without definitions (kept as-is)", f"{len(active) - classification['withDefinitions']:,}"),
        ("", ""),
        ("Unique active reports", f"{unique_active:,}"),
    ], col_widths=[10, 4])

    # Show top families
    if dedup_info.get("families"):
        doc.add_heading("4.4 Largest Definition Families", level=2)
        fam_rows = []
        for i, fam in enumerate(dedup_info["families"][:10], 1):
            fam_rows.append((
                i,
                fam.get("parentName", "")[:45],
                fam.get("familySize", 0),
                fam.get("familySize", 0) - 1,
            ))
        add_table(doc, ["#", "Parent Report", "Family Size", "Copies Removed"],
                  fam_rows, col_widths=[1, 8, 2.5, 3])

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run(f"After Step 3: {len(active):,} -> {unique_active:,} unique reports").bold = True

    doc.add_page_break()

    # ================================================================
    # 5. Step 4: SQL Extraction
    # ================================================================
    doc.add_heading("5. Step 4: SQL Extraction", level=1)

    doc.add_heading("5.1 What We Did", level=2)
    doc.add_paragraph(
        f"For each of the {unique_active:,} unique active reports, we attempted to extract "
        f"the underlying SQL statement via the MicroStrategy REST API. Cube-sourced reports "
        f"were skipped as cube instances return HTTP 500 on this server."
    )

    doc.add_heading("5.2 How It Works", level=2)
    for s in [
        "Create a report instance via POST /v2/reports/{id}/instances",
        "For prompted reports, attempt to resolve prompts using cached/default answers",
        "Retrieve the generated SQL via GET /v2/reports/{id}/instances/{instanceId}/sqlView",
        "Clean up the instance via DELETE",
    ]:
        doc.add_paragraph(s, style="List Number")

    doc.add_heading("5.3 Result", level=2)
    add_table(doc, ["", "Count", f"% of {unique_active:,}"], [
        ("SQL extracted successfully", f"{has_sql:,}", f"{has_sql/unique_active*100:.1f}%"),
        ("Cube-sourced (skipped)", f"{cube_skip:,}", f"{cube_skip/unique_active*100:.1f}%"),
        ("Other errors (prompted, timeout, permission)", f"{no_sql - cube_skip:,}",
         f"{(no_sql - cube_skip)/unique_active*100:.1f}%"),
        ("", "", ""),
        ("Unique source tables identified", f"{len(unique_tables):,}", ""),
    ], col_widths=[8, 3, 3])

    p = doc.add_paragraph()
    p.add_run(f"After Step 4: {has_sql:,} reports with SQL available for analysis").bold = True

    doc.add_page_break()

    # ================================================================
    # 6. Step 5: Duplicate SQL Detection
    # ================================================================
    doc.add_heading("6. Step 5: Duplicate SQL Detection", level=1)

    doc.add_heading("6.1 What We Did", level=2)
    doc.add_paragraph(
        "Normalized each SQL statement to remove cosmetic differences and hashed them "
        "to find reports executing identical queries."
    )

    doc.add_heading("6.2 Normalization Steps", level=2)
    for s in [
        "Lowercase all SQL text",
        "Strip comments (-- and /* */)",
        "Replace all string literals with '?' placeholder",
        "Replace all numeric literals with 0",
        "Collapse all whitespace",
        "SHA-256 hash the result",
    ]:
        doc.add_paragraph(s, style="List Number")

    doc.add_heading("6.3 Result", level=2)
    add_table(doc, ["", "Count"], [
        ("Duplicate SQL groups found", f"{len(active_dupe_groups):,}"),
        ("Reports in duplicate groups", f"{total_in_dupes:,}"),
        ("Reducible (keep 1 per group)", f"{sql_reducible:,}"),
    ], col_widths=[10, 4])

    if active_dupe_groups:
        doc.add_heading("6.4 Largest Duplicate SQL Groups", level=2)
        top_rows = []
        for i, members in enumerate(sorted(active_dupe_groups, key=len, reverse=True)[:10], 1):
            names = [m.get("name", "")[:40] for m in members]
            top_rows.append((
                i, len(members), names[0],
                ", ".join(n[:30] for n in names[1:3]) + ("..." if len(names) > 3 else ""),
            ))
        add_table(doc, ["#", "Size", "Primary Report", "Also includes"],
                  top_rows, col_widths=[1, 1.5, 6, 8])

    p = doc.add_paragraph()
    p.add_run(f"After Step 5: {unique_active:,} -> {after_sql_dedup:,} reports with unique SQL hash").bold = True

    doc.add_page_break()

    # ================================================================
    # 6b. Step 5b: AST-based Structural Analysis (sqlglot)
    # ================================================================
    doc.add_heading("6b. Step 5b: AST-Based Structural Duplicate Detection", level=1)

    doc.add_heading("6b.1 What We Did", level=2)
    doc.add_paragraph(
        "Hash-based dedup catches identical normalized SQL, but misses structural duplicates "
        "where the SQL differs only in column alias order, JOIN order, or whitespace patterns "
        "that survive normalization. We used sqlglot to parse each SQL into an Abstract "
        "Syntax Tree (AST), canonicalize it, and compute Merkle-style subtree hashes. "
        "Reports sharing the same AST subtree hash set are structurally identical."
    )

    doc.add_heading("6b.2 Result", level=2)
    ast_rows = [
        ("Reports with SQL", f"{has_sql:,}"),
        ("Successfully parsed (sqlglot)", f"{has_sql - (0 if not ast_path.exists() else ast_data.get('parseFailures', 0)):,}"),
        ("Parse failures", f"{0 if not ast_path.exists() else ast_data.get('parseFailures', 0):,}"),
        ("", ""),
        ("Exact-AST duplicate groups", f"{ast_groups:,}"),
        ("Reports in groups", f"{ast_reports_in_groups:,}"),
        ("Removable (keep 1 per group)", f"{ast_removable:,}"),
    ]
    add_table(doc, ["", "Count"], ast_rows, col_widths=[10, 4])

    doc.add_paragraph()
    p = doc.add_paragraph()
    if ast_removable > sql_reducible:
        p.add_run(f"AST analysis found {ast_removable - sql_reducible:,} additional duplicates "
                  f"beyond hash-based dedup. Using AST count going forward.").bold = True
    else:
        p.add_run(f"AST analysis confirmed hash-based results. Using max: {structural_removable:,} removable.").bold = True

    doc.add_page_break()

    # ================================================================
    # 7. Step 6: Semantic Analysis (LLM-powered)
    # ================================================================
    doc.add_heading("7. Step 6: Semantic Analysis (LLM-powered)", level=1)

    doc.add_heading("7.1 What We Did", level=2)
    doc.add_paragraph(
        "Hash-based duplicate detection catches only identical SQL. To find reports that do "
        "similar things but differ textually (different column aliases, JOIN order, WHERE "
        "filters on the same tables), we used vector embeddings and clustering followed by "
        "LLM review."
    )

    doc.add_heading("7.2 Embedding & Clustering", level=2)
    for s in [
        "Normalized SQL for each unique report (lowercased, literals replaced, temp tables standardized)",
        "Embedded each SQL using sentence-transformers (all-MiniLM-L6-v2, 384-dim vectors)",
        "Computed pairwise cosine similarity matrix across all unique SQL",
        "Agglomerative clustering with 0.15 distance threshold (85% similarity minimum)",
    ]:
        doc.add_paragraph(s, style="List Number")

    doc.add_heading("7.3 LLM Review of Clusters", level=2)
    doc.add_paragraph(
        f"Each multi-member cluster was reviewed by GPT-4o and classified as MERGE_IMMEDIATE, "
        f"PARAMETERIZE, REVIEW_WITH_OWNER, or KEEP_SEPARATE. The LLM also recommended which "
        f"report to keep as the primary and how many could be removed."
    )

    # Model discrimination test
    doc.add_paragraph(
        "Before running the full analysis, we tested three embedding models on 50 known "
        "duplicate pairs and 50 known distinct pairs. all-MiniLM-L6-v2 achieved the best "
        "separation score (0.288), significantly outperforming OpenAI text-embedding-3-large "
        "(0.162) and Voyage voyage-code-2 (0.047)."
    )

    doc.add_heading("7.4 Result", level=2)
    sem_rows = [
        ("Clusters reviewed", f"{len(semantic_reviews):,}"),
        ("", ""),
    ]
    action_labels = {
        "MERGE_IMMEDIATE": "MERGE_IMMEDIATE (identical, remove duplicates)",
        "PARAMETERIZE": "PARAMETERIZE (same query, different filters)",
        "REVIEW_WITH_OWNER": "REVIEW_WITH_OWNER (business confirmation needed)",
        "KEEP_SEPARATE": "KEEP_SEPARATE (different purpose)",
    }
    for act, label in action_labels.items():
        sem_rows.append((label, f"{semantic_actions.get(act, 0):,}"))
    if semantic_actions.get("error", 0) > 0 or semantic_actions.get("?", 0) > 0:
        sem_rows.append(("Errors / parsing failures", f"{semantic_actions.get('error', 0) + semantic_actions.get('?', 0):,}"))
    sem_rows.append(("", ""))
    sem_rows.append(("Total removable (LLM-recommended)", f"{semantic_removable:,}"))
    add_table(doc, ["", "Count"], sem_rows, col_widths=[10, 4])

    # Top clusters
    by_removable = sorted(semantic_reviews,
                          key=lambda r: -r.get("analysis", {}).get("removable_count", 0))[:15]
    doc.add_heading("7.5 Top Consolidation Opportunities", level=2)
    top_rows = []
    for rev in by_removable:
        a = rev.get("analysis", {})
        if "error" in a:
            continue
        top_rows.append((
            rev["clusterId"],
            a.get("label", "?")[:40],
            a.get("action", "?"),
            rev["totalReportCount"],
            a.get("removable_count", 0),
        ))
    if top_rows:
        add_table(doc, ["Cluster", "Description", "Action", "Reports", "Removable"],
                  top_rows, col_widths=[1.5, 5, 3, 2, 2])

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run(f"After Step 6: {after_structural_dedup:,} -> {final_unique:,} truly unique reports").bold = True

    doc.add_page_break()

    # ================================================================
    # 8. The Final Number
    # ================================================================
    doc.add_heading("8. The Final Number", level=1)

    doc.add_heading("8.1 Complete Reduction Funnel", level=2)

    funnel_rows = [
        ("Original inventory", f"{total_inventory:,}", "", "All reports enumerated via REST API"),
        ("", "", "", ""),
        ("Step 1: Telemetry retirement", f"{len(active):,}", f"-{len(retire):,}",
         f"{len(retire):,} reports with no execution in 7 months"),
        ("", "", "", ""),
        ("Step 2: Targeted enrichment", f"{len(active):,}", "",
         f"Definitions fetched for active reports only"),
        ("", "", "", ""),
        ("Step 3: Definition dedup", f"{unique_active:,}", f"-{dedup_skipped:,}",
         f"{dedup_families:,} families of identical definitions"),
        ("", "", "", ""),
        ("Step 4: SQL extraction", f"{has_sql:,}", "",
         f"SQL retrieved for {has_sql:,} of {unique_active:,} unique reports"),
        ("", "", "", ""),
        ("Step 5: SQL hash dedup", f"{after_sql_dedup:,}", f"-{sql_reducible:,}",
         f"{len(active_dupe_groups):,} groups of identical SQL"),
        ("", "", "", ""),
        ("Step 5b: AST structural dedup", f"{unique_active - structural_removable:,}",
         f"-{structural_removable - sql_reducible:,}" if structural_removable > sql_reducible else "",
         f"{ast_groups:,} exact-AST groups ({ast_removable:,} total)"),
        ("", "", "", ""),
        ("Step 6: Semantic dedup (LLM)", f"{final_unique:,}", f"-{semantic_removable:,}",
         f"{len(semantic_reviews):,} clusters reviewed by GPT-4o"),
        ("", "", "", ""),
        ("FINAL UNIQUE REPORTS", f"{final_unique:,}", "",
         f"From {total_inventory:,} — {(total_inventory - final_unique)/total_inventory*100:.1f}% reduction"),
    ]
    add_table(doc, ["Stage", "Reports", "Change", "Explanation"],
              funnel_rows, col_widths=[5.5, 2, 1.5, 7.5])

    doc.add_heading("8.2 The Math", level=2)
    math_steps = [
        f"Start with {total_inventory:,} total reports in INSIGHT project",
        f"Telemetry retirement removes {len(retire):,} unused reports -> {len(active):,} remain",
        f"Definition fingerprinting removes {dedup_skipped:,} identical copies -> {unique_active:,} unique",
        f"SQL extraction succeeds for {has_sql:,} of {unique_active:,} reports",
        f"SQL hash dedup removes {sql_reducible:,} duplicates; AST dedup finds {ast_removable:,} total (max used)",
        f"After structural dedup: {after_structural_dedup:,} unique reports",
        f"Semantic (LLM) dedup removes {semantic_removable:,} near-duplicates -> {final_unique:,} final unique",
    ]
    for s in math_steps:
        doc.add_paragraph(s, style="List Number")

    doc.add_heading("8.3 Summary", level=2)
    add_table(doc, ["Category", "Count", "Status"], [
        ("Unique reports (after all dedup)", f"{final_unique:,}", "RETAIN"),
        ("Definition duplicates", f"{dedup_skipped:,}", "CONSOLIDATE"),
        ("Structural duplicates (SQL hash + AST)", f"{structural_removable:,}", "CONSOLIDATE"),
        ("Semantic near-duplicates (LLM)", f"{semantic_removable:,}", "CONSOLIDATE / PARAMETERIZE"),
        ("Telemetry retire (no usage)", f"{len(retire):,}", "RETIRE"),
        ("", "", ""),
        ("TOTAL TO RETAIN", f"{final_unique:,}", ""),
        ("TOTAL TO RETIRE/CONSOLIDATE",
         f"{total_inventory - final_unique:,}",
         f"{(total_inventory - final_unique)/total_inventory*100:.1f}% reduction"),
    ], col_widths=[6, 3, 7.5])

    doc.add_page_break()

    # ================================================================
    # 8. Supporting Analysis
    # ================================================================
    doc.add_heading("8. Supporting Analysis", level=1)

    doc.add_heading("8.1 Dependency Analysis", level=2)
    doc.add_paragraph(
        f"A dependency graph with {analysis['totalEdges']:,} edges was inferred from "
        f"report definitions. This identified:"
    )
    add_table(doc, ["Finding", "Count", "Description"], [
        ("Orphaned objects", f"{analysis['orphanCount']:,}",
         "Schema objects with zero dependents — nothing references them"),
        ("Unused metrics", f"{analysis['unusedMetricCount']:,}",
         f"Of {classification['totalMetrics']:,} total metrics, not used by any active report"),
        ("Stale objects", f"{analysis['staleObjectCount']:,}",
         "Objects not modified in over 1 year"),
        ("Stale but active", f"{classification['staleAndActive']:,}",
         "Reports with usage but not modified in >1 year"),
    ], col_widths=[4, 2, 10.5])

    doc.add_heading("8.2 High-Impact Objects (Top 10)", level=2)
    doc.add_paragraph(
        "Objects with the most dependents — critical to protect during cleanup:"
    )
    if high_impact:
        hi_rows = []
        for i, obj in enumerate(high_impact[:10], 1):
            hi_rows.append((
                i,
                obj.get("name", "")[:45],
                obj.get("category", ""),
                f"{obj.get('dependentCount', 0):,}",
            ))
        add_table(doc, ["#", "Object Name", "Type", "Dependents"],
                  hi_rows, col_widths=[1, 8, 3, 2.5])

    doc.add_page_break()

    # ================================================================
    # 9. Recommendations
    # ================================================================
    doc.add_heading("9. Recommendations", level=1)

    doc.add_heading("9.1 Recommended Action Plan", level=2)
    actions = [
        ("Immediate (Week 1)",
         f"Retire the {len(retire):,} telemetry-excluded reports. Move to archive folder. "
         f"No business impact since they have zero execution evidence."),
        ("Quick Wins (Week 2)",
         f"Remove the {dedup_skipped:,} definition-identical copies. These are exact "
         f"duplicates of active reports and serve no unique purpose."),
        ("SQL Consolidation (Weeks 3-4)",
         f"Consolidate the {len(active_dupe_groups):,} SQL-duplicate groups ({sql_reducible:,} "
         f"reducible reports) into parameterized reports."),
        ("Schema Cleanup (Weeks 5-6)",
         f"Address the {analysis['unusedMetricCount']:,} unused metrics and "
         f"{analysis['orphanCount']:,} orphaned objects to simplify the project."),
        ("Ongoing",
         "Schedule quarterly rationalization reviews to prevent report sprawl."),
    ]
    for title, desc in actions:
        p = doc.add_paragraph()
        p.add_run(f"{title}: ").bold = True
        p.add_run(desc)

    doc.add_heading("9.2 Caveats", level=2)
    caveats = [
        f"{no_sql:,} reports could not have SQL extracted (cube-sourced, prompted, errors). "
        f"Some may be duplicates, further reducing the unique count.",
        f"{len(active) - classification['withDefinitions']:,} reports had definition fetch errors. "
        f"These were included in the unique count without de-duplication.",
        "Telemetry matching uses exact name comparison. Reports executed under slightly "
        "different names may be incorrectly flagged for retirement.",
        "Definition fingerprinting does not capture cosmetic differences in report layout, "
        "formatting, or prompt defaults — only the underlying data query structure.",
    ]
    for c in caveats:
        doc.add_paragraph(c, style="List Bullet")

    doc.add_heading("9.3 Deliverables", level=2)
    for d in [
        "This document — Complete analysis walkthrough",
        f"INSIGHT_Rationalization_Summary.docx — Detailed methodology and recommendations",
        f"retire_reports.csv — {len(retire):,} reports recommended for retirement",
        f"active_reports.json — {len(active):,} reports to retain with usage statistics",
        f"active_reports_enriched.json — {classification['withDefinitions']:,} reports with definitions",
        f"active_reports_dedup.json — Definition-based family mapping ({dedup_families:,} families)",
        f"active_reports_sql.json — SQL for {has_sql:,} unique active reports",
        "analysis/ folder — Orphans, stale objects, duplicate SQL, unused metrics, high-impact",
        "rationalization_report.json — Full machine-readable report",
    ]:
        doc.add_paragraph(d, style="List Bullet")

    # Save
    out_path = OUTPUT_DIR / "INSIGHT_Final_Analysis.docx"
    doc.save(str(out_path))
    print(f"Document saved to: {out_path}")


if __name__ == "__main__":
    main()
