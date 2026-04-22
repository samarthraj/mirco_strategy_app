"""Rebuild raw_telemetry directly from raw_user_activity (the UserActivity.csv load).

Replaces the legacy flow that relied on pre-computed
`<project>/telemetry/active_reports.json` + `retire_reports.json` files. The
legacy files were themselves derived from UserActivity.csv by an offline
matcher — so sourcing from raw_user_activity cuts out the middleman AND
unlocks richer per-activity fields (sessions, errors, distinct users,
per-action breakdown).

Matching tiers, in order (highest-confidence first):
  Tier 0 — exact_id   : raw_user_activity.object_id == raw_inventory.report_id
                         AND activity's folder_path starts with project prefix
  Tier 1 — full_path  : normalized name AND folder path both match inventory
  Tier 2 — exact_name : normalized name matches, scoped to project prefix
  Tier 3 — fuzzy      : substring-containment + Jaccard >= 0.60 (optional, off by default)

Aggregates per inventory record:
  - total_executions = SUM(executions)
  - total_users      = COUNT(DISTINCT user)
  - last_exec_ts     = MAX(last_execution)
  - match_tier       = tier at which we matched

Inventory records that get NO match → matched=0 (retired).

Usage:
    python db/rebuild_telemetry.py                    # all 3 projects
    python db/rebuild_telemetry.py --project insight  # single project
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.db import connect, init, PROJECTS, get_project


def _normalize(s: str) -> str:
    return " ".join((s or "").lower().replace("*", " ").split())


def _project_folder_prefix(proj_name: str) -> str:
    """Project folder paths in UserActivity.csv use the MSTR project name as
    the first path component. Strip leading slashes for the LIKE."""
    return proj_name.replace("/", "") + "%"


def rebuild_for_project(conn, project_id: str, verbose: bool = True) -> dict:
    cfg = get_project(project_id)
    proj_name = cfg["name"]
    prefix1 = f"/{proj_name}/%"
    prefix2 = f"{proj_name}/%"

    if verbose:
        print(f"\n=== Rebuilding raw_telemetry for {proj_name} ({project_id}) ===")

    # ---------------- Pre-aggregate user activity for this project ---------
    # Sum per object_id across all (user, action) rows whose folder_path is
    # under the project.
    #
    # EXCLUSION: For dev/admin users (see db.user_filters.DEV_ADMIN_USERS),
    # drop their personal-folder (/<proj>/Profiles/...) rows so dev-sandbox
    # reports don't inherit execution counts from testing/validation runs.
    from db.user_filters import is_dev_admin
    dev_admin_lower = [
        r[0].lower()
        for r in conn.execute(
            "SELECT DISTINCT user FROM raw_user_activity WHERE user != ''"
        )
        if is_dev_admin(r[0])
    ]
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

    agg_sql = f"""
        SELECT object_id,
               report_name,
               folder_path,
               SUM(executions)     AS total_executions,
               COUNT(DISTINCT user) AS total_users,
               MAX(last_execution) AS last_exec_ts,
               SUM(sessions)       AS total_sessions,
               SUM(errors)         AS total_errors
          FROM raw_user_activity
         WHERE (folder_path LIKE ? OR folder_path LIKE ?)
           AND object_id != ''
           AND object_id != 'Unknown'
           {excl_clause}
         GROUP BY object_id, report_name, folder_path
    """
    activity = conn.execute(
        agg_sql, [prefix1, prefix2, *excl_params]
    ).fetchall()
    if verbose and dev_admin_lower:
        print(f"  excluding personal-folder activity for {len(dev_admin_lower)} dev/admin users")
    if verbose:
        print(f"  activity aggregates (this project): {len(activity):,}")

    # Build lookup indexes for matching
    activity_by_id: dict[str, dict] = {}
    activity_by_path_name: dict[tuple, dict] = {}
    activity_by_name: dict[str, list[dict]] = {}
    for r in activity:
        rec = {
            "object_id": r[0], "name": r[1] or "", "folder_path": r[2] or "",
            "executions": r[3] or 0, "users": r[4] or 0,
            "last_exec": r[5] or "", "sessions": r[6] or 0, "errors": r[7] or 0,
        }
        # Sum across duplicates at the (id) level — shouldn't happen much, but be safe
        if rec["object_id"] in activity_by_id:
            existing = activity_by_id[rec["object_id"]]
            existing["executions"] += rec["executions"]
            existing["users"] = max(existing["users"], rec["users"])
            existing["sessions"] += rec["sessions"]
            existing["errors"] += rec["errors"]
            if rec["last_exec"] > existing["last_exec"]:
                existing["last_exec"] = rec["last_exec"]
        else:
            activity_by_id[rec["object_id"]] = rec
        key = (_normalize(rec["name"]), rec["folder_path"].lstrip("/"))
        activity_by_path_name[key] = rec
        activity_by_name.setdefault(_normalize(rec["name"]), []).append(rec)

    # ---------------- Load all inventory for this project --------------------
    inventory = conn.execute(
        """SELECT DISTINCT report_id, name, folder_path
             FROM raw_inventory WHERE project_id = ?""",
        (project_id,),
    ).fetchall()
    if verbose:
        print(f"  inventory rows: {len(inventory):,}")

    # ---------------- Match inventory to activity (tier waterfall) -----------
    matched_rows = []  # raw_telemetry rows for matched inventory
    unmatched_rows = []
    consumed_ids = set()  # activity object_ids we've already attached

    tier_counts = {"exact_id": 0, "full_path": 0, "exact_name": 0, "unmatched": 0}

    # Build fast lookup for name matching scoped to the project
    for rid, name, folder_path in inventory:
        name_n = _normalize(name)
        fp = (folder_path or "").lstrip("/")

        match = None
        tier = None

        # Tier 0: exact object_id
        if rid in activity_by_id:
            match = activity_by_id[rid]
            tier = "exact_id"

        # Tier 1: full_path (normalized name + folder path match exactly)
        if not match:
            candidate = activity_by_path_name.get((name_n, fp))
            if candidate and candidate["object_id"] not in consumed_ids:
                match = candidate; tier = "full_path"

        # Tier 2: exact_name only — take the highest-exec match
        if not match and name_n:
            cands = [c for c in activity_by_name.get(name_n, [])
                     if c["object_id"] not in consumed_ids]
            if cands:
                match = max(cands, key=lambda c: c["executions"])
                tier = "exact_name"

        if match:
            tier_counts[tier] += 1
            consumed_ids.add(match["object_id"])
            matched_rows.append((
                project_id, rid, 1, tier, None,
                match["name"], match["folder_path"],
                match["executions"], match["users"], match["last_exec"],
            ))
        else:
            tier_counts["unmatched"] += 1
            unmatched_rows.append((
                project_id, rid, 0, "none", None, None, None, 0, 0, "",
            ))

    # ---------------- Write ----------------------------------------------------
    with conn:
        conn.execute("DELETE FROM raw_telemetry WHERE project_id = ?", (project_id,))
        conn.executemany(
            """INSERT INTO raw_telemetry
               (project_id, report_id, matched, match_tier, match_score,
                matched_telemetry_name, telemetry_path,
                total_executions, total_users, last_exec_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            matched_rows,
        )
        conn.executemany(
            """INSERT INTO raw_telemetry
               (project_id, report_id, matched, match_tier, match_score,
                matched_telemetry_name, telemetry_path,
                total_executions, total_users, last_exec_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            unmatched_rows,
        )

    if verbose:
        print(f"  tier breakdown:")
        for t, n in tier_counts.items():
            print(f"    {t:12}  {n:>8,}")
        # Count unassigned activity (object_ids that never matched any inventory)
        assigned = len(consumed_ids)
        stray = len(activity_by_id) - assigned
        print(f"  activity assigned:   {assigned:,}")
        print(f"  activity not in inventory (orphans): {stray:,}")

    return tier_counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="all",
                    choices=["global-operational", "global-insight", "insight", "all"])
    args = ap.parse_args()

    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        targets = (
            ["global-operational", "global-insight", "insight"]
            if args.project == "all" else [args.project]
        )
        overall = {"exact_id": 0, "full_path": 0, "exact_name": 0, "unmatched": 0}
        for tgt in targets:
            counts = rebuild_for_project(conn, tgt)
            for k, v in counts.items():
                overall[k] = overall.get(k, 0) + v
        print()
        print("=== Totals across projects ===")
        for t, n in overall.items():
            print(f"  {t:12}  {n:>8,}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
