"""Backfill blank `report.path` / `raw_inventory.folder_path` values from
per-project paths.json caches (written by fetch_all_missing_paths.py).

Context:
  - `raw_inventory.folder_path` is what MSTR's inventory export gave us.
  - For many reports (22% of GI at last count, 886 of them still active),
    MSTR returns an empty folder_path even though the object does have
    ancestors. `fetch_all_missing_paths.py` fixes that one report at a time
    via `GET /objects/{id}?type=3` and writes to `<project>/inventory/paths.json`.
  - `db/ingest_raw.py ingest_paths()` loads that JSON into `raw_fetched_path`,
    but NOTHING in the compute pipeline currently reads from that table — so
    the fetched paths never reach the UI.

This script closes the gap: it updates `raw_inventory.folder_path` AND
`report.path` in-place for rows that are currently blank, pulling from
`raw_fetched_path`. After running, re-emit with `python -m db.emit` to
refresh the UI's JSON.

Usage:
    python -m db.backfill_paths --project global-insight
    python -m db.backfill_paths --project all
    python -m db.backfill_paths --project global-insight --dry-run
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.db import connect, init, get_project, PROJECTS


def backfill_project(conn, project_id: str, dry_run: bool = False) -> dict:
    """Fill blank paths for one project from raw_fetched_path. Returns a
    small stats dict."""
    # Available fetched paths for this project
    fetched_count = conn.execute(
        "SELECT COUNT(*) FROM raw_fetched_path WHERE project_id=?",
        (project_id,),
    ).fetchone()[0]

    # How many blanks we have before
    inv_blank_before = conn.execute(
        "SELECT COUNT(*) FROM raw_inventory "
        "WHERE project_id=? AND (folder_path IS NULL OR folder_path='')",
        (project_id,),
    ).fetchone()[0]
    rep_blank_before = conn.execute(
        "SELECT COUNT(*) FROM report "
        "WHERE project_id=? AND (path IS NULL OR path='')",
        (project_id,),
    ).fetchone()[0]

    # Preview: how many of the blanks will get filled
    inv_would_fill = conn.execute(
        """SELECT COUNT(*)
             FROM raw_inventory ri
             JOIN raw_fetched_path fp
               ON fp.project_id = ri.project_id AND fp.report_id = ri.report_id
            WHERE ri.project_id=?
              AND (ri.folder_path IS NULL OR ri.folder_path='')
              AND fp.path IS NOT NULL AND fp.path <> ''""",
        (project_id,),
    ).fetchone()[0]
    rep_would_fill = conn.execute(
        """SELECT COUNT(*)
             FROM report r
             JOIN raw_fetched_path fp
               ON fp.project_id = r.project_id AND fp.report_id = r.report_id
            WHERE r.project_id=?
              AND (r.path IS NULL OR r.path='')
              AND fp.path IS NOT NULL AND fp.path <> ''""",
        (project_id,),
    ).fetchone()[0]
    # Active subset (what the UI cares about most)
    rep_would_fill_active = conn.execute(
        """SELECT COUNT(*)
             FROM report r
             JOIN raw_fetched_path fp
               ON fp.project_id = r.project_id AND fp.report_id = r.report_id
            WHERE r.project_id=? AND r.status='active'
              AND (r.path IS NULL OR r.path='')
              AND fp.path IS NOT NULL AND fp.path <> ''""",
        (project_id,),
    ).fetchone()[0]

    print(f"\n=== {project_id} ===")
    print(f"  fetched paths available:     {fetched_count:,}")
    print(f"  raw_inventory blank paths:   {inv_blank_before:,}")
    print(f"  report       blank paths:    {rep_blank_before:,}")
    print(f"  will fill raw_inventory:     {inv_would_fill:,}")
    print(f"  will fill report:            {rep_would_fill:,}  ({rep_would_fill_active:,} active)")

    if dry_run:
        print("  [dry-run] no writes performed")
        return {
            "project_id": project_id,
            "fetched_count": fetched_count,
            "inv_would_fill": inv_would_fill,
            "rep_would_fill": rep_would_fill,
            "rep_would_fill_active": rep_would_fill_active,
            "applied": False,
        }

    with conn:
        # Update raw_inventory — persists across pipeline reruns too.
        conn.execute(
            """UPDATE raw_inventory
                  SET folder_path = (
                      SELECT fp.path FROM raw_fetched_path fp
                       WHERE fp.project_id = raw_inventory.project_id
                         AND fp.report_id  = raw_inventory.report_id
                  )
                WHERE project_id = ?
                  AND (folder_path IS NULL OR folder_path = '')
                  AND EXISTS (
                      SELECT 1 FROM raw_fetched_path fp
                       WHERE fp.project_id = raw_inventory.project_id
                         AND fp.report_id  = raw_inventory.report_id
                         AND fp.path IS NOT NULL AND fp.path <> ''
                  )""",
            (project_id,),
        )
        # Update report — UI-facing field; in sync with raw_inventory now.
        conn.execute(
            """UPDATE report
                  SET path = (
                      SELECT fp.path FROM raw_fetched_path fp
                       WHERE fp.project_id = report.project_id
                         AND fp.report_id  = report.report_id
                  )
                WHERE project_id = ?
                  AND (path IS NULL OR path = '')
                  AND EXISTS (
                      SELECT 1 FROM raw_fetched_path fp
                       WHERE fp.project_id = report.project_id
                         AND fp.report_id  = report.report_id
                         AND fp.path IS NOT NULL AND fp.path <> ''
                  )""",
            (project_id,),
        )

    # Post-stats
    inv_blank_after = conn.execute(
        "SELECT COUNT(*) FROM raw_inventory "
        "WHERE project_id=? AND (folder_path IS NULL OR folder_path='')",
        (project_id,),
    ).fetchone()[0]
    rep_blank_after = conn.execute(
        "SELECT COUNT(*) FROM report "
        "WHERE project_id=? AND (path IS NULL OR path='')",
        (project_id,),
    ).fetchone()[0]

    print(f"  raw_inventory filled:        {inv_blank_before - inv_blank_after:,}"
          f"  ({inv_blank_after:,} still blank)")
    print(f"  report filled:               {rep_blank_before - rep_blank_after:,}"
          f"  ({rep_blank_after:,} still blank)")
    return {
        "project_id": project_id,
        "fetched_count": fetched_count,
        "inv_filled": inv_blank_before - inv_blank_after,
        "rep_filled": rep_blank_before - rep_blank_after,
        "applied": True,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill blank paths from raw_fetched_path")
    ap.add_argument("--project", required=True,
                    help='Project id (e.g. "global-insight") or "all"')
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview what would change without writing")
    args = ap.parse_args()

    init()
    conn = connect()
    try:
        if args.project == "all":
            targets = [p["project_id"] for p in PROJECTS]
        else:
            get_project(args.project)  # validate
            targets = [args.project]
        for pid in targets:
            backfill_project(conn, pid, dry_run=args.dry_run)
        if not args.dry_run:
            print(
                "\nDone. Re-emit the project(s) to refresh the UI's JSON:"
                "\n  python -m db.emit  (or the project-specific emit step you use)"
            )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
