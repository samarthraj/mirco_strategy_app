#!/usr/bin/env python3
"""Build web dashboard JSON files for Global Insight.

Outputs to public/data/Global Insight/:
  - summary.json     KPIs, funnel, tier breakdown
  - reports.json     slim report list for the UI
  - clusters.json    semantic cluster metadata
  - families.json    family groupings
  - similarities.json top similar pairs
  - fingerprints.json (stub/empty if not computed)
  - ast_clusters.json (from ast_cache.json if available)
  - ast_hashes.json   (from ast_cache.json if available)
"""

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


PROJECT_DIR = Path("Global Insight")
OUTPUT_DIR = Path("public/data/Global Insight")


def family_base(name):
    parts = name.rsplit(" - ", 1)
    if len(parts) == 2 and len(parts[0]) >= 10:
        return parts[0].strip()
    return name.strip()


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Loading data from {PROJECT_DIR}...")

    with open(PROJECT_DIR / "inventory" / "reports.json", "r", encoding="utf-8") as f:
        all_reports = json.load(f)
    with open(PROJECT_DIR / "telemetry" / "active_reports.json", "r", encoding="utf-8") as f:
        active = json.load(f)
    with open(PROJECT_DIR / "telemetry" / "retire_reports.json", "r", encoding="utf-8") as f:
        retire = json.load(f)
    with open(PROJECT_DIR / "analysis" / "summary.json", "r", encoding="utf-8") as f:
        analysis = json.load(f)

    # Manifest for project metadata
    manifest = {}
    manifest_path = PROJECT_DIR / "run_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Active-only analysis (preferred)
    active_analysis = {}
    a_sum_path = PROJECT_DIR / "analysis_active" / "summary.json"
    if a_sum_path.exists():
        active_analysis = json.loads(a_sum_path.read_text(encoding="utf-8"))

    # --- Telemetry-row collision collapse (shared with GO / INSIGHT) --------
    # Build active_lookup from the telemetry file so the collapse helper can
    # reason about (executions, lastExec) signatures.
    active_lookup_for_collapse = {}
    for a in active:
        active_lookup_for_collapse[a["id"]] = {
            "totalExecutions": a.get("totalExecutions", 0),
            "totalUsers": a.get("totalUsers", 0),
            "lastExecTs": a.get("lastExecTs", ""),
            "matchTier": a.get("matchTier", ""),
        }

    # The "combined" population for GI is the active reports joined back with
    # their full inventory record (which has owner, path, etc.).
    reports_by_id = {r["id"]: r for r in all_reports}
    combined_for_collapse = []
    for a in active:
        full = reports_by_id.get(a["id"])
        if full:
            # Merge telemetry fields onto the full record so collapse has name/path/owner
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

    # Defer collisions.json — we'll enrich with path_lookup and emit later.

    # Filter `active` down to the post-collision canonicals so all downstream
    # dedup steps (family, fingerprint, similarity) operate on the same
    # 4,124 canonical reports. This keeps the funnel math sequential.
    active = [a for a in active if a["id"] in canonical_ids]

    # De-dup active by family
    groups = defaultdict(list)
    for r in active:
        groups[family_base(r.get("name", ""))].append(r)

    families_dict = {b: recs for b, recs in groups.items() if len(recs) >= 2}
    standalone = {b: recs[0] for b, recs in groups.items() if len(recs) == 1}

    deduped_ids = set()
    for base, recs in groups.items():
        if len(recs) == 1:
            deduped_ids.add(recs[0]["id"])
        else:
            parent = max(recs, key=lambda x: x.get("totalExecutions", 0))
            deduped_ids.add(parent["id"])

    total_inventory = len(all_reports)
    # after_telemetry = BEFORE collision collapse (pre_collision is the count of
    # reports with any telemetry match). `after_collision_collapse` is the
    # next funnel stage, and family/fingerprint operate on that.
    after_telemetry = pre_collision
    after_family = len(deduped_ids)
    family_reducible = after_collision_collapse - after_family

    # Exact SQL dup
    fp_groups = active_analysis.get("duplicateSqlGroups", 0)
    fp_removable = active_analysis.get("reducibleViaDupeSql", 0)
    after_fingerprint = after_family - fp_removable

    # Semantic clusters
    semantic_data = {}
    sc_path = PROJECT_DIR / "sql" / "semantic_clusters.json"
    if sc_path.exists():
        semantic_data = json.loads(sc_path.read_text(encoding="utf-8"))
    sc_summary = semantic_data.get("summary", {}) if isinstance(semantic_data, dict) else {}
    clusters_list = semantic_data.get("clusters", []) if isinstance(semantic_data, dict) else []

    # LLM reviews
    llm_reviews = []
    rv_path = PROJECT_DIR / "sql" / "cluster_reviews.json"
    if rv_path.exists():
        rv = json.loads(rv_path.read_text(encoding="utf-8"))
        llm_reviews = rv.get("reviews", []) if isinstance(rv, dict) else rv
    llm_removable_raw = sum((r.get("analysis") or {}).get("removable_count", 0) for r in llm_reviews)
    # Clamp so the funnel stays sequential.
    llm_removable = min(llm_removable_raw, max(0, after_fingerprint - 1))

    # Parity metadata with Global Operational: action distribution and
    # HIGH-confidence "safe to auto-consolidate" cluster count.
    llm_actions: dict[str, int] = {}
    llm_safe_clusters = 0
    for r in llm_reviews:
        a = (r.get("analysis") or {}).get("action", "UNKNOWN")
        llm_actions[a] = llm_actions.get(a, 0) + 1
        conf = (r.get("analysis") or {}).get("confidence", "")
        if a in ("MERGE_IMMEDIATE", "PARAMETERIZE") and conf == "HIGH":
            llm_safe_clusters += 1

    after_similarity = after_fingerprint - llm_removable

    # AST cache
    ast_data = {}
    ast_path = PROJECT_DIR / "analysis" / "ast_cache.json"
    if ast_path.exists():
        ast_data = json.loads(ast_path.read_text(encoding="utf-8"))

    ast_groups = defaultdict(list)
    if isinstance(ast_data, dict):
        for rid, hashes in ast_data.items():
            if isinstance(hashes, list):
                ast_groups[frozenset(hashes)].append(rid)
    ast_multi = [g for g in ast_groups.values() if len(g) >= 2]
    ast_parsed = len(ast_data) if isinstance(ast_data, dict) else 0
    ast_reports_in_groups = sum(len(g) for g in ast_multi)
    ast_removable = sum(len(g) - 1 for g in ast_multi)

    reduction_total = total_inventory - after_similarity
    reduction_pct = reduction_total / total_inventory * 100 if total_inventory else 0.0

    # Build funnel
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
        {"label": "After Family De-dup", "value": after_family,
         "detail": f"Collapsed {family_reducible:,} variants to family parents"},
        {"label": "After Exact-SQL De-dup", "value": after_fingerprint,
         "detail": f"{fp_removable} removable via identical SQL hashing"},
        {"label": "After LLM Parameterization", "value": after_similarity,
         "detail": f"{llm_removable} removable via semantic clustering + LLM review"},
    ])

    # Tier breakdown from telemetry
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

    # Top metrics, tables, filters
    metric_counter = Counter()
    table_counter = Counter()
    filter_counter = Counter()
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

    # Scenarios from LLM reviews
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

    # --- Emit inventory_all.json and retired.json (for KPI drill-downs) ---
    active_ids_set = {r["id"] for r in active}
    retire_ids_set = {r["id"] for r in retire}
    # Build an id -> folderPath lookup from every source we have, so missing
    # paths in the raw inventory get backfilled where possible.
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

    # --- WRITE summary.json ---
    summary = {
        "projectName": "Global Insight",
        "projectId": manifest.get("project", {}).get("id", ""),
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
        "llmReviewsTotal": len(llm_reviews),
        "llmReviewsByAction": llm_actions,
        "llmSafeClusters": llm_safe_clusters,
        "llmRemovableRaw": llm_removable_raw,
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
    with open(OUTPUT_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"  -> summary.json")

    # --- WRITE reports.json (matches ReportDetail UI interface) ---
    # Build telemetry lookup so we can attach executions/users/lastExec/matchTier
    # to every report the UI expects in that shape.
    tel_by_id = {r["id"]: r for r in active}

    def _norm_table(t: str) -> str:
        if not t:
            return ""
        return t.strip('"').split(".")[-1].lower()

    def _walk_filter_tree(node, out: set):
        """Recursively extract filter attribute/filter names from a MSTR filter tree."""
        if not isinstance(node, dict):
            return
        # function node (and/or/not) -> recurse into children
        if node.get("function"):
            for ch in node.get("children") or []:
                _walk_filter_tree(ch, out)
            return
        # leaf predicate -> look for predicateTree.attribute.name or .filter.name
        pt = node.get("predicateTree")
        if isinstance(pt, dict):
            attr = pt.get("attribute")
            if isinstance(attr, dict) and attr.get("name"):
                out.add(attr["name"])
            flt = pt.get("filter")
            if isinstance(flt, dict) and flt.get("name"):
                out.add(flt["name"])

    def _extract_fp(rec: dict):
        metrics = [m.get("name") for m in (rec.get("metrics") or []) if isinstance(m, dict) and m.get("name")]
        tables = [_norm_table(t) for t in (rec.get("sourceTables") or []) if t]
        filter_names: set = set()
        flt = rec.get("filter")
        if isinstance(flt, dict) and isinstance(flt.get("tree"), dict):
            _walk_filter_tree(flt["tree"], filter_names)
        # Back-compat: some records may still carry attributes as a flat list.
        for a in (rec.get("attributes") or []):
            if isinstance(a, dict) and a.get("name"):
                filter_names.add(a["name"])
        return (
            sorted({m for m in metrics if m}),
            sorted({t for t in tables if t}),
            sorted(filter_names),
        )

    # Map each report to its cluster (for clusterId on each report)
    cluster_id_by_report: dict[str, str] = {}

    reports_out = []
    active_ids_set = {r["id"] for r in active}
    retire_ids_set = {r["id"] for r in retire}
    for r in all_reports:
        rid = r["id"]
        tel = tel_by_id.get(rid, {})
        metrics, tables, filters = _extract_fp(r)
        reports_out.append({
            "id": rid,
            "name": r.get("name", "") or tel.get("name", ""),
            "owner": r.get("owner", ""),
            "path": r.get("folderPath") or r.get("path") or path_lookup.get(rid, ""),
            "executions": tel.get("totalExecutions", 0),
            "users": tel.get("totalUsers", 0),
            "lastExec": tel.get("lastExecTs", ""),
            "matchTier": tel.get("matchTier", ""),
            "metrics": metrics,
            "tables": tables,
            "filters": filters,
            "metricCount": len(metrics),
            "tableCount": len(tables),
            "filterCount": len(filters),
            "clusterId": None,  # filled in after clusters.json is built
            "familyBase": family_base(r.get("name", "")),
            "dateCreated": r.get("dateCreated"),
            "dateModified": r.get("dateModified"),
            "sql": r.get("sql"),
            "sqlError": r.get("sqlError"),
        })
    # We'll patch clusterId after clusters_out is built below.
    reports_out_by_id = {r["id"]: r for r in reports_out}

    # --- WRITE clusters.json (semantic + LLM) — matches ClusterMeta UI interface ---
    llm_by_cid = {r.get("clusterId"): (r.get("analysis") or {}) for r in llm_reviews}
    active_by_id_c = {r["id"]: r for r in active}
    all_report_by_id = {r["id"]: r for r in all_reports}
    clusters_out = []
    for c in clusters_list:
        members_raw = c.get("members", c.get("objects", []))
        if not isinstance(members_raw, list) or len(members_raw) < 2:
            continue

        member_ids = []
        for m in members_raw:
            if isinstance(m, dict) and "allReports" in m:
                for rep in m["allReports"]:
                    member_ids.append(rep["id"])
            elif isinstance(m, dict) and "id" in m:
                member_ids.append(m["id"])

        def _exec(rid):
            return active_by_id_c.get(rid, {}).get("totalExecutions", 0)
        primary_id = max(member_ids, key=_exec) if member_ids else ""
        primary_rec = all_report_by_id.get(primary_id, {})
        primary_tel = active_by_id_c.get(primary_id, {})
        primary_name = primary_tel.get("name") or primary_rec.get("name", "")

        metric_set, table_set, filter_set = set(), set(), set()
        total_exec = 0
        users_set = set()
        for rid in member_ids:
            rec = all_report_by_id.get(rid, {})
            tel = active_by_id_c.get(rid, {})
            ms, ts, fs = _extract_fp(rec)
            metric_set.update(ms)
            table_set.update(ts)
            filter_set.update(fs)
            total_exec += tel.get("totalExecutions", 0)
            if tel.get("totalUsers"):
                users_set.add(rid)

        cid = c.get("id", c.get("clusterId", len(clusters_out)))
        llm = llm_by_cid.get(cid, {})
        entry = {
            "id": f"C{cid}",
            "clusterId": cid,
            "size": len(member_ids),
            "uniqueSqlCount": c.get("uniqueSqlCount", len(members_raw)),
            "avgSimilarity": c.get("avgSimilarity", c.get("avg_similarity", 0)),
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

    clusters_out.sort(key=lambda c: -c["size"])
    with open(OUTPUT_DIR / "clusters.json", "w", encoding="utf-8") as f:
        json.dump(clusters_out, f, indent=2, default=str)
    print(f"  -> clusters.json ({len(clusters_out):,} clusters)")

    # Patch clusterId onto each report row and write reports.json in the shape
    # the UI expects (ReportDetail).
    for c in clusters_out:
        cid_str = c.get("id")
        for mid in c.get("memberIds", []):
            row = reports_out_by_id.get(mid)
            if row and row.get("clusterId") is None:
                row["clusterId"] = cid_str
    with open(OUTPUT_DIR / "reports.json", "w", encoding="utf-8") as f:
        json.dump(reports_out, f, indent=2, default=str)
    print(f"  -> reports.json ({len(reports_out):,} reports, ReportDetail schema)")

    # --- WRITE llm_reviews.json (shared UI schema, parity with Global Operational) ---
    cluster_by_cid = {c["clusterId"]: c for c in clusters_out}
    llm_reviews_out = []
    for r in llm_reviews:
        analysis = r.get("analysis") or {}
        cid_raw = r.get("clusterId")
        cluster_row = cluster_by_cid.get(cid_raw, {})
        cluster_size = cluster_row.get("size") or r.get("totalReportCount", 0)
        primary_name = cluster_row.get("primaryName") or (
            (r.get("sampleReports") or [""])[0] if r.get("sampleReports") else ""
        )
        llm_reviews_out.append({
            "clusterId": f"C{cid_raw}" if cid_raw is not None else "",
            "clusterSize": cluster_size,
            "primaryName": primary_name,
            "label": analysis.get("label", ""),
            "business_function": analysis.get("business_function", ""),
            "relationship": analysis.get("relationship", ""),
            "consolidation_action": analysis.get("action", ""),
            "confidence": analysis.get("confidence", ""),
            "consolidation_detail": analysis.get("consolidation_detail", ""),
            "keep_report": analysis.get("keep_report", ""),
            "removable_count": analysis.get("removable_count", 0),
        })
    with open(OUTPUT_DIR / "llm_reviews.json", "w", encoding="utf-8") as f:
        json.dump(llm_reviews_out, f, indent=2, default=str)
    print(f"  -> llm_reviews.json ({len(llm_reviews_out):,} reviews)")

    # --- WRITE families.json (matches Family UI interface) ---
    families_out = []
    for base, recs in families_dict.items():
        members = []
        total_exec = 0
        for r in recs:
            rid = r["id"]
            row = reports_out_by_id.get(rid, {})
            tel = tel_by_id.get(rid, {})
            name = row.get("name") or r.get("name", "")
            # variantSuffix = the part of the name after the base (after " - ")
            suffix = ""
            if name and base and name.startswith(base):
                suffix = name[len(base):].lstrip(" -")
            execs = row.get("executions") or tel.get("totalExecutions", 0) or 0
            total_exec += execs
            members.append({
                "id": rid,
                "name": name,
                "variantSuffix": suffix,
                "executions": execs,
                "users": row.get("users") or tel.get("totalUsers", 0) or 0,
                "lastExec": row.get("lastExec") or tel.get("lastExecTs", "") or "",
            })
        members.sort(key=lambda m: -m["executions"])
        families_out.append({
            "base": base,
            "size": len(recs),
            "reducible": max(0, len(recs) - 1),
            "totalExecutions": total_exec,
            "members": members,
        })
    families_out.sort(key=lambda f: -f["size"])
    with open(OUTPUT_DIR / "families.json", "w", encoding="utf-8") as f:
        json.dump(families_out, f, indent=2, default=str)
    print(f"  -> families.json ({len(families_out):,} families)")

    # --- WRITE similarities.json (UI expects Record<"idA|idB", PairSimilarity>) ---
    sim_map: dict[str, dict] = {}
    for c in clusters_out:
        mids = c.get("memberIds", [])
        sim = c.get("avgSimilarity") or 0.0
        try:
            sim = float(sim)
        except Exception:
            sim = 0.0
        # Cap per-cluster pairs to keep the file bounded for huge clusters.
        cap = mids[:30]
        for i, a in enumerate(cap):
            for b in cap[i + 1:]:
                key = f"{a}|{b}"
                if key in sim_map:
                    continue
                ra = reports_out_by_id.get(a, {})
                rb = reports_out_by_id.get(b, {})
                ma, mb = set(ra.get("metrics") or []), set(rb.get("metrics") or [])
                ta, tb = set(ra.get("tables") or []), set(rb.get("tables") or [])
                fa, fb = set(ra.get("filters") or []), set(rb.get("filters") or [])
                def _j(x, y):
                    if not x and not y:
                        return 1.0
                    u = x | y
                    return (len(x & y) / len(u)) if u else 0.0
                sim_map[key] = {
                    "combined": round(sim, 4),
                    "metric": round(_j(ma, mb), 4),
                    "table": round(_j(ta, tb), 4),
                    "filter": round(_j(fa, fb), 4),
                }
    with open(OUTPUT_DIR / "similarities.json", "w", encoding="utf-8") as f:
        json.dump(sim_map, f, indent=2, default=str)
    print(f"  -> similarities.json ({len(sim_map):,} pairs)")

    # --- WRITE fingerprints.json (real exact-match groups on active canonicals) ---
    # Group active canonical reports by (metrics, tables, filters) triple and
    # keep only groups with >= 2 members (multi-groups). Singleton fingerprint
    # reports are implicit — they don't need a row.
    fp_groups_map: dict[tuple, list[str]] = defaultdict(list)
    for rid in canonical_ids:
        row = reports_out_by_id.get(rid)
        if not row:
            continue
        key = (
            tuple(row.get("metrics") or []),
            tuple(row.get("tables") or []),
            tuple(row.get("filters") or []),
        )
        # Skip reports with zero metadata — they'd all collapse into one noise group.
        if not any(key):
            continue
        fp_groups_map[key].append(rid)

    fp_out = []
    gi = 0
    for (metrics_t, tables_t, filters_t), ids in fp_groups_map.items():
        if len(ids) < 2:
            continue
        members = []
        total_exec = 0
        for rid in ids:
            row = reports_out_by_id.get(rid, {})
            execs = row.get("executions", 0) or 0
            total_exec += execs
            members.append({
                "id": rid,
                "name": row.get("name", ""),
                "path": row.get("path", ""),
                "executions": execs,
                "users": row.get("users", 0) or 0,
                "lastExec": row.get("lastExec", "") or "",
            })
        members.sort(key=lambda m: -m["executions"])
        fp_out.append({
            "id": f"FP{gi:04d}",
            "size": len(ids),
            "reducible": len(ids) - 1,
            "metricCount": len(metrics_t),
            "tableCount": len(tables_t),
            "filterCount": len(filters_t),
            "metrics": list(metrics_t),
            "tables": list(tables_t),
            "filters": list(filters_t),
            "totalExecutions": total_exec,
            "members": members,
        })
        gi += 1
    fp_out.sort(key=lambda g: -g["size"])
    with open(OUTPUT_DIR / "fingerprints.json", "w", encoding="utf-8") as f:
        json.dump(fp_out, f, indent=2, default=str)
    fp_reducible = sum(g["reducible"] for g in fp_out)
    fp_reports_in_groups = sum(g["size"] for g in fp_out)
    print(f"  -> fingerprints.json ({len(fp_out):,} multi-groups, {fp_reducible} reducible across {fp_reports_in_groups} reports)")

    # --- WRITE sql_hash_groups.json ---
    # Load duplicate SQL groups from analysis
    sqlhash_out = []
    dup_sql_path = PROJECT_DIR / "analysis" / "duplicate_sql.json"
    if dup_sql_path.exists():
        dup_data = json.loads(dup_sql_path.read_text(encoding="utf-8"))
        active_by_id_local = {r["id"]: r for r in active}
        active_report_lookup = {r["id"]: r for r in all_reports}
        cid = 0
        for grp in dup_data:
            objs = grp.get("objects", [])
            # Only include groups where objects are in deduped active set
            active_members = [o for o in objs if o["id"] in deduped_ids]
            if len(active_members) < 2:
                continue
            members = []
            total_exec = 0
            for o in active_members:
                tel = active_by_id_local.get(o["id"], {})
                rec = active_report_lookup.get(o["id"], {})
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
            sqlhash_out.append({
                "id": f"H{cid:04d}",
                "size": len(active_members),
                "reducible": len(active_members) - 1,
                "totalExecutions": total_exec,
                "sqlHash": grp.get("sqlHash", ""),
                "sqlPreview": (active_members[0].get("sql_preview") or "")[:300] if active_members else "",
                "members": members,
            })
            cid += 1
        sqlhash_out.sort(key=lambda g: -g["size"])
    with open(OUTPUT_DIR / "sql_hash_groups.json", "w", encoding="utf-8") as f:
        json.dump(sqlhash_out, f, indent=2, default=str)
    print(f"  -> sql_hash_groups.json ({len(sqlhash_out):,} groups)")

    # --- WRITE ast_clusters.json / ast_hashes.json ---
    sqlhash_member_sets = [frozenset(m["id"] for m in g["members"]) for g in sqlhash_out]
    if ast_data:
        ast_clusters_out = []
        for i, (ast_key, rids) in enumerate(ast_groups.items()):
            if len(rids) >= 2:
                rid_set = frozenset(rids)
                covered = any(rid_set == hs or rid_set.issubset(hs) for hs in sqlhash_member_sets)
                ast_clusters_out.append({
                    "clusterId": i,
                    "size": len(rids),
                    "members": rids,
                    "inSqlHash": covered,
                    "astExclusive": not covered,
                })
        with open(OUTPUT_DIR / "ast_clusters.json", "w", encoding="utf-8") as f:
            json.dump(ast_clusters_out, f, indent=2, default=str)
        print(f"  -> ast_clusters.json ({len(ast_clusters_out):,} multi-member clusters)")

        # ast_hashes.json: reportId -> list of subtree hashes
        with open(OUTPUT_DIR / "ast_hashes.json", "w", encoding="utf-8") as f:
            json.dump(ast_data, f, default=str)
        print(f"  -> ast_hashes.json")
    else:
        for name in ["ast_clusters.json", "ast_hashes.json"]:
            fpath = OUTPUT_DIR / name
            if not fpath.exists():
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump([] if "clusters" in name else {}, f)

    print(f"\nAll files written to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
