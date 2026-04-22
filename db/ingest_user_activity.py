"""Load UserActivity.csv into the raw_user_activity table.

The CSV is produced by MSTR's user-activity export (~64 MB, ~150k rows).
It captures per-(user, report, action) rows — much more granular than the
aggregate counts in raw_telemetry. Useful for cross-referencing who runs
what across the whole server, which reports appear in projects we haven't
extracted, etc.

Idempotent: clears the table before re-inserting.
"""
from __future__ import annotations
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.db import connect, init


CSV_PATH = Path(__file__).resolve().parent.parent / "UserActivity.csv"


def main() -> int:
    if not CSV_PATH.exists():
        sys.exit(f"UserActivity.csv not found at {CSV_PATH}")

    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")

    with conn:
        conn.execute("DELETE FROM raw_user_activity")

    t0 = time.time()
    inserted = 0
    batch = []
    with open(CSV_PATH, encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            batch.append((
                row.get("Report Name") or "",
                row.get("Object ID") or "",
                row.get("Folder Path") or "",
                row.get("User") or "",
                row.get("Action") or "",
                int((row.get("Executions") or "0").strip() or 0),
                int((row.get("Sessions") or "0").strip() or 0),
                int((row.get("Errors") or "0").strip() or 0),
                row.get("Last Execution") or "",
            ))
            if len(batch) >= 5000:
                with conn:
                    conn.executemany(
                        """INSERT INTO raw_user_activity
                           (report_name, object_id, folder_path, user, action,
                            executions, sessions, errors, last_execution)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        batch,
                    )
                inserted += len(batch)
                batch.clear()
        if batch:
            with conn:
                conn.executemany(
                    """INSERT INTO raw_user_activity
                       (report_name, object_id, folder_path, user, action,
                        executions, sessions, errors, last_execution)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    batch,
                )
            inserted += len(batch)

    # Quick summary
    print(f"Loaded {inserted:,} rows in {time.time()-t0:.1f}s")
    q = conn.execute
    print(f"  unique Object IDs: {q('SELECT COUNT(DISTINCT object_id) FROM raw_user_activity').fetchone()[0]:,}")
    print(f"  unique users:      {q('SELECT COUNT(DISTINCT user) FROM raw_user_activity').fetchone()[0]:,}")
    print(f"  unique actions:    {q('SELECT COUNT(DISTINCT action) FROM raw_user_activity').fetchone()[0]:,}")
    n_unknown = q("SELECT COUNT(*) FROM raw_user_activity WHERE object_id = 'Unknown'").fetchone()[0]
    print(f"  rows with Unknown object_id: {n_unknown:,}")
    print()
    print("  Top actions by row count:")
    for r in q("SELECT action, COUNT(*) n FROM raw_user_activity GROUP BY action ORDER BY n DESC LIMIT 10"):
        print(f"    {r[1]:>8,}  {r[0]}")
    print()
    print("  Scope by project (via folder_path prefix):")
    for r in q("""SELECT CASE
                     WHEN folder_path LIKE '/Global Operational%' OR folder_path LIKE 'Global Operational%' THEN 'Global Operational'
                     WHEN folder_path LIKE '/Global Insight%' OR folder_path LIKE 'Global Insight%' THEN 'Global Insight'
                     WHEN folder_path LIKE '/INSIGHT%' OR folder_path LIKE 'INSIGHT%' THEN 'INSIGHT'
                     WHEN folder_path = 'N/A' OR folder_path = '' THEN '(no path)'
                     ELSE 'Other'
                   END AS proj,
                   COUNT(*) n,
                   COUNT(DISTINCT object_id) n_objs
                   FROM raw_user_activity
                   GROUP BY 1 ORDER BY n DESC"""):
        print(f"    {r[0]:22} rows={r[1]:>7,} uniq_objects={r[2]:,}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
