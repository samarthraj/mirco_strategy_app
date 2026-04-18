#!/usr/bin/env python3
"""
Generate a PowerPoint rationalization report from mstr_rationalization_full/ data.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.chart.data import CategoryChartData

# ── Colors ──

DARK_BG = RGBColor(0x0F, 0x0F, 0x1A)
CARD_BG = RGBColor(0x16, 0x21, 0x3E)
BORDER = RGBColor(0x1E, 0x2D, 0x50)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GRAY = RGBColor(0x9C, 0xA3, 0xAF)
LIGHT_GRAY = RGBColor(0xD1, 0xD5, 0xDB)
RED = RGBColor(0xEF, 0x44, 0x44)
GREEN = RGBColor(0x22, 0xC5, 0x5E)
BLUE = RGBColor(0x3B, 0x82, 0xF6)
YELLOW = RGBColor(0xEA, 0xB3, 0x08)
ORANGE = RGBColor(0xF9, 0x73, 0x16)
CYAN = RGBColor(0x06, 0xB6, 0xD4)
PURPLE = RGBColor(0xA8, 0x55, 0xF7)
PINK = RGBColor(0xEC, 0x48, 0x99)

CAT_COLORS = {
    "reports": BLUE, "documents": PURPLE, "cubes": GREEN,
    "metrics": YELLOW, "filters": ORANGE, "prompts": PINK,
    "attributes": CYAN, "facts": RGBColor(0x14, 0xB8, 0xA6),
    "tables": RGBColor(0x63, 0x66, 0xF1),
}


def set_slide_bg(slide, color=DARK_BG):
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_text_box(slide, left, top, width, height, text, font_size=14,
                 color=WHITE, bold=False, alignment=PP_ALIGN.LEFT, font_name="Calibri"):
    txBox = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.color.rgb = color
    p.font.bold = bold
    p.font.name = font_name
    p.alignment = alignment
    return txBox


def add_stat_card(slide, left, top, label, value, color=BLUE):
    """Add a stat card with value and label."""
    width, height = 2.1, 1.0
    shape = slide.shapes.add_shape(
        1, Inches(left), Inches(top), Inches(width), Inches(height)  # 1 = rectangle
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARD_BG
    shape.line.color.rgb = BORDER
    shape.line.width = Pt(1)

    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_top = Pt(8)
    tf.margin_left = Pt(10)

    p = tf.paragraphs[0]
    p.text = str(value)
    p.font.size = Pt(22)
    p.font.bold = True
    p.font.color.rgb = color

    p2 = tf.add_paragraph()
    p2.text = label
    p2.font.size = Pt(9)
    p2.font.color.rgb = GRAY


def add_table(slide, left, top, width, col_widths, headers, rows,
              header_color=GRAY, row_color=LIGHT_GRAY):
    """Add a styled table."""
    n_rows = len(rows) + 1
    n_cols = len(headers)
    table_shape = slide.shapes.add_table(n_rows, n_cols, Inches(left), Inches(top),
                                          Inches(width), Inches(0.3 * n_rows))
    table = table_shape.table

    # Set column widths
    for i, w in enumerate(col_widths):
        table.columns[i].width = Inches(w)

    # Header row
    for i, h in enumerate(headers):
        cell = table.cell(0, i)
        cell.text = h
        for p in cell.text_frame.paragraphs:
            p.font.size = Pt(9)
            p.font.bold = True
            p.font.color.rgb = WHITE
            p.font.name = "Calibri"
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor(0x1A, 0x2A, 0x4A)

    # Data rows
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            cell = table.cell(r + 1, c)
            cell.text = str(val)
            for p in cell.text_frame.paragraphs:
                p.font.size = Pt(8)
                p.font.color.rgb = row_color
                p.font.name = "Calibri"
            cell.fill.solid()
            cell.fill.fore_color.rgb = CARD_BG if r % 2 == 0 else RGBColor(0x12, 0x1A, 0x2E)

    return table_shape


def build_deck(data_dir: Path, output_path: Path):
    # Load data
    report = json.loads((data_dir / "rationalization_report.json").read_text(encoding="utf-8"))
    summary = report["summary"]
    orphans = report["orphans"]
    duplicates = report["duplicateSql"]
    high_impact = report["highImpact"]
    stale = report["staleObjects"]
    unused_metrics = report["unusedMetrics"]
    objects = report["objects"]
    dependents_of = report.get("dependentsOf", {})
    dependencies_of = report.get("dependenciesOf", {})

    total = summary["totalObjects"]
    orphan_ids = set(o["id"] for o in orphans)
    dup_remove_ids = set()
    for g in duplicates:
        for obj in g["objects"][1:]:
            dup_remove_ids.add(obj["id"])
    skip_ids = orphan_ids | dup_remove_ids
    migrate_ids = set(objects.keys()) - skip_ids

    # Categorize migrate
    migrate_cats = {}
    for oid in migrate_ids:
        cat = objects[oid].get("category", "?")
        migrate_cats[cat] = migrate_cats.get(cat, 0) + 1

    consumers = {oid for oid in migrate_ids if objects[oid].get("category") in ("reports", "documents", "cubes")}
    supporting = {oid for oid in migrate_ids if objects[oid].get("category") in ("metrics", "filters", "prompts")}
    schema_objs = {oid for oid in migrate_ids if objects[oid].get("category") in ("attributes", "facts", "tables")}

    orphan_cats = {}
    for o in orphans:
        orphan_cats[o["category"]] = orphan_cats.get(o["category"], 0) + 1

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 1: Title
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    set_slide_bg(slide)

    add_text_box(slide, 1, 1.5, 11, 1.2,
                 "MicroStrategy Project Rationalization", 36, WHITE, True, PP_ALIGN.CENTER)
    add_text_box(slide, 1, 2.8, 11, 0.8,
                 "Global Operational", 28, BLUE, False, PP_ALIGN.CENTER)
    add_text_box(slide, 1, 3.8, 11, 0.5,
                 f"Analysis Date: {datetime.now().strftime('%B %d, %Y')}", 14, GRAY, False, PP_ALIGN.CENTER)
    add_text_box(slide, 1, 4.5, 11, 0.5,
                 f"{total:,} objects analyzed  |  {summary['totalEdges']:,} dependency edges mapped", 14, GRAY, False, PP_ALIGN.CENTER)
    add_text_box(slide, 1, 6.2, 11, 0.5,
                 "Ralph Lauren - Sandbox Environment", 12, RGBColor(0x6B, 0x72, 0x80), False, PP_ALIGN.CENTER)

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 2: Executive Summary
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide)
    add_text_box(slide, 0.5, 0.3, 12, 0.6, "Executive Summary", 28, WHITE, True)

    add_stat_card(slide, 0.5, 1.2, "Total Objects", f"{total:,}", BLUE)
    add_stat_card(slide, 2.8, 1.2, "Migrate", f"{len(migrate_ids):,}", GREEN)
    add_stat_card(slide, 5.1, 1.2, "Skip / Archive", f"{len(skip_ids):,}", RED)
    add_stat_card(slide, 7.4, 1.2, "Reduction", f"{len(skip_ids)/total*100:.0f}%", ORANGE)

    add_text_box(slide, 0.5, 2.5, 12, 0.4, "Key Findings:", 16, WHITE, True)

    findings = [
        f"53% of the project ({len(skip_ids):,} objects) can be safely excluded from migration",
        f"2,060 orphan schema objects are not referenced by any report or cube",
        f"30 duplicate reports with identical SQL can be consolidated into 11",
        f"1,840 objects ({len(migrate_ids)/total*100:.0f}%) should be migrated to the new system",
        f"Top high-impact object: \"Season Hierarchy\" prompt with 593 dependent reports",
        f"Execution/usage history is NOT available via REST API - migrate set may be further reducible",
    ]
    y = 3.0
    for f in findings:
        add_text_box(slide, 0.8, y, 11, 0.35, f"  \u2022  {f}", 12, LIGHT_GRAY)
        y += 0.35

    add_text_box(slide, 0.5, 5.5, 12, 0.8,
                 "Note: This analysis is based on object definitions and dependency graphs. "
                 "\"dateModified\" reflects when an object was last edited, NOT when it was last executed. "
                 "Reports built years ago may still be actively used. To identify truly unused reports, "
                 "execution history from the MicroStrategy metadata database (audit tables) is required.",
                 10, YELLOW)

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 3: Inventory Breakdown
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide)
    add_text_box(slide, 0.5, 0.3, 12, 0.6, "Full Inventory Breakdown", 28, WHITE, True)

    # Table
    cats_sorted = sorted(summary["byCategory"].items(), key=lambda x: -x[1])
    cats_sorted = [(c, n) for c, n in cats_sorted if n > 0]
    rows = [[cat, f"{count:,}", f"{count/total*100:.1f}%"] for cat, count in cats_sorted]
    rows.append(["TOTAL", f"{total:,}", "100%"])

    add_table(slide, 0.5, 1.2, 5, [2, 1.5, 1.5],
              ["Category", "Count", "% of Total"], rows)

    # Chart
    chart_data = CategoryChartData()
    chart_data.categories = [c for c, _ in cats_sorted]
    chart_data.add_series("Objects", [n for _, n in cats_sorted])

    chart_frame = slide.shapes.add_chart(
        XL_CHART_TYPE.BAR_CLUSTERED, Inches(6), Inches(1.2), Inches(6.5), Inches(5),
        chart_data
    )
    chart = chart_frame.chart
    chart.has_legend = False
    plot = chart.plots[0]
    series = plot.series[0]
    series.format.fill.solid()
    series.format.fill.fore_color.rgb = BLUE

    # Style chart
    chart.chart_style = 2
    cat_axis = chart.category_axis
    val_axis = chart.value_axis
    cat_axis.tick_labels.font.size = Pt(9)
    cat_axis.tick_labels.font.color.rgb = LIGHT_GRAY
    val_axis.tick_labels.font.size = Pt(9)
    val_axis.tick_labels.font.color.rgb = GRAY

    add_text_box(slide, 0.5, 6.5, 12, 0.4,
                 f"Attributes (30.7%) and Reports (29.5%) make up 60% of the project. "
                 f"Schema objects (attributes, facts, tables) total {1206+182+17:,} ({(1206+182+17)/total*100:.0f}%).",
                 10, GRAY)

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 4: Migration Verdict
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide)
    add_text_box(slide, 0.5, 0.3, 12, 0.6, "Migration Verdict", 28, WHITE, True)
    add_text_box(slide, 0.5, 0.9, 12, 0.4,
                 "Based on dependency analysis (not dateModified)", 14, GRAY)

    # Pie chart
    pie_data = CategoryChartData()
    pie_data.categories = ["Migrate", "Skip / Archive"]
    pie_data.add_series("Objects", [len(migrate_ids), len(skip_ids)])

    pie_frame = slide.shapes.add_chart(
        XL_CHART_TYPE.PIE, Inches(0.5), Inches(1.5), Inches(5), Inches(4.5),
        pie_data
    )
    pie_chart = pie_frame.chart
    pie_chart.has_legend = True
    pie_chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    pie_chart.legend.font.size = Pt(11)
    pie_chart.legend.font.color.rgb = LIGHT_GRAY

    plot = pie_chart.plots[0]
    point0 = plot.series[0].points[0]
    point0.format.fill.solid()
    point0.format.fill.fore_color.rgb = GREEN
    point1 = plot.series[0].points[1]
    point1.format.fill.solid()
    point1.format.fill.fore_color.rgb = RED

    # Migration breakdown table
    mig_rows = sorted(migrate_cats.items(), key=lambda x: -x[1])
    table_rows = [[cat, f"{count:,}", f"{count/total*100:.1f}%"] for cat, count in mig_rows]
    table_rows.append(["TOTAL MIGRATE", f"{len(migrate_ids):,}", f"{len(migrate_ids)/total*100:.1f}%"])
    add_table(slide, 6, 1.5, 6, [2.5, 1.5, 2],
              ["Category", "Migrate Count", "% of Total"], table_rows)

    # Role breakdown
    add_text_box(slide, 6, 5.0, 6, 0.4, "Migration by Role:", 14, WHITE, True)
    add_text_box(slide, 6.2, 5.4, 6, 0.3, f"Consumer objects (reports/docs/cubes): {len(consumers):,}", 11, LIGHT_GRAY)
    add_text_box(slide, 6.2, 5.7, 6, 0.3, f"Supporting objects (metrics/filters/prompts): {len(supporting):,}", 11, LIGHT_GRAY)
    add_text_box(slide, 6.2, 6.0, 6, 0.3, f"Schema objects (attributes/facts/tables): {len(schema_objs):,}", 11, LIGHT_GRAY)

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 5: Orphan Analysis
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide)
    add_text_box(slide, 0.5, 0.3, 12, 0.6, "Orphan Analysis — Safe to Archive", 28, WHITE, True)
    add_text_box(slide, 0.5, 0.9, 12, 0.5,
                 f"{len(orphan_ids):,} schema objects are not referenced by ANY report, cube, or document. "
                 "These are dead objects that can be safely excluded from migration.",
                 13, GRAY)

    orphan_sorted = sorted(orphan_cats.items(), key=lambda x: -x[1])
    orph_rows = [[cat, f"{count:,}", f"{count/len(orphan_ids)*100:.1f}%"] for cat, count in orphan_sorted]
    orph_rows.append(["TOTAL ORPHANS", f"{len(orphan_ids):,}", "100%"])
    add_table(slide, 0.5, 1.8, 6, [2.5, 1.5, 2],
              ["Category", "Orphan Count", "% of Orphans"], orph_rows)

    # Chart
    orph_chart_data = CategoryChartData()
    orph_chart_data.categories = [c for c, _ in orphan_sorted]
    orph_chart_data.add_series("Orphans", [n for _, n in orphan_sorted])
    orph_frame = slide.shapes.add_chart(
        XL_CHART_TYPE.BAR_CLUSTERED, Inches(7), Inches(1.8), Inches(5.5), Inches(3.5),
        orph_chart_data
    )
    orph_chart = orph_frame.chart
    orph_chart.has_legend = False
    orph_chart.plots[0].series[0].format.fill.solid()
    orph_chart.plots[0].series[0].format.fill.fore_color.rgb = YELLOW

    add_text_box(slide, 0.5, 5.8, 12, 0.8,
                 "How orphans were identified:\n"
                 "For each report and cube, we extracted the attribute IDs, metric IDs, and filter IDs "
                 "from the report definition (dataSource.dataTemplate.units and filter expression tree). "
                 "Any schema object NOT referenced by any report or cube is classified as orphan. "
                 "This is structural — it does not depend on execution history.",
                 10, GRAY)

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 6: Duplicate SQL
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide)
    add_text_box(slide, 0.5, 0.3, 12, 0.6, "Duplicate SQL Analysis", 28, WHITE, True)

    total_dup = sum(g["count"] for g in duplicates)
    add_text_box(slide, 0.5, 0.9, 12, 0.5,
                 f"{len(duplicates)} groups of reports with identical SQL queries. "
                 f"{total_dup} reports can be consolidated into {len(duplicates)}, removing {len(dup_remove_ids)} copies.",
                 13, GRAY)

    dup_rows = []
    for i, g in enumerate(duplicates, 1):
        keep_name = g["objects"][0]["name"][:40]
        dup_rows.append([
            f"Group {i}", str(g["count"]), str(g["count"] - 1), keep_name
        ])
    dup_rows.append(["TOTAL", str(total_dup), str(len(dup_remove_ids)), ""])

    add_table(slide, 0.5, 1.6, 12, [1.5, 1.2, 1.5, 7.8],
              ["Group", "Total Copies", "Removable", "Keep (sample name)"], dup_rows)

    add_text_box(slide, 0.5, 5.5, 12, 0.8,
                 "How duplicates were identified:\n"
                 "For each report/cube, we retrieved the SQL via the sqlView API. The SQL was normalized "
                 "(lowercased, whitespace collapsed, string/numeric literals replaced with placeholders) "
                 "then hashed with SHA-256. Reports with identical hashes have identical queries. "
                 "The largest group is 14 MSRP Price List reports (2014-2026) — all hit the same SQL.",
                 10, GRAY)

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 7: Dependency Graph
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide)
    add_text_box(slide, 0.5, 0.3, 12, 0.6, "Dependency Graph & High-Impact Objects", 28, WHITE, True)

    add_stat_card(slide, 0.5, 1.2, "Dependency Edges", f"{summary['totalEdges']:,}", CYAN)
    add_stat_card(slide, 2.8, 1.2, "Objects with Deps", f"{len(dependents_of):,}", GREEN)
    add_stat_card(slide, 5.1, 1.2, "Consumer Objects", f"{len(dependencies_of):,}", BLUE)
    add_stat_card(slide, 7.4, 1.2, "High-Impact (50+)", f"{summary['highImpactCount']}", RED)

    add_text_box(slide, 0.5, 2.5, 12, 0.4, "Top 15 High-Impact Objects (most dependents):", 14, WHITE, True)
    add_text_box(slide, 0.5, 2.9, 12, 0.3,
                 "Changing these objects affects the most downstream consumers. Handle with extreme care during migration.",
                 11, GRAY)

    hi_rows = []
    for h in high_impact[:15]:
        hi_rows.append([
            h["name"][:45], h["category"], str(h["dependentCount"]),
            "MIGRATE" if h["id"] in migrate_ids else "SKIP"
        ])
    add_table(slide, 0.5, 3.4, 12, [5.5, 2, 2, 2.5],
              ["Object Name", "Category", "Dependents", "Migration Status"], hi_rows)

    add_text_box(slide, 0.5, 6.5, 12, 0.5,
                 "How dependencies were built: The /dependents REST API returned 404 on this server. "
                 "Dependencies were inferred from report/cube definitions by extracting attribute, metric, "
                 "and filter IDs from dataSource.dataTemplate.units and matching against the inventory.",
                 10, GRAY)

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 8: Staleness (with caveat)
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide)
    add_text_box(slide, 0.5, 0.3, 12, 0.6, "Staleness Analysis (Informational Only)", 28, WHITE, True)

    add_text_box(slide, 0.5, 0.9, 12, 0.6,
                 "IMPORTANT: dateModified only reflects when an object's definition was last edited. "
                 "It does NOT indicate when the object was last executed. A report built 5 years ago "
                 "could still be run daily by hundreds of users. This data is informational only "
                 "and was NOT used in the migration recommendation.",
                 12, YELLOW)

    # Stale buckets
    buckets = {"Active (< 1yr)": 0, "1-2 years": 0, "2-3 years": 0, "3-5 years": 0, "5+ years": 0}
    for s in stale:
        d = s["daysSinceModified"]
        if d < 365: buckets["Active (< 1yr)"] += 1
        elif d < 730: buckets["1-2 years"] += 1
        elif d < 1095: buckets["2-3 years"] += 1
        elif d < 1825: buckets["3-5 years"] += 1
        else: buckets["5+ years"] += 1
    active_count = total - len(stale)
    buckets["Active (< 1yr)"] = active_count

    stale_rows = [[label, f"{count:,}", f"{count/total*100:.1f}%"] for label, count in buckets.items()]
    stale_rows.append(["TOTAL", f"{total:,}", "100%"])
    add_table(slide, 0.5, 2.0, 5.5, [2.5, 1.5, 1.5],
              ["Time Since Last Modified", "Count", "%"], stale_rows)

    # Chart
    stale_chart_data = CategoryChartData()
    stale_chart_data.categories = list(buckets.keys())
    stale_chart_data.add_series("Objects", list(buckets.values()))
    stale_frame = slide.shapes.add_chart(
        XL_CHART_TYPE.PIE, Inches(6.5), Inches(2.0), Inches(6), Inches(4.5),
        stale_chart_data
    )
    stale_chart = stale_frame.chart
    stale_chart.has_legend = True
    stale_chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    stale_chart.legend.font.size = Pt(10)
    stale_chart.legend.font.color.rgb = LIGHT_GRAY

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 9: Unused Metrics
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide)
    add_text_box(slide, 0.5, 0.3, 12, 0.6, "Unused Metrics", 28, WHITE, True)
    add_text_box(slide, 0.5, 0.9, 12, 0.5,
                 f"{len(unused_metrics)} of {summary['byCategory'].get('metrics', 0)} metrics "
                 f"({len(unused_metrics)/max(summary['byCategory'].get('metrics', 1), 1)*100:.0f}%) "
                 f"are not referenced by any report or cube.",
                 13, GRAY)

    um_rows = [[m["name"][:50], m.get("owner", "-"), (m.get("dateModified") or "-").split("T")[0]]
               for m in unused_metrics[:30]]
    if len(unused_metrics) > 30:
        um_rows.append([f"... and {len(unused_metrics) - 30} more", "", ""])

    add_table(slide, 0.5, 1.6, 12, [6, 3, 3],
              ["Metric Name", "Owner", "Last Modified"], um_rows)

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 10: Methodology
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide)
    add_text_box(slide, 0.5, 0.3, 12, 0.6, "Methodology", 28, WHITE, True)

    methods = [
        ("Phase 1: Inventory",
         "Enumerated all 11 object types via GET /searches/results?type={type}. "
         "For reports (1,161) and cubes (70), fetched rich definitions via GET /model/reports/{id} "
         "and SQL via POST /v2/reports/{id}/instances + GET sqlView. "
         f"Total API calls: ~7,636. Runtime: 1h 54m with throttling (10 POST per batch, 5s pause)."),
        ("Phase 2: Dependency Graph",
         "The /objects/{id}/dependents REST API returned 404 on this server. "
         "Dependencies were inferred from report/cube definitions by extracting attribute IDs, "
         "metric IDs, and filter object IDs from dataSource.dataTemplate.units and the filter "
         "expression tree. Each ID was matched against the inventory to build edges. "
         f"Result: {summary['totalEdges']:,} edges across {len(dependents_of)} referenced objects."),
        ("Phase 3: Analysis",
         "Orphans: schema objects (metrics, attributes, filters, facts, prompts, tables) with zero "
         "entries in dependents_of. Duplicate SQL: normalized SQL (lowercase, strip comments, "
         "replace literals) hashed with SHA-256, grouped by hash. High-impact: objects sorted by "
         "dependent count. Staleness: dateModified parsed and compared to current date."),
        ("Migration Logic",
         "SKIP = orphans + duplicate removals (keep 1 per group). "
         "MIGRATE = everything else. This is conservative — without execution history, "
         "we cannot determine which unmodified reports are actively used, so we include them all."),
        ("What's Missing",
         "Execution history (who ran what, when) is not available via the REST API. "
         "This data lives in the MicroStrategy metadata warehouse (IS_REP_FACT, IS_EXEC_FACT tables). "
         "With this data, the migrate set could be further reduced by identifying truly unused reports."),
    ]

    y = 1.2
    for title, body in methods:
        add_text_box(slide, 0.5, y, 12, 0.3, title, 13, BLUE, True)
        add_text_box(slide, 0.5, y + 0.3, 12, 0.6, body, 10, LIGHT_GRAY)
        y += 1.1

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 11: Recommendations
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide)
    add_text_box(slide, 0.5, 0.3, 12, 0.6, "Recommendations", 28, WHITE, True)

    recs = [
        ("1. Archive 2,060 orphan objects",
         "These schema objects are not referenced by any consumer. They add clutter and "
         "maintenance burden. Archive them before migration.", RED),
        ("2. Consolidate 30 duplicate reports",
         "11 groups of reports with identical SQL. The 14-copy MSRP Price List is the "
         "biggest win — parameterize by year instead of duplicating.", RED),
        ("3. Migrate 1,840 objects",
         "All reports, documents, cubes, and their supporting schema objects. This is the "
         "conservative recommendation without execution history.", GREEN),
        ("4. Obtain execution history",
         "Query the MicroStrategy metadata database (IS_REP_FACT / IS_EXEC_FACT) for "
         "report execution counts over the last 6-12 months. This could reduce the "
         "migrate set significantly by identifying truly unused reports.", YELLOW),
        ("5. Protect high-impact objects",
         "The top 15 high-impact objects (mostly prompts) are used by 370-593 reports each. "
         "Any changes to these during migration must be carefully validated.", ORANGE),
        ("6. Run this analysis on remaining projects",
         "INSIGHT_prod and Global Insight_prod1 have not been analyzed yet. "
         "The same tool can be run against them.", BLUE),
    ]

    y = 1.2
    for title, body, color in recs:
        add_text_box(slide, 0.5, y, 12, 0.3, title, 14, color, True)
        add_text_box(slide, 0.8, y + 0.35, 11, 0.5, body, 11, LIGHT_GRAY)
        y += 0.9

    # ════════════════════════════════════════════════════════════════════
    # SLIDE 12: Next Steps
    # ════════════════════════════════════════════════════════════════════
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide)
    add_text_box(slide, 0.5, 0.3, 12, 0.6, "Next Steps", 28, WHITE, True)

    steps = [
        "1.  Review orphan list with business stakeholders to confirm no false positives",
        "2.  Obtain execution history from metadata database for usage-based filtering",
        "3.  Run rationalization on INSIGHT_prod and Global Insight_prod1 projects",
        "4.  Create migration manifest: prioritized list of objects by dependency order",
        "5.  Consolidate duplicate SQL groups before migration",
        "6.  Validate high-impact objects post-migration with regression testing",
        "7.  Archive confirmed unused objects in source environment",
    ]
    y = 1.3
    for step in steps:
        add_text_box(slide, 0.8, y, 11, 0.4, step, 14, LIGHT_GRAY)
        y += 0.55

    # Save
    prs.save(str(output_path))
    print(f"Deck saved to: {output_path.resolve()}")


if __name__ == "__main__":
    data_dir = Path("mstr_rationalization_full")
    output_path = Path("mstr_rationalization_full") / "Rationalization_Report_Global_Operational.pptx"
    build_deck(data_dir, output_path)
