"""Combined Reports Project — union of isFinalCanonical=true reports
from GO, GI, and INSIGHT.

This is a VIRTUAL project: it has no raw inventory of its own. Running
`build_combined_reports()` snapshots final-rationalized reports from the
three source projects into a single `project_id = 'combined-reports'`
workspace, so the full Rationalization pipeline (fingerprint → sql_hash
→ ast → family → similarity → semantic → llm_review) can re-cluster
across project boundaries. The goal: cross-project duplicates surface as
multi-project clusters you can consolidate.

Data flow:
  for each (GO, GI, INSIGHT):
    final-kept reports = primary of each multi-cluster + post-family singletons
    copy their report / report_metric / report_table / report_filter /
         report_attribute / report_sql / telemetry_match / ast_hash rows
         into project_id = 'combined-reports'.
    stamp each row with source_project_id so the UI can render "[GO]" /
         "[GI]" / "[IN]" origin pills.

Subsequent runs of the pipeline against 'combined-reports' use the
existing stages (they all key on project_id). The Collision stage is a
no-op because raw_telemetry isn't copied — that's by design, collisions
are a per-project dedup of telemetry rows, not a cross-project concept.

Run:
    python -m db.compute.combined_reports
    python -m db.compute.combined_reports --no-pipeline  # just copy, skip rebuild
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.db import connect, init, get_project, PROJECTS


COMBINED_PROJECT_ID = "combined-reports"
SOURCE_PROJECT_IDS = ["global-operational", "global-insight", "insight"]

# Compact source tag shown in the UI: [GO] / [GI] / [IN]
SOURCE_TAGS = {
    "global-operational": "GO",
    "global-insight":     "GI",
    "insight":            "IN",
}

# Playground experiment location — we read per-source "final kept" from the
# active semantic experiment here (primaries + singletons), so the combined
# project mirrors what each source decided is canonical under semantic
# clustering. Falls back to the Jaccard `similarity_cluster` primaries +
# post-family singletons when a source has no active experiment (e.g. GO).
_PLAYGROUND_DIR = Path(__file__).resolve().parents[2] / "data" / "_playground"


_ENSURE_SCHEMA_SQL = """
-- Track source project per copied row (only meaningful for virtual projects)
-- We ALTER TABLE here idempotently — SQLite will error if the column already
-- exists, so the driver swallows via try/except in _ensure_schema().

-- Bookkeeping table: when was each source last rebuilt + how many rows
CREATE TABLE IF NOT EXISTS combined_build_log (
    source_project_id TEXT PRIMARY KEY,
    built_at          TEXT NOT NULL,
    report_count      INTEGER NOT NULL,
    source_data_hash  TEXT,  -- fingerprint of the source's final-kept set
                             -- so we can detect drift for the UI warning
    source_built_at   TEXT   -- the source project's run_summary timestamp (if available)
);
"""


def _ensure_schema(conn) -> None:
    # Add source_project_id to tables that will carry combined-project rows.
    # SQLite's ALTER TABLE can't be conditional, so we try/except.
    for table in ("report",):
        try:
            with conn:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN source_project_id TEXT")
        except Exception:
            pass  # column already there
    with conn:
        conn.executescript(_ENSURE_SCHEMA_SQL)


def _active_experiment_final_kept(source_pid: str) -> tuple[set[str], str] | None:
    """If a source has an active Playground semantic experiment, return
    (primary_ids ∪ singleton_ids, experiment_id). This matches the
    experiment's stats.finalUnique (~1,200 total across GI + IN + GO vs
    ~8,164 from the Jaccard pipeline output)."""
    active_path = _PLAYGROUND_DIR / "active.json"
    if not active_path.exists():
        return None
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    exp_id = active.get(source_pid)
    if not exp_id:
        return None
    exp_path = _PLAYGROUND_DIR / f"{exp_id}.json"
    if not exp_path.exists():
        return None
    try:
        exp = json.loads(exp_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    primaries = {c.get("primaryReportId") for c in (exp.get("clusters") or []) if c.get("primaryReportId")}
    singletons = set(exp.get("singletonIds") or [])
    return (primaries | singletons), exp_id


def _final_kept_ids(conn, source_pid: str) -> set[str]:
    """Per source, prefer the active Playground semantic experiment's
    finalUnique set (primaries + singletons). Falls back to the Jaccard
    pipeline's similarity_cluster primaries + post-family singletons when
    no experiment is active (e.g. GO). Matches the `isFinalCanonical`
    semantics: one canonical per dedup-group + reports unique under the
    chosen clustering method."""
    exp_out = _active_experiment_final_kept(source_pid)
    if exp_out is not None:
        ids, _exp_id = exp_out
        return ids

    from db.compute.pipeline import _post_ast_family_canonical_ids

    cluster_primaries = {
        r[0] for r in conn.execute(
            "SELECT primary_report_id FROM similarity_cluster WHERE project_id=?",
            (source_pid,),
        ) if r[0]
    }
    similarity_members = {
        r[0] for r in conn.execute(
            "SELECT report_id FROM similarity_cluster_member WHERE project_id=?",
            (source_pid,),
        )
    }
    post_family = _post_ast_family_canonical_ids(conn, source_pid)
    singletons = post_family - similarity_members
    return cluster_primaries | singletons


def _final_kept_source(source_pid: str) -> str:
    """What method produced this source's final-kept set — for logging."""
    exp_out = _active_experiment_final_kept(source_pid)
    if exp_out is not None:
        return f"active experiment ({exp_out[1]})"
    return "Jaccard similarity_cluster + post-family singletons"


def _copy_scalar_rows(conn, source_pid: str, rids: list[str]) -> int:
    """Copy report, report_metric/table/filter/attribute, report_sql,
    telemetry_match, ast_hash rows for the given report IDs from source
    into combined-reports. The report-name gets no prefix — we use the
    source_project_id column + UI pill for disambiguation."""
    if not rids:
        return 0
    ph = ",".join("?" * len(rids))

    # report — the main denormalized row
    conn.execute(
        f"""INSERT OR REPLACE INTO report
            (project_id, report_id, name, owner, path, date_created,
             date_modified, object_type, subtype, match_tier, family_base,
             status, cluster_id, metric_count, table_count, filter_count,
             attribute_count, source_project_id)
            SELECT ?, report_id, name, owner, path, date_created,
                   date_modified, object_type, subtype, match_tier, family_base,
                   'active', NULL, metric_count, table_count, filter_count,
                   attribute_count, ?
              FROM report
             WHERE project_id = ? AND report_id IN ({ph})""",
        [COMBINED_PROJECT_ID, source_pid, source_pid, *rids],
    )

    # Many-to-many feature tables
    for table, cols in [
        ("report_metric",   "project_id, report_id, metric_name"),
        ("report_table",    "project_id, report_id, table_name"),
        ("report_filter",   "project_id, report_id, filter_name"),
        ("report_attribute","project_id, report_id, attribute_name"),
    ]:
        # Strip project_id, select the others, insert with COMBINED_PROJECT_ID
        select_cols = cols.replace("project_id, ", "")
        conn.execute(
            f"""INSERT OR IGNORE INTO {table} ({cols})
                SELECT ?, {select_cols} FROM {table}
                 WHERE project_id = ? AND report_id IN ({ph})""",
            [COMBINED_PROJECT_ID, source_pid, *rids],
        )

    # SQL text — report_sql has only (project_id, report_id, sql_text, sql_error)
    conn.execute(
        f"""INSERT OR REPLACE INTO report_sql
            (project_id, report_id, sql_text, sql_error)
            SELECT ?, report_id, sql_text, sql_error
              FROM report_sql
             WHERE project_id = ? AND report_id IN ({ph})""",
        [COMBINED_PROJECT_ID, source_pid, *rids],
    )

    # Telemetry — carry executions/users into combined so the pipeline can
    # pick primaries by usage in cross-project clusters
    conn.execute(
        f"""INSERT OR REPLACE INTO telemetry_match
            (project_id, report_id, match_tier, match_score,
             matched_telemetry_name, telemetry_path,
             total_executions, total_users, last_exec_ts)
            SELECT ?, report_id, match_tier, match_score,
                   matched_telemetry_name, telemetry_path,
                   total_executions, total_users, last_exec_ts
              FROM telemetry_match
             WHERE project_id = ? AND report_id IN ({ph})""",
        [COMBINED_PROJECT_ID, source_pid, *rids],
    )

    # raw_telemetry — required by `_canonical_ids_after_collisions`, which
    # defines a report as "active" only if raw_telemetry.total_executions > 0.
    # Without this, every downstream stage (fingerprint / sql_hash / ast /
    # family / similarity) sees an empty canonical set and produces 0
    # clusters. Copy it from the source project.
    conn.execute(
        f"""INSERT OR REPLACE INTO raw_telemetry
            (project_id, report_id, matched, match_tier, match_score,
             matched_telemetry_name, telemetry_path,
             total_executions, total_users, last_exec_ts)
            SELECT ?, report_id, matched, match_tier, match_score,
                   matched_telemetry_name, telemetry_path,
                   total_executions, total_users, last_exec_ts
              FROM raw_telemetry
             WHERE project_id = ? AND report_id IN ({ph})""",
        [COMBINED_PROJECT_ID, source_pid, *rids],
    )

    # AST hashes — reuse so the ast stage doesn't have to re-parse SQL
    conn.execute(
        f"""INSERT OR IGNORE INTO ast_hash
            (project_id, report_id, hash)
            SELECT ?, report_id, hash
              FROM ast_hash
             WHERE project_id = ? AND report_id IN ({ph})""",
        [COMBINED_PROJECT_ID, source_pid, *rids],
    )

    # Minimal raw_inventory shim — the pipeline checks batch membership when
    # classifying active vs retired. Give every combined row batch='combined'.
    conn.execute(
        f"""INSERT OR REPLACE INTO raw_inventory
            (project_id, report_id, batch, name, owner, folder_path,
             date_created, date_modified, object_type, subtype,
             has_sql, sql_hash, source_type)
            SELECT ?, report_id, 'combined', name, owner, path,
                   date_created, date_modified, object_type, subtype,
                   CASE WHEN EXISTS (SELECT 1 FROM report_sql rs
                                      WHERE rs.project_id=? AND rs.report_id=r.report_id
                                        AND rs.sql_text IS NOT NULL AND rs.sql_text<>'')
                        THEN 1 ELSE 0 END,
                   NULL,
                   (SELECT source_type FROM raw_inventory ri
                     WHERE ri.project_id=? AND ri.report_id=r.report_id
                       AND ri.source_type IS NOT NULL LIMIT 1)
              FROM report r
             WHERE project_id = ? AND report_id IN ({ph})""",
        [COMBINED_PROJECT_ID, source_pid, source_pid, source_pid, *rids],
    )

    # raw_inventory_metric/table/filter/attribute/sql — stage_reports rebuilds
    # the report_metric/table/filter/attribute tables from these, so without
    # them every report becomes a zero-feature ghost (fingerprint skips them,
    # similarity gets jaccard(empty,empty)=1.0 and collapses everything).
    conn.execute(
        f"""INSERT OR IGNORE INTO raw_inventory_metric
            (project_id, report_id, batch, metric_id, metric_name)
            SELECT ?, report_id, 'combined', metric_id, metric_name
              FROM raw_inventory_metric
             WHERE project_id = ? AND report_id IN ({ph})""",
        [COMBINED_PROJECT_ID, source_pid, *rids],
    )
    conn.execute(
        f"""INSERT OR IGNORE INTO raw_inventory_table
            (project_id, report_id, batch, table_name)
            SELECT ?, report_id, 'combined', table_name
              FROM raw_inventory_table
             WHERE project_id = ? AND report_id IN ({ph})""",
        [COMBINED_PROJECT_ID, source_pid, *rids],
    )
    conn.execute(
        f"""INSERT OR IGNORE INTO raw_inventory_filter
            (project_id, report_id, batch, filter_id, filter_name)
            SELECT ?, report_id, 'combined', filter_id, filter_name
              FROM raw_inventory_filter
             WHERE project_id = ? AND report_id IN ({ph})""",
        [COMBINED_PROJECT_ID, source_pid, *rids],
    )
    conn.execute(
        f"""INSERT OR IGNORE INTO raw_inventory_attribute
            (project_id, report_id, batch, attribute_id, attribute_name)
            SELECT ?, report_id, 'combined', attribute_id, attribute_name
              FROM raw_inventory_attribute
             WHERE project_id = ? AND report_id IN ({ph})""",
        [COMBINED_PROJECT_ID, source_pid, *rids],
    )
    conn.execute(
        f"""INSERT OR IGNORE INTO raw_inventory_sql
            (project_id, report_id, batch, sql_text, sql_hash, sql_error)
            SELECT ?, report_id, 'combined', sql_text, sql_hash, sql_error
              FROM raw_inventory_sql
             WHERE project_id = ? AND report_id IN ({ph})""",
        [COMBINED_PROJECT_ID, source_pid, *rids],
    )

    return len(rids)


def _source_fingerprint(conn, source_pid: str, rids: set[str]) -> str:
    """A cheap drift hash: count + sorted-id-concat checksum. Matches only
    if the same set of reports is final-kept at rebuild time."""
    import hashlib
    s = "|".join(sorted(rids))
    return f"n={len(rids)};" + hashlib.sha256(s.encode()).hexdigest()[:16]


def _clear_combined(conn) -> None:
    """Wipe all combined-project rows. FK cascades take care of dependent
    tables. Called at the start of every rebuild for a clean slate."""
    with conn:
        conn.execute("DELETE FROM raw_inventory WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM raw_inventory_metric WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM raw_inventory_table WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM raw_inventory_filter WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM raw_inventory_attribute WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM raw_inventory_sql WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM raw_telemetry WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM report_sql WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM report_metric WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM report_table WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM report_filter WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM report_attribute WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM telemetry_match WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM ast_hash WHERE project_id=?", (COMBINED_PROJECT_ID,))
        conn.execute("DELETE FROM report WHERE project_id=?", (COMBINED_PROJECT_ID,))


def build_combined_reports(config: dict | None = None, verbose: bool = True) -> dict:
    """Snapshot final-kept reports from GO/GI/INSIGHT into combined-reports,
    then run the Rationalization pipeline on the combined set.

    config (optional):
      - run_pipeline: bool (default True) — run compute stages after copy.
      - emit: bool (default True) — write public/data/Combined Reports Project/*.json
    """
    config = config or {}
    run_pipeline_after = config.get("run_pipeline", True)
    emit_after = config.get("emit", True)

    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    _ensure_schema(conn)

    try:
        print(f"\n=== Building '{COMBINED_PROJECT_ID}' ===")
        t0 = time.time()

        # Upsert the project row so emit_summary can join against it.
        # (load.py normally does this during real ingestion; virtual projects
        # skip load.py, so we mirror the upsert here.)
        cfg = get_project(COMBINED_PROJECT_ID)
        with conn:
            conn.execute(
                """INSERT INTO project (project_id, name, mstr_project_id, snapshot_date)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET
                     name = excluded.name,
                     mstr_project_id = excluded.mstr_project_id,
                     snapshot_date = excluded.snapshot_date,
                     loaded_at = CURRENT_TIMESTAMP""",
                (COMBINED_PROJECT_ID, cfg["name"], cfg.get("mstr_project_id") or "", None),
            )

        # 1. Collect final-kept IDs from each source
        id_sets: dict[str, set[str]] = {}
        all_ids: set[str] = set()
        for src in SOURCE_PROJECT_IDS:
            fk = _final_kept_ids(conn, src)
            id_sets[src] = fk
            all_ids.update(fk)
            method = _final_kept_source(src)
            print(f"  {src:20} {len(fk):>7,} final-kept reports  [{method}]")
        print(f"  {'total':20} {len(all_ids):>7,} combined (no ID collisions expected — MSTR GUIDs are unique)")

        # 2. Reset combined workspace
        _clear_combined(conn)

        # 3. Copy per source
        copied = 0
        for src in SOURCE_PROJECT_IDS:
            rids = sorted(id_sets[src])
            with conn:
                n = _copy_scalar_rows(conn, src, rids)
            copied += n
            now = datetime.now(timezone.utc).isoformat()
            # Try to read the source's last rebuild time (for drift warning)
            src_built = conn.execute(
                "SELECT run_at FROM run_summary WHERE project_id=?", (src,)
            ).fetchone() if _has_run_at_column(conn) else None
            src_built = src_built[0] if src_built else None
            fp = _source_fingerprint(conn, src, id_sets[src])
            with conn:
                conn.execute(
                    """INSERT OR REPLACE INTO combined_build_log
                       (source_project_id, built_at, report_count,
                        source_data_hash, source_built_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (src, now, n, fp, src_built),
                )
            if verbose:
                print(f"  copied {n:,} rows from {src}")

        elapsed = time.time() - t0
        print(f"\n  copied {copied:,} total rows in {elapsed:.1f}s")

        stats: dict = {
            "reportCount": copied,
            "perSource": {src: len(id_sets[src]) for src in SOURCE_PROJECT_IDS},
            "copiedAt": datetime.now(timezone.utc).isoformat(),
            "elapsedSec": round(elapsed, 1),
        }

        # 4. Run the Rationalization pipeline on the combined set
        if run_pipeline_after:
            print(f"\n=== Running pipeline on '{COMBINED_PROJECT_ID}' ===")
            from db.compute.pipeline import run_pipeline
            run_pipeline(COMBINED_PROJECT_ID, conn=conn)
            stats["pipelineRan"] = True
        else:
            stats["pipelineRan"] = False

        # 4b. Re-stamp source_project_id on the report table.
        # stage_reports overwrites `report` from raw_inventory (which has no
        # source_project_id column), so we restore the tag using id_sets.
        print(f"\n=== Re-stamping source_project_id ===")
        with conn:
            for src, rids in id_sets.items():
                if not rids:
                    continue
                rid_list = sorted(rids)
                # Chunk to avoid SQLite parameter limit (default ~999)
                CHUNK = 500
                total = 0
                for i in range(0, len(rid_list), CHUNK):
                    chunk = rid_list[i:i + CHUNK]
                    ph = ",".join("?" * len(chunk))
                    conn.execute(
                        f"""UPDATE report SET source_project_id = ?
                             WHERE project_id = ? AND report_id IN ({ph})""",
                        [src, COMBINED_PROJECT_ID, *chunk],
                    )
                    total += len(chunk)
                print(f"  stamped {total:,} rows with source={src}")

        # 5. Emit JSON for the UI
        if emit_after:
            print(f"\n=== Emitting JSON for '{COMBINED_PROJECT_ID}' ===")
            from db.emit import emit_project
            cfg = get_project(COMBINED_PROJECT_ID)
            emit_project(cfg, conn)
            stats["emitted"] = True

        print(f"\nDone. Total elapsed: {time.time() - t0:.1f}s")
        return stats
    finally:
        conn.close()


def _has_run_at_column(conn) -> bool:
    """Some older schemas don't have run_summary.run_at. Check before using."""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(run_summary)")}
        return "run_at" in cols
    except Exception:
        return False


def combined_status() -> dict:
    """Return freshness info for the UI's 'Last rebuilt / stale?' banner."""
    init()
    conn = connect()
    _ensure_schema(conn)
    try:
        # Per-source last-built + current source fingerprint
        entries = []
        is_stale = False
        for src in SOURCE_PROJECT_IDS:
            row = conn.execute(
                """SELECT built_at, report_count, source_data_hash
                     FROM combined_build_log WHERE source_project_id=?""",
                (src,),
            ).fetchone()
            if not row:
                # Never built
                current_fp = _source_fingerprint(conn, src, _final_kept_ids(conn, src))
                entries.append({
                    "sourceProjectId": src,
                    "sourceName": get_project(src)["name"],
                    "builtAt": None, "reportCount": 0,
                    "stale": True, "reason": "never built",
                    "currentCount": len(_final_kept_ids(conn, src)),
                })
                is_stale = True
                continue
            built_at, report_count, stored_hash = row
            current_ids = _final_kept_ids(conn, src)
            current_fp = _source_fingerprint(conn, src, current_ids)
            stale = stored_hash != current_fp
            entries.append({
                "sourceProjectId": src,
                "sourceName": get_project(src)["name"],
                "builtAt": built_at,
                "reportCount": report_count,
                "stale": stale,
                "reason": None if not stale else f"source final-kept set changed ({len(current_ids)} now, {report_count} at last build)",
                "currentCount": len(current_ids),
            })
            if stale:
                is_stale = True

        total_current = conn.execute(
            "SELECT COUNT(*) FROM report WHERE project_id=?",
            (COMBINED_PROJECT_ID,),
        ).fetchone()[0]
        last_built = max((e["builtAt"] for e in entries if e["builtAt"]), default=None)
        return {
            "projectId": COMBINED_PROJECT_ID,
            "totalReports": total_current,
            "lastBuiltAt": last_built,
            "stale": is_stale,
            "sources": entries,
        }
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Build or refresh the Combined Reports Project")
    ap.add_argument("--no-pipeline", action="store_true",
                    help="Only copy rows; skip the compute pipeline")
    ap.add_argument("--no-emit", action="store_true",
                    help="Skip JSON emit")
    ap.add_argument("--status", action="store_true",
                    help="Print current freshness info and exit")
    args = ap.parse_args()

    if args.status:
        print(json.dumps(combined_status(), indent=2, default=str))
        return 0

    result = build_combined_reports({
        "run_pipeline": not args.no_pipeline,
        "emit": not args.no_emit,
    })
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
