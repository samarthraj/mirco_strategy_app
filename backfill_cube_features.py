"""Backfill attributes + metrics for cube-sourced reports whose original
/v2/cubes/{id} extraction returned HTTP 500 ("Failed to get cube
information from Intelligence Server").

Approach: for every zero-feature cube-sourced report that has telemetry
executions, hit /v2/reports/{id} (which — surprisingly — succeeds even
for cubes) and parse attributes + metrics from definition.availableObjects.

This is the differential: only reports with zero features + source_type='cube'
get refetched. The 7k+ reports already enriched during the original ingest
are untouched.

Per-project handling:
  - GO  (global-operational):  2 reports
  - GI  (global-insight):    264 reports
  - IN  (insight):           411 reports
  Total expected: ~677 API calls (plus 5 + 416 + 72 = 493 zero-exec reports
  if you uncomment INCLUDE_UNUSED).

Each call writes immediately to raw_inventory_metric / raw_inventory_attribute
and updates raw_inventory.{metric_count, attribute_count}.

Usage:
    python backfill_cube_features.py                  # all 3 projects
    python backfill_cube_features.py --project insight  # one project
    python backfill_cube_features.py --include-unused  # also hit zero-exec reports

Idempotent: an already-backfilled report (metric_count > 0) is skipped.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(Path(__file__).parent / ".env.local")

from db.db import DB_PATH, connect, init

BASE_URL = os.environ["MSTR_BASE_URL"].rstrip("/")
USERNAME = os.environ["MSTR_USERNAME"]
PASSWORD = os.environ["MSTR_PASSWORD"]

# Per-project MSTR project_id + batch tag we'll use for backfilled rows.
PROJECT_MAP = {
    "global-operational": {
        "mstr_project_id": "E77B77894C04BF0E6D244F9363CFAF64",
        "batch":           "added",  # matches active_batches
    },
    "global-insight": {
        "mstr_project_id": "07E2CE9311EB6800B59F0080EF050FB2",
        "batch":           "inventory",
    },
    "insight": {
        "mstr_project_id": "6104D29041297D66C6BD16B602F2705F",
        "batch":           "inventory",
    },
}


def login(session: requests.Session, project_mstr_id: str) -> None:
    r = session.post(
        f"{BASE_URL}/auth/login",
        json={"username": USERNAME, "password": PASSWORD, "loginMode": 1},
    )
    r.raise_for_status()
    token = r.headers["X-MSTR-AuthToken"]
    session.headers.update({
        "X-MSTR-AuthToken": token,
        "X-MSTR-ProjectID": project_mstr_id,
        "Content-Type":     "application/json",
        "Accept":           "application/json",
    })


def fetch_report_definition(session: requests.Session, rid: str) -> dict | None:
    """Returns dict with {attrs: [{id,name}], metrics: [{id,name}]} or None
    on failure. Retries 5xx with backoff."""
    url = f"{BASE_URL}/v2/reports/{rid}"
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30)
        except Exception as e:
            time.sleep(1 + attempt)
            continue
        if r.status_code == 200:
            try:
                d = r.json()
            except Exception:
                return None
            defn  = d.get("definition", {}) or {}
            avail = defn.get("availableObjects", {}) or {}
            attrs = avail.get("attributes") or defn.get("attributes") or []
            mets  = avail.get("metrics")    or defn.get("metrics")    or []
            return {
                "attrs":   [{"id": a.get("id"), "name": a.get("name") or ""} for a in attrs],
                "metrics": [{"id": m.get("id"), "name": m.get("name") or ""} for m in mets],
            }
        if r.status_code in (401,):
            return "NEED_RELOGIN"  # type: ignore
        if r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        # 403/404/etc — non-retryable
        return None
    return None


def select_candidates(conn: sqlite3.Connection, project_id: str,
                       include_unused: bool) -> list[tuple[str, str, str]]:
    """Return [(report_id, name, batch), ...] for cube-sourced reports with 0 features.
    The batch is read per-row so FK inserts into raw_inventory_metric succeed
    (the FK ties (project_id, report_id, batch) back to raw_inventory)."""
    exec_filter = "" if include_unused else "AND t.total_executions > 0"
    join = "JOIN" if not include_unused else "LEFT JOIN"
    rows = conn.execute(
        f"""SELECT ri.report_id, ri.name, ri.batch
              FROM raw_inventory ri
              {join} raw_telemetry t
                ON t.project_id = ri.project_id AND t.report_id = ri.report_id
             WHERE ri.project_id = ?
               AND ri.source_type = 'cube'
               AND (ri.metric_count = 0 OR ri.metric_count IS NULL)
               {exec_filter}
             ORDER BY COALESCE(t.total_executions, 0) DESC""",
        (project_id,),
    ).fetchall()
    return [(r[0], r[1] or "", r[2]) for r in rows]


def backfill_project(conn: sqlite3.Connection, project_id: str,
                     include_unused: bool, limit: int | None = None) -> dict:
    meta = PROJECT_MAP[project_id]
    candidates = select_candidates(conn, project_id, include_unused)
    if limit:
        candidates = candidates[:limit]
    print(f"\n=== {project_id} — {len(candidates)} zero-feature cube reports ===")
    if not candidates:
        return {"processed": 0, "ok": 0, "empty": 0, "failed": 0}

    session = requests.Session()
    login(session, meta["mstr_project_id"])

    stats = {"processed": 0, "ok": 0, "empty": 0, "failed": 0}
    t0 = time.time()

    for i, (rid, name, batch) in enumerate(candidates, 1):
        stats["processed"] += 1
        result = fetch_report_definition(session, rid)
        if result == "NEED_RELOGIN":
            login(session, meta["mstr_project_id"])
            result = fetch_report_definition(session, rid)

        if result is None:
            stats["failed"] += 1
            print(f"  [{i}/{len(candidates)}] FAIL  {rid}  {name[:50]!r}")
            continue

        attrs_list = result["attrs"]
        mets_list  = result["metrics"]
        if not attrs_list and not mets_list:
            stats["empty"] += 1
            if i % 25 == 0 or i == len(candidates):
                rate = stats["processed"] / (time.time() - t0)
                print(f"  [{i}/{len(candidates)}] (empty)  rate={rate:.1f}/s "
                      f"ok={stats['ok']} empty={stats['empty']} failed={stats['failed']}")
            continue

        # Dedupe within this report's lists — MSTR sometimes returns the
        # same metric name twice (different IDs, different template slots).
        # The (project_id, report_id, batch, metric_name) PK forbids dupes.
        seen_m: set[str] = set()
        met_rows = []
        for m in mets_list:
            nm = m["name"]
            if nm and nm not in seen_m:
                seen_m.add(nm)
                met_rows.append((project_id, rid, batch, m["id"], nm))
        seen_a: set[str] = set()
        attr_rows = []
        for a in attrs_list:
            nm = a["name"]
            if nm and nm not in seen_a:
                seen_a.add(nm)
                attr_rows.append((project_id, rid, batch, a["id"], nm))

        # Write into raw_inventory_*. INSERT OR IGNORE is belt-and-braces
        # in case an existing backfill left stale rows for this report.
        with conn:
            conn.execute(
                "DELETE FROM raw_inventory_metric WHERE project_id=? AND report_id=?",
                (project_id, rid),
            )
            conn.execute(
                "DELETE FROM raw_inventory_attribute WHERE project_id=? AND report_id=?",
                (project_id, rid),
            )
            conn.executemany(
                """INSERT OR IGNORE INTO raw_inventory_metric
                   (project_id, report_id, batch, metric_id, metric_name)
                   VALUES (?, ?, ?, ?, ?)""",
                met_rows,
            )
            conn.executemany(
                """INSERT OR IGNORE INTO raw_inventory_attribute
                   (project_id, report_id, batch, attribute_id, attribute_name)
                   VALUES (?, ?, ?, ?, ?)""",
                attr_rows,
            )
            conn.execute(
                """UPDATE raw_inventory
                      SET metric_count = ?
                    WHERE project_id = ? AND report_id = ?""",
                (len(met_rows), project_id, rid),
            )
        stats["ok"] += 1
        if i % 25 == 0 or i == len(candidates) or stats["ok"] <= 5:
            rate = stats["processed"] / (time.time() - t0)
            print(f"  [{i}/{len(candidates)}] {rid}  {name[:50]!r}  "
                  f"attrs={len(attrs_list)} metrics={len(mets_list)}  "
                  f"rate={rate:.1f}/s ok={stats['ok']} empty={stats['empty']} failed={stats['failed']}")
        time.sleep(0.2)  # gentle rate limit

    elapsed = time.time() - t0
    print(f"\n  done in {elapsed:.1f}s. ok={stats['ok']} empty={stats['empty']} failed={stats['failed']}")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", choices=list(PROJECT_MAP.keys()),
                    help="Run only this project (default: all 3)")
    ap.add_argument("--include-unused", action="store_true",
                    help="Also process reports with 0 telemetry executions")
    ap.add_argument("--limit", type=int, help="Stop after N reports per project (for testing)")
    args = ap.parse_args()

    init()
    conn = connect()

    targets = [args.project] if args.project else list(PROJECT_MAP.keys())
    all_stats = {}
    for pid in targets:
        try:
            all_stats[pid] = backfill_project(conn, pid, args.include_unused, args.limit)
        except Exception as e:
            print(f"\n!! {pid} aborted: {e}")
            all_stats[pid] = {"error": str(e)}

    print("\n=== Summary ===")
    for pid, s in all_stats.items():
        print(f"  {pid}: {s}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
