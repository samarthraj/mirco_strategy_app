#!/usr/bin/env python3
"""
Generate missing artifacts to match Global Insight output:
  1. parent_reports.csv — parent report family analysis (name, ID, copies, children, total)
  2. analysis_active/ folder — active-only analysis with unused_attributes, unused_metrics, top tables
"""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


OUTPUT_DIR = Path("INSIGHT/rationalization")


def main():
    # Load data
    enriched = json.loads(
        (OUTPUT_DIR / "inventory" / "active_reports_enriched.json").read_text(encoding="utf-8")
    )
    sql_reports = json.loads(
        (OUTPUT_DIR / "inventory" / "active_reports_sql.json").read_text(encoding="utf-8")
    )
    active = json.loads(
        (OUTPUT_DIR / "telemetry" / "active_reports.json").read_text(encoding="utf-8")
    )
    duplicates = json.loads(
        (OUTPUT_DIR / "analysis" / "duplicate_sql.json").read_text(encoding="utf-8")
    )
    dedup_info = json.loads(
        (OUTPUT_DIR / "inventory" / "active_reports_dedup.json").read_text(encoding="utf-8")
    )
    classification = json.loads(
        (OUTPUT_DIR / "analysis" / "classification.json").read_text(encoding="utf-8")
    )

    # Attributes and metrics from full inventory (for unused checks)
    all_attributes = json.loads(
        (OUTPUT_DIR / "inventory" / "attributes.json").read_text(encoding="utf-8")
    )
    all_metrics = json.loads(
        (OUTPUT_DIR / "inventory" / "metrics.json").read_text(encoding="utf-8")
    )

    # ================================================================
    # 1. parent_reports.csv — definition-based family analysis
    # ================================================================
    print("Generating parent_reports.csv...")

    # Build map: id -> executions for sort ordering
    exec_by_id = {r["id"]: r.get("totalExecutions", 0) for r in active}

    # Each family from dedup_info is a "parent" report with children
    # Format: Report Name, Report ID, Copies (always 1), Children (family_size - 1), Total Family Size
    rows = []

    # Add reports from families (parent + children)
    family_parent_ids = set()
    for fam in dedup_info.get("families", []):
        parent_id = fam["parentId"]
        family_parent_ids.add(parent_id)
        family_size = fam["familySize"]
        rows.append({
            "Report Name": fam["parentName"],
            "Report ID": parent_id,
            "Copies": 1,
            "Children": family_size - 1,
            "Total Family Size": family_size,
        })

    # Add standalone reports (no family — family size 1)
    enriched_by_id = {r["id"]: r for r in enriched}
    children_ids = set()
    for fam in dedup_info.get("families", []):
        for child in fam.get("children", []):
            children_ids.add(child["id"])

    for r in enriched:
        if r["id"] in family_parent_ids or r["id"] in children_ids:
            continue
        rows.append({
            "Report Name": r.get("name", ""),
            "Report ID": r["id"],
            "Copies": 1,
            "Children": 0,
            "Total Family Size": 1,
        })

    # Sort by family size descending, then by name
    rows.sort(key=lambda r: (-r["Total Family Size"], r["Report Name"].lower()))

    out_csv = OUTPUT_DIR / "parent_reports.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Report Name", "Report ID", "Copies", "Children", "Total Family Size"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Wrote {len(rows):,} parent reports to {out_csv}")
    print(f"  Families (2+ members): {len(dedup_info.get('families', []))}")
    print(f"  Top family size: {rows[0]['Total Family Size'] if rows else 0}")

    # ================================================================
    # 2. analysis_active/ folder
    # ================================================================
    print("\nGenerating analysis_active/ folder...")

    analysis_active = OUTPUT_DIR / "analysis_active"
    analysis_active.mkdir(parents=True, exist_ok=True)

    # De-duped active report IDs (parents only from families + standalones)
    deduped_ids = set(r["id"] for r in enriched) - children_ids

    # Reports with SQL among de-duped
    sql_by_id = {r["id"]: r for r in sql_reports}
    reports_with_sql = sum(1 for rid in deduped_ids if sql_by_id.get(rid, {}).get("sql"))

    # Duplicate SQL groups restricted to de-duped set
    active_dupe_groups = []
    for g in duplicates:
        members = [o for o in g["objects"] if o["id"] in deduped_ids]
        if len(members) >= 2:
            active_dupe_groups.append(members)
    reports_in_dupes = sum(len(g) for g in active_dupe_groups)
    reducible_via_dupe_sql = reports_in_dupes - len(active_dupe_groups)

    # Semantic dedup results
    reviews_path = OUTPUT_DIR / "sql" / "cluster_reviews.json"
    semantic_removable = 0
    if reviews_path.exists():
        rev_data = json.loads(reviews_path.read_text(encoding="utf-8"))
        for rev in rev_data.get("reviews", []):
            a = rev.get("analysis", {})
            if "error" not in a:
                semantic_removable += a.get("removable_count", 0)

    final_after_all_dedup = len(deduped_ids) - reducible_via_dupe_sql - semantic_removable

    # Used metrics/attributes: compute from enriched active reports
    used_metric_ids = set()
    used_attr_ids = set()
    for r in enriched:
        for m in r.get("metrics", []):
            if m.get("id"):
                used_metric_ids.add(m["id"])
        for a in r.get("attributes", []):
            if a.get("id"):
                used_attr_ids.add(a["id"])

    unused_metrics = [m for m in all_metrics if m["id"] not in used_metric_ids]
    unused_attributes = [a for a in all_attributes if a["id"] not in used_attr_ids]

    # Top source tables across active reports
    table_counter = Counter()
    for r in sql_reports:
        for t in r.get("sourceTables", []):
            table_counter[t] += 1
    top_tables = [{"table": t, "count": c} for t, c in table_counter.most_common(20)]

    # Save summary
    summary_active = {
        "dedupedActiveReports": len(deduped_ids),
        "reportsWithSql": reports_with_sql,
        "duplicateSqlGroups": len(active_dupe_groups),
        "reportsInDupes": reports_in_dupes,
        "reducibleViaDupeSql": reducible_via_dupe_sql,
        "semanticClustersReviewed": len(rev_data.get("reviews", [])) if reviews_path.exists() else 0,
        "reducibleViaSemantic": semantic_removable,
        "finalAfterAllDedup": final_after_all_dedup,
        "totalMetrics": len(all_metrics),
        "usedMetrics": len(used_metric_ids),
        "unusedMetrics": len(unused_metrics),
        "totalAttributes": len(all_attributes),
        "usedAttributes": len(used_attr_ids),
        "unusedAttributes": len(unused_attributes),
        "uniqueSourceTables": len(table_counter),
        "topTables": top_tables,
    }
    (analysis_active / "summary.json").write_text(
        json.dumps(summary_active, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Save unused metrics (full records for downstream analysis)
    (analysis_active / "unused_metrics.json").write_text(
        json.dumps(unused_metrics, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    # Save unused attributes
    (analysis_active / "unused_attributes.json").write_text(
        json.dumps(unused_attributes, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    # Save duplicate SQL groups (restricted to active de-duped)
    dup_out = []
    for members in active_dupe_groups:
        dup_out.append({
            "count": len(members),
            "objects": members,
        })
    (analysis_active / "duplicate_sql.json").write_text(
        json.dumps(dup_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"  summary.json                  — {summary_active['dedupedActiveReports']:,} de-duped active")
    print(f"  unused_metrics.json           — {len(unused_metrics):,} unused")
    print(f"  unused_attributes.json        — {len(unused_attributes):,} unused")
    print(f"  duplicate_sql.json            — {len(active_dupe_groups):,} groups")

    print(f"\nDone!")
    print(f"  parent_reports.csv  -> {out_csv}")
    print(f"  analysis_active/    -> {analysis_active}")


if __name__ == "__main__":
    main()
