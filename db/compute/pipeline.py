"""Canonical Phase 3b compute pipeline.

ONE pipeline that works for all three projects. The only per-project input
is the config from db.db.PROJECTS (active_batches, retired_batches, etc.).

Stages (run in order):
  1. collisions  — telemetry-row collision collapse
  2. fingerprint — exact (metrics, tables, filters) match
  3. sql_hash    — sequential dedup on fingerprint canonicals by normalized SQL hash
  4. ast         — sequential dedup on sql-hash canonicals by AST Jaccard
  5. family      — name-pattern grouping on canonical reports
  6. similarity  — weighted Jaccard clustering on post-AST canonicals
  7. summary     — scalars + funnel + top-lists + tier breakdown + object types

Each stage reads/writes DB tables only. No project-specific branching.
"""
from __future__ import annotations
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.db import connect, init, get_project
from build_web_data_common import collapse_telemetry_collisions


THRESHOLD = 0.80

# ============================================================
# Shared helpers
# ============================================================

def _jaccard(a: set | frozenset, b: set | frozenset) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _metrics_by_id(conn, pid, rids: set[str]) -> dict[str, set[str]]:
    if not rids:
        return {}
    out: dict[str, set[str]] = defaultdict(set)
    rids_list = list(rids)
    for i in range(0, len(rids_list), 500):
        batch = rids_list[i : i + 500]
        placeholders = ",".join("?" * len(batch))
        for row in conn.execute(
            f"""SELECT report_id, metric_name FROM raw_inventory_metric
                 WHERE project_id = ? AND report_id IN ({placeholders})""",
            [pid, *batch],
        ):
            out[row[0]].add(row[1].lower())
    return dict(out)


def _tables_by_id(conn, pid, rids: set[str]) -> dict[str, set[str]]:
    if not rids:
        return {}
    out: dict[str, set[str]] = defaultdict(set)
    rids_list = list(rids)
    for i in range(0, len(rids_list), 500):
        batch = rids_list[i : i + 500]
        placeholders = ",".join("?" * len(batch))
        for row in conn.execute(
            f"""SELECT report_id, table_name FROM raw_inventory_table
                 WHERE project_id = ? AND report_id IN ({placeholders})""",
            [pid, *batch],
        ):
            out[row[0]].add(row[1].lower())
    return dict(out)


def _filters_by_id(conn, pid, rids: set[str]) -> dict[str, set[str]]:
    if not rids:
        return {}
    out: dict[str, set[str]] = defaultdict(set)
    rids_list = list(rids)
    for i in range(0, len(rids_list), 500):
        batch = rids_list[i : i + 500]
        placeholders = ",".join("?" * len(batch))
        for row in conn.execute(
            f"""SELECT report_id, filter_name FROM raw_inventory_filter
                 WHERE project_id = ? AND report_id IN ({placeholders})""",
            [pid, *batch],
        ):
            out[row[0]].add(row[1].lower())
    return dict(out)


def _sql_hash_by_id(conn, pid, rids: set[str]) -> dict[str, str]:
    if not rids:
        return {}
    out: dict[str, str] = {}
    rids_list = list(rids)
    for i in range(0, len(rids_list), 500):
        batch = rids_list[i : i + 500]
        placeholders = ",".join("?" * len(batch))
        for row in conn.execute(
            f"""SELECT report_id, sql_hash FROM raw_inventory_sql
                 WHERE project_id = ? AND report_id IN ({placeholders})
                   AND sql_hash IS NOT NULL AND sql_hash != ''""",
            [pid, *batch],
        ):
            out[row[0]] = row[1]
    return out


def _ast_hashes_by_id(conn, pid, rids: set[str]) -> dict[str, set[str]]:
    """Return {report_id: set of subtree hashes}. Only populated for reports
    that have ast_hash entries with kind='report' (GO's shape); returns empty
    dict for projects whose raw_ast_cache is keyed by ast_cluster hash."""
    if not rids:
        return {}
    out: dict[str, set[str]] = defaultdict(set)
    rids_list = list(rids)
    for i in range(0, len(rids_list), 500):
        batch = rids_list[i : i + 500]
        placeholders = ",".join("?" * len(batch))
        for row in conn.execute(
            f"""SELECT report_id, hash FROM ast_hash
                 WHERE project_id = ? AND report_id IN ({placeholders})""",
            [pid, *batch],
        ):
            out[row[0]].add(row[1])
    return dict(out)


def _telemetry_by_id(conn, pid, rids: set[str] | None = None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if rids is None:
        rows = conn.execute(
            """SELECT report_id, total_executions, total_users, last_exec_ts, match_tier
                 FROM raw_telemetry WHERE project_id = ? AND matched = 1""",
            (pid,),
        )
    else:
        rids_list = list(rids)
        rows_acc = []
        for i in range(0, len(rids_list), 500):
            batch = rids_list[i : i + 500]
            placeholders = ",".join("?" * len(batch))
            rows_acc.extend(conn.execute(
                f"""SELECT report_id, total_executions, total_users, last_exec_ts, match_tier
                     FROM raw_telemetry WHERE project_id = ? AND matched = 1
                       AND report_id IN ({placeholders})""",
                [pid, *batch],
            ).fetchall())
        rows = rows_acc
    for r in rows:
        out[r[0]] = {
            "totalExecutions": r[1] or 0, "totalUsers": r[2] or 0,
            "lastExecTs": r[3] or "", "matchTier": r[4] or "",
        }
    return out


def _record_by_id(conn, pid, rids: set[str]) -> dict[str, dict]:
    """Core inventory fields keyed by report_id (first batch wins when a
    report exists in multiple batches)."""
    if not rids:
        return {}
    out: dict[str, dict] = {}
    rids_list = list(rids)
    for i in range(0, len(rids_list), 500):
        batch = rids_list[i : i + 500]
        placeholders = ",".join("?" * len(batch))
        for r in conn.execute(
            f"""SELECT report_id, name, owner, folder_path, date_created, date_modified,
                       object_type, subtype, source_type
                  FROM raw_inventory
                 WHERE project_id = ? AND report_id IN ({placeholders})""",
            [pid, *batch],
        ):
            if r[0] not in out:  # first wins
                out[r[0]] = {
                    "id": r[0], "name": r[1] or "", "owner": r[2] or "",
                    "folderPath": r[3] or "", "dateCreated": r[4],
                    "dateModified": r[5], "type": r[6] or 3, "subtype": r[7],
                    "sourceType": r[8],
                }
    return out


def _family_base(name: str) -> str:
    parts = (name or "").rsplit(" - ", 1)
    if len(parts) == 2 and len(parts[0]) >= 10:
        return parts[0].strip()
    return (name or "").strip()


# ============================================================
# Stage 1: collision collapse
# ============================================================

def stage_collisions(conn, project_id: str) -> dict:
    cfg = get_project(project_id)
    batches = cfg["active_batches"]
    batch_placeholders = ",".join("?" * len(batches))

    # Active universe: raw_inventory rows in active batches with telemetry > 0
    rows = conn.execute(
        f"""SELECT ri.report_id, ri.name, ri.owner, ri.folder_path, ri.batch,
                   t.total_executions, t.last_exec_ts, t.match_tier
              FROM raw_inventory ri
              LEFT JOIN raw_telemetry t
                ON t.project_id = ri.project_id AND t.report_id = ri.report_id
             WHERE ri.project_id = ?
               AND ri.batch IN ({batch_placeholders})
               AND t.total_executions > 0""",
        [project_id, *batches],
    ).fetchall()

    # Dedup to first-occurrence per report_id (some projects repeat ids across batches)
    seen: set[str] = set()
    combined = []
    for r in rows:
        if r[0] in seen:
            continue
        seen.add(r[0])
        combined.append({
            "id": r[0], "name": r[1] or "", "owner": r[2] or "",
            "folderPath": r[3] or "",
            "_telemetry": {"executions": r[5] or 0, "lastExec": r[6] or "",
                           "tier": r[7] or ""},
        })
    active_lookup = _telemetry_by_id(conn, project_id)

    canonical, collision_groups, collapsed_count = collapse_telemetry_collisions(
        combined, active_lookup, collision_min=3, verbose=True,
    )

    # Write collision_group + collision_member (replace)
    with conn:
        conn.execute("DELETE FROM collision_member WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM collision_group WHERE project_id = ?", (project_id,))
        group_rows, member_rows = [], []
        for i, g in enumerate(collision_groups, 1):
            gid = f"COL{i:05d}"
            group_rows.append((
                project_id, gid, g.get("pass"),
                g.get("canonicalId"), g.get("canonicalName"), g.get("canonicalPath"),
                g.get("normalizedName"), g.get("executions"), g.get("lastExec"),
                g.get("size"), g.get("collapsed"),
            ))
            for m in g.get("members") or []:
                if m.get("id"):
                    member_rows.append((
                        project_id, gid, m["id"],
                        m.get("matchTier"), m.get("folderPath"), m.get("owner"),
                    ))
        if group_rows:
            conn.executemany(
                """INSERT INTO collision_group
                   (project_id, group_id, pass, canonical_id, canonical_name, canonical_path,
                    normalized_name, executions, last_exec, size, collapsed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                group_rows,
            )
        if member_rows:
            conn.executemany(
                """INSERT OR IGNORE INTO collision_member
                   (project_id, group_id, report_id, match_tier, folder_path, owner)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                member_rows,
            )

    canonical_ids = {r["id"] for r in canonical}
    return {
        "active_universe": len(combined),
        "canonical": len(canonical_ids),
        "collapsed": collapsed_count,
        "collision_groups": len(collision_groups),
    }


def _canonical_ids_after_collisions(conn, project_id: str) -> set[str]:
    """Rebuild the post-collision canonical set from DB (collision_group
    canonical_ids ∪ reports in the active universe that weren't in any group)."""
    cfg = get_project(project_id)
    batches = cfg["active_batches"]
    batch_placeholders = ",".join("?" * len(batches))
    active_ids = {
        r[0] for r in conn.execute(
            f"""SELECT DISTINCT ri.report_id
                  FROM raw_inventory ri
                  JOIN raw_telemetry t
                    ON t.project_id = ri.project_id AND t.report_id = ri.report_id
                 WHERE ri.project_id = ?
                   AND ri.batch IN ({batch_placeholders})
                   AND t.total_executions > 0""",
            [project_id, *batches],
        )
    }
    collapsed = {
        r[0] for r in conn.execute(
            """SELECT cm.report_id FROM collision_member cm
                JOIN collision_group cg
                  ON cg.project_id = cm.project_id AND cg.group_id = cm.group_id
               WHERE cm.project_id = ? AND cm.report_id != cg.canonical_id""",
            (project_id,),
        )
    }
    return active_ids - collapsed


# ============================================================
# Stage 2: fingerprint dedup (exact metric+table+filter match)
# ============================================================

def stage_fingerprint(conn, project_id: str) -> dict:
    """Fingerprint dedup — reports are duplicates iff they share the same
    (metrics, tables, filters) triple.

    Quality gate: reports with effectively-empty feature sets
    (total metrics + tables + filters < MIN_SIGNAL) cannot be reliably matched
    because their enrichment data is missing (MSTR /model/ endpoint failed
    for them). Skipping them prevents a "collapse-everything-to-empty-set"
    false positive that previously merged unrelated reports.
    """
    MIN_SIGNAL = 3  # require at least 3 total features across m + t + f

    canonical_ids = _canonical_ids_after_collisions(conn, project_id)
    metrics = _metrics_by_id(conn, project_id, canonical_ids)
    tables = _tables_by_id(conn, project_id, canonical_ids)
    filters = _filters_by_id(conn, project_id, canonical_ids)

    # Group by (frozenset metrics, tables, filters)
    fp_groups: dict[tuple, list[str]] = defaultdict(list)
    skipped_low_signal = 0
    for rid in canonical_ids:
        m = frozenset(metrics.get(rid, set()))
        t = frozenset(tables.get(rid, set()))
        f = frozenset(filters.get(rid, set()))
        if len(m) + len(t) + len(f) < MIN_SIGNAL:
            skipped_low_signal += 1
            continue  # don't group low-signal reports — they'd false-match
        fp_groups[(m, t, f)].append(rid)

    # Only emit multi-member groups (singletons are singletons by definition)
    multi = [(k, rids) for k, rids in fp_groups.items() if len(rids) >= 2]
    tel = _telemetry_by_id(conn, project_id, canonical_ids)

    with conn:
        conn.execute("DELETE FROM fingerprint_member WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM fingerprint_group WHERE project_id = ?", (project_id,))
        group_rows, member_rows = [], []
        for i, (key, rids) in enumerate(
            sorted(multi, key=lambda kv: (-len(kv[1]), kv[1][0])), 1
        ):
            gid = f"FP{i:04d}"
            mset, tset, fset = key
            total_exec = sum(tel.get(rid, {}).get("totalExecutions", 0) for rid in rids)
            group_rows.append((
                project_id, gid, len(rids), len(rids) - 1,
                len(mset), len(tset), len(fset), total_exec,
                json.dumps(sorted(mset)),
                json.dumps(sorted(tset)),
                json.dumps(sorted(fset)),
            ))
            for rid in rids:
                member_rows.append((project_id, gid, rid))
        if group_rows:
            conn.executemany(
                """INSERT INTO fingerprint_group
                   (project_id, group_id, size, reducible, metric_count, table_count, filter_count,
                    total_executions, metrics_json, tables_json, filters_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                group_rows,
            )
        if member_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO fingerprint_member (project_id, group_id, report_id) VALUES (?, ?, ?)",
                member_rows,
            )

    # fp canonical = the highest-execs member of each multi-group + all singletons
    tel = _telemetry_by_id(conn, project_id, canonical_ids)
    fp_canonical_ids = set()
    collapsed = set()
    for (key, rids) in multi:
        best = max(rids, key=lambda rid: tel.get(rid, {}).get("totalExecutions", 0))
        fp_canonical_ids.add(best)
        for rid in rids:
            if rid != best:
                collapsed.add(rid)
    fp_canonical_ids |= (canonical_ids - collapsed - fp_canonical_ids)

    return {
        "canonical_in": len(canonical_ids),
        "skipped_low_signal": skipped_low_signal,
        "fp_multi_groups": len(multi),
        "fp_total_groups": len(fp_groups),
        "fp_canonical": len(fp_canonical_ids),
    }


def _fp_canonical_ids(conn, project_id: str) -> set[str]:
    """Rebuild fingerprint canonical set = collision canonicals that are
    either not in any fingerprint group OR are the top-execs member of one."""
    canonical_ids = _canonical_ids_after_collisions(conn, project_id)
    # Reports in a multi fingerprint group:
    fp_member_ids = {
        r[0] for r in conn.execute(
            "SELECT report_id FROM fingerprint_member WHERE project_id = ?",
            (project_id,),
        )
    }
    # For each fp group pick the top-execs member as canonical.
    fp_canon: set[str] = set()
    for g in conn.execute(
        "SELECT group_id FROM fingerprint_group WHERE project_id = ?", (project_id,)
    ):
        group_id = g[0]
        member_rows = conn.execute(
            """SELECT fm.report_id, COALESCE(t.total_executions, 0)
                 FROM fingerprint_member fm
                 LEFT JOIN raw_telemetry t
                   ON t.project_id = fm.project_id AND t.report_id = fm.report_id
                WHERE fm.project_id = ? AND fm.group_id = ?""",
            (project_id, group_id),
        ).fetchall()
        if not member_rows:
            continue
        best = max(member_rows, key=lambda r: r[1])[0]
        fp_canon.add(best)
    return fp_canon | (canonical_ids - fp_member_ids)


# ============================================================
# Stage 3: sequential SQL hash dedup on fp canonicals
# ============================================================

def stage_sql_hash(conn, project_id: str) -> dict:
    """SQL hash grouping at post-collision level (emitted to UI as
    sql_hash_groups.json), plus sequential-dedup scalar computed against
    fingerprint canonicals.

    The sql_hash_group / sql_hash_member tables hold the VIEW-level
    groupings — all post-collision canonicals with identical normalized
    SQL. The sequential dedup impact for the funnel is derived from the
    intersection with fingerprint canonicals.
    """
    post_collision = _canonical_ids_after_collisions(conn, project_id)
    fp_canon = _fp_canonical_ids(conn, project_id)
    sql_hashes = _sql_hash_by_id(conn, project_id, post_collision)

    groups: dict[str, list[str]] = defaultdict(list)
    for rid, h in sql_hashes.items():
        groups[h].append(rid)
    multi = [(h, rids) for h, rids in groups.items() if len(rids) >= 2]

    tel = _telemetry_by_id(conn, project_id, post_collision)
    records = _record_by_id(conn, project_id, post_collision)

    # Grab SQL preview (first 300 chars of any member's SQL)
    sql_preview_by_hash: dict[str, str] = {}
    rids_in_multi = {rid for _, rids in multi for rid in rids}
    if rids_in_multi:
        rids_list = list(rids_in_multi)
        for i in range(0, len(rids_list), 500):
            batch = rids_list[i : i + 500]
            placeholders = ",".join("?" * len(batch))
            for row in conn.execute(
                f"""SELECT sql_hash, sql_text FROM raw_inventory_sql
                     WHERE project_id = ? AND report_id IN ({placeholders})
                       AND sql_hash IS NOT NULL""",
                [project_id, *batch],
            ):
                h = row[0]
                if h and h not in sql_preview_by_hash and row[1]:
                    sql_preview_by_hash[h] = row[1][:300]

    with conn:
        conn.execute("DELETE FROM sql_hash_member WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM sql_hash_group WHERE project_id = ?", (project_id,))
        group_rows, member_rows = [], []
        for i, (h, rids) in enumerate(
            sorted(multi, key=lambda kv: (-len(kv[1]), kv[1][0])), 1
        ):
            gid = f"H{i:04d}"
            total_exec = sum(tel.get(rid, {}).get("totalExecutions", 0) for rid in rids)
            group_rows.append((
                project_id, gid, h, sql_preview_by_hash.get(h, ""),
                len(rids), len(rids) - 1, total_exec,
            ))
            for rid in rids:
                member_rows.append((project_id, gid, rid))
        if group_rows:
            conn.executemany(
                """INSERT INTO sql_hash_group
                   (project_id, group_id, sql_hash, sql_preview, size, reducible, total_executions)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                group_rows,
            )
        if member_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO sql_hash_member (project_id, group_id, report_id) VALUES (?, ?, ?)",
                member_rows,
            )

    # Sequential impact: among the fp canonicals, how many would be collapsed
    # by SQL-hash grouping? (Count of fp canonicals that share a hash with
    # another fp canonical.)
    fp_hash_groups: dict[str, list[str]] = defaultdict(list)
    for rid in fp_canon:
        h = sql_hashes.get(rid)
        if h:
            fp_hash_groups[h].append(rid)
    fp_multi = [(h, rids) for h, rids in fp_hash_groups.items() if len(rids) >= 2]
    sequential_removable = sum(len(rids) - 1 for _, rids in fp_multi)

    return {
        "post_collision_in": len(post_collision),
        "fp_canonical_in": len(fp_canon),
        "sql_hash_multi_groups": len(multi),
        "sql_hash_sequential_groups": len(fp_multi),
        "sql_hash_sequential_removable": sequential_removable,
        "sql_hash_canonical": len(fp_canon) - sequential_removable,
    }


def _sql_hash_canonical_ids(conn, project_id: str) -> set[str]:
    """fp_canonical minus the collapsed-out members of each sql_hash group."""
    fp_canon = _fp_canonical_ids(conn, project_id)
    collapsed: set[str] = set()
    for g in conn.execute(
        "SELECT group_id FROM sql_hash_group WHERE project_id = ?", (project_id,)
    ):
        group_id = g[0]
        member_rows = conn.execute(
            """SELECT shm.report_id, COALESCE(t.total_executions, 0)
                 FROM sql_hash_member shm
                 LEFT JOIN raw_telemetry t
                   ON t.project_id = shm.project_id AND t.report_id = shm.report_id
                WHERE shm.project_id = ? AND shm.group_id = ?""",
            (project_id, group_id),
        ).fetchall()
        if not member_rows:
            continue
        best = max(member_rows, key=lambda r: r[1])[0]
        for (rid, _) in member_rows:
            if rid != best:
                collapsed.add(rid)
    return fp_canon - collapsed


# ============================================================
# Stage 4: sequential AST dedup on sql-hash canonicals
# ============================================================

def stage_ast(conn, project_id: str) -> dict:
    sh_canon = _sql_hash_canonical_ids(conn, project_id)

    # Compute AST hashes for EVERY report that has SQL (matches legacy scope).
    # Clustering downstream operates on the sh_canon subset, but the ast_hash
    # table stores the full corpus so emit's ast_hashes.json reflects reality.
    existing = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT report_id FROM ast_hash WHERE project_id = ?",
            (project_id,),
        )
    }
    to_compute = [
        r[0] for r in conn.execute(
            """SELECT DISTINCT report_id FROM raw_inventory_sql
                WHERE project_id = ? AND sql_text IS NOT NULL AND sql_text != ''""",
            (project_id,),
        ) if r[0] not in existing
    ]
    if to_compute:
        sql_rows: list[tuple[str, str]] = []
        for i in range(0, len(to_compute), 500):
            b = to_compute[i : i + 500]
            ph = ",".join("?" * len(b))
            for row in conn.execute(
                f"""SELECT report_id, sql_text FROM raw_inventory_sql
                     WHERE project_id = ? AND report_id IN ({ph})""",
                [project_id, *b],
            ):
                sql_rows.append((row[0], row[1]))
        if sql_rows:
            from db.compute import sql_ast_sim as _ast_sim
            ast_input = [{"id": rid, "sql": sql} for rid, sql in sql_rows]
            computed_hashes, _errs = _ast_sim.compute_ast_hashes(
                ast_input, dialect="redshift", verbose=False,
            )
            insert_rows = []
            for rid, hashes in computed_hashes.items():
                for h in hashes:
                    insert_rows.append((project_id, rid, h))
            if insert_rows:
                with conn:
                    conn.executemany(
                        "INSERT OR IGNORE INTO ast_hash (project_id, report_id, hash) VALUES (?, ?, ?)",
                        insert_rows,
                    )
            print(f"    (computed AST hashes for {len(computed_hashes)} reports)")

    # Now load the sh_canon subset for clustering.
    ast = _ast_hashes_by_id(conn, project_id, sh_canon)
    ids_with_sql = [rid for rid in sh_canon if rid in ast]

    adj: dict[str, set[str]] = defaultdict(set)
    for i in range(len(ids_with_sql)):
        a = ast[ids_with_sql[i]]
        for j in range(i + 1, len(ids_with_sql)):
            if _jaccard(a, ast[ids_with_sql[j]]) >= THRESHOLD:
                adj[ids_with_sql[i]].add(ids_with_sql[j])
                adj[ids_with_sql[j]].add(ids_with_sql[i])

    visited: set[str] = set()
    components: list[list[str]] = []
    for start in ids_with_sql:
        if start in visited:
            continue
        stack = [start]
        comp: list[str] = []
        while stack:
            x = stack.pop()
            if x in visited:
                continue
            visited.add(x)
            comp.append(x)
            stack.extend(adj[x] - visited)
        components.append(comp)
    multi = [c for c in components if len(c) >= 2]

    tel = _telemetry_by_id(conn, project_id, set(ids_with_sql))

    # Store scores per cluster
    with conn:
        conn.execute("DELETE FROM ast_cluster_member WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM ast_cluster WHERE project_id = ?", (project_id,))
        group_rows, member_rows = [], []
        for i, comp in enumerate(
            sorted(multi, key=lambda c: (-len(c), c[0])), 1
        ):
            cid = f"AST{i:04d}"
            # All-exact iff every member's ast hash set is identical
            all_exact = len({frozenset(ast[rid]) for rid in comp}) == 1
            # Average pairwise similarity
            if len(comp) > 1:
                pair_sims = []
                for x in range(len(comp)):
                    for y in range(x + 1, len(comp)):
                        pair_sims.append(_jaccard(ast[comp[x]], ast[comp[y]]))
                avg = sum(pair_sims) / len(pair_sims)
                mn = min(pair_sims)
            else:
                avg = mn = 1.0
            total_exec = sum(tel.get(rid, {}).get("totalExecutions", 0) for rid in comp)
            group_rows.append((
                project_id, cid, len(comp), len(comp) - 1,
                1 if all_exact else 0,
                round(avg, 3), round(mn, 3),
                total_exec,
                0,  # in_sql_hash (not computed here)
                0,  # ast_exclusive
            ))
            for rid in comp:
                member_rows.append((project_id, cid, rid))
        if group_rows:
            conn.executemany(
                """INSERT INTO ast_cluster
                   (project_id, cluster_id, size, reducible, all_exact, avg_score, min_score,
                    total_executions, in_sql_hash, ast_exclusive)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                group_rows,
            )
        if member_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO ast_cluster_member (project_id, cluster_id, report_id) VALUES (?, ?, ?)",
                member_rows,
            )

    return {
        "sh_canonical_in": len(sh_canon),
        "with_ast": len(ids_with_sql),
        "ast_multi_clusters": len(multi),
    }


def _ast_canonical_ids(conn, project_id: str) -> set[str]:
    sh_canon = _sql_hash_canonical_ids(conn, project_id)
    collapsed: set[str] = set()
    for g in conn.execute(
        "SELECT cluster_id FROM ast_cluster WHERE project_id = ?", (project_id,)
    ):
        cid = g[0]
        member_rows = conn.execute(
            """SELECT acm.report_id, COALESCE(t.total_executions, 0)
                 FROM ast_cluster_member acm
                 LEFT JOIN raw_telemetry t
                   ON t.project_id = acm.project_id AND t.report_id = acm.report_id
                WHERE acm.project_id = ? AND acm.cluster_id = ?""",
            (project_id, cid),
        ).fetchall()
        if not member_rows:
            continue
        best = max(member_rows, key=lambda r: r[1])[0]
        for (rid, _) in member_rows:
            if rid != best:
                collapsed.add(rid)
    return sh_canon - collapsed


# ============================================================
# Stage 5: family grouping
# ============================================================

def stage_family(conn, project_id: str) -> dict:
    """Family collapse on the post-AST canonical set.

    Groups post-AST survivors by family_base. Each multi-member family elects
    a canonical (highest executions); the rest are excluded from downstream
    clustering input (Similarity + Semantic).
    """
    canonical_ids = _ast_canonical_ids(conn, project_id)
    records = _record_by_id(conn, project_id, canonical_ids)
    tel = _telemetry_by_id(conn, project_id, canonical_ids)

    families: dict[str, list[str]] = defaultdict(list)
    for rid in canonical_ids:
        name = records.get(rid, {}).get("name", "")
        base = _family_base(name)
        if not base:
            continue
        families[base].append(rid)
    multi = {b: rids for b, rids in families.items() if len(rids) >= 2}

    with conn:
        conn.execute("DELETE FROM family_member WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM family WHERE project_id = ?", (project_id,))
        fam_rows, fam_member_rows = [], []
        for base, rids in sorted(multi.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            canon = max(rids, key=lambda r: tel.get(r, {}).get("totalExecutions", 0))
            total_exec = sum(tel.get(rid, {}).get("totalExecutions", 0) for rid in rids)
            fam_rows.append((
                project_id, base, len(rids), max(0, len(rids) - 1), total_exec, canon,
            ))
            for rid in rids:
                name = records.get(rid, {}).get("name", "")
                suffix = name[len(base):].lstrip(" -") if name.startswith(base) else ""
                fam_member_rows.append((
                    project_id, base, rid, suffix, 1 if rid == canon else 0,
                ))
        if fam_rows:
            conn.executemany(
                """INSERT INTO family
                   (project_id, base, size, reducible, total_executions, canonical_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                fam_rows,
            )
        if fam_member_rows:
            conn.executemany(
                """INSERT OR IGNORE INTO family_member
                   (project_id, base, report_id, variant_suffix, is_canonical)
                   VALUES (?, ?, ?, ?, ?)""",
                fam_member_rows,
            )

    collapsed = sum(len(rids) - 1 for rids in multi.values())
    return {
        "families_multi": len(multi),
        "after_family": len(canonical_ids) - collapsed,
        "family_collapsed": collapsed,
    }


def _post_ast_family_canonical_ids(conn, project_id: str) -> set[str]:
    """Post-AST survivors minus non-canonical family members. Family data
    lives in `family` / `family_member` — stage_family runs on the post-AST
    set and marks the elected canonical for each multi-member family."""
    ast_canon = _ast_canonical_ids(conn, project_id)
    collapsed = {
        r[0] for r in conn.execute(
            """SELECT report_id FROM family_member
               WHERE project_id = ? AND is_canonical = 0""",
            (project_id,),
        )
    }
    return ast_canon - collapsed


# ============================================================
# Stage 6: similarity clustering on post-AST-family canonicals
# ============================================================

def stage_similarity(conn, project_id: str) -> dict:
    ast_canon = _post_ast_family_canonical_ids(conn, project_id)
    # Load feature sets
    metrics = _metrics_by_id(conn, project_id, ast_canon)
    tables = _tables_by_id(conn, project_id, ast_canon)
    filters = _filters_by_id(conn, project_id, ast_canon)
    ast = _ast_hashes_by_id(conn, project_id, ast_canon)
    tel = _telemetry_by_id(conn, project_id, ast_canon)

    ids = list(ast_canon)
    adj: dict[str, set[str]] = defaultdict(set)
    pairs: dict[tuple[str, str], dict] = {}
    for i in range(len(ids)):
        id_i = ids[i]
        m1, t1, f1, a1 = metrics.get(id_i, set()), tables.get(id_i, set()), filters.get(id_i, set()), ast.get(id_i)
        for j in range(i + 1, len(ids)):
            id_j = ids[j]
            m2, t2, f2, a2 = metrics.get(id_j, set()), tables.get(id_j, set()), filters.get(id_j, set()), ast.get(id_j)
            m_sim = _jaccard(m1, m2)
            t_sim = _jaccard(t1, t2)
            f_sim = _jaccard(f1, f2)
            bag_sim = 0.4 * m_sim + 0.4 * t_sim + 0.2 * f_sim
            if a1 and a2:
                ast_sim = _jaccard(a1, a2)
                s = 0.6 * ast_sim + 0.25 * m_sim + 0.15 * t_sim
            else:
                ast_sim = None
                s = bag_sim
            if s >= THRESHOLD:
                adj[id_i].add(id_j)
                adj[id_j].add(id_i)
                pair = {
                    "combined": round(s, 3),
                    "metric": round(m_sim, 3),
                    "table": round(t_sim, 3),
                    "filter": round(f_sim, 3),
                }
                if ast_sim is not None:
                    pair["ast"] = round(ast_sim, 3)
                pairs[(id_i, id_j)] = pair

    visited: set[str] = set()
    components: list[list[str]] = []
    for start in ids:
        if start in visited:
            continue
        stack = [start]
        comp: list[str] = []
        while stack:
            x = stack.pop()
            if x in visited:
                continue
            visited.add(x)
            comp.append(x)
            stack.extend(adj[x] - visited)
        components.append(comp)
    multi = sorted([c for c in components if len(c) >= 2], key=lambda c: -len(c))

    records = _record_by_id(conn, project_id, ast_canon)

    with conn:
        conn.execute("DELETE FROM similarity_cluster_common WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM similarity_cluster_member WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM similarity_cluster WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM similarity_pair WHERE project_id = ?", (project_id,))
        sc_rows, sm_rows, sc_common = [], [], []

        for i, comp in enumerate(multi, 1):
            cid = f"C{i:03d}"
            primary = max(
                comp,
                key=lambda rid: tel.get(rid, {}).get("totalExecutions", 0),
            )
            primary_name = records.get(primary, {}).get("name", "")
            tot_exec = sum(tel.get(rid, {}).get("totalExecutions", 0) for rid in comp)
            tot_users = len({rid for rid in comp if tel.get(rid, {}).get("totalUsers", 0) > 0})
            common_m: set[str] = set()
            common_t: set[str] = set()
            common_f: set[str] = set()
            for idx, rid in enumerate(comp):
                rm = metrics.get(rid, set())
                rt = tables.get(rid, set())
                rf = filters.get(rid, set())
                if idx == 0:
                    common_m = set(rm); common_t = set(rt); common_f = set(rf)
                else:
                    common_m &= rm; common_t &= rt; common_f &= rf
            # Compute avg similarity inside component
            pair_ss = []
            for x in range(len(comp)):
                for y in range(x + 1, len(comp)):
                    key = (comp[x], comp[y]) if (comp[x], comp[y]) in pairs else (comp[y], comp[x])
                    if key in pairs:
                        pair_ss.append(pairs[key]["combined"])
            avg_sim = round(sum(pair_ss) / len(pair_ss), 3) if pair_ss else None
            sc_rows.append((
                project_id, cid, len(comp), primary, primary_name,
                tot_exec, tot_users,
                len(common_m), len(common_t), len(common_f), avg_sim,
                None, None, None,
            ))
            for rid in comp:
                sm_rows.append((project_id, cid, rid))
            for m in sorted(common_m):
                sc_common.append((project_id, cid, "metric", m))
            for t in sorted(common_t):
                sc_common.append((project_id, cid, "table", t))
            for f in sorted(common_f):
                sc_common.append((project_id, cid, "filter", f))

        if sc_rows:
            conn.executemany(
                """INSERT INTO similarity_cluster
                   (project_id, cluster_id, size, primary_report_id, primary_name,
                    total_executions, total_users, metric_count, table_count, filter_count,
                    avg_similarity, llm_label, llm_action, llm_removable)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                sc_rows,
            )
        if sm_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO similarity_cluster_member (project_id, cluster_id, report_id) VALUES (?, ?, ?)",
                sm_rows,
            )
        if sc_common:
            conn.executemany(
                "INSERT OR IGNORE INTO similarity_cluster_common (project_id, cluster_id, kind, name) VALUES (?, ?, ?, ?)",
                sc_common,
            )

        pair_rows = [
            (project_id, a, b, p["combined"], p["metric"], p["table"], p["filter"], p.get("ast"))
            for (a, b), p in pairs.items()
        ]
        if pair_rows:
            conn.executemany(
                """INSERT INTO similarity_pair
                   (project_id, id_a, id_b, combined, metric, tables, filters, ast)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                pair_rows,
            )

    return {
        "ast_canonical_in": len(ast_canon),
        "components": len(components),
        "multi_clusters": len(multi),
        "pairs": len(pairs),
    }


# ============================================================
# Stage 7: summary + funnel + top lists
# ============================================================

def stage_summary(conn, project_id: str) -> dict:
    cfg = get_project(project_id)

    # Scalars
    active_uni = conn.execute(
        f"""SELECT COUNT(DISTINCT ri.report_id)
              FROM raw_inventory ri
              JOIN raw_telemetry t
                ON t.project_id = ri.project_id AND t.report_id = ri.report_id
             WHERE ri.project_id = ?
               AND ri.batch IN ({",".join("?"*len(cfg["active_batches"]))})
               AND t.total_executions > 0""",
        [project_id, *cfg["active_batches"]],
    ).fetchone()[0]

    canonical = len(_canonical_ids_after_collisions(conn, project_id))
    fp_canonical = len(_fp_canonical_ids(conn, project_id))
    sh_canonical = len(_sql_hash_canonical_ids(conn, project_id))
    ast_canonical = len(_ast_canonical_ids(conn, project_id))
    paf_canonical = len(_post_ast_family_canonical_ids(conn, project_id))
    paf_collapsed = ast_canonical - paf_canonical

    cluster_count = conn.execute(
        "SELECT COUNT(*) FROM similarity_cluster WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    multi_members = conn.execute(
        "SELECT COUNT(*) FROM similarity_cluster_member WHERE project_id = ?",
        (project_id,),
    ).fetchone()[0]
    # after_similarity = post-AST-family canonicals not in any multi cluster, plus one-per-cluster
    after_similarity = paf_canonical - (multi_members - cluster_count) if multi_members > cluster_count else paf_canonical

    total_retired = conn.execute(
        """SELECT COUNT(DISTINCT ri.report_id)
             FROM raw_inventory ri
            WHERE ri.project_id = ?
              AND (ri.batch IN ('new_retire', 'added_retire')
                   OR EXISTS (SELECT 1 FROM raw_telemetry t
                                WHERE t.project_id = ri.project_id
                                  AND t.report_id = ri.report_id
                                  AND t.matched = 0))""",
        (project_id,),
    ).fetchone()[0]
    total_inventory = conn.execute(
        """SELECT COUNT(DISTINCT report_id) FROM raw_inventory WHERE project_id = ?""",
        (project_id,),
    ).fetchone()[0]

    collision_collapsed = active_uni - canonical
    fp_removable = canonical - fp_canonical
    sh_removable = fp_canonical - sh_canonical
    ast_removable = sh_canonical - ast_canonical
    sim_removable = paf_canonical - after_similarity
    reduction_total = total_inventory - after_similarity
    reduction_pct = round((reduction_total / total_inventory) * 100, 1) if total_inventory else 0.0

    with conn:
        conn.execute(
            """INSERT OR REPLACE INTO run_summary (
                project_id, total_inventory, after_telemetry, retired,
                after_collision_collapse, collision_collapsed,
                after_fingerprint, fingerprint_groups, fingerprint_multi_groups,
                fingerprint_reports_in_groups, fingerprint_removable,
                after_sql_hash, sql_hash_sequential_removable, sql_hash_sequential_groups,
                after_ast, ast_sequential_removable, ast_sequential_groups,
                after_family, family_reducible,
                after_post_ast_family, post_ast_family_collapsed,
                after_similarity, clusters_total, clusters_multi, clusters_singleton,
                reduction_total, reduction_pct
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id, total_inventory, active_uni, total_retired,
                canonical, collision_collapsed,
                fp_canonical,
                conn.execute("SELECT COUNT(*) FROM fingerprint_group WHERE project_id=?", (project_id,)).fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM fingerprint_group WHERE project_id=? AND size>=2", (project_id,)).fetchone()[0],
                conn.execute("SELECT COALESCE(SUM(size),0) FROM fingerprint_group WHERE project_id=?", (project_id,)).fetchone()[0],
                fp_removable,
                sh_canonical, sh_removable,
                conn.execute("SELECT COUNT(*) FROM sql_hash_group WHERE project_id=?", (project_id,)).fetchone()[0],
                ast_canonical, ast_removable,
                conn.execute("SELECT COUNT(*) FROM ast_cluster WHERE project_id=?", (project_id,)).fetchone()[0],
                # after_family / family_reducible — unified family stage on post-AST
                paf_canonical, paf_collapsed,
                # after_post_ast_family — same value, kept for backwards compat
                paf_canonical, paf_collapsed,
                after_similarity, cluster_count, cluster_count,
                max(0, after_similarity - cluster_count),
                reduction_total, reduction_pct,
            ),
        )

        # Funnel (7 stages)
        conn.execute("DELETE FROM funnel_stage WHERE project_id = ?", (project_id,))
        funnel = [
            ("Original Inventory", total_inventory, f"All reports enumerated"),
            ("After Telemetry Retirement", active_uni, f"{total_retired} reports with no usage removed"),
            ("After Collision Collapse", canonical, f"{collision_collapsed} reports collapsed by telemetry-row signature"),
            ("After Fingerprint Dedup", fp_canonical, f"{fp_removable} exact-fingerprint duplicates removed"),
            ("After SQL Hash Dedup", sh_canonical, f"{sh_removable} byte-identical-SQL duplicates removed"),
            ("After AST Dedup", ast_canonical, f"{ast_removable} AST-structural duplicates removed"),
            ("After Family Collapse", paf_canonical, f"{paf_collapsed} name-sibling duplicates collapsed"),
            ("After Similarity Clustering", after_similarity, f"{sim_removable} reports grouped into {cluster_count} clusters"),
        ]
        for i, (label, value, detail) in enumerate(funnel):
            conn.execute(
                "INSERT INTO funnel_stage (project_id, ordinal, label, value, detail) VALUES (?, ?, ?, ?, ?)",
                (project_id, i, label, value, detail),
            )

        # Object types from raw_object_type
        conn.execute("DELETE FROM object_type_count WHERE project_id = ?", (project_id,))
        for r in conn.execute(
            "SELECT category, count FROM raw_object_type WHERE project_id = ?", (project_id,)
        ).fetchall():
            conn.execute(
                "INSERT INTO object_type_count (project_id, name, count) VALUES (?, ?, ?)",
                (project_id, r[0], r[1]),
            )

        # tier_breakdown from raw_telemetry
        conn.execute("DELETE FROM tier_breakdown WHERE project_id = ?", (project_id,))
        tier_rows = conn.execute(
            """SELECT match_tier, COUNT(*) FROM raw_telemetry
                WHERE project_id = ? AND matched = 1
                GROUP BY match_tier""",
            (project_id,),
        ).fetchall()
        exact = fuzzy = 0
        for t, n in tier_rows:
            tl = (t or "").lower()
            if tl.startswith("exact"):
                exact += n
            elif tl.startswith("fuzzy"):
                fuzzy += n
        conn.execute("INSERT INTO tier_breakdown (project_id, tier, count) VALUES (?, ?, ?)",
                     (project_id, "exact", exact))
        conn.execute("INSERT INTO tier_breakdown (project_id, tier, count) VALUES (?, ?, ?)",
                     (project_id, "fuzzy", fuzzy))
        conn.execute("INSERT INTO tier_breakdown (project_id, tier, count) VALUES (?, ?, ?)",
                     (project_id, "noMatch", total_retired))

        # Top lists
        conn.execute("DELETE FROM top_list WHERE project_id = ?", (project_id,))
        # top metrics/tables/filters: aggregated over canonical reports
        for kind, table, col in (
            ("metric", "raw_inventory_metric", "metric_name"),
            ("table", "raw_inventory_table", "table_name"),
            ("filter", "raw_inventory_filter", "filter_name"),
        ):
            rows = conn.execute(
                f"""SELECT {col}, COUNT(*) n FROM {table}
                    WHERE project_id = ?
                    GROUP BY {col} ORDER BY n DESC LIMIT 15""",
                (project_id,),
            ).fetchall()
            for rank, (name, count) in enumerate(rows, 1):
                conn.execute(
                    """INSERT INTO top_list (project_id, kind, rank, name, count)
                       VALUES (?, ?, ?, ?, ?)""",
                    (project_id, kind, rank, name, count),
                )
        # top executed: top 20 telemetry rows
        exec_rows = conn.execute(
            """SELECT t.report_id, ri.name, t.total_executions, t.total_users, t.last_exec_ts
                 FROM raw_telemetry t
                 LEFT JOIN raw_inventory ri
                   ON ri.project_id = t.project_id AND ri.report_id = t.report_id
                WHERE t.project_id = ? AND t.matched = 1
                ORDER BY t.total_executions DESC LIMIT 20""",
            (project_id,),
        ).fetchall()
        for rank, (rid, name, execs, users, lx) in enumerate(exec_rows, 1):
            conn.execute(
                """INSERT INTO top_list (project_id, kind, rank, name, report_id, executions, users, last_exec)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (project_id, "executed", rank, name or "", rid, execs or 0, users or 0, lx or ""),
            )

    return {
        "total_inventory": total_inventory,
        "after_similarity": after_similarity,
        "reduction_pct": reduction_pct,
    }


# ============================================================
# Orchestrator
# ============================================================

# ============================================================
# Stage 8: LLM reviews import (not computed — loaded from project file)
# ============================================================

def stage_llm_reviews(conn, project_id: str) -> dict:
    """Import LLM cluster reviews from any available project source:
      - public/data/<project>/llm_reviews.json  (canonical, already in UI format)
      - <project>/llm_reviews_cache.json       (legacy GO cache)
      - <project>/sql/cluster_reviews.json     (GI/INSIGHT offline review file)

    Writes to llm_review and llm_action_count tables.
    """
    from db.db import REPO_ROOT
    cfg = get_project(project_id)
    data_dir = cfg["data_dir"]

    reviews: list[dict] = []
    # 1) Canonical file, if present
    p = data_dir / "llm_reviews.json"
    if p.exists():
        import json as _json
        raw = _json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            reviews = raw

    # 2) GI/INSIGHT cluster_reviews.json (offline review shape)
    if not reviews:
        project_source_dir = REPO_ROOT / cfg["name"]
        alt_paths = [
            project_source_dir / "sql" / "cluster_reviews.json",
            REPO_ROOT / "INSIGHT" / "rationalization" / "sql" / "cluster_reviews.json"
                if project_id == "insight" else None,
        ]
        for alt in alt_paths:
            if alt and alt.exists():
                import json as _json
                data = _json.loads(alt.read_text(encoding="utf-8"))
                rvs = data.get("reviews", []) if isinstance(data, dict) else data
                # Flatten to canonical shape
                for r in rvs:
                    analysis = r.get("analysis") or {}
                    cid_raw = r.get("clusterId")
                    reviews.append({
                        "clusterId": f"C{cid_raw}" if cid_raw is not None else "",
                        "consolidation_action": analysis.get("action", ""),
                        "confidence": analysis.get("confidence", ""),
                        "relationship": analysis.get("relationship", ""),
                        "label": analysis.get("label", ""),
                        "business_function": analysis.get("business_function", ""),
                        "consolidation_detail": analysis.get("consolidation_detail", ""),
                        "keep_report": analysis.get("keep_report", ""),
                        "removable_count": analysis.get("removable_count", 0),
                    })
                break

    # Write
    with conn:
        conn.execute("DELETE FROM llm_review WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM llm_action_count WHERE project_id = ?", (project_id,))
        action_counts: dict[str, int] = {}
        rows = []
        for r in reviews:
            cid = r.get("clusterId")
            if not cid:
                continue
            rows.append((
                project_id, cid,
                r.get("label", ""), r.get("business_function", ""),
                r.get("relationship", ""),
                r.get("consolidation_action", ""), r.get("confidence", ""),
                r.get("consolidation_detail", ""), r.get("keep_report", ""),
                r.get("removable_count", 0) or 0,
            ))
            a = r.get("consolidation_action") or "UNKNOWN"
            action_counts[a] = action_counts.get(a, 0) + 1
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO llm_review
                   (project_id, cluster_id, label, business_function, relationship,
                    action, confidence, consolidation_detail, keep_report, removable_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        for action, n in action_counts.items():
            conn.execute(
                "INSERT OR REPLACE INTO llm_action_count (project_id, action, count) VALUES (?, ?, ?)",
                (project_id, action, n),
            )

    return {"reviews_loaded": len(rows), "actions": action_counts}


# ============================================================
# Stage 0 / 9: populate the report table from raw data
#
# This stage populates the main `report` table (and the
# report_metric/table/filter/sql + telemetry_match + retired_report tables)
# from the raw_* tables. It runs AFTER compute stages so it can stamp each
# report with its status (active / collapsed / retired / dossier) and
# cluster_id. emit.py reads from these tables to produce the UI JSON.
# ============================================================

def stage_reports(conn, project_id: str) -> dict:
    """Populate report + report_* + telemetry_match + retired_report + ast_hash
    from the raw_* tables and the just-computed derived data."""
    cfg = get_project(project_id)

    # Classification maps
    active_universe = {
        r[0] for r in conn.execute(
            f"""SELECT DISTINCT ri.report_id FROM raw_inventory ri
                  JOIN raw_telemetry t
                    ON t.project_id = ri.project_id AND t.report_id = ri.report_id
                 WHERE ri.project_id = ?
                   AND ri.batch IN ({",".join("?"*len(cfg["active_batches"]))})
                   AND t.total_executions > 0""",
            [project_id, *cfg["active_batches"]],
        )
    }
    collision_canonical = {
        r[0] for r in conn.execute(
            "SELECT canonical_id FROM collision_group WHERE project_id = ?",
            (project_id,),
        )
    }
    collision_members_non_canon = {
        r[0] for r in conn.execute(
            """SELECT cm.report_id FROM collision_member cm
                JOIN collision_group cg
                  ON cg.project_id = cm.project_id AND cg.group_id = cm.group_id
               WHERE cm.project_id = ? AND cm.report_id != cg.canonical_id""",
            (project_id,),
        )
    }
    retired_from_telemetry = {
        r[0] for r in conn.execute(
            "SELECT report_id FROM raw_telemetry WHERE project_id = ? AND matched = 0",
            (project_id,),
        )
    }
    dossier_batches = cfg.get("dossier_batches", [])
    dossier_ids: set[str] = set()
    if dossier_batches:
        dossier_ids = {
            r[0] for r in conn.execute(
                f"""SELECT report_id FROM raw_inventory
                     WHERE project_id = ? AND batch IN ({",".join("?"*len(dossier_batches))})""",
                [project_id, *dossier_batches],
            )
        }
    retired_batches = cfg.get("retired_batches", [])
    retired_from_batch: set[str] = set()
    if retired_batches:
        retired_from_batch = {
            r[0] for r in conn.execute(
                f"""SELECT report_id FROM raw_inventory
                     WHERE project_id = ? AND batch IN ({",".join("?"*len(retired_batches))})""",
                [project_id, *retired_batches],
            )
        }

    # cluster_id map (from similarity)
    cluster_by_report = {
        r[0]: r[1] for r in conn.execute(
            "SELECT report_id, cluster_id FROM similarity_cluster_member WHERE project_id = ?",
            (project_id,),
        )
    }

    # Wipe existing report rows for this project (and dependent tables) so we
    # rebuild cleanly from raw. FKs cascade.
    #
    # NOTE: ast_hash is intentionally NOT cleared here. It's owned by a
    # separate AST compute path (sql_ast_sim.compute_ast_hashes fed into the
    # table from public/data/ast_hashes.json or a future canonical AST stage)
    # and only overlaps with the raw_ast_cache when the project's cache is
    # keyed by report_id. Clearing it here would erase data we can't restore.
    with conn:
        conn.execute("DELETE FROM report WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM retired_report WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM telemetry_match WHERE project_id = ?", (project_id,))

        # Pull every raw_inventory row; pick first per (project_id, report_id).
        seen: set[str] = set()
        report_rows = []
        for r in conn.execute(
            """SELECT report_id, batch, name, owner, folder_path,
                      date_created, date_modified, object_type, subtype
                 FROM raw_inventory WHERE project_id = ?
                 ORDER BY CASE batch
                   WHEN 'original' THEN 0 WHEN 'inventory' THEN 0
                   WHEN 'new' THEN 1 WHEN 'added' THEN 2
                   WHEN 'dossier' THEN 3
                   ELSE 9 END""",
            (project_id,),
        ):
            rid = r[0]
            if rid in seen:
                continue
            seen.add(rid)
            # Determine status
            if rid in dossier_ids:
                status = "dossier"
            elif rid in collision_members_non_canon:
                status = "collapsed"
            elif rid in active_universe:
                status = "active"
            elif rid in retired_from_batch or rid in retired_from_telemetry:
                status = "retired"
            else:
                status = "active"  # conservative default

            from_name = r[2] or ""
            report_rows.append((
                project_id, rid, from_name, r[3] or "", r[4] or "",
                r[5], r[6], r[7] or 3, r[8],
                None,  # match_tier — filled from telemetry_match
                _family_base(from_name),
                status,
                cluster_by_report.get(rid),
                None, None, None,  # counts — filled below
            ))
        conn.executemany(
            """INSERT INTO report
               (project_id, report_id, name, owner, path, date_created, date_modified,
                object_type, subtype, match_tier, family_base, status, cluster_id,
                metric_count, table_count, filter_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            report_rows,
        )

        # Fill report_metric / report_table / report_filter / report_attribute
        # from raw_inventory_*
        conn.execute("DELETE FROM report_metric WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM report_table WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM report_filter WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM report_attribute WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM report_sql WHERE project_id = ?", (project_id,))
        conn.execute(
            """INSERT OR IGNORE INTO report_metric (project_id, report_id, metric_name)
               SELECT DISTINCT project_id, report_id, metric_name
                 FROM raw_inventory_metric WHERE project_id = ?""",
            (project_id,),
        )
        conn.execute(
            """INSERT OR IGNORE INTO report_table (project_id, report_id, table_name)
               SELECT DISTINCT project_id, report_id, table_name
                 FROM raw_inventory_table WHERE project_id = ?""",
            (project_id,),
        )
        conn.execute(
            """INSERT OR IGNORE INTO report_filter (project_id, report_id, filter_name)
               SELECT DISTINCT project_id, report_id, filter_name
                 FROM raw_inventory_filter WHERE project_id = ?""",
            (project_id,),
        )
        conn.execute(
            """INSERT OR IGNORE INTO report_attribute (project_id, report_id, attribute_name)
               SELECT DISTINCT project_id, report_id, attribute_name
                 FROM raw_inventory_attribute WHERE project_id = ?""",
            (project_id,),
        )
        conn.execute(
            """INSERT INTO report_sql (project_id, report_id, sql_text, sql_error)
               SELECT project_id, report_id, sql_text, sql_error
                 FROM raw_inventory_sql WHERE project_id = ?""",
            (project_id,),
        )

        # Update denormalized counts on report
        conn.execute(
            """UPDATE report SET
                 metric_count    = COALESCE((SELECT COUNT(*) FROM report_metric rm
                                          WHERE rm.project_id = report.project_id
                                            AND rm.report_id = report.report_id), 0),
                 table_count     = COALESCE((SELECT COUNT(*) FROM report_table rt
                                          WHERE rt.project_id = report.project_id
                                            AND rt.report_id = report.report_id), 0),
                 filter_count    = COALESCE((SELECT COUNT(*) FROM report_filter rf
                                          WHERE rf.project_id = report.project_id
                                            AND rf.report_id = report.report_id), 0),
                 attribute_count = COALESCE((SELECT COUNT(*) FROM report_attribute ra
                                          WHERE ra.project_id = report.project_id
                                            AND ra.report_id = report.report_id), 0)
               WHERE project_id = ?""",
            (project_id,),
        )

        # Telemetry matches for active
        conn.execute(
            """INSERT INTO telemetry_match
               (project_id, report_id, match_tier, match_score, matched_telemetry_name,
                telemetry_path, total_executions, total_users, last_exec_ts)
               SELECT project_id, report_id, match_tier, match_score,
                      matched_telemetry_name, telemetry_path,
                      total_executions, total_users, last_exec_ts
                 FROM raw_telemetry
                WHERE project_id = ? AND matched = 1""",
            (project_id,),
        )

        # Update match_tier on report from telemetry_match
        conn.execute(
            """UPDATE report SET match_tier = (
                 SELECT match_tier FROM telemetry_match tm
                  WHERE tm.project_id = report.project_id
                    AND tm.report_id = report.report_id
               ) WHERE project_id = ?""",
            (project_id,),
        )

        # Retired classification
        for rid in retired_from_batch:
            conn.execute(
                "INSERT OR REPLACE INTO retired_report (project_id, report_id, bucket) VALUES (?, ?, ?)",
                (project_id, rid, "batch"),
            )
        for rid in retired_from_telemetry:
            if rid not in retired_from_batch:
                conn.execute(
                    "INSERT OR REPLACE INTO retired_report (project_id, report_id, bucket) VALUES (?, ?, ?)",
                    (project_id, rid, "telemetry"),
                )

        # AST hashes from raw_ast_cache where kind='report' (augmentative).
        # If raw_ast_cache is keyed by ast_cluster_hash (GO/GI), nothing inserts
        # here and ast_hash retains whatever was populated by the AST compute
        # path or a prior load.
        conn.execute(
            """INSERT OR IGNORE INTO ast_hash (project_id, report_id, hash)
               SELECT ac.project_id, ac.cache_key,
                      json_each.value
                 FROM raw_ast_cache ac,
                      json_each(ac.subtree_hashes)
                WHERE ac.project_id = ? AND ac.kind = 'report'""",
            (project_id,),
        )

    stats = {
        "active_universe": len(active_universe),
        "dossier": len(dossier_ids),
        "retired_telemetry": len(retired_from_telemetry),
        "retired_batch": len(retired_from_batch),
        "report_rows": len(report_rows),
    }
    return stats


# ============================================================
# Cross-project rationalization
#
# These aren't per-project stages. They scan across all projects at once and
# populate the cross_project_* tables. Run this AFTER per-project builds for
# all 3 projects have completed.
# ============================================================

def stage_cross_project(conn) -> dict:
    """Identify reports + data patterns shared across 2+ projects.

    Populates:
      cross_project_name_match / cross_project_name_member
      cross_project_sql_match  / cross_project_sql_member
      cross_project_shared_table
      cross_project_shared_metric
      cross_project_family
    """
    print("\n[cross_project]")
    with conn:
        for t in (
            "cross_project_name_member", "cross_project_name_match",
            "cross_project_sql_member", "cross_project_sql_match",
            "cross_project_shared_table", "cross_project_shared_metric",
            "cross_project_family",
        ):
            conn.execute(f"DELETE FROM {t}")

    # --- 1. Shared report NAMES across projects --------------------
    rows = conn.execute(
        """SELECT LOWER(name) AS nm,
                  GROUP_CONCAT(project_id || '::' || report_id) AS pairs,
                  COUNT(DISTINCT project_id) AS n_projects,
                  COUNT(*) AS n_reports,
                  GROUP_CONCAT(DISTINCT project_id) AS projects
             FROM report
            WHERE name IS NOT NULL AND name != '' AND status IN ('active', 'dossier')
            GROUP BY LOWER(name)
           HAVING COUNT(DISTINCT project_id) >= 2"""
    ).fetchall()
    name_matches = 0
    for r in rows:
        nm, n_proj, n_reps, projects_csv = r[0], r[2], r[3], ",".join(sorted((r[4] or "").split(",")))
        cur = conn.execute(
            "INSERT INTO cross_project_name_match (normalized_name, n_projects, n_reports, projects_csv) VALUES (?, ?, ?, ?)",
            (nm, n_proj, n_reps, projects_csv),
        )
        match_id = cur.lastrowid
        members = []
        for pair in (r[1] or "").split(","):
            if "::" not in pair:
                continue
            pid, rid = pair.split("::", 1)
            meta = conn.execute(
                """SELECT name, path, COALESCE(t.total_executions, 0)
                     FROM report r LEFT JOIN telemetry_match t
                       ON t.project_id = r.project_id AND t.report_id = r.report_id
                    WHERE r.project_id = ? AND r.report_id = ?""",
                (pid, rid),
            ).fetchone()
            if meta:
                members.append((match_id, pid, rid, meta[0], meta[1], meta[2]))
        if members:
            conn.executemany(
                """INSERT OR IGNORE INTO cross_project_name_member
                   (match_id, project_id, report_id, name, path, executions)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                members,
            )
            name_matches += 1
    conn.commit()
    print(f"  cross_project_name_match: {name_matches}")

    # --- 2. Identical normalized SQL across projects ---------------
    rows = conn.execute(
        """SELECT sql_hash,
                  GROUP_CONCAT(project_id || '::' || report_id) AS pairs,
                  COUNT(DISTINCT project_id) AS n_proj,
                  COUNT(*) AS n_reps,
                  GROUP_CONCAT(DISTINCT project_id) AS projs
             FROM raw_inventory_sql
            WHERE sql_hash IS NOT NULL AND sql_hash != ''
            GROUP BY sql_hash
           HAVING COUNT(DISTINCT project_id) >= 2"""
    ).fetchall()
    sql_matches = 0
    for r in rows:
        projects_csv = ",".join(sorted((r[4] or "").split(",")))
        cur = conn.execute(
            "INSERT INTO cross_project_sql_match (sql_hash, n_projects, n_reports, projects_csv) VALUES (?, ?, ?, ?)",
            (r[0], r[2], r[3], projects_csv),
        )
        mid = cur.lastrowid
        members = []
        for pair in (r[1] or "").split(","):
            if "::" in pair:
                pid, rid = pair.split("::", 1)
                members.append((mid, pid, rid))
        if members:
            conn.executemany(
                "INSERT OR IGNORE INTO cross_project_sql_member (match_id, project_id, report_id) VALUES (?, ?, ?)",
                members,
            )
            sql_matches += 1
    conn.commit()
    print(f"  cross_project_sql_match:  {sql_matches}")

    # --- 3. Shared TABLES across projects --------------------------
    with conn:
        conn.execute(
            """INSERT INTO cross_project_shared_table
                 (table_name, n_projects, n_references, projects_csv)
               SELECT table_name, COUNT(DISTINCT project_id), COUNT(*),
                      GROUP_CONCAT(DISTINCT project_id)
                 FROM raw_inventory_table
                GROUP BY table_name
               HAVING COUNT(DISTINCT project_id) >= 2"""
        )
    table_count = conn.execute("SELECT COUNT(*) FROM cross_project_shared_table").fetchone()[0]
    print(f"  cross_project_shared_table: {table_count}")

    # --- 4. Shared METRICS across projects -------------------------
    with conn:
        conn.execute(
            """INSERT INTO cross_project_shared_metric
                 (metric_name, n_projects, n_references, projects_csv)
               SELECT metric_name, COUNT(DISTINCT project_id), COUNT(*),
                      GROUP_CONCAT(DISTINCT project_id)
                 FROM raw_inventory_metric
                GROUP BY metric_name
               HAVING COUNT(DISTINCT project_id) >= 2"""
        )
    metric_count = conn.execute("SELECT COUNT(*) FROM cross_project_shared_metric").fetchone()[0]
    print(f"  cross_project_shared_metric: {metric_count}")

    # --- 5. Shared FAMILY names across projects --------------------
    with conn:
        conn.execute(
            """INSERT INTO cross_project_family
                 (family_base, n_projects, n_reports, projects_csv)
               SELECT family_base, COUNT(DISTINCT project_id), COUNT(*),
                      GROUP_CONCAT(DISTINCT project_id)
                 FROM report
                WHERE family_base IS NOT NULL AND family_base != ''
                  AND status IN ('active', 'dossier')
                GROUP BY family_base
               HAVING COUNT(DISTINCT project_id) >= 2"""
        )
    family_count = conn.execute("SELECT COUNT(*) FROM cross_project_family").fetchone()[0]
    print(f"  cross_project_family:       {family_count}")

    return {
        "name_matches": name_matches,
        "sql_matches": sql_matches,
        "shared_tables": table_count,
        "shared_metrics": metric_count,
        "shared_families": family_count,
    }


STAGES = (
    ("collisions", stage_collisions),
    ("fingerprint", stage_fingerprint),
    ("sql_hash", stage_sql_hash),
    ("ast", stage_ast),
    ("family", stage_family),   # runs on post-AST; elects canonical per family
    ("similarity", stage_similarity),
    ("llm_reviews", stage_llm_reviews),
    ("reports", stage_reports),   # populate report table from raw (dossiers/retired/etc)
    ("summary", stage_summary),
)


def run_pipeline(project_id: str, conn: sqlite3.Connection | None = None) -> dict:
    """Run all stages for the given project. Idempotent (each stage clears
    its own output rows for the project before inserting).

    FK enforcement is relaxed for the duration — ast_hash has an FK to
    `report`, but stage_reports (which populates `report`) runs AFTER stage_ast
    which inserts ast_hash rows. Without this, those inserts fail silently.
    """
    owned_conn = conn is None
    if owned_conn:
        init()
        conn = connect()
        conn.execute("PRAGMA foreign_keys = OFF")
    try:
        cfg = get_project(project_id)
        print(f"\n=== Pipeline: {cfg['name']} ({project_id}) ===")
        print(f"  active_batches={cfg['active_batches']}  retired_batches={cfg['retired_batches']}")
        results: dict[str, dict] = {}
        for name, fn in STAGES:
            print(f"\n[{name}]")
            stage_result = fn(conn, project_id)
            for k, v in stage_result.items():
                print(f"  {k}: {v}")
            results[name] = stage_result
        return results
    finally:
        if owned_conn:
            conn.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True,
                   choices=["global-operational", "global-insight", "insight", "all"])
    args = p.parse_args()
    targets = (
        ["global-operational", "global-insight", "insight"]
        if args.project == "all" else [args.project]
    )
    for tgt in targets:
        run_pipeline(tgt)
    sys.exit(0)
