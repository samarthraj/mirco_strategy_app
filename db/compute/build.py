"""Unified build orchestrator — the Phase 3b replacement for the three
legacy build scripts (build_web_dashboard_data.py, build_web_data_gi.py,
build_web_data_insight.py).

Steps, in order, per project:
  1. ingest_raw   — load raw_* tables from project JSON files
  2. pipeline     — run 7-stage canonical compute, writing derived tables
  3. emit         — write public/data/<project>/*.json from DB

Run: python -m db.compute.build --project global-operational
     python -m db.compute.build --project all
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.db import PROJECTS, connect, init, get_project
from db.ingest_raw import ingest_go, ingest_gi, ingest_insight
from db.compute.pipeline import run_pipeline, stage_cross_project
from db.emit import emit_project, emit_cross_project
from db.rebuild_telemetry import rebuild_for_project


PROJECT_INGESTERS = {
    "global-operational": ingest_go,
    "global-insight":     ingest_gi,
    "insight":            ingest_insight,
}


def build_project(project_id: str, *, skip_ingest: bool = False,
                  skip_compute: bool = False, skip_emit: bool = False) -> dict:
    """Run ingest → compute → emit for one project."""
    cfg = get_project(project_id)
    print(f"\n============================================================")
    print(f"Building {cfg['name']} ({project_id})")
    print(f"============================================================")

    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        if not skip_ingest:
            ingester = PROJECT_INGESTERS[project_id]
            ingester(conn)

        # Telemetry is built from raw_user_activity (loaded separately by
        # db/ingest_user_activity.py). Rebuild it on every compute so it
        # reflects the current activity snapshot + current raw_inventory.
        if not skip_compute:
            rebuild_for_project(conn, project_id, verbose=True)

        if not skip_compute:
            run_pipeline(project_id, conn=conn)

        if not skip_emit:
            print(f"\n[emit] Writing public/data/{cfg['name']}/*.json ...")
            emit_project(cfg, conn)
    finally:
        conn.close()

    return {"project": project_id, "ok": True}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project",
                   choices=list(PROJECT_INGESTERS.keys()) + ["all"],
                   default="all")
    p.add_argument("--skip-ingest", action="store_true",
                   help="Skip raw ingestion (if raw_* tables are already current)")
    p.add_argument("--skip-compute", action="store_true",
                   help="Skip compute pipeline (only re-emit from current derived tables)")
    p.add_argument("--skip-emit", action="store_true",
                   help="Skip JSON emission (DB-only build)")
    args = p.parse_args()

    targets = (
        list(PROJECT_INGESTERS.keys())
        if args.project == "all"
        else [args.project]
    )
    for tgt in targets:
        build_project(
            tgt,
            skip_ingest=args.skip_ingest,
            skip_compute=args.skip_compute,
            skip_emit=args.skip_emit,
        )

    # Cross-project rationalization: runs only when all projects have been
    # built (so the DB has current data for everyone). We trigger it whenever
    # the 'all' option is used, or always if not skipping compute/emit.
    if args.project == "all" and not args.skip_compute:
        from pathlib import Path as _P
        _init = init  # already imported
        _init()
        conn = connect()
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            stage_cross_project(conn)
            if not args.skip_emit:
                cross_dir = _P(__file__).resolve().parents[2] / "public" / "data" / "_cross"
                emit_cross_project(conn, cross_dir)
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
