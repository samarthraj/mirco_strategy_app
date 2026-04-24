"""Run only the first 5 dedup stages of the Rationalization pipeline
(collisions -> fingerprint -> sql_hash -> ast -> family) for each of the
3 real projects. Skips similarity, llm_reviews, reports, summary.

Why stop at family: after the cube backfill, these stages consume the
updated raw_inventory_metric / raw_inventory_attribute tables directly,
so duplicate detection across identical cubes now works correctly.
Running later stages (similarity etc.) is deferred — the user wants to
inspect the dedup counts first.
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db.db import connect, init
from db.compute.pipeline import (
    stage_collisions, stage_fingerprint, stage_sql_hash,
    stage_ast, stage_family,
)

PROJECTS = ["global-operational", "global-insight", "insight"]
STAGES = [
    ("collisions",  stage_collisions),
    ("fingerprint", stage_fingerprint),
    ("sql_hash",    stage_sql_hash),
    ("ast",         stage_ast),
    ("family",      stage_family),
]


def main() -> int:
    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")

    t_all = time.time()
    for pid in PROJECTS:
        print(f"\n{'=' * 72}")
        print(f"=== {pid} — 5-stage dedup ===")
        print(f"{'=' * 72}")
        t_proj = time.time()
        for name, fn in STAGES:
            print(f"\n  [{name}]")
            t_stage = time.time()
            result = fn(conn, pid)
            dt = time.time() - t_stage
            for k, v in result.items():
                print(f"    {k}: {v}")
            print(f"    ({dt:.1f}s)")
        print(f"\n  {pid} complete in {time.time() - t_proj:.1f}s")

    print(f"\n\nALL PROJECTS complete in {time.time() - t_all:.1f}s")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
