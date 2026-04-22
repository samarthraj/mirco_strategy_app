"""Re-extract template attributes from MSTR for reports where the original
inventory capture missed them.

Our inventory ingest populates `raw_inventory_attribute` from the source
JSON file's `attributes[]` field. That field was empty for Global Operational
and Global Insight because the original extraction script didn't walk
`dataTemplate.units[]` with type=="attribute". This script calls the live
MSTR REST API per report and fills the gap.

Scope:
  - Active reports only (status='active' in the `report` table)
  - Skips reports that already have attributes > 0 (resume-safe)
  - Also skips reports in `report_fetch_errors` from prior failed runs

Usage:
  python recover_attributes.py --project global-operational
  python recover_attributes.py --project all --limit 100    # dry-run
  python recover_attributes.py --project all --force        # re-fetch even
                                                              if attrs exist

IMPORTANT: The target project must be loaded on the Intelligence Server.
If you see 'projects are idle' errors, the MSTR team needs to load it.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

env = Path(__file__).resolve().parent / ".env.local"
if env.exists():
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v

sys.path.insert(0, str(Path(__file__).parent))
from mstr_report_inventory import MstrClient
from db.db import connect, init

BASE_URL = os.environ.get(
    "MSTR_BASE_URL", "https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api"
)

PROJECT_GUIDS = {
    "global-operational": "E77B77894C04BF0E6D244F9363CFAF64",
    "global-insight":     "07E2CE9311EB6800B59F0080EF050FB2",
    "insight":            "6104D29041297D66C6BD16B602F2705F",
}


def _ensure_error_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS report_fetch_errors (
              project_id TEXT NOT NULL,
              report_id  TEXT NOT NULL,
              endpoint   TEXT,
              http_code  INTEGER,
              error_msg  TEXT,
              fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (project_id, report_id)
           )"""
    )


def _extract_attributes_from_definition(defn: dict) -> list[tuple[str, str]]:
    """Walk dataSource.dataTemplate.units[] and collect type=='attribute' entries."""
    out: dict[str, str] = {}
    ds = (defn or {}).get("dataSource") or {}
    tmpl = ds.get("dataTemplate") or {}
    for u in (tmpl.get("units") or []):
        if not isinstance(u, dict):
            continue
        # Match either type == "attribute" or subType containing "attribute"
        t = (u.get("type") or "").lower()
        st = (u.get("subType") or "").lower()
        if t == "attribute" or "attribute" in st:
            aid = u.get("id") or ""
            aname = u.get("name") or ""
            if aname:
                out[aname] = aid
    return [(aid, aname) for aname, aid in out.items()]


def recover_for_project(conn, project_id: str, *, limit: int | None = None,
                        force: bool = False, sleep_ms: int = 100,
                        scope: str = "final-kept") -> dict:
    """Scopes:
      - 'final-kept': only similarity-cluster primaries + post-AST singletons
                      (what the UI actually shows — smallest set)
      - 'post-ast':   all post-AST survivors (entered similarity stage)
      - 'active':     any status='active' report (largest set — includes
                      collapsed-at-downstream-stages reports)
    """
    print(f"\n=== Recovering attributes for {project_id} (scope={scope}) ===")
    proj_guid = PROJECT_GUIDS.get(project_id)
    if not proj_guid:
        print(f"  unknown project {project_id}")
        return {"done": 0, "err": 0, "skip": 0}

    _ensure_error_table(conn)

    # Build target id set based on scope
    if scope == "final-kept":
        # primaries of multi-member similarity clusters
        primaries = {r[0] for r in conn.execute(
            "SELECT primary_report_id FROM similarity_cluster WHERE project_id = ?",
            (project_id,),
        ) if r[0]}
        # post-AST canonicals not in any cluster member list
        members = {r[0] for r in conn.execute(
            "SELECT report_id FROM similarity_cluster_member WHERE project_id = ?",
            (project_id,),
        )}
        try:
            from db.compute.pipeline import _ast_canonical_ids
            post_ast = _ast_canonical_ids(conn, project_id)
        except Exception:
            post_ast = set()
        target_ids = primaries | (post_ast - members)
    elif scope == "post-ast":
        from db.compute.pipeline import _ast_canonical_ids
        target_ids = _ast_canonical_ids(conn, project_id)
    elif scope == "active":
        target_ids = {r[0] for r in conn.execute(
            "SELECT report_id FROM report WHERE project_id = ? AND status = 'active'",
            (project_id,),
        )}
    else:
        sys.exit(f"unknown --scope {scope}")

    # Filter out ones already done (unless --force) and previous failures
    if not force:
        already = {r[0] for r in conn.execute(
            "SELECT report_id FROM report WHERE project_id = ? AND attribute_count > 0",
            (project_id,),
        )}
        errored = {r[0] for r in conn.execute(
            "SELECT report_id FROM report_fetch_errors WHERE project_id = ?",
            (project_id,),
        )}
        target_ids = target_ids - already - errored

    rids = sorted(target_ids)
    if limit:
        rids = rids[:limit]
    total = len(rids)
    print(f"  reports to fetch: {total:,}")
    if total == 0:
        return {"done": 0, "err": 0, "skip": 0}

    client = MstrClient(
        base_url=BASE_URL,
        username=os.environ["MSTR_USERNAME"],
        password=os.environ["MSTR_PASSWORD"],
        verify_ssl=True, timeout=120,
    )
    client.login()
    client.project_id = proj_guid

    done = err = 0
    batch = "recovered"   # batch label for raw_inventory_attribute FK
    # Ensure the raw_inventory row exists for this (project, report, batch)
    # combo — FK requires it. We'll copy from the first existing batch.
    existing_rows = {
        r[0]: r[1] for r in conn.execute(
            """SELECT report_id, MIN(batch) FROM raw_inventory WHERE project_id = ?
               GROUP BY report_id""", (project_id,)
        )
    }

    for i, rid in enumerate(rids, 1):
        try:
            resp = client.session.get(
                client._url(f"/model/reports/{rid}"),
                headers=client._headers(),
                params={"showExpressionAs": "tree"},
                verify=client.verify_ssl,
                timeout=60,
            )
            if resp.status_code != 200:
                conn.execute(
                    """INSERT OR REPLACE INTO report_fetch_errors
                       (project_id, report_id, endpoint, http_code, error_msg)
                       VALUES (?, ?, ?, ?, ?)""",
                    (project_id, rid, "/model/reports", resp.status_code,
                     resp.text[:500]),
                )
                err += 1
                if i % 50 == 0 or resp.status_code in (401, 500, 503):
                    print(f"  [{i}/{total}] {rid[:8]} HTTP {resp.status_code}")
                continue
            defn = resp.json()
            attrs = _extract_attributes_from_definition(defn)
            if not attrs:
                done += 1  # counted as processed; just no attributes to write
                continue

            # Need an existing raw_inventory row to satisfy FK. Use the first
            # existing batch; if missing, synthesize a minimal row under 'recovered'.
            use_batch = existing_rows.get(rid)
            if not use_batch:
                # Create a minimal raw_inventory row
                info = defn.get("information") or {}
                conn.execute(
                    """INSERT OR REPLACE INTO raw_inventory
                       (project_id, report_id, batch, name, object_type)
                       VALUES (?, ?, ?, ?, 3)""",
                    (project_id, rid, batch, info.get("name") or ""),
                )
                existing_rows[rid] = batch
                use_batch = batch

            # Write to raw_inventory_attribute AND report_attribute
            conn.executemany(
                """INSERT OR IGNORE INTO raw_inventory_attribute
                   (project_id, report_id, batch, attribute_id, attribute_name)
                   VALUES (?, ?, ?, ?, ?)""",
                [(project_id, rid, use_batch, aid, aname) for (aid, aname) in attrs],
            )
            conn.executemany(
                """INSERT OR IGNORE INTO report_attribute
                   (project_id, report_id, attribute_name) VALUES (?, ?, ?)""",
                [(project_id, rid, aname) for (_aid, aname) in attrs],
            )
            conn.execute(
                """UPDATE report SET attribute_count =
                    (SELECT COUNT(*) FROM report_attribute
                      WHERE project_id = report.project_id AND report_id = report.report_id)
                   WHERE project_id = ? AND report_id = ?""",
                (project_id, rid),
            )
            done += 1
            if i % 50 == 0 or i == total:
                conn.commit()
                print(f"  [{i}/{total}] {rid[:8]} {len(attrs)} attrs "
                      f"(done={done} err={err})")
            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)
        except Exception as e:
            conn.execute(
                """INSERT OR REPLACE INTO report_fetch_errors
                   (project_id, report_id, endpoint, http_code, error_msg)
                   VALUES (?, ?, ?, 0, ?)""",
                (project_id, rid, "/model/reports", str(e)[:500]),
            )
            err += 1

    conn.commit()
    print(f"  DONE  done={done} err={err}")
    return {"done": done, "err": err}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="all",
                    choices=list(PROJECT_GUIDS.keys()) + ["all"])
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap number of reports per project (for dry-run)")
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch even if attribute_count>0 or prior error exists")
    ap.add_argument("--sleep-ms", type=int, default=100,
                    help="Throttle between requests")
    ap.add_argument("--scope", choices=["final-kept", "post-ast", "active"],
                    default="final-kept",
                    help="Which reports to fetch. Default 'final-kept' = "
                         "similarity-cluster primaries + post-AST singletons (UI-visible set).")
    args = ap.parse_args()

    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    targets = (list(PROJECT_GUIDS.keys()) if args.project == "all"
               else [args.project])
    totals = {"done": 0, "err": 0}
    for tgt in targets:
        r = recover_for_project(conn, tgt, limit=args.limit, force=args.force,
                                 sleep_ms=args.sleep_ms, scope=args.scope)
        for k in totals:
            totals[k] += r.get(k, 0)
    print(f"\n=== TOTAL  done={totals['done']}  err={totals['err']} ===")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
