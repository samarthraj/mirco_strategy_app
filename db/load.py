"""Load one project's public/data/<project>/*.json into the SQLite DB.

Idempotent: the load is wrapped in a transaction, and we delete all rows
for the given project before re-inserting. Same schema serves all 3 projects.
"""
from __future__ import annotations
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.db import connect, init, PROJECTS

# Order matters (respect FK cascade). project is upserted, not deleted.
TABLES_TO_CLEAR = [
    "llm_action_count", "top_list", "object_type_count", "tier_breakdown",
    "funnel_stage", "run_summary",
    "llm_review",
    "similarity_pair", "similarity_cluster_common",
    "similarity_cluster_member", "similarity_cluster",
    "family_member", "family",
    "ast_cluster_member", "ast_cluster",
    "sql_hash_member", "sql_hash_group",
    "fingerprint_member", "fingerprint_group",
    "collision_member", "collision_group",
    "retired_report", "telemetry_match",
    "ast_hash",
    "report_sql", "report_filter", "report_table", "report_metric",
    "report",
]


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  WARN: could not parse {path.name}: {e}")
        return default


def _executemany(conn: sqlite3.Connection, sql: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    conn.executemany(sql, rows)
    return len(rows)


def load_project(project: dict, conn: sqlite3.Connection) -> dict:
    """Load one project from its public/data dir. Returns row counts per table."""
    pid = project["project_id"]
    name = project["name"]
    data_dir = project["data_dir"]
    if not data_dir.exists():
        print(f"  SKIP: data dir missing for {name}: {data_dir}")
        return {}

    print(f"\nLoading project: {name}  ({data_dir})")

    summary = _read_json(data_dir / "summary.json", {}) or {}
    reports = _read_json(data_dir / "reports.json", []) or []
    inventory_all = _read_json(data_dir / "inventory_all.json", []) or []
    retired = _read_json(data_dir / "retired.json", []) or []
    collisions = _read_json(data_dir / "collisions.json", []) or []
    fingerprints = _read_json(data_dir / "fingerprints.json", []) or []
    sql_hash_groups = _read_json(data_dir / "sql_hash_groups.json", []) or []
    ast_clusters = _read_json(data_dir / "ast_clusters.json", []) or []
    ast_hashes = _read_json(data_dir / "ast_hashes.json", {}) or {}
    families = _read_json(data_dir / "families.json", []) or []
    clusters = _read_json(data_dir / "clusters.json", []) or []
    similarities = _read_json(data_dir / "similarities.json", {}) or {}
    llm_reviews = _read_json(data_dir / "llm_reviews.json", []) or []

    # Coerce some schemas. GI's fingerprints.json may still be a dict stub in older builds.
    if isinstance(fingerprints, dict):
        fingerprints = fingerprints.get("groups") or []
    if isinstance(ast_clusters, dict):
        ast_clusters = ast_clusters.get("clusters") or []

    # ----- Clear existing rows for this project -----
    with conn:
        for t in TABLES_TO_CLEAR:
            conn.execute(f"DELETE FROM {t} WHERE project_id = ?", (pid,))

        # Upsert project row
        conn.execute(
            """INSERT INTO project (project_id, name, mstr_project_id, snapshot_date)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(project_id) DO UPDATE SET
                 name = excluded.name,
                 mstr_project_id = excluded.mstr_project_id,
                 snapshot_date = excluded.snapshot_date,
                 loaded_at = CURRENT_TIMESTAMP""",
            (pid, name, summary.get("projectId") or project.get("mstr_project_id") or "",
             summary.get("snapshotDate")),
        )

    counts: dict[str, int] = {}

    # ----- report (from inventory_all.json, then overlay richer data from reports.json) -----
    # inventory_all has every object (active/retired/collapsed/dossier) including name/owner/path
    inv_by_id: dict[str, dict] = {r["id"]: r for r in inventory_all if r.get("id")}
    rep_by_id: dict[str, dict] = {r["id"]: r for r in reports if r.get("id")}
    retired_by_id = {r["id"]: r for r in retired if isinstance(r, dict) and r.get("id")}

    # Use the union of IDs. reports.json may contain extras (e.g. enriched canonicals that
    # don't show up in inventory_all under the same status) — capture them both.
    all_ids = set(inv_by_id.keys()) | set(rep_by_id.keys())

    def _to_text(v) -> str:
        """Coerce owner/path-like fields that sometimes come through as dicts
        ({name, id, ...}) or lists of ancestor-name dicts into plain strings."""
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            return str(v.get("name") or v.get("id") or "")
        if isinstance(v, list):
            return "/".join(_to_text(x) for x in v if x)
        return str(v)

    report_rows = []
    metric_rows = []
    table_rows = []
    filter_rows = []
    sql_rows = []
    for rid in all_ids:
        inv = inv_by_id.get(rid, {})
        rep = rep_by_id.get(rid, {})
        # Pick richer source preferentially
        name_v = _to_text(rep.get("name") or inv.get("name") or "")
        owner_v = _to_text(rep.get("owner") or inv.get("owner") or "")
        path_v = _to_text(rep.get("path") or inv.get("path") or "")
        date_created = rep.get("dateCreated") or inv.get("dateCreated")
        date_modified = rep.get("dateModified") or inv.get("dateModified")
        status = inv.get("status") or ("active" if rid in rep_by_id else "unknown")
        object_type = 55 if status == "dossier" else 3
        match_tier = rep.get("matchTier") or ""
        family_base = rep.get("familyBase") or ""
        cluster_id = rep.get("clusterId")
        metrics = rep.get("metrics") or []
        tables = rep.get("tables") or []
        filters = rep.get("filters") or []
        report_rows.append((
            pid, rid, name_v, owner_v, path_v,
            date_created, date_modified,
            object_type, None, match_tier, family_base, status, cluster_id,
            rep.get("metricCount") or len(metrics),
            rep.get("tableCount") or len(tables),
            rep.get("filterCount") or len(filters),
        ))
        for m in metrics:
            if m:
                metric_rows.append((pid, rid, m))
        for t in tables:
            if t:
                table_rows.append((pid, rid, t))
        for f in filters:
            if f:
                filter_rows.append((pid, rid, f))
        sql_text = rep.get("sql")
        sql_err = rep.get("sqlError")
        if sql_text is not None or sql_err is not None:
            sql_rows.append((pid, rid, sql_text, sql_err))

    with conn:
        counts["report"] = _executemany(conn,
            """INSERT INTO report (project_id, report_id, name, owner, path,
                                   date_created, date_modified,
                                   object_type, subtype, match_tier, family_base, status, cluster_id,
                                   metric_count, table_count, filter_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", report_rows)

    # Known report IDs — every child table must FK-reference this set.
    known_ids = set(all_ids)

    def _keep(rows, rid_idx=1):
        """Drop rows whose report_id (position rid_idx) is not in known_ids."""
        return [r for r in rows if r[rid_idx] in known_ids]

    with conn:
        counts["report_metric"] = _executemany(conn,
            "INSERT OR IGNORE INTO report_metric (project_id, report_id, metric_name) VALUES (?, ?, ?)", _keep(metric_rows))
        counts["report_table"] = _executemany(conn,
            "INSERT OR IGNORE INTO report_table (project_id, report_id, table_name) VALUES (?, ?, ?)", _keep(table_rows))
        counts["report_filter"] = _executemany(conn,
            "INSERT OR IGNORE INTO report_filter (project_id, report_id, filter_name) VALUES (?, ?, ?)", _keep(filter_rows))
        counts["report_sql"] = _executemany(conn,
            "INSERT OR REPLACE INTO report_sql (project_id, report_id, sql_text, sql_error) VALUES (?, ?, ?, ?)", _keep(sql_rows))

    # ----- telemetry_match — derived from reports.json (executions/users/lastExec live there) -----
    tel_rows = []
    for r in reports:
        rid = r.get("id")
        if not rid:
            continue
        execs = r.get("executions") or 0
        if not execs and not r.get("lastExec"):
            continue  # no telemetry
        tel_rows.append((
            pid, rid,
            r.get("matchTier"), None,
            None, None,
            execs, r.get("users") or 0,
            r.get("lastExec"),
        ))
    with conn:
        counts["telemetry_match"] = _executemany(conn,
            """INSERT OR REPLACE INTO telemetry_match
               (project_id, report_id, match_tier, match_score, matched_telemetry_name, telemetry_path,
                total_executions, total_users, last_exec_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", _keep(tel_rows))

    # ----- retired_report -----
    retired_rows = []
    for r in retired:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        retired_rows.append((pid, r["id"], r.get("bucket")))
    with conn:
        counts["retired_report"] = _executemany(conn,
            "INSERT OR REPLACE INTO retired_report (project_id, report_id, bucket) VALUES (?, ?, ?)",
            _keep(retired_rows))

    # ----- ast_hash -----
    # GO shape: {report_id(32-char): [subtree_hash, ...]} → maps to our schema.
    # GI shape: {ast_cluster_hash(16-char): [subtree_hash, ...]} → keys are AST
    # cluster fingerprints, NOT report IDs. Skip in that case.
    ast_hash_rows = []
    if isinstance(ast_hashes, dict) and ast_hashes:
        sample_key = next(iter(ast_hashes.keys()))
        if len(sample_key) >= 30:  # looks like a report UUID
            for rid, hashes in ast_hashes.items():
                if not isinstance(hashes, list):
                    continue
                for h in hashes:
                    if h:
                        ast_hash_rows.append((pid, rid, h))
    with conn:
        counts["ast_hash"] = _executemany(conn,
            "INSERT OR IGNORE INTO ast_hash (project_id, report_id, hash) VALUES (?, ?, ?)", _keep(ast_hash_rows))

    # ----- collision_group + collision_member -----
    cg_rows = []
    cm_rows = []
    for i, c in enumerate(collisions, 1):
        gid = c.get("id") or f"COL{i:04d}"
        cg_rows.append((
            pid, gid, c.get("pass"),
            c.get("canonicalId"), c.get("canonicalName"), c.get("canonicalPath"),
            c.get("normalizedName"), c.get("executions"), c.get("lastExec"),
            c.get("size"), c.get("collapsed"),
        ))
        for m in (c.get("members") or []):
            if not m.get("id"):
                continue
            cm_rows.append((pid, gid, m["id"], m.get("matchTier"), m.get("folderPath"), m.get("owner")))
    with conn:
        counts["collision_group"] = _executemany(conn,
            """INSERT INTO collision_group
               (project_id, group_id, pass, canonical_id, canonical_name, canonical_path,
                normalized_name, executions, last_exec, size, collapsed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", cg_rows)
        counts["collision_member"] = _executemany(conn,
            """INSERT OR IGNORE INTO collision_member
               (project_id, group_id, report_id, match_tier, folder_path, owner)
               VALUES (?, ?, ?, ?, ?, ?)""", _keep(cm_rows, rid_idx=2))

    # ----- fingerprint_group + fingerprint_member -----
    fg_rows = []
    fm_rows = []
    for g in fingerprints:
        gid = g.get("id")
        if not gid:
            continue
        fg_rows.append((
            pid, gid, g.get("size"), g.get("reducible"),
            g.get("metricCount"), g.get("tableCount"), g.get("filterCount"),
            g.get("totalExecutions"),
            json.dumps(g.get("metrics") or []),
            json.dumps(g.get("tables") or []),
            json.dumps(g.get("filters") or []),
        ))
        for m in (g.get("members") or []):
            if m.get("id"):
                fm_rows.append((pid, gid, m["id"]))
    with conn:
        counts["fingerprint_group"] = _executemany(conn,
            """INSERT INTO fingerprint_group
               (project_id, group_id, size, reducible, metric_count, table_count, filter_count,
                total_executions, metrics_json, tables_json, filters_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", fg_rows)
        counts["fingerprint_member"] = _executemany(conn,
            "INSERT OR IGNORE INTO fingerprint_member (project_id, group_id, report_id) VALUES (?, ?, ?)",
            _keep(fm_rows, rid_idx=2))

    # ----- sql_hash_group + sql_hash_member -----
    shg_rows = []
    shm_rows = []
    for g in sql_hash_groups:
        gid = g.get("id")
        if not gid:
            continue
        shg_rows.append((
            pid, gid, g.get("sqlHash"), g.get("sqlPreview"),
            g.get("size"), g.get("reducible"), g.get("totalExecutions"),
        ))
        for m in (g.get("members") or []):
            if m.get("id"):
                shm_rows.append((pid, gid, m["id"]))
    with conn:
        counts["sql_hash_group"] = _executemany(conn,
            """INSERT INTO sql_hash_group
               (project_id, group_id, sql_hash, sql_preview, size, reducible, total_executions)
               VALUES (?, ?, ?, ?, ?, ?, ?)""", shg_rows)
        counts["sql_hash_member"] = _executemany(conn,
            "INSERT OR IGNORE INTO sql_hash_member (project_id, group_id, report_id) VALUES (?, ?, ?)",
            _keep(shm_rows, rid_idx=2))

    # ----- ast_cluster + ast_cluster_member -----
    acg_rows = []
    acm_rows = []
    for i, c in enumerate(ast_clusters, 1):
        cid = str(c.get("id") or c.get("clusterId") or f"AST{i:04d}")
        acg_rows.append((
            pid, cid, c.get("size"), c.get("reducible"),
            1 if c.get("allExact") else 0,
            c.get("avgScore"), c.get("minScore"),
            c.get("totalExecutions"),
            1 if c.get("inSqlHash") else 0,
            1 if c.get("astExclusive") else 0,
        ))
        members = c.get("members") or []
        # Support both list-of-dicts and list-of-id-strings (GI older shape).
        for m in members:
            if isinstance(m, dict):
                rid = m.get("id")
            else:
                rid = str(m)
            if rid:
                acm_rows.append((pid, cid, rid))
    with conn:
        counts["ast_cluster"] = _executemany(conn,
            """INSERT INTO ast_cluster
               (project_id, cluster_id, size, reducible, all_exact, avg_score, min_score,
                total_executions, in_sql_hash, ast_exclusive)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", acg_rows)
        counts["ast_cluster_member"] = _executemany(conn,
            "INSERT OR IGNORE INTO ast_cluster_member (project_id, cluster_id, report_id) VALUES (?, ?, ?)",
            _keep(acm_rows, rid_idx=2))

    # ----- family + family_member -----
    fam_rows = []
    famm_rows = []
    for f in families:
        base = f.get("base")
        if not base:
            continue
        fam_rows.append((pid, base, f.get("size"), f.get("reducible"), f.get("totalExecutions")))
        for m in (f.get("members") or []):
            if m.get("id"):
                famm_rows.append((pid, base, m["id"], m.get("variantSuffix")))
    with conn:
        counts["family"] = _executemany(conn,
            "INSERT OR REPLACE INTO family (project_id, base, size, reducible, total_executions) VALUES (?, ?, ?, ?, ?)",
            fam_rows)
        counts["family_member"] = _executemany(conn,
            "INSERT OR IGNORE INTO family_member (project_id, base, report_id, variant_suffix) VALUES (?, ?, ?, ?)",
            _keep(famm_rows, rid_idx=2))

    # ----- similarity_cluster + members + common -----
    sc_rows = []
    scm_rows = []
    scc_rows = []
    for c in clusters:
        cid = c.get("id")
        if not cid:
            continue
        sc_rows.append((
            pid, cid, c.get("size"),
            c.get("primaryReportId"), c.get("primaryName"),
            c.get("totalExecutions"), c.get("totalUsers"),
            c.get("metricCount"), c.get("tableCount"), c.get("filterCount"),
            c.get("avgSimilarity"),
            c.get("llmLabel"), c.get("llmAction"), c.get("llmRemovable"),
        ))
        for mid in (c.get("memberIds") or []):
            if mid:
                scm_rows.append((pid, cid, mid))
        for kind_key in ("commonMetrics", "commonTables", "commonFilters"):
            kind = {"commonMetrics": "metric", "commonTables": "table", "commonFilters": "filter"}[kind_key]
            for v in (c.get(kind_key) or []):
                if v:
                    scc_rows.append((pid, cid, kind, v))
    with conn:
        counts["similarity_cluster"] = _executemany(conn,
            """INSERT INTO similarity_cluster
               (project_id, cluster_id, size, primary_report_id, primary_name,
                total_executions, total_users, metric_count, table_count, filter_count,
                avg_similarity, llm_label, llm_action, llm_removable)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", sc_rows)
        counts["similarity_cluster_member"] = _executemany(conn,
            "INSERT OR IGNORE INTO similarity_cluster_member (project_id, cluster_id, report_id) VALUES (?, ?, ?)",
            _keep(scm_rows, rid_idx=2))
        counts["similarity_cluster_common"] = _executemany(conn,
            "INSERT OR IGNORE INTO similarity_cluster_common (project_id, cluster_id, kind, name) VALUES (?, ?, ?, ?)",
            scc_rows)

    # ----- similarity_pair (both endpoints must exist in report) -----
    # Two shapes in the wild:
    #   GO: {"idA|idB": {combined, metric, table, filter, ast?}}
    #   INSIGHT: [{a, b, score}]
    sp_rows = []
    if isinstance(similarities, dict):
        for key, v in similarities.items():
            if "|" not in key or not isinstance(v, dict):
                continue
            a, b = key.split("|", 1)
            if a not in known_ids or b not in known_ids:
                continue
            sp_rows.append((
                pid, a, b,
                v.get("combined"), v.get("metric"), v.get("table"), v.get("filter"), v.get("ast"),
            ))
    elif isinstance(similarities, list):
        for item in similarities:
            if not isinstance(item, dict):
                continue
            a = item.get("a")
            b = item.get("b")
            if not a or not b or a not in known_ids or b not in known_ids:
                continue
            sp_rows.append((pid, a, b, item.get("score"), None, None, None, None))
    with conn:
        counts["similarity_pair"] = _executemany(conn,
            """INSERT OR REPLACE INTO similarity_pair
               (project_id, id_a, id_b, combined, metric, tables, filters, ast)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", sp_rows)

    # ----- llm_review -----
    llm_rows = []
    for r in llm_reviews:
        cid = r.get("clusterId")
        if not cid:
            continue
        llm_rows.append((
            pid, cid,
            r.get("label"), r.get("business_function"), r.get("relationship"),
            r.get("consolidation_action"), r.get("confidence"),
            r.get("consolidation_detail"), r.get("keep_report"),
            r.get("removable_count"),
        ))
    with conn:
        counts["llm_review"] = _executemany(conn,
            """INSERT OR REPLACE INTO llm_review
               (project_id, cluster_id, label, business_function, relationship, action,
                confidence, consolidation_detail, keep_report, removable_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", llm_rows)

    # ----- run_summary -----
    afm = summary.get("alternativeFamilyMethod") or {}
    with conn:
        conn.execute(
            """INSERT OR REPLACE INTO run_summary (
                project_id, total_inventory, total_dossiers, dossiers_with_telemetry, total_objects,
                after_telemetry, retired, after_collision_collapse, collision_collapsed,
                after_fingerprint, fingerprint_groups, fingerprint_multi_groups,
                fingerprint_reports_in_groups, fingerprint_removable,
                ast_parsed, ast_clusters, ast_multi_clusters, ast_reducible,
                ast_exact_groups, ast_exact_removable, ast_final_to_migrate,
                after_sql_hash, sql_hash_sequential_removable, sql_hash_sequential_groups,
                after_ast, ast_sequential_removable, ast_sequential_groups,
                after_family, family_reducible,
                after_similarity, clusters_total, clusters_multi, clusters_singleton,
                llm_reviews_total, llm_removable_raw, llm_safe_clusters,
                reduction_total, reduction_pct,
                alt_family_label, alt_family_value, alt_family_detail
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pid,
                summary.get("totalInventory"), summary.get("totalDossiers"),
                summary.get("dossiersWithTelemetry"), summary.get("totalObjects"),
                summary.get("afterTelemetry"), summary.get("retired"),
                summary.get("afterCollisionCollapse"), summary.get("collisionCollapsed"),
                summary.get("afterFingerprint"), summary.get("fingerprintGroups"),
                summary.get("fingerprintMultiGroups"),
                summary.get("fingerprintReportsInGroups"), summary.get("fingerprintRemovable"),
                summary.get("astParsed"), summary.get("astClusters"), summary.get("astMultiClusters"),
                summary.get("astReducible"),
                summary.get("astExactGroups"), summary.get("astExactRemovable"),
                summary.get("astFinalToMigrate"),
                summary.get("afterSqlHash"), summary.get("sqlHashSequentialRemovable"),
                summary.get("sqlHashSequentialGroups"),
                summary.get("afterAst"), summary.get("astSequentialRemovable"),
                summary.get("astSequentialGroups"),
                summary.get("afterFamily"), summary.get("familyReducible"),
                summary.get("afterSimilarity"), summary.get("clustersTotal"),
                summary.get("clustersMulti"), summary.get("clustersSingleton"),
                summary.get("llmReviewsTotal"), summary.get("llmRemovableRaw"),
                summary.get("llmSafeClusters"),
                summary.get("reductionTotal"), summary.get("reductionPct"),
                afm.get("label"), afm.get("value"), afm.get("detail"),
            ),
        )
        counts["run_summary"] = 1

    # ----- funnel_stage -----
    fs_rows = []
    for i, stage in enumerate(summary.get("funnel") or []):
        fs_rows.append((pid, i, stage.get("label"), stage.get("value"), stage.get("detail")))
    with conn:
        counts["funnel_stage"] = _executemany(conn,
            "INSERT INTO funnel_stage (project_id, ordinal, label, value, detail) VALUES (?, ?, ?, ?, ?)",
            fs_rows)

    # ----- tier_breakdown -----
    tier = summary.get("tierBreakdown") or {}
    tb_rows = [(pid, k, v) for k, v in tier.items()]
    with conn:
        counts["tier_breakdown"] = _executemany(conn,
            "INSERT OR REPLACE INTO tier_breakdown (project_id, tier, count) VALUES (?, ?, ?)", tb_rows)

    # ----- object_type_count -----
    ot_rows = [(pid, o.get("name"), o.get("count")) for o in (summary.get("objectTypes") or [])]
    with conn:
        counts["object_type_count"] = _executemany(conn,
            "INSERT OR REPLACE INTO object_type_count (project_id, name, count) VALUES (?, ?, ?)", ot_rows)

    # ----- top_list -----
    tl_rows = []
    for kind_key, kind in (("topMetrics", "metric"), ("topTables", "table"), ("topFilters", "filter")):
        for i, item in enumerate(summary.get(kind_key) or [], 1):
            tl_rows.append((pid, kind, i, item.get("name"), item.get("count"), None, None, None, None))
    for i, item in enumerate(summary.get("topExecuted") or [], 1):
        tl_rows.append((
            pid, "executed", i, item.get("name"), None,
            item.get("id"), item.get("executions"), item.get("users"), item.get("lastExec"),
        ))
    with conn:
        counts["top_list"] = _executemany(conn,
            """INSERT OR REPLACE INTO top_list
               (project_id, kind, rank, name, count, report_id, executions, users, last_exec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", tl_rows)

    # ----- llm_action_count -----
    la_rows = [(pid, k, v) for k, v in (summary.get("llmReviewsByAction") or {}).items()]
    with conn:
        counts["llm_action_count"] = _executemany(conn,
            "INSERT OR REPLACE INTO llm_action_count (project_id, action, count) VALUES (?, ?, ?)", la_rows)

    # Pretty-print counts
    for k, v in counts.items():
        print(f"  {k:32}  {v:>8,}")

    return counts


def main() -> int:
    init()  # ensure schema exists
    conn = connect()
    try:
        all_counts = {}
        for proj in PROJECTS:
            all_counts[proj["project_id"]] = load_project(proj, conn)
        print("\n=== Summary across all projects ===")
        # Aggregate totals
        totals: dict[str, int] = {}
        for counts in all_counts.values():
            for k, v in counts.items():
                totals[k] = totals.get(k, 0) + v
        for k, v in totals.items():
            print(f"  {k:32}  {v:>8,}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
