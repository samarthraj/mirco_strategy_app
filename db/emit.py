"""Emit public/data/<project>/*.json from the SQLite DB.

This is the reverse of db/load.py — proves the DB holds everything the
React UI needs, and lets the DB become the authoritative staging layer.

Writes the 12+1 files the UI fetches per project:
  summary.json, reports.json, clusters.json, similarities.json,
  families.json, fingerprints.json, sql_hash_groups.json,
  ast_clusters.json, ast_hashes.json, collisions.json,
  inventory_all.json, retired.json, llm_reviews.json (optional).
"""
from __future__ import annotations
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.db import connect, init, PROJECTS, get_project


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _json_dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


# ---------------- summary.json ------------------------------------

def emit_summary(pid: str, conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT * FROM run_summary WHERE project_id = ?", (pid,)
    ).fetchone()
    proj = conn.execute(
        "SELECT * FROM project WHERE project_id = ?", (pid,)
    ).fetchone()
    if not row or not proj:
        return {}

    # Scalar fields from run_summary (snake_case → camelCase for the UI)
    field_map = {
        "total_inventory": "totalInventory", "total_dossiers": "totalDossiers",
        "dossiers_with_telemetry": "dossiersWithTelemetry", "total_objects": "totalObjects",
        "after_telemetry": "afterTelemetry", "retired": "retired",
        "after_collision_collapse": "afterCollisionCollapse",
        "collision_collapsed": "collisionCollapsed",
        "after_fingerprint": "afterFingerprint", "fingerprint_groups": "fingerprintGroups",
        "fingerprint_multi_groups": "fingerprintMultiGroups",
        "fingerprint_reports_in_groups": "fingerprintReportsInGroups",
        "fingerprint_removable": "fingerprintRemovable",
        "ast_parsed": "astParsed", "ast_clusters": "astClusters",
        "ast_multi_clusters": "astMultiClusters", "ast_reducible": "astReducible",
        "ast_exact_groups": "astExactGroups", "ast_exact_removable": "astExactRemovable",
        "ast_final_to_migrate": "astFinalToMigrate",
        "after_sql_hash": "afterSqlHash",
        "sql_hash_sequential_removable": "sqlHashSequentialRemovable",
        "sql_hash_sequential_groups": "sqlHashSequentialGroups",
        "after_ast": "afterAst",
        "ast_sequential_removable": "astSequentialRemovable",
        "ast_sequential_groups": "astSequentialGroups",
        "after_family": "afterFamily", "family_reducible": "familyReducible",
        "after_post_ast_family": "afterPostAstFamily",
        "post_ast_family_collapsed": "postAstFamilyCollapsed",
        "after_similarity": "afterSimilarity",
        "clusters_total": "clustersTotal", "clusters_multi": "clustersMulti",
        "clusters_singleton": "clustersSingleton",
        "llm_reviews_total": "llmReviewsTotal",
        "llm_removable_raw": "llmRemovableRaw",
        "llm_safe_clusters": "llmSafeClusters",
        "reduction_total": "reductionTotal", "reduction_pct": "reductionPct",
    }
    out: dict = {
        "projectName": proj["name"],
        "projectId": proj["mstr_project_id"] or "",
    }
    if proj["snapshot_date"]:
        out["snapshotDate"] = proj["snapshot_date"]
    for sc, cc in field_map.items():
        v = row[sc]
        if v is not None:
            out[cc] = v

    # Reports-only inventory count (excludes dossiers). The raw totalInventory
    # includes every object type; business users typically want "how many
    # reports did we start with", not counting dossiers.
    reports_only = conn.execute(
        """SELECT COUNT(*) FROM report
           WHERE project_id = ? AND (status IS NULL OR status != 'dossier')""",
        (pid,),
    ).fetchone()[0]
    out["totalReportsInventory"] = int(reports_only)
    dossier_count = conn.execute(
        "SELECT COUNT(*) FROM report WHERE project_id = ? AND status = 'dossier'",
        (pid,),
    ).fetchone()[0]
    out["totalDossiers"] = int(dossier_count)

    # Semantic final count — reads from the semantic_cluster tables (which
    # are not in run_summary). afterSemantic = multi-cluster count + singletons.
    sem_multi = conn.execute(
        "SELECT COUNT(*) FROM semantic_cluster WHERE project_id = ?", (pid,)
    ).fetchone()[0]
    sem_members = conn.execute(
        "SELECT COUNT(DISTINCT report_id) FROM semantic_cluster_member WHERE project_id = ?",
        (pid,),
    ).fetchone()[0]
    # Singletons = post-family canonicals that aren't in any multi-member semantic cluster
    paf_canonical = (
        row["after_post_ast_family"] if "after_post_ast_family" in row.keys()
        and row["after_post_ast_family"] is not None
        else (row["after_family"] if "after_family" in row.keys() else None)
    )
    if paf_canonical is not None:
        sem_singletons = max(0, paf_canonical - sem_members)
        out["afterSemantic"] = sem_multi + sem_singletons
        out["semanticClustersTotal"] = sem_multi
        out["semanticSingletons"] = sem_singletons
    # LLM-removable count across semantic reviews
    llm_rem = conn.execute(
        """SELECT COALESCE(SUM(removable_count), 0) FROM semantic_llm_review
            WHERE project_id = ? AND label IS NOT NULL AND label != ''""",
        (pid,),
    ).fetchone()[0]
    if llm_rem:
        out["semanticLlmRemovable"] = int(llm_rem)

    # Funnel (ordered)
    out["funnel"] = [
        {"label": r["label"], "value": r["value"], "detail": r["detail"]}
        for r in conn.execute(
            "SELECT label, value, detail FROM funnel_stage WHERE project_id = ? ORDER BY ordinal",
            (pid,),
        )
    ]

    # Tier breakdown
    out["tierBreakdown"] = {
        r["tier"]: r["count"]
        for r in conn.execute(
            "SELECT tier, count FROM tier_breakdown WHERE project_id = ?", (pid,)
        )
    }

    # Object types (keep insertion order of load)
    out["objectTypes"] = [
        {"name": r["name"], "count": r["count"]}
        for r in conn.execute(
            "SELECT name, count FROM object_type_count WHERE project_id = ? ORDER BY count DESC",
            (pid,),
        )
    ]

    # top_list: kind → list, ranked
    def tl(kind: str) -> list:
        rows = conn.execute(
            "SELECT * FROM top_list WHERE project_id = ? AND kind = ? ORDER BY rank",
            (pid, kind),
        ).fetchall()
        if kind == "executed":
            return [
                {
                    "id": r["report_id"], "name": r["name"],
                    "executions": r["executions"], "users": r["users"],
                    "lastExec": r["last_exec"],
                }
                for r in rows
            ]
        return [{"name": r["name"], "count": r["count"]} for r in rows]

    out["topMetrics"] = tl("metric")
    out["topTables"] = tl("table")
    out["topFilters"] = tl("filter")
    out["topExecuted"] = tl("executed")

    # LLM action histogram
    lac = {
        r["action"]: r["count"]
        for r in conn.execute(
            "SELECT action, count FROM llm_action_count WHERE project_id = ?", (pid,)
        )
    }
    if lac:
        out["llmReviewsByAction"] = lac

    # alternativeFamilyMethod — small dict the Overview tab reads
    if row["alt_family_label"] or row["alt_family_value"] is not None:
        out["alternativeFamilyMethod"] = {
            "label": row["alt_family_label"],
            "value": row["alt_family_value"],
            "detail": row["alt_family_detail"],
        }

    # scenarios: not modeled explicitly in DB yet; emit empty for UI safety.
    out.setdefault("scenarios", [])

    return out


# ---------------- reports.json ------------------------------------

def emit_reports(pid: str, conn: sqlite3.Connection, scope: str = "all") -> list[dict]:
    scope_clause = "AND r.status = 'active'" if scope == "active" else ""
    reports = _rows(conn.execute(
        f"""SELECT r.*, t.total_executions, t.total_users, t.last_exec_ts,
                  rs.sql_text, rs.sql_error,
                  (SELECT source_type FROM raw_inventory ri
                    WHERE ri.project_id = r.project_id AND ri.report_id = r.report_id
                      AND ri.source_type IS NOT NULL
                    LIMIT 1) AS source_type
             FROM report r
             LEFT JOIN telemetry_match t
               ON t.project_id = r.project_id AND t.report_id = r.report_id
             LEFT JOIN report_sql rs
               ON rs.project_id = r.project_id AND rs.report_id = r.report_id
            WHERE r.project_id = ? {scope_clause}""",
        (pid,),
    ))

    # Batch-load metrics/tables/filters per report
    def by_report(table: str, col: str) -> dict[str, list]:
        m: dict[str, list] = {}
        for rid, name in conn.execute(
            f"SELECT report_id, {col} FROM {table} WHERE project_id = ? ORDER BY {col}",
            (pid,),
        ):
            m.setdefault(rid, []).append(name)
        return m

    metrics = by_report("report_metric", "metric_name")
    tables = by_report("report_table", "table_name")
    filters = by_report("report_filter", "filter_name")
    attributes = by_report("report_attribute", "attribute_name")

    # Family siblings: for each family canonical, the non-canonical members
    # that were collapsed into it. Surfacing this lets the UI show "this
    # primary stands for N dated siblings" in cluster drill-downs — the
    # collapsed sibling otherwise disappears from Jaccard/Semantic views.
    family_siblings_by_canonical: dict[str, list[dict]] = {}
    sibling_rows = list(conn.execute(
        """SELECT f.canonical_id, fm.report_id, r.name, fm.variant_suffix,
                  r.status, t.total_executions
             FROM family f
             JOIN family_member fm
               ON fm.project_id = f.project_id AND fm.base = f.base
             JOIN report r
               ON r.project_id = fm.project_id AND r.report_id = fm.report_id
             LEFT JOIN telemetry_match t
               ON t.project_id = r.project_id AND t.report_id = r.report_id
            WHERE f.project_id = ? AND fm.is_canonical = 0""",
        (pid,),
    ))
    for canon_id, sib_id, sib_name, suffix, status, execs in sibling_rows:
        family_siblings_by_canonical.setdefault(canon_id, []).append({
            "id": sib_id,
            "name": sib_name or "",
            "suffix": suffix or "",
            "status": status or "",
            "executions": execs or 0,
        })
    # Sort siblings by executions desc for stable UI ordering
    for lst in family_siblings_by_canonical.values():
        lst.sort(key=lambda x: (-x["executions"], x["name"]))

    # Final canonicals = primaries of multi-member similarity clusters + post-AST singletons
    cluster_primaries: set[str] = set()
    for row in conn.execute(
        "SELECT primary_report_id FROM similarity_cluster WHERE project_id = ?", (pid,)
    ):
        if row[0]:
            cluster_primaries.add(row[0])
    similarity_members: set[str] = set()
    for row in conn.execute(
        "SELECT report_id FROM similarity_cluster_member WHERE project_id = ?", (pid,)
    ):
        similarity_members.add(row[0])
    try:
        from db.compute.pipeline import _post_ast_family_canonical_ids
        post_family = _post_ast_family_canonical_ids(conn, pid)
    except Exception:
        post_family = set()
    post_family_singletons = post_family - similarity_members
    final_kept = cluster_primaries | post_family_singletons

    out = []
    for r in reports:
        rid = r["report_id"]
        m = metrics.get(rid, [])
        t = tables.get(rid, [])
        f = filters.get(rid, [])
        a = attributes.get(rid, [])
        out.append({
            "id": rid,
            "name": r["name"] or "",
            "owner": r["owner"] or "",
            "path": r["path"] or "",
            "executions": r["total_executions"] or 0,
            "users": r["total_users"] or 0,
            "lastExec": r["last_exec_ts"] or "",
            "matchTier": r["match_tier"] or "",
            "metrics": m,
            "tables": t,
            "filters": f,
            "attributes": a,
            "metricCount": r["metric_count"] or len(m),
            "tableCount": r["table_count"] or len(t),
            "filterCount": r["filter_count"] or len(f),
            "attributeCount": (r["attribute_count"] if "attribute_count" in r.keys() else None) or len(a),
            "clusterId": r["cluster_id"],
            "familyBase": r["family_base"] or "",
            "dateCreated": r["date_created"],
            "dateModified": r["date_modified"],
            "sql": r["sql_text"],
            "sqlError": r["sql_error"],
            "sourceType": r["source_type"] or "",
            "status": r["status"] or "",
            "isFinalCanonical": rid in final_kept,
            # Collapsed family siblings that this canonical stands for. Empty
            # list when the report isn't a family canonical (or has no sibs).
            "familySiblings": family_siblings_by_canonical.get(rid, []),
        })
    return out


# ---------------- inventory_all.json ------------------------------

def emit_inventory_all(pid: str, conn: sqlite3.Connection) -> list[dict]:
    return [
        {
            "id": r["report_id"],
            "name": r["name"] or "",
            "owner": r["owner"] or "",
            "path": r["path"] or "",
            "dateCreated": r["date_created"],
            "dateModified": r["date_modified"],
            "status": r["status"] or "active",
            "sourceType": r["source_type"] or "",
        }
        for r in conn.execute(
            """SELECT r.report_id, r.name, r.owner, r.path, r.date_created, r.date_modified,
                      r.status,
                      (SELECT source_type FROM raw_inventory ri
                        WHERE ri.project_id = r.project_id AND ri.report_id = r.report_id
                          AND ri.source_type IS NOT NULL LIMIT 1) AS source_type
                 FROM report r WHERE r.project_id = ?""",
            (pid,),
        )
    ]


# ---------------- retired.json ------------------------------------

def emit_retired(pid: str, conn: sqlite3.Connection) -> list[dict]:
    return [
        {
            "id": r["report_id"],
            "name": r["name"] or "",
            "owner": r["owner"] or "",
            "path": r["path"] or "",
            "dateCreated": r["date_created"],
            "dateModified": r["date_modified"],
            "bucket": r["bucket"],
            "sourceType": r["source_type"] or "",
        }
        for r in conn.execute(
            """SELECT r.report_id, r.name, r.owner, r.path, r.date_created, r.date_modified,
                      rr.bucket,
                      (SELECT source_type FROM raw_inventory ri
                        WHERE ri.project_id = r.project_id AND ri.report_id = r.report_id
                          AND ri.source_type IS NOT NULL LIMIT 1) AS source_type
                 FROM retired_report rr
                 JOIN report r
                   ON r.project_id = rr.project_id AND r.report_id = rr.report_id
                WHERE rr.project_id = ?""",
            (pid,),
        )
    ]


# ---------------- collisions.json ---------------------------------

def emit_collisions(pid: str, conn: sqlite3.Connection) -> list[dict]:
    groups = _rows(conn.execute(
        """SELECT * FROM collision_group WHERE project_id = ? ORDER BY size DESC, group_id""",
        (pid,),
    ))
    members_by_group: dict[str, list] = {}
    for r in conn.execute(
        """SELECT cm.group_id, cm.report_id, cm.match_tier, cm.folder_path, cm.owner,
                  r.name,
                  rm.metrics, rt.tables_, rf.filters_
             FROM collision_member cm
             JOIN report r
               ON r.project_id = cm.project_id AND r.report_id = cm.report_id
             LEFT JOIN (
                SELECT project_id, report_id, json_group_array(metric_name) AS metrics
                  FROM report_metric GROUP BY project_id, report_id
             ) rm ON rm.project_id = cm.project_id AND rm.report_id = cm.report_id
             LEFT JOIN (
                SELECT project_id, report_id, json_group_array(table_name) AS tables_
                  FROM report_table GROUP BY project_id, report_id
             ) rt ON rt.project_id = cm.project_id AND rt.report_id = cm.report_id
             LEFT JOIN (
                SELECT project_id, report_id, json_group_array(filter_name) AS filters_
                  FROM report_filter GROUP BY project_id, report_id
             ) rf ON rf.project_id = cm.project_id AND rf.report_id = cm.report_id
            WHERE cm.project_id = ?""",
        (pid,),
    ):
        members_by_group.setdefault(r["group_id"], []).append({
            "id": r["report_id"],
            "name": r["name"] or "",
            "matchTier": r["match_tier"] or "",
            "folderPath": r["folder_path"] or "",
            "owner": r["owner"] or "",
            "metrics": json.loads(r["metrics"] or "[]"),
            "tables": json.loads(r["tables_"] or "[]"),
            "filters": json.loads(r["filters_"] or "[]"),
        })

    out = []
    for g in groups:
        out.append({
            "pass": g["pass"],
            "executions": g["executions"],
            "lastExec": g["last_exec"] or "",
            "normalizedName": g["normalized_name"],
            "canonicalId": g["canonical_id"],
            "canonicalName": g["canonical_name"] or "",
            "canonicalPath": g["canonical_path"] or "",
            "size": g["size"],
            "collapsed": g["collapsed"],
            "members": members_by_group.get(g["group_id"], []),
        })
    return out


# ---------------- fingerprints.json -------------------------------

def emit_fingerprints(pid: str, conn: sqlite3.Connection) -> list[dict]:
    groups = _rows(conn.execute(
        """SELECT * FROM fingerprint_group WHERE project_id = ? ORDER BY size DESC, group_id""",
        (pid,),
    ))
    members_by_group: dict[str, list] = {}
    for r in conn.execute(
        """SELECT fm.group_id, fm.report_id, r.name, r.path,
                  t.total_executions, t.total_users, t.last_exec_ts
             FROM fingerprint_member fm
             JOIN report r
               ON r.project_id = fm.project_id AND r.report_id = fm.report_id
             LEFT JOIN telemetry_match t
               ON t.project_id = fm.project_id AND t.report_id = fm.report_id
            WHERE fm.project_id = ?""",
        (pid,),
    ):
        members_by_group.setdefault(r["group_id"], []).append({
            "id": r["report_id"],
            "name": r["name"] or "",
            "path": r["path"] or "",
            "executions": r["total_executions"] or 0,
            "users": r["total_users"] or 0,
            "lastExec": r["last_exec_ts"] or "",
        })

    out = []
    for g in groups:
        out.append({
            "id": g["group_id"],
            "size": g["size"],
            "reducible": g["reducible"],
            "metricCount": g["metric_count"],
            "tableCount": g["table_count"],
            "filterCount": g["filter_count"],
            "totalExecutions": g["total_executions"] or 0,
            "metrics": json.loads(g["metrics_json"] or "[]"),
            "tables": json.loads(g["tables_json"] or "[]"),
            "filters": json.loads(g["filters_json"] or "[]"),
            "members": sorted(members_by_group.get(g["group_id"], []),
                              key=lambda m: -m["executions"]),
        })
    return out


# ---------------- sql_hash_groups.json ----------------------------

def emit_sql_hash_groups(pid: str, conn: sqlite3.Connection) -> list[dict]:
    groups = _rows(conn.execute(
        """SELECT * FROM sql_hash_group WHERE project_id = ? ORDER BY size DESC, group_id""",
        (pid,),
    ))
    members_by_group: dict[str, list] = {}
    for r in conn.execute(
        """SELECT shm.group_id, shm.report_id, r.name, r.path,
                  t.total_executions, t.total_users, t.last_exec_ts
             FROM sql_hash_member shm
             JOIN report r
               ON r.project_id = shm.project_id AND r.report_id = shm.report_id
             LEFT JOIN telemetry_match t
               ON t.project_id = shm.project_id AND t.report_id = shm.report_id
            WHERE shm.project_id = ?""",
        (pid,),
    ):
        members_by_group.setdefault(r["group_id"], []).append({
            "id": r["report_id"],
            "name": r["name"] or "",
            "path": r["path"] or "",
            "executions": r["total_executions"] or 0,
            "users": r["total_users"] or 0,
            "lastExec": r["last_exec_ts"] or "",
        })

    out = []
    for g in groups:
        out.append({
            "id": g["group_id"],
            "size": g["size"],
            "reducible": g["reducible"],
            "totalExecutions": g["total_executions"] or 0,
            "sqlHash": g["sql_hash"] or "",
            "sqlPreview": g["sql_preview"] or "",
            "members": sorted(members_by_group.get(g["group_id"], []),
                              key=lambda m: -m["executions"]),
        })
    return out


# ---------------- ast_clusters.json + ast_hashes.json -------------

def emit_ast_clusters(pid: str, conn: sqlite3.Connection) -> list[dict]:
    clusters = _rows(conn.execute(
        """SELECT * FROM ast_cluster WHERE project_id = ? ORDER BY size DESC, cluster_id""",
        (pid,),
    ))
    members_by_cluster: dict[str, list] = {}
    for r in conn.execute(
        """SELECT acm.cluster_id, acm.report_id, r.name, r.path,
                  t.total_executions, t.total_users, t.last_exec_ts
             FROM ast_cluster_member acm
             JOIN report r
               ON r.project_id = acm.project_id AND r.report_id = acm.report_id
             LEFT JOIN telemetry_match t
               ON t.project_id = acm.project_id AND t.report_id = acm.report_id
            WHERE acm.project_id = ?""",
        (pid,),
    ):
        members_by_cluster.setdefault(r["cluster_id"], []).append({
            "id": r["report_id"],
            "name": r["name"] or "",
            "path": r["path"] or "",
            "executions": r["total_executions"] or 0,
            "users": r["total_users"] or 0,
            "lastExec": r["last_exec_ts"] or "",
        })

    out = []
    for c in clusters:
        out.append({
            "id": c["cluster_id"],
            "size": c["size"],
            "reducible": c["reducible"],
            "allExact": bool(c["all_exact"]),
            "avgScore": c["avg_score"],
            "minScore": c["min_score"],
            "totalExecutions": c["total_executions"] or 0,
            "inSqlHash": bool(c["in_sql_hash"]),
            "astExclusive": bool(c["ast_exclusive"]),
            "members": sorted(members_by_cluster.get(c["cluster_id"], []),
                              key=lambda m: -m["executions"]),
        })
    return out


def emit_ast_hashes(pid: str, conn: sqlite3.Connection) -> dict:
    out: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT report_id, hash FROM ast_hash WHERE project_id = ?", (pid,)
    ):
        out.setdefault(r["report_id"], []).append(r["hash"])
    return out


# ---------------- families.json -----------------------------------

def emit_families(pid: str, conn: sqlite3.Connection) -> list[dict]:
    families = _rows(conn.execute(
        """SELECT * FROM family WHERE project_id = ? ORDER BY size DESC, base""",
        (pid,),
    ))
    members_by_fam: dict[str, list] = {}
    for r in conn.execute(
        """SELECT fm.base, fm.report_id, fm.variant_suffix, fm.is_canonical,
                  r.name, t.total_executions, t.total_users, t.last_exec_ts
             FROM family_member fm
             JOIN report r
               ON r.project_id = fm.project_id AND r.report_id = fm.report_id
             LEFT JOIN telemetry_match t
               ON t.project_id = fm.project_id AND t.report_id = fm.report_id
            WHERE fm.project_id = ?""",
        (pid,),
    ):
        members_by_fam.setdefault(r["base"], []).append({
            "id": r["report_id"],
            "name": r["name"] or "",
            "variantSuffix": r["variant_suffix"] or "",
            "isCanonical": bool(r["is_canonical"]),
            "executions": r["total_executions"] or 0,
            "users": r["total_users"] or 0,
            "lastExec": r["last_exec_ts"] or "",
        })

    out = []
    for f in families:
        out.append({
            "base": f["base"],
            "size": f["size"],
            "reducible": f["reducible"],
            "canonicalId": f["canonical_id"] if "canonical_id" in f.keys() else None,
            "totalExecutions": f["total_executions"] or 0,
            "members": sorted(members_by_fam.get(f["base"], []),
                              key=lambda m: -m["executions"]),
        })
    return out


# ---------------- clusters.json -----------------------------------

def emit_clusters(pid: str, conn: sqlite3.Connection) -> list[dict]:
    clusters = _rows(conn.execute(
        """SELECT * FROM similarity_cluster WHERE project_id = ? ORDER BY size DESC, cluster_id""",
        (pid,),
    ))
    members_by_cluster: dict[str, list] = {}
    for r in conn.execute(
        "SELECT cluster_id, report_id FROM similarity_cluster_member WHERE project_id = ?",
        (pid,),
    ):
        members_by_cluster.setdefault(r["cluster_id"], []).append(r["report_id"])

    # Build the synthetic "SINGLETONS" entry: all post-family survivors that
    # did NOT join any multi-member similarity cluster. Clustering runs on the
    # post-family set (family-collapsed siblings are not part of the universe
    # and should NOT be listed as singletons here).
    from db.compute.pipeline import _post_ast_family_canonical_ids
    post_family = _post_ast_family_canonical_ids(conn, pid)
    in_cluster = {rid for members in members_by_cluster.values() for rid in members}
    singleton_ids = sorted(post_family - in_cluster)

    common_by_cluster: dict[str, dict[str, list]] = {}
    for r in conn.execute(
        """SELECT cluster_id, kind, name FROM similarity_cluster_common
            WHERE project_id = ? ORDER BY name""",
        (pid,),
    ):
        common_by_cluster.setdefault(r["cluster_id"], {"metric": [], "table": [], "filter": []})[r["kind"]].append(r["name"])

    # Per-report feature counts for data-quality scoring.
    # A cluster where most members have no captured metadata (no metrics, no
    # tables, no attributes) isn't a real "similar reports" cluster — it's a
    # bucket of reports whose inventory extraction failed. We label those
    # distinctly so business users don't act on them as rationalization targets.
    feature_counts = {}
    for r in conn.execute(
        """SELECT r.report_id,
                  (COALESCE(r.metric_count,0) + COALESCE(r.table_count,0)
                   + COALESCE(r.filter_count,0) + COALESCE(r.attribute_count,0)) AS total_features
             FROM report r WHERE r.project_id = ?""",
        (pid,),
    ):
        feature_counts[r[0]] = r[1] or 0

    def classify_quality(member_ids: list[str]) -> tuple[str, str, int]:
        """Returns (quality, reasonCode, emptyCount)."""
        if not member_ids:
            return ("unknown", "no_members", 0)
        empty = sum(1 for m in member_ids if feature_counts.get(m, 0) == 0)
        low = sum(1 for m in member_ids if feature_counts.get(m, 0) < 3)
        total = len(member_ids)
        if empty / total >= 0.80:
            return ("low", "empty_features", empty)
        if low / total >= 0.80:
            return ("low", "sparse_features", empty)
        if empty / total >= 0.50:
            return ("mixed", "mixed_features", empty)
        return ("high", "ok", empty)

    out = []
    for c in clusters:
        cm = common_by_cluster.get(c["cluster_id"], {"metric": [], "table": [], "filter": []})
        member_ids = members_by_cluster.get(c["cluster_id"], [])
        quality, reason, empty_count = classify_quality(member_ids)
        entry = {
            "id": c["cluster_id"],
            "size": c["size"],
            "primaryReportId": c["primary_report_id"] or "",
            "primaryName": c["primary_name"] or "",
            "totalExecutions": c["total_executions"] or 0,
            "totalUsers": c["total_users"] or 0,
            "commonMetrics": cm["metric"],
            "commonTables": cm["table"],
            "commonFilters": cm["filter"],
            "metricCount": c["metric_count"] or 0,
            "tableCount": c["table_count"] or 0,
            "filterCount": c["filter_count"] or 0,
            "memberIds": member_ids,
            "dataQuality": quality,                  # "high" | "mixed" | "low" | "unknown"
            "dataQualityReason": reason,             # machine-readable code
            "emptyMemberCount": empty_count,         # how many have 0 features
        }
        if c["avg_similarity"] is not None:
            entry["avgSimilarity"] = c["avg_similarity"]
        if c["llm_label"] is not None:
            entry["llmLabel"] = c["llm_label"]
        if c["llm_action"] is not None:
            entry["llmAction"] = c["llm_action"]
        if c["llm_removable"] is not None:
            entry["llmRemovable"] = c["llm_removable"]
        out.append(entry)

    # Prepend the synthetic singletons entry so it sits at the top of the list.
    if singleton_ids:
        tel_rows = list(conn.execute(
            f"""SELECT r.report_id, r.name, t.total_executions, t.total_users
                  FROM report r
                  LEFT JOIN telemetry_match t
                    ON t.project_id = r.project_id AND t.report_id = r.report_id
                 WHERE r.project_id = ? AND r.report_id IN ({",".join("?" * len(singleton_ids))})""",
            [pid, *singleton_ids],
        ))
        total_exec = sum((r[2] or 0) for r in tel_rows)
        total_users = sum((r[3] or 0) for r in tel_rows)
        primary_name = ""
        if tel_rows:
            top = max(tel_rows, key=lambda r: r[2] or 0)
            primary_name = top[1] or ""
        singletons_entry = {
            "id": "SINGLETONS",
            "size": len(singleton_ids),
            "primaryReportId": "",
            "primaryName": f"Unmatched post-AST reports ({len(singleton_ids):,} singletons)",
            "totalExecutions": total_exec,
            "totalUsers": total_users,
            "commonMetrics": [],
            "commonTables": [],
            "commonFilters": [],
            "metricCount": 0,
            "tableCount": 0,
            "filterCount": 0,
            "memberIds": singleton_ids,
            "isSingletons": True,
            "topExampleName": primary_name,
        }
        out.insert(0, singletons_entry)
    return out


# ---------------- cluster_comparison.json -------------------------

def emit_cluster_comparison(pid: str, conn: sqlite3.Connection) -> dict:
    """Compare which post-AST survivors landed in Jaccard vs Semantic clusters.

    Returns:
      - stats: counts per quadrant (both, jaccard-only, semantic-only, neither)
      - reports: per-report row with both cluster IDs (null when singleton in
        that method), cluster sizes, and a category label.

    Baseline universe is the post-family set — the actual input to both
    Jaccard and Semantic clustering. Family-collapsed siblings are NOT
    included (they never entered either clustering pass).
    """
    from db.compute.pipeline import _post_ast_family_canonical_ids
    try:
        post_ast = _post_ast_family_canonical_ids(conn, pid)
    except Exception:
        post_ast = set()

    # Jaccard membership
    j_cluster: dict[str, str] = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT report_id, cluster_id FROM similarity_cluster_member WHERE project_id = ?",
            (pid,),
        )
    }
    j_size: dict[str, int] = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT cluster_id, size FROM similarity_cluster WHERE project_id = ?",
            (pid,),
        )
    }
    # Semantic membership
    s_cluster: dict[str, str] = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT report_id, cluster_id FROM semantic_cluster_member WHERE project_id = ?",
            (pid,),
        )
    }
    s_size: dict[str, int] = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT cluster_id, size FROM semantic_cluster WHERE project_id = ?",
            (pid,),
        )
    }
    s_label: dict[str, str] = {
        r[0]: r[1] or ""
        for r in conn.execute(
            "SELECT cluster_id, llm_label FROM semantic_cluster WHERE project_id = ?",
            (pid,),
        )
    }

    # Report metadata
    meta: dict[str, dict] = {}
    for r in conn.execute(
        """SELECT r.report_id, r.name, r.path, r.owner,
                  t.total_executions, rs.sql_text IS NOT NULL AS has_sql
             FROM report r
             LEFT JOIN telemetry_match t USING (project_id, report_id)
             LEFT JOIN report_sql rs USING (project_id, report_id)
            WHERE r.project_id = ?""",
        (pid,),
    ):
        meta[r[0]] = {
            "name": r[1] or "",
            "path": r[2] or "",
            "owner": r[3] or "",
            "executions": r[4] or 0,
            "hasSql": bool(r[5]),
        }

    reports = []
    stats = {"both": 0, "jaccardOnly": 0, "semanticOnly": 0, "neither": 0}
    for rid in post_ast:
        jcid = j_cluster.get(rid)
        scid = s_cluster.get(rid)
        if jcid and scid:
            cat = "both"
        elif jcid:
            cat = "jaccardOnly"
        elif scid:
            cat = "semanticOnly"
        else:
            cat = "neither"
        stats[cat] += 1
        m = meta.get(rid, {})
        reports.append({
            "id": rid,
            "name": m.get("name", ""),
            "path": m.get("path", ""),
            "owner": m.get("owner", ""),
            "executions": m.get("executions", 0),
            "hasSql": m.get("hasSql", False),
            "category": cat,
            "jaccardClusterId": jcid,
            "jaccardClusterSize": j_size.get(jcid) if jcid else None,
            "semanticClusterId": scid,
            "semanticClusterSize": s_size.get(scid) if scid else None,
            "semanticLabel": s_label.get(scid) if scid else None,
        })
    # Sort: category priority then executions desc
    cat_order = {"both": 0, "semanticOnly": 1, "jaccardOnly": 2, "neither": 3}
    reports.sort(key=lambda r: (cat_order[r["category"]], -(r["executions"] or 0)))

    return {
        "postAstTotal": len(post_ast),
        "stats": stats,
        "reports": reports,
    }


# ---------------- heavy_users.json --------------------------------

def emit_heavy_users(pid: str, conn: sqlite3.Connection, top_user_cap: int = 100) -> list[dict]:
    """Aggregate raw_user_activity scoped to this project (by folder path prefix).
    Returns the top N users with per-report breakdown.
    """
    cfg = get_project(pid)
    proj_name = cfg["name"]
    like1 = f"/{proj_name}/%"
    like2 = f"{proj_name}/%"

    # Service-account heuristics — flagged separately in the UI
    SERVICE_MARKERS = ("ODS_PROD", "System Operation", "Administrator", "dataiku",
                       "Atlan", "ds_admin", "_svc", "service", "automation")

    from db.user_filters import is_dev_admin

    # Dev/admin users: their personal-folder activity (/Profiles/.../My Reports)
    # is excluded from the aggregate so their dev-sandbox reports don't inflate
    # their activity numbers. Build the list once from the DB.
    all_users = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT user FROM raw_user_activity WHERE user != ''"
        )
    ]
    dev_admin_lower = [u.lower() for u in all_users if is_dev_admin(u)]

    # Build the exclusion clause with placeholders; empty list → no exclusion.
    if dev_admin_lower:
        excl_placeholders = ",".join("?" * len(dev_admin_lower))
        excl_clause = (
            "AND NOT ((folder_path LIKE ? OR folder_path LIKE ?) "
            f"AND lower(user) IN ({excl_placeholders}))"
        )
        excl_params = [
            f"/{proj_name}/Profiles/%",
            f"{proj_name}/Profiles/%",
            *dev_admin_lower,
        ]
    else:
        excl_clause = ""
        excl_params = []

    users = conn.execute(
        f"""SELECT user,
                   SUM(executions) AS execs,
                   COUNT(DISTINCT object_id) AS uniq_reports,
                   SUM(sessions) AS sessions,
                   SUM(errors) AS errors,
                   MAX(last_execution) AS last_exec
              FROM raw_user_activity
             WHERE (folder_path LIKE ? OR folder_path LIKE ?)
               AND user != ''
               {excl_clause}
             GROUP BY user
             ORDER BY execs DESC
             LIMIT ?""",
        [like1, like2, *excl_params, top_user_cap],
    ).fetchall()

    # Lookup for report status + cluster
    kept_primaries: set[str] = set()
    for row in conn.execute(
        "SELECT primary_report_id FROM similarity_cluster WHERE project_id = ?", (pid,)
    ):
        if row[0]:
            kept_primaries.add(row[0])
    similarity_members: set[str] = set()
    for row in conn.execute(
        "SELECT report_id FROM similarity_cluster_member WHERE project_id = ?", (pid,)
    ):
        similarity_members.add(row[0])
    try:
        from db.compute.pipeline import _post_ast_family_canonical_ids
        post_family = _post_ast_family_canonical_ids(conn, pid)
    except Exception:
        post_family = set()
    final_kept = kept_primaries | (post_family - similarity_members)

    # Status map — {report_id: status}
    status_by_id: dict[str, str] = {}
    name_by_id: dict[str, str] = {}
    cluster_by_id: dict[str, str | None] = {}
    for r in conn.execute(
        """SELECT report_id, name, status, cluster_id FROM report WHERE project_id = ?""",
        (pid,),
    ):
        status_by_id[r[0]] = r[2] or ""
        name_by_id[r[0]] = r[1] or ""
        cluster_by_id[r[0]] = r[3]

    out = []
    for u in users:
        user = u[0]
        is_dev = is_dev_admin(user)
        is_service = (
            is_dev
            or any(m.lower() in (user or "").lower() for m in SERVICE_MARKERS)
        )
        # Top reports this user runs in this project. For dev/admin users, drop
        # personal-folder rows so only the public-folder activity is shown.
        if is_dev:
            top_reports = conn.execute(
                """SELECT object_id, report_name, SUM(executions) e, SUM(sessions) s,
                          SUM(errors) err, MAX(last_execution) le, folder_path
                     FROM raw_user_activity
                    WHERE user = ?
                      AND (folder_path LIKE ? OR folder_path LIKE ?)
                      AND NOT (folder_path LIKE ? OR folder_path LIKE ?)
                    GROUP BY object_id, report_name, folder_path
                    ORDER BY e DESC LIMIT 25""",
                (user, like1, like2,
                 f"/{proj_name}/Profiles/%", f"{proj_name}/Profiles/%"),
            ).fetchall()
        else:
            top_reports = conn.execute(
                """SELECT object_id, report_name, SUM(executions) e, SUM(sessions) s,
                          SUM(errors) err, MAX(last_execution) le, folder_path
                     FROM raw_user_activity
                    WHERE user = ?
                      AND (folder_path LIKE ? OR folder_path LIKE ?)
                    GROUP BY object_id, report_name, folder_path
                    ORDER BY e DESC LIMIT 25""",
                (user, like1, like2),
            ).fetchall()
        report_list = []
        for tr in top_reports:
            obj_id = tr[0]
            in_inv = obj_id in status_by_id
            report_list.append({
                "objectId": obj_id,
                "reportName": tr[1] or "",
                "executions": tr[2] or 0,
                "sessions": tr[3] or 0,
                "errors": tr[4] or 0,
                "lastExec": tr[5] or "",
                "folderPath": tr[6] or "",
                "inInventory": in_inv,
                "status": status_by_id.get(obj_id) if in_inv else "not-in-inventory",
                "clusterId": cluster_by_id.get(obj_id) if in_inv else None,
                "isFinalCanonical": obj_id in final_kept,
                "inventoryName": name_by_id.get(obj_id) if in_inv else None,
            })
        out.append({
            "user": user,
            "isService": is_service,
            "totalExecutions": u[1] or 0,
            "uniqueReports": u[2] or 0,
            "sessions": u[3] or 0,
            "errors": u[4] or 0,
            "lastExec": u[5] or "",
            "topReports": report_list,
        })
    return out


# ---------------- semantic_clusters.json --------------------------

def emit_semantic_clusters(pid: str, conn: sqlite3.Connection) -> list[dict]:
    """Emit semantic clusters (embedding-based). Returns [] when the semantic
    stage hasn't been run yet, which the UI renders as an empty-state.
    """
    clusters = _rows(conn.execute(
        """SELECT * FROM semantic_cluster WHERE project_id = ?
            ORDER BY size DESC, cluster_id""",
        (pid,),
    ))
    if not clusters:
        return []

    members_by_cluster: dict[str, list] = {}
    for r in conn.execute(
        """SELECT cluster_id, report_id, cosine_to_primary
             FROM semantic_cluster_member WHERE project_id = ?
            ORDER BY cluster_id, cosine_to_primary DESC""",
        (pid,),
    ):
        members_by_cluster.setdefault(r["cluster_id"], []).append({
            "id": r["report_id"],
            "cosineToPrimary": r["cosine_to_primary"],
        })

    reviews_by_cluster: dict[str, dict] = {}
    for r in conn.execute(
        """SELECT cluster_id, label, business_function, relationship, action,
                  confidence, consolidation_detail, keep_report, removable_count, error
             FROM semantic_llm_review WHERE project_id = ?""",
        (pid,),
    ):
        reviews_by_cluster[r["cluster_id"]] = {
            "label": r["label"] or "",
            "businessFunction": r["business_function"] or "",
            "relationship": r["relationship"] or "",
            "action": r["action"] or "",
            "confidence": r["confidence"] or "",
            "detail": r["consolidation_detail"] or "",
            "keepReport": r["keep_report"] or "",
            "removableCount": r["removable_count"] or 0,
            "error": r["error"] or None,
        }

    # Compute singleton list — post-family survivors (the semantic stage's
    # input) minus reports in any multi-member semantic cluster. Using the
    # post-AST set here would wrongly include 865 INSIGHT reports that were
    # collapsed by the Family stage and never entered semantic clustering.
    from db.compute.pipeline import _post_ast_family_canonical_ids
    try:
        post_family = _post_ast_family_canonical_ids(conn, pid)
    except Exception:
        post_family = set()
    clustered_ids = {m["id"] for members in members_by_cluster.values() for m in members}
    singleton_ids = sorted(post_family - clustered_ids)

    # Per-report feature counts for data-quality scoring (same as emit_clusters).
    feature_counts = {}
    for r in conn.execute(
        """SELECT report_id,
                  (COALESCE(metric_count,0) + COALESCE(table_count,0)
                   + COALESCE(filter_count,0) + COALESCE(attribute_count,0)) AS total
             FROM report WHERE project_id = ?""",
        (pid,),
    ):
        feature_counts[r[0]] = r[1] or 0

    def _classify(member_ids: list[str]) -> tuple[str, str, int]:
        if not member_ids:
            return ("unknown", "no_members", 0)
        empty = sum(1 for m in member_ids if feature_counts.get(m, 0) == 0)
        low = sum(1 for m in member_ids if feature_counts.get(m, 0) < 3)
        total = len(member_ids)
        if empty / total >= 0.80:
            return ("low", "empty_features", empty)
        if low / total >= 0.80:
            return ("low", "sparse_features", empty)
        if empty / total >= 0.50:
            return ("mixed", "mixed_features", empty)
        return ("high", "ok", empty)

    out = []
    for c in clusters:
        cid = c["cluster_id"]
        members = members_by_cluster.get(cid, [])
        member_ids = [m["id"] for m in members]
        quality, reason, empty_count = _classify(member_ids)
        entry = {
            "id": cid,
            "size": c["size"],
            "primaryReportId": c["primary_report_id"] or "",
            "primaryName": c["primary_name"] or "",
            "totalExecutions": c["total_executions"] or 0,
            "totalUsers": c["total_users"] or 0,
            "avgCosine": c["avg_cosine"],
            "minCosine": c["min_cosine"],
            "members": members,
            "memberIds": member_ids,
            "dataQuality": quality,
            "dataQualityReason": reason,
            "emptyMemberCount": empty_count,
        }
        review = reviews_by_cluster.get(cid)
        if review:
            entry["llm"] = review
        out.append(entry)

    if singleton_ids:
        sing_rows = list(conn.execute(
            f"""SELECT r.report_id, r.name, t.total_executions, t.total_users
                  FROM report r
                  LEFT JOIN telemetry_match t USING (project_id, report_id)
                 WHERE r.project_id = ? AND r.report_id IN ({",".join("?" * len(singleton_ids))})""",
            [pid, *singleton_ids],
        ))
        tot_exec = sum((r[2] or 0) for r in sing_rows)
        tot_users = sum((r[3] or 0) for r in sing_rows)
        primary_name = ""
        if sing_rows:
            top = max(sing_rows, key=lambda r: r[2] or 0)
            primary_name = top[1] or ""
        out.insert(0, {
            "id": "SC_SINGLETONS",
            "size": len(singleton_ids),
            "primaryReportId": "",
            "primaryName": f"Semantic singletons ({len(singleton_ids):,} post-AST reports with no embedding pair \u2265 threshold)",
            "totalExecutions": tot_exec,
            "totalUsers": tot_users,
            "avgCosine": None,
            "minCosine": None,
            "members": [{"id": rid, "cosineToPrimary": None} for rid in singleton_ids],
            "memberIds": singleton_ids,
            "isSingletons": True,
            "topExampleName": primary_name,
        })
    return out


# ---------------- similarities.json -------------------------------

def emit_similarities(pid: str, conn: sqlite3.Connection) -> dict | list:
    """Reconstruct the similarities file.

    Returns a dict keyed by 'idA|idB' when per-dimension scores are present,
    or a list of {a, b, score} when only a combined score was captured
    (preserves the INSIGHT shape that came in that way).
    """
    rows = _rows(conn.execute(
        """SELECT id_a, id_b, combined, metric, tables, filters, ast
             FROM similarity_pair WHERE project_id = ?""",
        (pid,),
    ))
    if not rows:
        return {}
    # INSIGHT loaded list-shaped similarities with only `combined` set.
    any_detail = any(r["metric"] is not None or r["tables"] is not None for r in rows)
    if any_detail:
        out: dict = {}
        for r in rows:
            d: dict = {"combined": r["combined"]}
            if r["metric"] is not None:
                d["metric"] = r["metric"]
            if r["tables"] is not None:
                d["table"] = r["tables"]
            if r["filters"] is not None:
                d["filter"] = r["filters"]
            if r["ast"] is not None:
                d["ast"] = r["ast"]
            out[f"{r['id_a']}|{r['id_b']}"] = d
        return out
    # Fallback: list shape
    return [
        {"a": r["id_a"], "b": r["id_b"], "score": r["combined"]}
        for r in rows
    ]


# ---------------- llm_reviews.json --------------------------------

def emit_llm_reviews(pid: str, conn: sqlite3.Connection) -> list[dict]:
    out = []
    for r in conn.execute(
        """SELECT lr.*, sc.size AS cluster_size, sc.primary_name
             FROM llm_review lr
             LEFT JOIN similarity_cluster sc
               ON sc.project_id = lr.project_id AND sc.cluster_id = lr.cluster_id
            WHERE lr.project_id = ?
            ORDER BY lr.cluster_id""",
        (pid,),
    ):
        out.append({
            "clusterId": r["cluster_id"],
            "clusterSize": r["cluster_size"] or 0,
            "primaryName": r["primary_name"] or "",
            "label": r["label"] or "",
            "business_function": r["business_function"] or "",
            "relationship": r["relationship"] or "",
            "consolidation_action": r["action"] or "",
            "confidence": r["confidence"] or "",
            "consolidation_detail": r["consolidation_detail"] or "",
            "keep_report": r["keep_report"] or "",
            "removable_count": r["removable_count"] or 0,
        })
    return out


def emit_cross_project(conn: sqlite3.Connection, out_dir: Path) -> dict:
    """Emit public/data/_cross/*.json from cross_project_* tables.

    Produces: name_matches.json, sql_matches.json, shared_tables.json,
    shared_metrics.json, shared_families.json, summary.json.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    def _dump(name: str, data):
        (out_dir / name).write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        n = len(data) if isinstance(data, (list, dict)) else 1
        print(f"  {name:28}  {n:>8,}")

    print(f"\nEmitting cross-project data -> {out_dir}")

    # Name matches with members
    name_groups = []
    for m in conn.execute(
        """SELECT match_id, normalized_name, n_projects, n_reports, projects_csv
             FROM cross_project_name_match
            ORDER BY n_projects DESC, n_reports DESC"""
    ):
        members = [
            {
                "projectId": r[0], "reportId": r[1], "name": r[2] or "",
                "path": r[3] or "", "executions": r[4] or 0,
            }
            for r in conn.execute(
                """SELECT project_id, report_id, name, path, executions
                     FROM cross_project_name_member WHERE match_id = ?""",
                (m[0],),
            )
        ]
        name_groups.append({
            "id": f"NM{m[0]:05d}",
            "normalizedName": m[1],
            "nProjects": m[2], "nReports": m[3],
            "projects": sorted((m[4] or "").split(",")),
            "members": members,
        })
    _dump("name_matches.json", name_groups)

    # SQL hash matches
    sql_groups = []
    for m in conn.execute(
        """SELECT match_id, sql_hash, n_projects, n_reports, projects_csv
             FROM cross_project_sql_match ORDER BY n_reports DESC"""
    ):
        members = [
            {"projectId": r[0], "reportId": r[1]}
            for r in conn.execute(
                "SELECT project_id, report_id FROM cross_project_sql_member WHERE match_id = ?",
                (m[0],),
            )
        ]
        sql_groups.append({
            "id": f"SH{m[0]:05d}",
            "sqlHash": m[1], "nProjects": m[2], "nReports": m[3],
            "projects": sorted((m[4] or "").split(",")),
            "members": members,
        })
    _dump("sql_matches.json", sql_groups)

    shared_tables = [
        {"tableName": r[0], "nProjects": r[1], "nReferences": r[2],
         "projects": sorted((r[3] or "").split(","))}
        for r in conn.execute(
            """SELECT table_name, n_projects, n_references, projects_csv
                 FROM cross_project_shared_table
                ORDER BY n_projects DESC, n_references DESC"""
        )
    ]
    _dump("shared_tables.json", shared_tables)

    shared_metrics = [
        {"metricName": r[0], "nProjects": r[1], "nReferences": r[2],
         "projects": sorted((r[3] or "").split(","))}
        for r in conn.execute(
            """SELECT metric_name, n_projects, n_references, projects_csv
                 FROM cross_project_shared_metric
                ORDER BY n_projects DESC, n_references DESC"""
        )
    ]
    _dump("shared_metrics.json", shared_metrics)

    shared_families = [
        {"familyBase": r[0], "nProjects": r[1], "nReports": r[2],
         "projects": sorted((r[3] or "").split(","))}
        for r in conn.execute(
            """SELECT family_base, n_projects, n_reports, projects_csv
                 FROM cross_project_family
                ORDER BY n_projects DESC, n_reports DESC"""
        )
    ]
    _dump("shared_families.json", shared_families)

    summary = {
        "nameMatches": len(name_groups),
        "sqlMatches": len(sql_groups),
        "sharedTables": len(shared_tables),
        "sharedMetrics": len(shared_metrics),
        "sharedFamilies": len(shared_families),
        "projects": sorted({r[0] for r in conn.execute("SELECT project_id FROM project")}),
    }
    _dump("summary.json", summary)
    return summary


# ---------------- driver -----------------------------------------

def emit_project(project: dict, conn: sqlite3.Connection, out_dir: Path | None = None) -> dict:
    pid = project["project_id"]
    out_dir = out_dir or project["data_dir"]
    scope = project.get("reports_scope", "all")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nEmitting project: {project['name']} -> {out_dir}  (reports_scope={scope})")

    emitters = [
        ("summary.json", lambda: emit_summary(pid, conn)),
        ("reports.json", lambda: emit_reports(pid, conn, scope)),
        ("inventory_all.json", lambda: emit_inventory_all(pid, conn)),
        ("retired.json", lambda: emit_retired(pid, conn)),
        ("collisions.json", lambda: emit_collisions(pid, conn)),
        ("fingerprints.json", lambda: emit_fingerprints(pid, conn)),
        ("sql_hash_groups.json", lambda: emit_sql_hash_groups(pid, conn)),
        ("ast_clusters.json", lambda: emit_ast_clusters(pid, conn)),
        ("ast_hashes.json", lambda: emit_ast_hashes(pid, conn)),
        ("families.json", lambda: emit_families(pid, conn)),
        ("clusters.json", lambda: emit_clusters(pid, conn)),
        ("similarities.json", lambda: emit_similarities(pid, conn)),
        ("llm_reviews.json", lambda: emit_llm_reviews(pid, conn)),
        ("semantic_clusters.json", lambda: emit_semantic_clusters(pid, conn)),
        ("cluster_comparison.json", lambda: emit_cluster_comparison(pid, conn)),
        ("heavy_users.json", lambda: emit_heavy_users(pid, conn)),
    ]

    counts: dict[str, int] = {}
    for fname, fn in emitters:
        data = fn()
        _json_dump(out_dir / fname, data)
        if isinstance(data, list):
            counts[fname] = len(data)
        elif isinstance(data, dict):
            counts[fname] = len(data) if fname != "summary.json" else 1
        else:
            counts[fname] = 0
        print(f"  {fname:28}  {counts[fname]:>8,}")
    return counts


def main(out_root: Path | None = None) -> int:
    init()
    conn = connect()
    try:
        for proj in PROJECTS:
            od = (out_root / proj["name"]) if out_root else None
            emit_project(proj, conn, od)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--out-root", type=Path, default=None,
                   help="Override output root (default: overwrite public/data/<project>)")
    args = p.parse_args()
    sys.exit(main(args.out_root))
