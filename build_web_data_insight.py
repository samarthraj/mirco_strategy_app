#!/usr/bin/env python3
"""Build web dashboard JSON files for INSIGHT."""

import json
from collections import Counter, defaultdict
from pathlib import Path

from build_web_data_common import (
    collapse_telemetry_collisions,
    emit_collisions,
    enrich_collisions_with_paths,
    emit_inventory_all,
    emit_retired_list,
)


PROJECT_DIR = Path("INSIGHT/rationalization")
OUTPUT_DIR = Path("public/data/INSIGHT")


def family_base(name):
    parts = name.rsplit(" - ", 1)
    if len(parts) == 2 and len(parts[0]) >= 10:
        return parts[0].strip()
    return name.strip()


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Loading data from {PROJECT_DIR}...")

    all_reports = json.loads((PROJECT_DIR / "inventory" / "reports.json").read_text(encoding="utf-8"))
    active = json.loads((PROJECT_DIR / "telemetry" / "active_reports.json").read_text(encoding="utf-8"))
    retire = json.loads((PROJECT_DIR / "telemetry" / "retire_reports.json").read_text(encoding="utf-8"))
    analysis = json.loads((PROJECT_DIR / "analysis" / "summary.json").read_text(encoding="utf-8"))
    enriched = json.loads((PROJECT_DIR / "inventory" / "active_reports_enriched.json").read_text(encoding="utf-8"))
    sql_reports = json.loads((PROJECT_DIR / "inventory" / "active_reports_sql.json").read_text(encoding="utf-8"))

    # Merge paths/sql/enriched back into all_reports
    paths_by_id = {}
    paths_path = PROJECT_DIR / "inventory" / "active_reports_paths.json"
    if paths_path.exists():
        for r in json.loads(paths_path.read_text(encoding="utf-8")):
            paths_by_id[r["id"]] = r.get("folderPath", "")

    enriched_by_id = {r["id"]: r for r in enriched}
    sql_by_id = {r["id"]: r for r in sql_reports}

    # Build merged reports dict
    merged = {}
    for r in all_reports:
        merged[r["id"]] = dict(r)
    for rid, pth in paths_by_id.items():
        if rid in merged:
            merged[rid]["folderPath"] = pth
    for rid, rec in enriched_by_id.items():
        if rid in merged:
            merged[rid].update({k: v for k, v in rec.items() if k in ("definition", "filter", "attributes", "metrics", "sourceType", "sourceCubeId")})
    for rid, rec in sql_by_id.items():
        if rid in merged:
            merged[rid].update({k: v for k, v in rec.items() if k in ("sql", "sqlHash", "sourceTables", "prompted")})

    all_reports = list(merged.values())

    # Manifest
    manifest = {}
    manifest_path = PROJECT_DIR / "run_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Active-only analysis
    active_analysis = {}
    a_sum_path = PROJECT_DIR / "analysis_active" / "summary.json"
    if a_sum_path.exists():
        active_analysis = json.loads(a_sum_path.read_text(encoding="utf-8"))

    # --- Telemetry-row collision collapse (shared with GO / GI) ------------
    active_lookup_for_collapse = {}
    for a in active:
        active_lookup_for_collapse[a["id"]] = {
            "totalExecutions": a.get("totalExecutions", 0),
            "totalUsers": a.get("totalUsers", 0),
            "lastExecTs": a.get("lastExecTs", ""),
            "matchTier": a.get("matchTier", ""),
        }
    reports_by_id = {r["id"]: r for r in all_reports}
    combined_for_collapse = []
    for a in active:
        full = reports_by_id.get(a["id"])
        if full:
            merged = dict(full)
            merged.setdefault("id", a["id"])
            combined_for_collapse.append(merged)
        else:
            combined_for_collapse.append(dict(a))

    pre_collision = len(combined_for_collapse)
    print("Collapsing telemetry-row collisions...")
    canonical, collision_groups, _collapsed = collapse_telemetry_collisions(
        combined_for_collapse, active_lookup_for_collapse, verbose=True,
    )
    after_collision_collapse = len(canonical)
    collision_collapsed = pre_collision - after_collision_collapse
    canonical_ids = {r["id"] for r in canonical}
    # Defer collisions.json — emitted later after path_lookup is built.

    # Filter active to post-collision canonicals so downstream dedup steps
    # (family, fingerprint, similarity) share the same baseline population.
    active = [a for a in active if a["id"] in canonical_ids]

    # Classification
    classification = {}
    c_path = PROJECT_DIR / "analysis" / "classification.json"
    if c_path.exists():
        classification = json.loads(c_path.read_text(encoding="utf-8"))

    # Dedup info (definition-based)
    dedup_info = {}
    d_path = PROJECT_DIR / "inventory" / "active_reports_dedup.json"
    if d_path.exists():
        dedup_info = json.loads(d_path.read_text(encoding="utf-8"))

    # Families (name-based for UI)
    groups = defaultdict(list)
    for r in active:
        groups[family_base(r.get("name", ""))].append(r)

    families_dict = {b: recs for b, recs in groups.items() if len(recs) >= 2}

    # De-duped IDs from definition-based families
    child_ids = set()
    for fam in dedup_info.get("families", []):
        for c in fam.get("children", []):
            child_ids.add(c["id"])
    deduped_ids = {r["id"] for r in active if r["id"] not in child_ids}

    total_inventory = len(all_reports)
    # after_telemetry = BEFORE collision collapse (the original size of active)
    after_telemetry = pre_collision
    # Prefer classification.json when it's consistent with post-collision active
    # (i.e., no larger than the canonical set). Otherwise fall back to the
    # locally-computed deduped_ids count.
    classification_family = classification.get("uniqueActiveReports")
    if classification_family is not None and classification_family <= after_collision_collapse:
        after_family = classification_family
    else:
        after_family = len(deduped_ids)
    family_reducible = after_collision_collapse - after_family

    # Exact SQL dup
    fp_groups = active_analysis.get("duplicateSqlGroups", 0)
    fp_removable = active_analysis.get("reducibleViaDupeSql", 0)
    after_fingerprint = after_family - fp_removable

    # AST
    ast_data = {}
    ast_cache_path = PROJECT_DIR / "analysis" / "ast_cache.json"
    if ast_cache_path.exists():
        ast_data = json.loads(ast_cache_path.read_text(encoding="utf-8"))

    ast_groups = defaultdict(list)
    if isinstance(ast_data, dict):
        # ast_cache is keyed by sql_hash, not report id
        # Convert to report_id -> hashes via sql_hash lookup
        sql_hash_to_ast = ast_data
        # Map report_id to ast hashes via the SQL content
        import hashlib
        def sql_hash(sql):
            return hashlib.sha256(sql.encode("utf-8", "replace")).hexdigest()[:16]

        report_to_ast = {}
        for r in sql_reports:
            if r.get("sql"):
                sh = sql_hash(r["sql"])
                if sh in sql_hash_to_ast:
                    report_to_ast[r["id"]] = sql_hash_to_ast[sh]

        for rid, hashes in report_to_ast.items():
            ast_groups[frozenset(hashes)].append(rid)

    ast_multi = [g for g in ast_groups.values() if len(g) >= 2]
    ast_parsed = len(report_to_ast) if ast_data else 0
    ast_removable = sum(len(g) - 1 for g in ast_multi)

    # LLM reviews
    llm_reviews = []
    rv_path = PROJECT_DIR / "sql" / "cluster_reviews.json"
    if rv_path.exists():
        rv = json.loads(rv_path.read_text(encoding="utf-8"))
        llm_reviews = rv.get("reviews", []) if isinstance(rv, dict) else rv
    llm_removable_raw = sum((r.get("analysis") or {}).get("removable_count", 0) for r in llm_reviews)
    # Clamp so the funnel stays sequential — LLM removal can't exceed the
    # current (after-fingerprint) canonical population.
    llm_removable = min(llm_removable_raw, max(0, after_fingerprint - 1))

    # Semantic clusters
    semantic_data = {}
    sc_path = PROJECT_DIR / "sql" / "semantic_clusters.json"
    if sc_path.exists():
        semantic_data = json.loads(sc_path.read_text(encoding="utf-8"))
    sc_summary = semantic_data.get("summary", {}) if isinstance(semantic_data, dict) else {}
    clusters_list = semantic_data.get("clusters", []) if isinstance(semantic_data, dict) else []

    after_similarity = after_fingerprint - llm_removable
    reduction_total = total_inventory - after_similarity
    reduction_pct = reduction_total / total_inventory * 100 if total_inventory else 0.0

    # Funnel
    funnel = [
        {"label": "Original Inventory", "value": total_inventory, "detail": "All reports found"},
        {"label": "After Telemetry", "value": after_telemetry,
         "detail": f"Retired {len(retire):,} reports with no recent executions"},
    ]
    if collision_collapsed > 0:
        funnel.append({
            "label": "After Collision Collapse",
            "value": after_collision_collapse,
            "detail": f"{collision_collapsed} reports collapsed by telemetry-row signature",
        })
    funnel.extend([
        {"label": "After Definition Dedup", "value": after_family,
         "detail": f"Collapsed {family_reducible:,} duplicate copies (definition fingerprint)"},
        {"label": "After Exact-SQL De-dup", "value": after_fingerprint,
         "detail": f"{fp_removable:,} removable via identical SQL hashing"},
        {"label": "After LLM Parameterization", "value": after_similarity,
         "detail": f"{llm_removable:,} removable via semantic clustering + LLM review"},
    ])

    # Tier breakdown
    tier = {"exact": 0, "fuzzy": 0, "noMatch": 0}
    for r in active:
        t = r.get("matchTier", "")
        if t == "exact_name":
            tier["exact"] += 1
        elif t == "fuzzy":
            tier["fuzzy"] += 1
    tier["noMatch"] = len(retire)

    # Object types
    obj_types = []
    for cat, count in sorted((analysis.get("byCategory") or {}).items(), key=lambda x: -x[1]):
        if count > 0:
            obj_types.append({"name": cat.replace("_", " ").title(), "count": count})

    # Top metrics, tables
    metric_counter = Counter()
    table_counter = Counter()
    for r in all_reports:
        if r["id"] not in deduped_ids:
            continue
        for m in (r.get("metrics") or []):
            if isinstance(m, dict) and m.get("name"):
                metric_counter[m["name"]] += 1
        for t in (r.get("sourceTables") or []):
            table_counter[t.strip('"').split(".")[-1].lower()] += 1

    top_metrics = [{"name": n, "count": c} for n, c in metric_counter.most_common(20)]
    top_tables = [{"name": n, "count": c} for n, c in table_counter.most_common(20)]
    top_filters = []

    # Top executed
    top_executed = []
    for r in sorted(active, key=lambda x: -(x.get("totalExecutions") or 0))[:20]:
        top_executed.append({
            "id": r["id"],
            "name": r.get("name", ""),
            "executions": r.get("totalExecutions", 0),
            "users": r.get("totalUsers", 0),
            "lastExec": r.get("lastExecTs", ""),
        })

    # Scenarios from LLM
    scenarios = []
    for r in llm_reviews:
        a = r.get("analysis") or {}
        if a.get("action") == "PARAMETERIZE":
            scenarios.append({
                "name": a.get("label", ""),
                "count": r.get("totalReportCount", 0),
                "approach": a.get("consolidation_detail", ""),
                "confidence": a.get("confidence", ""),
            })

    # --- Emit inventory_all.json and retired.json for KPI drill-downs ---
    active_ids_set = {r["id"] for r in active}
    retire_ids_set = {r["id"] for r in retire}
    # Build id -> folderPath lookup from every available source. INSIGHT has
    # `active_reports_paths.json` which was already merged into `all_reports`
    # via the `paths_by_id` step earlier, so `all_reports` carries most paths.
    path_lookup = {}
    for src in (all_reports, active, retire, canonical):
        for r in src:
            rid = r.get("id")
            if not rid:
                continue
            p = r.get("folderPath") or r.get("path") or ""
            if isinstance(p, list):
                p = "/".join((a.get("name", "") if isinstance(a, dict) else str(a)) for a in p)
            if p and rid not in path_lookup:
                path_lookup[rid] = p
    # Merge out-of-band fetched paths (from fetch_all_missing_paths.py)
    fetched_paths_file = PROJECT_DIR / "inventory" / "fetched_paths.json"
    if fetched_paths_file.exists():
        fetched = json.loads(fetched_paths_file.read_text(encoding="utf-8"))
        added_from_fetched = 0
        for rid, v in fetched.items():
            p = (v or {}).get("path") if isinstance(v, dict) else v
            if p and rid not in path_lookup:
                path_lookup[rid] = p
                added_from_fetched += 1
        print(f"  Merged {added_from_fetched} paths from fetched_paths.json")

    # Enrich collision groups with paths, then emit.
    enrich_collisions_with_paths(collision_groups, path_lookup)
    emit_collisions(OUTPUT_DIR, collision_groups)

    emit_inventory_all(OUTPUT_DIR, all_reports, active_ids_set, retire_ids_set, path_lookup=path_lookup)
    emit_retired_list(OUTPUT_DIR, [("original", retire)], path_lookup=path_lookup)

    # --- summary.json ---
    summary = {
        "projectName": "INSIGHT",
        "projectId": manifest.get("project", {}).get("id", ""),
        "snapshotDate": manifest.get("finishedAt") or manifest.get("startedAt") or "",
        "totalInventory": total_inventory,
        "totalObjects": analysis.get("totalObjects", 0),
        "afterTelemetry": after_telemetry,
        "retired": len(retire),
        "afterCollisionCollapse": after_collision_collapse,
        "collisionCollapsed": collision_collapsed,
        "afterFingerprint": after_fingerprint,
        "fingerprintGroups": fp_groups,
        "fingerprintMultiGroups": fp_groups,
        "fingerprintReportsInGroups": active_analysis.get("reportsInDupes", 0),
        "fingerprintRemovable": fp_removable,
        "astParsed": ast_parsed,
        "astClusters": len(ast_groups),
        "astMultiClusters": len(ast_multi),
        "astReducible": ast_removable,
        "astExactGroups": len(ast_multi),
        "astExactRemovable": ast_removable,
        "astFinalToMigrate": after_fingerprint - ast_removable if ast_removable else after_fingerprint,
        "afterFamily": after_family,
        "familyReducible": family_reducible,
        "afterSimilarity": after_similarity,
        "clustersTotal": sc_summary.get("totalClusters", 0),
        "clustersMulti": sc_summary.get("multiMemberClusters", 0),
        "clustersSingleton": sc_summary.get("singletonClusters", 0),
        "reductionTotal": reduction_total,
        "reductionPct": round(reduction_pct, 1),
        "funnel": funnel,
        "tierBreakdown": tier,
        "objectTypes": obj_types,
        "topMetrics": top_metrics,
        "topTables": top_tables,
        "topFilters": top_filters,
        "topExecuted": top_executed,
        "scenarios": scenarios,
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    print(f"  -> summary.json")

    # --- reports.json (slim) ---
    reports_slim = []
    active_ids = {r["id"] for r in active}
    retire_ids = {r["id"] for r in retire}
    active_by_id = {r["id"]: r for r in active}

    for r in all_reports:
        rid = r["id"]
        status = "active" if rid in active_ids else "retire" if rid in retire_ids else "unknown"
        tel = active_by_id.get(rid, {})

        # Extract metrics/filters/tables for comparison UI
        metrics = sorted({m.get("name", "") for m in r.get("metrics", []) if isinstance(m, dict) and m.get("name")})
        attrs = sorted({a.get("name", "") for a in r.get("attributes", []) if isinstance(a, dict) and a.get("name")})
        tables = sorted({t.strip('"').split(".")[-1].lower() for t in r.get("sourceTables", []) if t})

        # SQL error classification
        sql_error = None
        errors = r.get("errors", {}) or {}
        if errors.get("sql"):
            sql_error = str(errors["sql"])
        elif r.get("prompted") and not r.get("sql"):
            sql_error = "prompted (requires user input)"
        elif r.get("sourceCubeId") and not r.get("sql"):
            sql_error = "cube-sourced (sqlView unsupported)"

        reports_slim.append({
            "id": rid,
            "name": r.get("name", ""),
            "folderPath": r.get("folderPath", ""),
            "path": r.get("folderPath", "") or tel.get("path", ""),
            "sourceType": r.get("sourceType", ""),
            "dateCreated": r.get("dateCreated"),
            "dateModified": r.get("dateModified"),
            "owner": r.get("owner", ""),
            "hasSql": bool(r.get("sql")),
            "sql": r.get("sql"),
            "sqlError": sql_error,
            "sqlHash": r.get("sqlHash"),
            "sourceTables": r.get("sourceTables", []),
            "tables": tables,
            "metrics": metrics,
            "filters": attrs,
            "metricCount": len(metrics),
            "tableCount": len(tables),
            "filterCount": len(attrs),
            "prompted": r.get("prompted", False),
            "status": status,
            "deduped": rid in deduped_ids,
            "executions": tel.get("totalExecutions", 0),
            "users": tel.get("totalUsers", 0),
            "lastExec": tel.get("lastExecTs", ""),
            "matchTier": tel.get("matchTier", ""),
            "familyBase": family_base(r.get("name", "")),
        })
    (OUTPUT_DIR / "reports.json").write_text(
        json.dumps(reports_slim, indent=2, default=str), encoding="utf-8"
    )
    print(f"  -> reports.json ({len(reports_slim):,} reports)")

    # --- clusters.json ---
    # Build LLM lookup
    llm_by_cid = {}
    for r in llm_reviews:
        llm_by_cid[r.get("clusterId")] = r.get("analysis") or {}

    active_by_id_c = {r["id"]: r for r in active}
    clusters_out = []
    for c in clusters_list:
        cluster_members_raw = c.get("members", c.get("objects", []))
        if not isinstance(cluster_members_raw, list) or len(cluster_members_raw) < 2:
            continue

        # Expand to full report list — semantic_clusters has nested {representative, allReports}
        member_ids = []
        for m in cluster_members_raw:
            if isinstance(m, dict) and "allReports" in m:
                for rep in m["allReports"]:
                    member_ids.append(rep["id"])
            elif isinstance(m, dict) and "id" in m:
                member_ids.append(m["id"])

        # Pick primary = highest execution count
        def _exec(rid):
            return active_by_id_c.get(rid, {}).get("totalExecutions", 0)
        primary_id = max(member_ids, key=_exec) if member_ids else ""
        primary_rec = merged.get(primary_id, {})
        primary_tel = active_by_id_c.get(primary_id, {})
        primary_name = primary_tel.get("name") or primary_rec.get("name", "")

        # Aggregate metrics/tables/filters across all members
        metric_set = set()
        table_set = set()
        filter_set = set()
        total_exec = 0
        users_set = set()
        for rid in member_ids:
            rec = merged.get(rid, {})
            tel = active_by_id_c.get(rid, {})
            for mm in rec.get("metrics", []):
                if isinstance(mm, dict) and mm.get("name"):
                    metric_set.add(mm["name"])
            for aa in rec.get("attributes", []):
                if isinstance(aa, dict) and aa.get("name"):
                    filter_set.add(aa["name"])
            for tt in rec.get("sourceTables", []):
                if tt:
                    table_set.add(tt.strip('"').split(".")[-1].lower())
            total_exec += tel.get("totalExecutions", 0)
            if tel.get("totalUsers"):
                users_set.add(tel.get("id") or rid)  # approximate unique users counting

        cid = c.get("clusterId", c.get("id", len(clusters_out)))
        llm = llm_by_cid.get(cid, {})
        entry = {
            "id": f"C{cid}",
            "clusterId": cid,
            "size": len(member_ids),
            "uniqueSqlCount": c.get("uniqueSqlCount", len(cluster_members_raw)),
            "avgSimilarity": c.get("avgSimilarity", 0),
            "primaryReportId": primary_id,
            "primaryName": primary_name,
            "totalExecutions": total_exec,
            "totalUsers": len(users_set),
            "commonMetrics": sorted(metric_set)[:50],
            "commonTables": sorted(table_set)[:50],
            "commonFilters": sorted(filter_set)[:50],
            "metricCount": len(metric_set),
            "tableCount": len(table_set),
            "filterCount": len(filter_set),
            "memberIds": member_ids,
            "sampleReports": [],
        }
        if llm:
            entry["llmLabel"] = llm.get("label")
            entry["llmAction"] = llm.get("action")
            entry["llmRemovable"] = llm.get("removable_count")
        clusters_out.append(entry)

    # Sort by size desc
    clusters_out.sort(key=lambda c: -c["size"])
    (OUTPUT_DIR / "clusters.json").write_text(
        json.dumps(clusters_out, indent=2, default=str), encoding="utf-8"
    )
    print(f"  -> clusters.json ({len(clusters_out):,} clusters)")

    # --- families.json ---
    families_out = []
    for base, recs in families_dict.items():
        members = []
        total_exec = 0
        for r in recs:
            execs = r.get("totalExecutions", 0)
            total_exec += execs
            name = r.get("name", "")
            # Variant suffix is everything after the base name
            suffix = name[len(base):].lstrip(" -") if name.startswith(base) else ""
            members.append({
                "id": r["id"],
                "name": name,
                "variantSuffix": suffix,
                "executions": execs,
                "users": r.get("totalUsers", 0),
                "lastExec": r.get("lastExecTs", ""),
            })
        # Sort members by executions desc
        members.sort(key=lambda m: -m["executions"])
        families_out.append({
            "base": base,
            "size": len(recs),
            "reducible": len(recs) - 1,
            "totalExecutions": total_exec,
            "members": members,
        })
    # Sort families by size desc
    families_out.sort(key=lambda f: -f["size"])
    (OUTPUT_DIR / "families.json").write_text(
        json.dumps(families_out, indent=2, default=str), encoding="utf-8"
    )
    print(f"  -> families.json ({len(families_out):,} families)")

    # --- similarities.json ---
    similarities = []
    for c in clusters_list[:100]:
        members = c.get("members", c.get("objects", []))
        if not members or len(members) < 2:
            continue
        sim = c.get("avgSimilarity", c.get("avg_similarity", 0))
        for i, a in enumerate(members[:10]):
            for b in members[i + 1:i + 6]:
                aid = a.get("representative", {}).get("id") if isinstance(a, dict) and "representative" in a else (a.get("id") if isinstance(a, dict) else a)
                bid = b.get("representative", {}).get("id") if isinstance(b, dict) and "representative" in b else (b.get("id") if isinstance(b, dict) else b)
                if aid and bid:
                    similarities.append({"a": aid, "b": bid, "score": float(sim)})
    (OUTPUT_DIR / "similarities.json").write_text(
        json.dumps(similarities[:5000], indent=2, default=str), encoding="utf-8"
    )
    print(f"  -> similarities.json ({len(similarities[:5000]):,} pairs)")

    # --- fingerprints.json (definition-based families) ---
    # Match the frontend FingerprintGroup interface
    exec_by_id = {r["id"]: r for r in active}
    fp_out = []
    for fam in dedup_info.get("families", []):
        parent_id = fam.get("parentId", "")
        all_ids = [parent_id] + [c["id"] for c in fam.get("children", [])]

        # Get enriched data for the parent (representative)
        parent_rec = merged.get(parent_id, {})
        metrics = sorted({m.get("name", "") for m in parent_rec.get("metrics", []) if m.get("name")})
        attrs = sorted({a.get("name", "") for a in parent_rec.get("attributes", []) if a.get("name")})
        tables = sorted(set(parent_rec.get("sourceTables", [])))

        # Build members with telemetry data
        members = []
        total_exec = 0
        for rid in all_ids:
            tel = exec_by_id.get(rid, {})
            rec = merged.get(rid, {})
            execs = tel.get("totalExecutions", 0)
            total_exec += execs
            members.append({
                "id": rid,
                "name": tel.get("name") or rec.get("name", ""),
                "path": tel.get("path") or rec.get("folderPath", ""),
                "executions": execs,
                "users": tel.get("totalUsers", 0),
                "lastExec": tel.get("lastExecTs", ""),
            })

        fp_out.append({
            "id": fam.get("fingerprint", ""),
            "size": fam.get("familySize", 0),
            "reducible": fam.get("familySize", 0) - 1,
            "metricCount": len(metrics),
            "tableCount": len(tables),
            "filterCount": len(attrs),
            "metrics": metrics[:50],
            "tables": tables[:50],
            "filters": attrs[:50],
            "totalExecutions": total_exec,
            "members": members,
        })

    # Sort by size desc
    fp_out.sort(key=lambda x: -x["size"])
    (OUTPUT_DIR / "fingerprints.json").write_text(
        json.dumps(fp_out, indent=2, default=str), encoding="utf-8"
    )
    print(f"  -> fingerprints.json ({len(fp_out):,} groups)")

    # --- sql_hash_groups.json ---
    # Load duplicate SQL groups from analysis
    dup_path = PROJECT_DIR / "analysis" / "duplicate_sql.json"
    duplicates = json.loads(dup_path.read_text(encoding="utf-8")) if dup_path.exists() else []

    # Enrich duplicate SQL groups with telemetry/path for the UI
    sqlhash_groups_out = []
    active_by_id_local = {r["id"]: r for r in active}
    cid = 0
    for grp in duplicates:  # from analysis/duplicate_sql.json
        objs = grp.get("objects", [])
        # Only include groups where objects are in active (de-duped) set
        active_members = [o for o in objs if o["id"] in deduped_ids]
        if len(active_members) < 2:
            continue

        members = []
        total_exec = 0
        for o in active_members:
            tel = active_by_id_local.get(o["id"], {})
            rec = merged.get(o["id"], {})
            execs = tel.get("totalExecutions", 0)
            total_exec += execs
            members.append({
                "id": o["id"],
                "name": tel.get("name") or rec.get("name", ""),
                "path": tel.get("path") or rec.get("folderPath", ""),
                "executions": execs,
                "users": tel.get("totalUsers", 0),
                "lastExec": tel.get("lastExecTs", ""),
            })
        sqlhash_groups_out.append({
            "id": f"H{cid:04d}",
            "size": len(active_members),
            "reducible": len(active_members) - 1,
            "totalExecutions": total_exec,
            "sqlHash": grp.get("sqlHash", ""),
            "sqlPreview": (active_members[0].get("sql_preview") or "")[:300] if active_members else "",
            "members": members,
        })
        cid += 1

    sqlhash_groups_out.sort(key=lambda g: -g["size"])
    (OUTPUT_DIR / "sql_hash_groups.json").write_text(
        json.dumps(sqlhash_groups_out, indent=2, default=str), encoding="utf-8"
    )
    print(f"  -> sql_hash_groups.json ({len(sqlhash_groups_out):,} groups)")

    # Build SQL hash member sets (for AST "exclusive" flag below)
    sqlhash_member_sets = [frozenset(m["id"] for m in g["members"]) for g in sqlhash_groups_out]

    # --- ast_clusters.json / ast_hashes.json ---
    if ast_data:
        # Match AstCluster interface: id, size, reducible, allExact, avgScore, minScore, totalExecutions, members
        active_by_id = {r["id"]: r for r in active}
        merged_by_id = merged

        ast_clusters_out = []
        cluster_idx = 0
        for rids in (g for g in ast_groups.values() if len(g) >= 2):
            members_out = []
            total_exec = 0
            for rid in rids:
                tel = active_by_id.get(rid, {})
                rec = merged_by_id.get(rid, {})
                execs = tel.get("totalExecutions", 0)
                total_exec += execs
                members_out.append({
                    "id": rid,
                    "name": tel.get("name") or rec.get("name", ""),
                    "path": tel.get("path") or rec.get("folderPath", ""),
                    "executions": execs,
                    "users": tel.get("totalUsers", 0),
                    "lastExec": tel.get("lastExecTs", ""),
                })
            # Flag if this AST group is already covered by a SQL hash group
            rid_set = frozenset(rids)
            covered_by_sqlhash = any(rid_set == hs or rid_set.issubset(hs) for hs in sqlhash_member_sets)
            ast_clusters_out.append({
                "id": f"A{cluster_idx:04d}",
                "size": len(rids),
                "reducible": len(rids) - 1,
                "allExact": True,  # exact-AST groups
                "avgScore": 1.0,
                "minScore": 1.0,
                "totalExecutions": total_exec,
                "members": members_out,
                "inSqlHash": covered_by_sqlhash,
                "astExclusive": not covered_by_sqlhash,
            })
            cluster_idx += 1

        # Sort by size desc
        ast_clusters_out.sort(key=lambda c: -c["size"])
        (OUTPUT_DIR / "ast_clusters.json").write_text(
            json.dumps(ast_clusters_out, indent=2, default=str), encoding="utf-8"
        )
        print(f"  -> ast_clusters.json ({len(ast_clusters_out):,} multi-member clusters)")

        # ast_hashes.json — keyed by report ID; only write for reports with SQL
        # (this file is very large — 143MB for INSIGHT — so keep the structure minimal)
        (OUTPUT_DIR / "ast_hashes.json").write_text(
            json.dumps(report_to_ast, default=str), encoding="utf-8"
        )
        print(f"  -> ast_hashes.json")
    else:
        for name in ["ast_clusters.json", "ast_hashes.json"]:
            fpath = OUTPUT_DIR / name
            if not fpath.exists():
                fpath.write_text(json.dumps([] if "clusters" in name else {}), encoding="utf-8")

    print(f"\nAll files written to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
