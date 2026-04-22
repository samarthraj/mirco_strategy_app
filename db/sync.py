"""Sync a single project's public/data JSON into the SQLite DB.

Call this at the tail of a build script so the DB stays authoritative:

    from db.sync import sync_project
    sync_project("Global Operational")

Idempotent — clears all rows for the project first, then re-inserts.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.db import connect, init, PROJECTS
from db.load import load_project


def sync_project(project_name: str, verbose: bool = True) -> dict:
    """Re-load the named project (case-sensitive MSTR project name) into the DB.

    Returns the per-table row counts dict from load_project.
    """
    matches = [p for p in PROJECTS if p["name"] == project_name]
    if not matches:
        known = ", ".join(p["name"] for p in PROJECTS)
        raise ValueError(f"Unknown project '{project_name}'. Known: {known}")
    proj = matches[0]

    init()  # schema is idempotent
    conn = connect()
    try:
        if verbose:
            print(f"\n[db.sync] Syncing '{project_name}' into {conn_db_path(conn)}")
        counts = load_project(proj, conn)
        if verbose:
            total = sum(counts.values())
            print(f"[db.sync] Done. {total:,} rows across {len(counts)} tables.")
        return counts
    finally:
        conn.close()


def conn_db_path(conn) -> str:
    # sqlite filename reachable via PRAGMA database_list
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        for r in rows:
            if (r["name"] if hasattr(r, "keys") else r[1]) == "main":
                return r["file"] if hasattr(r, "keys") else r[2]
    except Exception:
        pass
    return "?"


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("project_name",
                   help="MSTR project name (e.g. 'Global Operational')")
    args = p.parse_args()
    sync_project(args.project_name)
