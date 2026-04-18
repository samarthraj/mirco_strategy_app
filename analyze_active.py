#!/usr/bin/env python3
"""Run analysis (duplicate SQL, unused metrics, orphans) against ONLY the de-duped active set."""

import json, re, sys
from collections import defaultdict, Counter
from pathlib import Path
from hashlib import sha256


def _base_name(name):
    parts = name.strip().rsplit(" - ", 1)
    if len(parts) == 2 and len(parts[0]) >= 10:
        return parts[0].strip()
    return name.strip()


def normalize_sql(sql: str) -> str:
    """Strip comments and normalize whitespace/literals for hashing."""
    if not sql:
        return ""
    # Remove line + block comments
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    # Normalize whitespace
    sql = re.sub(r"\s+", " ", sql).strip().lower()
    # Normalize string literals and numbers
    sql = re.sub(r"'[^']*'", "'?'", sql)
    sql = re.sub(r"\b\d+\b", "?", sql)
    return sql


def hash_sql(sql: str) -> str:
    return sha256(normalize_sql(sql).encode("utf-8")).hexdigest()


def main():
    output_dir = Path("Global Insight")

    print("Loading active reports...", flush=True)
    with open(output_dir / "telemetry" / "active_reports.json", "r", encoding="utf-8") as f:
        active = json.load(f)

    # De-dup by family
    groups = defaultdict(list)
    for r in active:
        groups[_base_name(r.get("name", ""))].append(r)

    deduped_ids = set()
    for base, recs in groups.items():
        if len(recs) == 1:
            deduped_ids.add(recs[0]["id"])
        else:
            parent = max(recs, key=lambda x: x.get("totalExecutions", 0))
            deduped_ids.add(parent["id"])

    print(f"  {len(active):,} active -> {len(deduped_ids):,} de-duped", flush=True)

    print("Loading full inventory...", flush=True)
    with open(output_dir / "inventory" / "reports.json", "r", encoding="utf-8") as f:
        all_reports = json.load(f)

    # Filter to de-duped active
    active_reports = [r for r in all_reports if r["id"] in deduped_ids]
    print(f"  {len(active_reports):,} active reports loaded from inventory", flush=True)

    # ============================================================
    # 1. DUPLICATE SQL — among active reports only
    # ============================================================
    print("\n[1] Detecting duplicate SQL among active reports...", flush=True)
    by_hash = defaultdict(list)
    reports_with_sql = 0
    for r in active_reports:
        sql = r.get("sql")
        if sql:
            reports_with_sql += 1
            h = hash_sql(sql)
            by_hash[h].append({
                "id": r["id"],
                "name": r.get("name", ""),
                "folderPath": r.get("folderPath", ""),
                "sqlLength": len(sql),
            })

    duplicate_groups = []
    for h, objs in by_hash.items():
        if len(objs) >= 2:
            duplicate_groups.append({
                "sqlHash": h,
                "count": len(objs),
                "objects": objs,
            })

    total_in_dupes = sum(g["count"] for g in duplicate_groups)
    reducible = total_in_dupes - len(duplicate_groups)

    print(f"  Reports with SQL:     {reports_with_sql:,}")
    print(f"  Duplicate groups:     {len(duplicate_groups):,}")
    print(f"  Reports in dupes:     {total_in_dupes:,}")
    print(f"  Reducible:            {reducible:,}")

    # ============================================================
    # 2. UNUSED METRICS — relative to active reports only
    # ============================================================
    print("\n[2] Finding unused metrics (relative to active reports only)...", flush=True)
    used_metric_ids = set()
    for r in active_reports:
        for m in (r.get("metrics") or []):
            if isinstance(m, dict) and m.get("id"):
                used_metric_ids.add(m["id"])

    with open(output_dir / "inventory" / "metrics.json", "r", encoding="utf-8") as f:
        all_metrics = json.load(f)

    unused_metrics = [m for m in all_metrics if m["id"] not in used_metric_ids]
    print(f"  Total metrics:                 {len(all_metrics):,}")
    print(f"  Used by active reports:        {len(used_metric_ids):,}")
    print(f"  Unused (retire candidates):    {len(unused_metrics):,}")

    # ============================================================
    # 3. UNUSED ATTRIBUTES — relative to active reports only
    # ============================================================
    print("\n[3] Finding unused attributes...", flush=True)
    used_attr_ids = set()
    for r in active_reports:
        for a in (r.get("attributes") or []):
            if isinstance(a, dict) and a.get("id"):
                used_attr_ids.add(a["id"])

    with open(output_dir / "inventory" / "attributes.json", "r", encoding="utf-8") as f:
        all_attrs = json.load(f)

    unused_attrs = [a for a in all_attrs if a["id"] not in used_attr_ids]
    print(f"  Total attributes:              {len(all_attrs):,}")
    print(f"  Used by active reports:        {len(used_attr_ids):,}")
    print(f"  Unused:                        {len(unused_attrs):,}")

    # ============================================================
    # 4. SOURCE TABLE ANALYSIS
    # ============================================================
    print("\n[4] Source table analysis...", flush=True)
    table_usage = Counter()
    for r in active_reports:
        for t in (r.get("sourceTables") or []):
            table_usage[t] += 1

    print(f"  Unique source tables: {len(table_usage):,}")
    print(f"  Top 10 most-used tables:")
    for tbl, count in table_usage.most_common(10):
        print(f"    {tbl[:60]:<60} {count:>5}")

    # ============================================================
    # Save results
    # ============================================================
    out_dir = output_dir / "analysis_active"
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "duplicate_sql.json", "w", encoding="utf-8") as f:
        json.dump(duplicate_groups, f, indent=2, default=str)
    with open(out_dir / "unused_metrics.json", "w", encoding="utf-8") as f:
        json.dump(unused_metrics, f, indent=2, default=str)
    with open(out_dir / "unused_attributes.json", "w", encoding="utf-8") as f:
        json.dump(unused_attrs, f, indent=2, default=str)

    summary = {
        "dedupedActiveReports": len(deduped_ids),
        "reportsWithSql": reports_with_sql,
        "duplicateSqlGroups": len(duplicate_groups),
        "reportsInDupes": total_in_dupes,
        "reducibleViaDupeSql": reducible,
        "finalAfterAllDedup": len(deduped_ids) - reducible,
        "totalMetrics": len(all_metrics),
        "usedMetrics": len(used_metric_ids),
        "unusedMetrics": len(unused_metrics),
        "totalAttributes": len(all_attrs),
        "usedAttributes": len(used_attr_ids),
        "unusedAttributes": len(unused_attrs),
        "uniqueSourceTables": len(table_usage),
        "topTables": [{"table": t, "count": c} for t, c in table_usage.most_common(20)],
    }
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\nResults saved to {out_dir}/")
    print(f"\n{'='*60}")
    print(f"  FINAL NUMBERS")
    print(f"{'='*60}")
    print(f"  De-duped active:          {len(deduped_ids):,}")
    print(f"  After SQL de-dup:         {len(deduped_ids) - reducible:,}")
    print(f"  Unused metrics to remove: {len(unused_metrics):,}")
    print(f"  Unused attrs to remove:   {len(unused_attrs):,}")


if __name__ == "__main__":
    main()
