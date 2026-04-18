#!/usr/bin/env python3
"""
AST-based SQL analysis using sqlglot.

Reuses the existing ast_cache.json to skip the slow parsing step.
Outputs exact-AST duplicate groups (much faster than full pairwise comparison).
"""

import hashlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "Global Operational/scripts")
from sql_ast_sim import compute_ast_hashes, subtree_hashes, parse_to_trees, jaccard


def main():
    project_dir = Path("INSIGHT/rationalization")
    reports_path = project_dir / "inventory" / "active_reports_sql.json"
    cache_path = project_dir / "analysis" / "ast_cache.json"

    # Patch the module's global paths
    import sql_ast_sim
    sql_ast_sim.REPORTS_JSON = reports_path
    sql_ast_sim.CACHE_PATH = cache_path

    print(f"Loading {reports_path}...")
    data = json.loads(reports_path.read_text(encoding="utf-8"))
    with_sql = [r for r in data if r.get("sql")]
    print(f"  {len(data)} reports, {len(with_sql)} with SQL")

    t0 = time.perf_counter()
    hashes_by_id, errors_by_id = compute_ast_hashes(
        with_sql, dialect="redshift", use_cache=True, verbose=True
    )
    elapsed = time.perf_counter() - t0
    print(f"  Parsed {len(hashes_by_id)}/{len(with_sql)} in {elapsed:.1f}s")

    if errors_by_id:
        print(f"  Parse failures: {len(errors_by_id)}")

    # Exact AST groups
    name_by_id = {r["id"]: r.get("name", "") for r in with_sql}
    groups = defaultdict(list)
    for rid, h in hashes_by_id.items():
        groups[frozenset(h)].append(rid)

    multi = [g for g in groups.values() if len(g) >= 2]
    dup_reports = sum(len(g) for g in multi)
    removable = sum(len(g) - 1 for g in multi)

    print(f"\nExact-AST groups: {len(multi)} multi-member groups")
    print(f"  Reports in groups: {dup_reports}")
    print(f"  Removable: {removable}")
    print()
    print("Top 15 largest groups:")
    for g in sorted(multi, key=lambda x: -len(x))[:15]:
        name = name_by_id[g[0]][:60]
        print(f"  size {len(g):4d}: {name}")

    # Save exact-AST duplicate analysis to a file
    ast_dup_output = {
        "totalReportsWithSql": len(with_sql),
        "parsedSuccessfully": len(hashes_by_id),
        "parseFailures": len(errors_by_id),
        "exactAstGroups": len(multi),
        "reportsInGroups": dup_reports,
        "removableReports": removable,
        "groups": [],
    }
    for g in sorted(multi, key=lambda x: -len(x)):
        ast_dup_output["groups"].append({
            "size": len(g),
            "primaryName": name_by_id[g[0]],
            "reportIds": g,
            "reportNames": [name_by_id[rid] for rid in g],
        })

    out_path = project_dir / "analysis" / "ast_duplicates.json"
    out_path.write_text(json.dumps(ast_dup_output, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
