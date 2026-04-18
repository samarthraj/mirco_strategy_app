#!/usr/bin/env python3
"""
Phase 2: Run all analysis on active INSIGHT reports.

Steps:
  1. Orphan Detection — metrics/attributes/filters not used by any active report
  2. Stale Object Detection — active reports not modified in 365+ days
  3. Duplicate SQL Detection — group reports with identical normalized SQL
  4. Unused Metrics — metrics not referenced by any active report
  5. High-Impact Objects — objects with the most dependents
  6. Active/Retire Classification — final summary

All local computation — no API calls needed.
"""

from __future__ import annotations
import json, sys, time
from collections import defaultdict
from pathlib import Path
from mstr_project_rationalize import (
    write_json, hash_sql, detect_orphans, detect_duplicate_sql,
    find_high_impact, find_stale_objects, find_unused_metrics,
    LEAF_CATEGORIES, SQL_CATEGORIES, _normalize_sql_preview,
)
from datetime import datetime, timezone


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run Phase 2 analysis on active reports")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--stale-days", type=int, default=365)
    parser.add_argument("--top-impact", type=int, default=50)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    inv_dir = output_dir / "inventory"
    analysis_dir = output_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    # ---------------------------------------------------------------
    # Load data
    # ---------------------------------------------------------------
    print("Loading data...")

    # Active reports with SQL
    sql_path = inv_dir / "active_reports_sql.json"
    enriched_path = inv_dir / "active_reports_enriched.json"

    if sql_path.exists():
        active_reports = json.loads(sql_path.read_text(encoding="utf-8"))
        print(f"  Active reports (with SQL): {len(active_reports)}")
    elif enriched_path.exists():
        active_reports = json.loads(enriched_path.read_text(encoding="utf-8"))
        print(f"  Active reports (enriched, no SQL): {len(active_reports)}")
    else:
        print("ERROR: No active reports file found.")
        return 1

    # Dedup info
    dedup_path = inv_dir / "active_reports_dedup.json"
    dedup_info = {}
    if dedup_path.exists():
        dedup_info = json.loads(dedup_path.read_text(encoding="utf-8"))
        print(f"  Dedup families: {dedup_info.get('familyCount', 0)}")
        print(f"  Duplicates skipped: {dedup_info.get('duplicateCount', 0)}")

    # Telemetry
    tel_path = output_dir / "telemetry" / "active_reports.json"
    retire_path = output_dir / "telemetry" / "retire_reports.json"
    active_tel = []
    retire_reports = []
    if tel_path.exists():
        active_tel = json.loads(tel_path.read_text(encoding="utf-8"))
        print(f"  Telemetry active: {len(active_tel)}")
    if retire_path.exists():
        retire_reports = json.loads(retire_path.read_text(encoding="utf-8"))
        print(f"  Telemetry retire: {len(retire_reports)}")

    # Supporting inventory (metrics, attributes, filters, etc.)
    inventory = {"reports": active_reports}
    for cat in ("metrics", "attributes", "filters", "cubes", "facts", "tables",
                "prompts", "documents", "security_filters", "custom_groups"):
        cat_file = inv_dir / f"{cat}.json"
        if cat_file.exists():
            inventory[cat] = json.loads(cat_file.read_text(encoding="utf-8"))

    total_objects = sum(len(recs) for recs in inventory.values())
    print(f"  Total objects loaded: {total_objects}")

    # Dependencies
    dep_path = output_dir / "active_dependencies.json"
    graph = {"edges": [], "dependents_of": {}, "dependencies_of": {}, "processed_ids": []}
    if dep_path.exists():
        graph = json.loads(dep_path.read_text(encoding="utf-8"))
        print(f"  Dependency edges: {len(graph.get('edges', []))}")

    # Paths
    paths_path = inv_dir / "active_reports_paths.json"
    if paths_path.exists():
        paths_data = json.loads(paths_path.read_text(encoding="utf-8"))
        path_by_id = {r["id"]: r.get("folderPath", "") for r in paths_data}
        # Merge paths into active reports
        for rec in active_reports:
            if not rec.get("folderPath") and rec["id"] in path_by_id:
                rec["folderPath"] = path_by_id[rec["id"]]
        print(f"  Paths merged: {len(path_by_id)}")

    # ---------------------------------------------------------------
    # Step 1: Orphan Detection
    # ---------------------------------------------------------------
    print("\n[1/6] Detecting orphans...")
    orphans = detect_orphans(inventory, graph)
    write_json(analysis_dir / "orphans.json", orphans)
    print(f"  Orphans found: {len(orphans)}")

    # ---------------------------------------------------------------
    # Step 2: Stale Object Detection
    # ---------------------------------------------------------------
    print("\n[2/6] Detecting stale objects...")
    stale = find_stale_objects(inventory, stale_days=args.stale_days)
    write_json(analysis_dir / "stale_objects.json", stale)
    print(f"  Stale objects (>{args.stale_days} days): {len(stale)}")

    # ---------------------------------------------------------------
    # Step 3: Duplicate SQL Detection
    # ---------------------------------------------------------------
    print("\n[3/6] Detecting duplicate SQL...")

    # Ensure all reports have sqlHash
    for rec in active_reports:
        if rec.get("sql") and not rec.get("sqlHash"):
            rec["sqlHash"] = hash_sql(rec["sql"])

    duplicates = detect_duplicate_sql(inventory)
    write_json(analysis_dir / "duplicate_sql.json", duplicates)
    dup_report_count = sum(g["count"] for g in duplicates)
    print(f"  Duplicate SQL groups: {len(duplicates)}")
    print(f"  Reports in duplicate groups: {dup_report_count}")

    # ---------------------------------------------------------------
    # Step 4: Unused Metrics
    # ---------------------------------------------------------------
    print("\n[4/6] Finding unused metrics...")
    unused_metrics = find_unused_metrics(inventory, graph)
    write_json(analysis_dir / "unused_metrics.json", unused_metrics)
    print(f"  Unused metrics: {len(unused_metrics)} / {len(inventory.get('metrics', []))}")

    # ---------------------------------------------------------------
    # Step 5: High-Impact Objects
    # ---------------------------------------------------------------
    print("\n[5/6] Finding high-impact objects...")
    high_impact = find_high_impact(inventory, graph, top_n=args.top_impact)
    write_json(analysis_dir / "high_impact.json", high_impact)
    print(f"  Top {args.top_impact} high-impact objects saved")
    if high_impact:
        print(f"  #1: {high_impact[0]['name']} ({high_impact[0]['dependentCount']} dependents)")

    # ---------------------------------------------------------------
    # Step 6: Active/Retire Classification Summary
    # ---------------------------------------------------------------
    print("\n[6/6] Building final classification...")

    # Collect report-level stats
    active_with_sql = sum(1 for r in active_reports if r.get("sql"))
    active_cube_sourced = sum(1 for r in active_reports if r.get("sourceCubeId"))
    active_prompted = sum(1 for r in active_reports if r.get("prompted"))
    active_stale_ids = {s["id"] for s in stale}
    active_and_stale = sum(1 for r in active_reports if r["id"] in active_stale_ids)

    # Exec counts from telemetry
    exec_by_id = {r["id"]: r.get("totalExecutions", 0) for r in active_tel}
    users_by_id = {r["id"]: r.get("totalUsers", 0) for r in active_tel}

    classification = {
        "totalInventoryReports": len(json.loads((inv_dir / "reports.json").read_text(encoding="utf-8"))) if (inv_dir / "reports.json").exists() else 0,
        "telemetryActive": len(active_tel),
        "telemetryRetire": len(retire_reports),
        "dedupMethod": dedup_info.get("method", "none"),
        "dedupFamilies": dedup_info.get("familyCount", 0),
        "dedupSkipped": dedup_info.get("duplicateCount", 0),
        "uniqueActiveReports": len(active_reports),
        "withDefinitions": sum(1 for r in active_reports if r.get("definition")),
        "withSQL": active_with_sql,
        "cubeSourced": active_cube_sourced,
        "prompted": active_prompted,
        "staleAndActive": active_and_stale,
        "duplicateSqlGroups": len(duplicates),
        "duplicateSqlReports": dup_report_count,
        "orphanCount": len(orphans),
        "unusedMetrics": len(unused_metrics),
        "totalMetrics": len(inventory.get("metrics", [])),
        "totalAttributes": len(inventory.get("attributes", [])),
        "totalFilters": len(inventory.get("filters", [])),
        "dependencyEdges": len(graph.get("edges", [])),
    }
    write_json(analysis_dir / "classification.json", classification)

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    error_count = sum(
        1 for recs in inventory.values() for obj in recs
        if any(obj.get("errors", {}).values())
    )

    summary = {
        "totalObjects": total_objects,
        "byCategory": {cat: len(recs) for cat, recs in inventory.items()},
        "totalEdges": len(graph.get("edges", [])),
        "orphanCount": len(orphans),
        "duplicateSqlGroupCount": len(duplicates),
        "duplicateSqlObjectCount": dup_report_count,
        "highImpactCount": len(high_impact),
        "staleObjectCount": len(stale),
        "unusedMetricCount": len(unused_metrics),
        "errorCount": error_count,
    }
    write_json(analysis_dir / "summary.json", summary)

    # Combined rationalization report
    objects_lookup = {}
    for records in inventory.values():
        for obj in records:
            entry = {
                "id": obj["id"],
                "name": obj.get("name"),
                "category": obj.get("category"),
                "type": obj.get("type"),
                "owner": obj.get("owner"),
                "dateModified": obj.get("dateModified"),
            }
            if obj.get("folderPath"):
                entry["folderPath"] = obj["folderPath"]
            objects_lookup[obj["id"]] = entry

    error_list = []
    for records in inventory.values():
        for obj in records:
            errors = obj.get("errors", {})
            if errors:
                for err_type, err_msg in errors.items():
                    if err_msg:
                        error_list.append({
                            "id": obj["id"],
                            "name": obj.get("name"),
                            "category": obj.get("category"),
                            "errorType": err_type,
                            "message": str(err_msg)[:300],
                        })

    report = {
        "summary": summary,
        "classification": classification,
        "orphans": orphans,
        "duplicateSql": duplicates,
        "highImpact": high_impact,
        "staleObjects": stale,
        "unusedMetrics": unused_metrics,
        "errors": error_list,
        "objects": objects_lookup,
        "dependentsOf": graph.get("dependents_of", {}),
        "dependenciesOf": graph.get("dependencies_of", {}),
    }
    write_json(output_dir / "rationalization_report.json", report)

    # Update manifest
    manifest_path = output_dir / "run_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if "analysis" not in manifest.get("completedPhases", []):
            manifest["completedPhases"].append("analysis")
        manifest["finishedAt"] = datetime.now(timezone.utc).isoformat()
        write_json(manifest_path, manifest)

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  ANALYSIS COMPLETE [{elapsed:.1f}s]")
    print(f"{'='*60}")
    print(f"  Total objects:         {total_objects:,}")
    print(f"  Dependency edges:      {len(graph.get('edges', [])):,}")
    print(f"  Orphans:               {len(orphans):,}")
    print(f"  Duplicate SQL groups:  {len(duplicates):,}")
    print(f"  Duplicate SQL reports: {dup_report_count:,}")
    print(f"  Stale objects:         {len(stale):,}")
    print(f"  Unused metrics:        {len(unused_metrics):,}")
    print(f"  High-impact (top {args.top_impact}):  {len(high_impact)}")
    print(f"  Errors:                {error_count:,}")
    print(f"{'='*60}")
    print(f"\nOutput: {analysis_dir}")
    print(f"Full report: {output_dir / 'rationalization_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
