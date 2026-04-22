"""Phase 3a — ingest raw project inputs into the SQLite DB.

Reads each project's `inventory/`, `telemetry/`, `analysis/`, and cache files
and populates the raw_* tables. The existing compute pipeline is unchanged —
build scripts still read raw JSON for the actual math. This just makes the
raw inputs queryable alongside the derived outputs.

Idempotent: deletes all raw_* rows for a project before re-inserting.

File layouts per project:
  - GO  : Global Operational/inventory, telemetry, analysis
  - GI  : Global Insight/inventory, telemetry, analysis
  - INS : INSIGHT/rationalization/inventory, telemetry, analysis

GI's inventory/reports.json is ~1.2 GB — we stream it with ijson record-by-record.
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import ijson  # streaming JSON parser

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.db import connect, init

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------- helpers -----------------------------------------

def _text(v) -> str:
    """Coerce dict/list/None owner/path-ish values to a plain string."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return str(v.get("name") or v.get("id") or "")
    if isinstance(v, list):
        return "/".join(_text(x) for x in v if x)
    return str(v)


def _norm_table(t: str) -> str:
    """Legacy-compatible table normalization.

    Mirrors rich_fingerprint()'s `t.strip('"').lower().split('.')[-1]` — so
    `"schema"."table_v"` becomes `"table_v` (leading embedded quote survives
    because strip() only removes OUTER quotes, not embedded ones). Stable
    across runs and matches the legacy fingerprint grouping exactly.
    """
    if not t:
        return ""
    return t.strip('"').lower().split(".")[-1]


def _walk_filter_tree(node, out: set):
    """Generic walker — collect every `name` value found anywhere in the tree.

    Mirrors legacy extract_filter_attrs(): walks every dict value, picks up
    any node's `name` key (attribute names, filter names, form names, etc.).
    Names are stored lowercased.
    """
    if isinstance(node, dict):
        name = node.get("name")
        if isinstance(name, str) and name:
            out.add(name.lower())
        for v in node.values():
            _walk_filter_tree(v, out)
    elif isinstance(node, list):
        for x in node:
            _walk_filter_tree(x, out)


def _extract_fields(rec: dict) -> tuple[list[tuple], list[str], list[tuple], list[tuple]]:
    """Return (metric_rows, table_names, filter_rows, attribute_rows) from a
    MSTR inventory record.

    The inventory JSON distinguishes three template signals:
      - rec['metrics']    — template metrics (aggregations)
      - rec['attributes'] — template attributes (GROUP BY dimensions)
      - rec['filter']     — WHERE-clause constraint tree
    Each becomes its own list; attributes are NO LONGER mixed into filters.
    """
    metric_rows = []
    for m in (rec.get("metrics") or []):
        if isinstance(m, dict) and m.get("name"):
            metric_rows.append((m.get("id"), m.get("name")))

    table_names = []
    for t in (rec.get("sourceTables") or []):
        n = _norm_table(t) if isinstance(t, str) else _norm_table(_text(t))
        if n:
            table_names.append(n)

    # Attributes — template dimensions (rows/columns of the grid).
    attr_rows: dict[str, str] = {}
    for a in (rec.get("attributes") or []):
        if isinstance(a, dict) and a.get("name"):
            attr_rows[a["name"]] = a.get("id", "")
    attribute_list = [(aid, aname) for aname, aid in attr_rows.items()]

    # Filter names — walk the filter tree (ONLY; attributes no longer mixed in).
    filter_rows: dict[str, str] = {}
    flt = rec.get("filter")
    if isinstance(flt, dict) and isinstance(flt.get("tree"), dict):
        names_from_tree: set = set()
        _walk_filter_tree(flt["tree"], names_from_tree)
        for n in names_from_tree:
            filter_rows.setdefault(n, "")
    filter_list = [(fid, fname) for fname, fid in filter_rows.items()]

    return metric_rows, list(set(table_names)), filter_list, attribute_list


def _path_from_record(rec: dict) -> str:
    """Best-effort folder path from inventory record."""
    p = rec.get("folderPath") or rec.get("path") or ""
    return _text(p)


def _log_ingest(conn, pid, source_file, kind, batch, rows_read, rows_loaded):
    conn.execute(
        """INSERT OR REPLACE INTO raw_ingest_log
           (project_id, source_file, kind, batch, rows_read, rows_loaded, ingested_at)
           VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (pid, source_file, kind, batch, rows_read, rows_loaded),
    )


def _clear_project(conn, pid):
    """Wipe all raw_* rows for this project."""
    for t in (
        "raw_ingest_log", "raw_fetched_path", "raw_object_type", "raw_ast_cache",
        "raw_telemetry", "raw_inventory_sql", "raw_inventory_filter",
        "raw_inventory_table", "raw_inventory_metric", "raw_inventory",
    ):
        conn.execute(f"DELETE FROM {t} WHERE project_id = ?", (pid,))


# ---------------- inventory ingestion -----------------------------

def _insert_record(conn, pid, batch, rec, source_file):
    """Extract + insert one inventory record. Returns 1 on success, 0 on skip."""
    rid = rec.get("id")
    if not rid:
        return 0
    metric_rows, tables, filters, attrs = _extract_fields(rec)

    conn.execute(
        """INSERT OR REPLACE INTO raw_inventory
           (project_id, report_id, batch, name, owner, folder_path,
            date_created, date_modified, object_type, subtype, source_type,
            prompted, has_sql, sql_hash, metric_count, table_count, filter_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pid, rid, batch,
            rec.get("name", ""),
            _text(rec.get("owner")),
            _path_from_record(rec),
            rec.get("dateCreated"),
            rec.get("dateModified"),
            rec.get("type") or (55 if batch == "dossier" else 3),
            rec.get("subType") or rec.get("subtype"),
            rec.get("sourceType"),
            1 if rec.get("prompted") else 0,
            1 if rec.get("sql") else 0,
            rec.get("sqlHash"),
            len(metric_rows), len(tables), len(filters),
        ),
    )

    if attrs:
        conn.executemany(
            """INSERT OR IGNORE INTO raw_inventory_attribute
               (project_id, report_id, batch, attribute_id, attribute_name) VALUES (?, ?, ?, ?, ?)""",
            [(pid, rid, batch, aid, aname) for (aid, aname) in attrs],
        )

    if metric_rows:
        conn.executemany(
            """INSERT OR IGNORE INTO raw_inventory_metric
               (project_id, report_id, batch, metric_id, metric_name) VALUES (?, ?, ?, ?, ?)""",
            [(pid, rid, batch, mid, mname) for (mid, mname) in metric_rows],
        )
    if tables:
        conn.executemany(
            """INSERT OR IGNORE INTO raw_inventory_table
               (project_id, report_id, batch, table_name) VALUES (?, ?, ?, ?)""",
            [(pid, rid, batch, t) for t in tables],
        )
    if filters:
        conn.executemany(
            """INSERT OR IGNORE INTO raw_inventory_filter
               (project_id, report_id, batch, filter_id, filter_name) VALUES (?, ?, ?, ?, ?)""",
            [(pid, rid, batch, fid, fname) for (fid, fname) in filters],
        )
    if rec.get("sql") or rec.get("sqlError"):
        conn.execute(
            """INSERT OR REPLACE INTO raw_inventory_sql
               (project_id, report_id, batch, sql_text, sql_hash, sql_error) VALUES (?, ?, ?, ?, ?, ?)""",
            (pid, rid, batch, rec.get("sql"), rec.get("sqlHash"), rec.get("sqlError")),
        )

    # raw_telemetry is now populated from raw_user_activity by
    # db/rebuild_telemetry.py, NOT from inline `_telemetry` or per-project
    # telemetry/*.json files. The inline path is left as-is in the source
    # records for historical reference only.
    return 1


def ingest_inventory_file(conn, pid, path: Path, batch: str, stream_threshold_mb: int = 200):
    """Load one inventory-style JSON file (list of report records) into raw_inventory + children.

    Uses ijson streaming if the file is larger than stream_threshold_mb.
    """
    if not path.exists():
        return 0, 0
    size_mb = path.stat().st_size / (1024 * 1024)
    rows_read = rows_loaded = 0
    t0 = time.time()
    print(f"  [{batch:10}] {path.relative_to(REPO_ROOT)} ({size_mb:,.1f} MB)...", end="", flush=True)

    if size_mb > stream_threshold_mb:
        # Stream — records parsed one at a time to keep memory bounded.
        with open(path, "rb") as f:
            for rec in ijson.items(f, "item"):
                rows_read += 1
                rows_loaded += _insert_record(conn, pid, batch, rec, str(path.name))
                if rows_read % 1000 == 0:
                    conn.commit()
                    print(f".", end="", flush=True)
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for rec in data:
                rows_read += 1
                rows_loaded += _insert_record(conn, pid, batch, rec, str(path.name))

    conn.commit()
    _log_ingest(conn, pid, str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "inventory", batch, rows_read, rows_loaded)
    print(f" {rows_loaded:,}/{rows_read:,} in {time.time()-t0:,.1f}s")
    return rows_read, rows_loaded


# ---------------- telemetry ---------------------------------------

def enrich_inventory_with_sql(conn, pid, path: Path, batch: str = "inventory"):
    """Secondary pass on a SQL-enriched inventory file.

    Some projects (INSIGHT) split the inventory into two files: one with
    modeling metadata (active_reports_enriched.json) and one with SQL +
    sourceTables (active_reports_sql.json). This streams the second and
    merges SQL + tables into the rows that ingest_inventory_file already
    created under the given batch.

    For records the primary pass missed, inserts a fresh raw_inventory row.
    """
    if not path.exists():
        return 0, 0
    size_mb = path.stat().st_size / (1024 * 1024)
    t0 = time.time()
    print(f"  [sql-enrich] {path.relative_to(REPO_ROOT)} ({size_mb:,.1f} MB)...", end="", flush=True)

    rows_read = 0
    rows_enriched = 0  # had SQL or sourceTables
    rows_inserted = 0  # were missing from primary pass and got added

    # Pre-load existing IDs for this project+batch
    existing = {
        r["report_id"]
        for r in conn.execute(
            "SELECT report_id FROM raw_inventory WHERE project_id = ? AND batch = ?",
            (pid, batch),
        )
    }

    with open(path, "rb") as f:
        for rec in ijson.items(f, "item"):
            rows_read += 1
            rid = rec.get("id")
            if not rid:
                continue
            sql_text = rec.get("sql")
            sql_hash = rec.get("sqlHash")
            sql_error = rec.get("sqlError")
            source_tables = rec.get("sourceTables") or []

            if rid in existing:
                # Update the existing raw_inventory row with SQL/table info
                if sql_text or sql_error:
                    conn.execute(
                        """INSERT OR REPLACE INTO raw_inventory_sql
                           (project_id, report_id, batch, sql_text, sql_hash, sql_error)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (pid, rid, batch, sql_text, sql_hash, sql_error),
                    )
                    conn.execute(
                        """UPDATE raw_inventory SET has_sql = 1, sql_hash = ?
                           WHERE project_id = ? AND report_id = ? AND batch = ?""",
                        (sql_hash, pid, rid, batch),
                    )
                if source_tables:
                    table_rows = [
                        (pid, rid, batch, _norm_table(t))
                        for t in source_tables
                        if isinstance(t, str) and _norm_table(t)
                    ]
                    if table_rows:
                        conn.executemany(
                            """INSERT OR IGNORE INTO raw_inventory_table
                               (project_id, report_id, batch, table_name) VALUES (?, ?, ?, ?)""",
                            table_rows,
                        )
                        # Update the denormalized count on raw_inventory
                        conn.execute(
                            """UPDATE raw_inventory
                               SET table_count = (SELECT COUNT(*) FROM raw_inventory_table
                                                  WHERE project_id = ? AND report_id = ? AND batch = ?)
                               WHERE project_id = ? AND report_id = ? AND batch = ?""",
                            (pid, rid, batch, pid, rid, batch),
                        )
                if sql_text or source_tables:
                    rows_enriched += 1
            else:
                # Missing from primary pass — insert fresh
                rows_inserted += _insert_record(conn, pid, batch, rec, str(path.name))

            if rows_read % 1000 == 0:
                conn.commit()
                print(".", end="", flush=True)

    conn.commit()
    _log_ingest(conn, pid, str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "inventory_sql", batch, rows_read, rows_enriched + rows_inserted)
    print(f" read={rows_read:,} enriched={rows_enriched:,} new={rows_inserted:,} in {time.time()-t0:,.1f}s")
    return rows_enriched, rows_inserted


# ---------------- ast cache ---------------------------------------

def ingest_ast_cache(conn, pid, path: Path):
    """Load analysis/ast_cache.json.

    Detects key shape:
      - 32-char keys → report IDs (GO shape)
      - 16-char keys → AST cluster hashes (GI / INSIGHT shape)
    """
    if not path.exists():
        return 0
    t0 = time.time()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        return 0
    sample_key = next(iter(data.keys()))
    kind = "report" if len(sample_key) >= 30 else "ast_cluster"
    rows = []
    for key, hashes in data.items():
        if not isinstance(hashes, list):
            continue
        rows.append((pid, key, kind, len(hashes), json.dumps(hashes)))
    conn.executemany(
        """INSERT OR REPLACE INTO raw_ast_cache
           (project_id, cache_key, kind, subtree_count, subtree_hashes)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    _log_ingest(conn, pid, str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "ast_cache", kind, len(data), len(rows))
    print(f"  [ast_cache] {path.name:30} kind={kind:12}  {len(rows):,} entries  {time.time()-t0:,.1f}s")
    return len(rows)


# ---------------- analysis/summary.json byCategory ----------------

def ingest_object_types(conn, pid, summary_path: Path):
    if not summary_path.exists():
        return 0
    t0 = time.time()
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    by_cat = data.get("byCategory") if isinstance(data, dict) else None
    if not isinstance(by_cat, dict):
        return 0
    rows = [(pid, k, v) for k, v in by_cat.items()]
    conn.executemany(
        "INSERT OR REPLACE INTO raw_object_type (project_id, category, count) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    _log_ingest(conn, pid, str(summary_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "object_type", None, len(rows), len(rows))
    print(f"  [obj_types] {summary_path.name:30} {len(rows)} categories  {time.time()-t0:,.1f}s")
    return len(rows)


# ---------------- fetched paths cache -----------------------------

def ingest_paths(conn, pid, path: Path):
    """Load the canonical <project>/inventory/paths.json.

    Canonical shape: [{"id": "...", "path": "..."}].
    Produced by db/migrate_paths.py and kept current by the upstream
    writers (fetch_all_missing_paths.py, merge_go_dossiers.py, etc.).
    """
    if not path.exists():
        print(f"  [paths]     {path.name:30} MISSING (run db/migrate_paths.py)")
        return 0
    t0 = time.time()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print(f"  [paths]     {path.name:30} UNEXPECTED SHAPE ({type(data).__name__}, expected list)")
        return 0
    rows = [
        (pid, item["id"], item.get("path") or "")
        for item in data
        if isinstance(item, dict) and item.get("id") and item.get("path")
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO raw_fetched_path (project_id, report_id, path) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    _log_ingest(conn, pid, str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "paths", None, len(data), len(rows))
    print(f"  [paths]     {path.name:30} {len(rows):,} paths  {time.time()-t0:,.1f}s")
    return len(rows)


# ---------------- per-project orchestrators -----------------------

def ingest_go(conn):
    pid = "global-operational"
    base = REPO_ROOT / "Global Operational"
    print(f"\n=== Ingesting GO raw inputs from {base} ===")
    _clear_project(conn, pid)

    # Inventory batches
    ingest_inventory_file(conn, pid, base / "inventory" / "reports.json", "original")
    ingest_inventory_file(conn, pid, base / "inventory" / "reports_new_enriched.json", "new")
    ingest_inventory_file(conn, pid, base / "inventory" / "reports_added_enriched.json", "added")
    ingest_inventory_file(conn, pid, base / "inventory" / "reports_new_no_telemetry.json", "new_retire")
    ingest_inventory_file(conn, pid, base / "inventory" / "reports_added_no_telemetry.json", "added_retire")
    ingest_inventory_file(conn, pid, base / "inventory" / "dossiers_enriched.json", "dossier")

    # (raw_telemetry is populated separately from raw_user_activity
    #  by db/rebuild_telemetry.py — no per-project telemetry file reads here.)

    # AST cache
    ingest_ast_cache(conn, pid, base / "analysis" / "ast_cache.json")

    # Object-type counts
    ingest_object_types(conn, pid, base / "analysis" / "summary.json")

    # Canonical path file
    ingest_paths(conn, pid, base / "inventory" / "paths.json")


def ingest_gi(conn):
    pid = "global-insight"
    base = REPO_ROOT / "Global Insight"
    print(f"\n=== Ingesting GI raw inputs from {base} ===")
    _clear_project(conn, pid)

    # Inventory (single enormous file; streams with ijson)
    ingest_inventory_file(conn, pid, base / "inventory" / "reports.json", "inventory")

    # (raw_telemetry is populated separately from raw_user_activity
    #  by db/rebuild_telemetry.py.)

    # AST cache (GI shape — keyed by AST cluster hash, not report id)
    ingest_ast_cache(conn, pid, base / "analysis" / "ast_cache.json")

    # Object-type counts
    ingest_object_types(conn, pid, base / "analysis" / "summary.json")

    # Canonical path file (GI paths.json is produced by migrate_paths.py
    # from the inline folderPath values in reports.json).
    ingest_paths(conn, pid, base / "inventory" / "paths.json")


def ingest_insight(conn):
    pid = "insight"
    base = REPO_ROOT / "INSIGHT" / "rationalization"
    print(f"\n=== Ingesting INSIGHT raw inputs from {base} ===")
    _clear_project(conn, pid)

    # INSIGHT keeps enriched inventory in active_reports_enriched.json (+ SQL in active_reports_sql.json).
    # No separate "old/new/added" batches here — it's one blob.
    ingest_inventory_file(conn, pid, base / "inventory" / "active_reports_enriched.json", "inventory")

    # SQL + sourceTables live in a separate 442 MB file. Merge into 'inventory' batch.
    enrich_inventory_with_sql(conn, pid, base / "inventory" / "active_reports_sql.json", "inventory")

    # Retired-inventory records (reports with no telemetry). Relocated from
    # telemetry/retire_reports.json → inventory/retired_reports.json in
    # Phase 3b cleanup — this file is the only source for INSIGHT's retired
    # reports so they can't be rebuilt from raw_user_activity alone.
    ingest_inventory_file(conn, pid, base / "inventory" / "retired_reports.json", "retire")

    # (raw_telemetry is populated separately from raw_user_activity
    #  by db/rebuild_telemetry.py.)

    # AST cache (INSIGHT is enormous — 141 MB file)
    ingest_ast_cache(conn, pid, base / "analysis" / "ast_cache.json")

    # Object-type counts
    ingest_object_types(conn, pid, base / "analysis" / "summary.json")

    # Canonical path file
    ingest_paths(conn, pid, base / "inventory" / "paths.json")


# ---------------- driver ------------------------------------------

def main() -> int:
    init()  # ensure schema exists
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")  # speeds up bulk inserts
    try:
        ingest_go(conn)
        ingest_gi(conn)
        ingest_insight(conn)

        # Per-table summary per project
        print("\n=== Raw row counts by project ===")
        for row in conn.execute(
            """SELECT 'raw_inventory' tbl, project_id, COUNT(*) n
                 FROM raw_inventory GROUP BY project_id
               UNION ALL
               SELECT 'raw_inventory_metric', project_id, COUNT(*)
                 FROM raw_inventory_metric GROUP BY project_id
               UNION ALL
               SELECT 'raw_inventory_table', project_id, COUNT(*)
                 FROM raw_inventory_table GROUP BY project_id
               UNION ALL
               SELECT 'raw_inventory_filter', project_id, COUNT(*)
                 FROM raw_inventory_filter GROUP BY project_id
               UNION ALL
               SELECT 'raw_inventory_sql', project_id, COUNT(*)
                 FROM raw_inventory_sql GROUP BY project_id
               UNION ALL
               SELECT 'raw_telemetry', project_id, COUNT(*)
                 FROM raw_telemetry GROUP BY project_id
               UNION ALL
               SELECT 'raw_ast_cache', project_id, COUNT(*)
                 FROM raw_ast_cache GROUP BY project_id
               UNION ALL
               SELECT 'raw_fetched_path', project_id, COUNT(*)
                 FROM raw_fetched_path GROUP BY project_id
               ORDER BY project_id, tbl"""
        ):
            print(f"  {row['project_id']:22}  {row['tbl']:28}  {row['n']:>10,}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--project", choices=["go", "gi", "insight", "all"], default="all")
    args = p.parse_args()

    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        if args.project in ("go", "all"):
            ingest_go(conn)
        if args.project in ("gi", "all"):
            ingest_gi(conn)
        if args.project in ("insight", "all"):
            ingest_insight(conn)
    finally:
        conn.close()
    sys.exit(0)
