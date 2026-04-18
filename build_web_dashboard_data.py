#!/usr/bin/env python3
"""Build per-project JSON files for the web dashboard.

Outputs to public/data/{projectName}/:
  - summary.json       (KPIs, funnel, tier breakdown)
  - clusters.json      (cluster metadata only - no members)
  - cluster_members.json (clusterId -> member list)
  - reports.json       (all reports with enrichment)
  - metrics_tables.json (top metrics, tables, filter attrs)
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

from build_web_data_common import (
    collapse_telemetry_collisions as _common_collapse_collisions,
    emit_collisions as _common_emit_collisions,
    enrich_collisions_with_paths as _common_enrich_collisions,
    enrich_collisions_with_fingerprints as _common_enrich_coll_fps,
    emit_inventory_all as _common_emit_inventory_all,
    emit_retired_list as _common_emit_retired,
    sequential_ast_dedup as _common_sequential_ast_dedup,
)

PROJECT_DIR = Path("Global Operational")
OUTPUT_DIR = Path("public/data/Global Operational")


def extract_filter_attrs(r):
    attrs = set()
    def walk(node):
        if isinstance(node, dict):
            name = node.get('name')
            if name:
                attrs.add(name.lower())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    if r.get('filter'):
        walk(r['filter'].get('tree', {}))
    return attrs


def rich_fingerprint(r):
    metrics = frozenset(
        (m.get('name', '').lower() if isinstance(m, dict) else str(m).lower())
        for m in r.get('metrics', [])
    )
    tables = frozenset(
        t.strip('"').lower().split('.')[-1]
        for t in r.get('sourceTables', [])
    )
    filter_attrs = frozenset(extract_filter_attrs(r))
    return metrics, tables, filter_attrs


def jaccard(a, b):
    if not a and not b: return 1.0
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)


def family_base(name):
    parts = name.rsplit(' - ', 1)
    if len(parts) == 2 and len(parts[0]) >= 10:
        return parts[0].strip()
    return name.strip()


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading data from {PROJECT_DIR}/...")
    with open(PROJECT_DIR / "inventory" / "reports.json", "r", encoding="utf-8") as f:
        old_reports = json.load(f)
    with open(PROJECT_DIR / "inventory" / "reports_new_enriched.json", "r", encoding="utf-8") as f:
        new_enriched = json.load(f)
    with open(PROJECT_DIR / "telemetry" / "active_reports.json", "r", encoding="utf-8") as f:
        old_active = json.load(f)
    with open(PROJECT_DIR / "telemetry" / "retire_reports.json", "r", encoding="utf-8") as f:
        old_retire = json.load(f)
    with open(PROJECT_DIR / "inventory" / "reports_new_no_telemetry.json", "r", encoding="utf-8") as f:
        new_retire = json.load(f)
    with open(PROJECT_DIR / "analysis" / "summary.json", "r", encoding="utf-8") as f:
        analysis = json.load(f)

    # Load recently-added batch (3rd generation of newly-accessible reports)
    added_enriched = []
    added_no_tel = []
    added_enriched_path = PROJECT_DIR / "inventory" / "reports_added_enriched.json"
    added_no_tel_path = PROJECT_DIR / "inventory" / "reports_added_no_telemetry.json"
    if added_enriched_path.exists():
        with open(added_enriched_path, "r", encoding="utf-8") as f:
            added_enriched = json.load(f)
    if added_no_tel_path.exists():
        with open(added_no_tel_path, "r", encoding="utf-8") as f:
            added_no_tel = json.load(f)

    old_retain_ids = {r["id"] for r in old_active}
    old_retained = [r for r in old_reports if r["id"] in old_retain_ids]

    # Deduplicate: added_enriched may contain IDs already in old_retained/new_enriched
    seen_ids = {r["id"] for r in old_retained} | {r["id"] for r in new_enriched}
    added_unique = [r for r in added_enriched if r["id"] not in seen_ids]

    combined = old_retained + new_enriched + added_unique
    pre_collision_count = len(combined)

    print(f"  Old retained:      {len(old_retained)}")
    print(f"  New enriched:      {len(new_enriched)}")
    print(f"  Added enriched:    {len(added_enriched)} ({len(added_unique)} unique)")
    print(f"  Combined:          {len(combined)}")

    # Active telemetry lookup
    active_lookup = {a["id"]: a for a in old_active}
    for r in new_enriched:
        if r.get("_telemetry"):
            active_lookup[r["id"]] = {
                "totalExecutions": r["_telemetry"].get("executions", 0),
                "totalUsers": 1,
                "lastExecTs": r["_telemetry"].get("lastExec", ""),
                "matchTier": r["_telemetry"].get("tier", "unknown"),
            }
    for r in added_unique:
        if r.get("_telemetry"):
            active_lookup[r["id"]] = {
                "totalExecutions": r["_telemetry"].get("executions", 0),
                "totalUsers": 1,
                "lastExecTs": r["_telemetry"].get("lastExec", ""),
                "matchTier": r["_telemetry"].get("tier", "unknown"),
            }

    # ---- Collapse telemetry-row collisions (inflation fix) ----
    # The telemetry matcher can attribute the SAME underlying telemetry row to
    # many MSTR objects (exact-name duplicates across folders AND fuzzy matches
    # that snap to the same base name). Each inherits the full execution count,
    # inflating family/cluster totals. Identify collisions by the
    # (executions, lastExec) signature — that pair uniquely identifies a
    # telemetry row — and keep only one canonical MSTR object per row.
    print("Collapsing telemetry-row collisions...")
    def _norm(s):
        return " ".join((s or "").lower().split())
    def _tel_sig(rid, rec):
        act = active_lookup.get(rid)
        if act:
            e = act.get("totalExecutions", 0)
            ts = act.get("lastExecTs", "")
            tier = act.get("matchTier", "")
            return (e, ts) if e else None, tier
        t = rec.get("_telemetry") or {}
        e = t.get("executions", 0)
        return ((e, t.get("lastExec", "")) if e else None, t.get("tier", ""))
    tel_groups = defaultdict(list)
    for r in combined:
        sig, tier = _tel_sig(r["id"], r)
        if sig is None:
            tel_groups[("__solo__", r["id"])].append((r, tier))
        else:
            tel_groups[sig].append((r, tier))
    canonical = []
    collapsed_count = 0
    collisions = 0
    collision_groups = []  # for UI drill-down
    COLLISION_MIN = 3  # 2-way collisions usually coincidental same-schedule runs...
    for sig, recs in tel_groups.items():
        is_solo = isinstance(sig, tuple) and sig[0] == "__solo__"
        # ...unless the 2 records share the exact same normalized name
        same_name_pair = (
            len(recs) == 2
            and _norm(recs[0][0].get("name", "")) == _norm(recs[1][0].get("name", ""))
        )
        if is_solo or (len(recs) < COLLISION_MIN and not same_name_pair):
            canonical.extend(r for r, _ in recs)
            continue
        collisions += 1
        # Prefer exact tier; within tier, the shortest normalized name (likely
        # the true base); tiebreak on id for determinism.
        def _score(item):
            r, tier = item
            name = _norm(r.get("name", ""))
            return (0 if tier == "exact" else 1, len(name), r["id"])
        recs_sorted = sorted(recs, key=_score)
        primary = recs_sorted[0][0]
        canonical.append(primary)
        # Capture the collision group for the UI
        members_for_ui = []
        for r, tier in recs_sorted:
            members_for_ui.append({
                "id": r["id"],
                "name": r.get("name", ""),
                "matchTier": tier,
                "folderPath": r.get("folderPath", "") or r.get("path", "") or "",
                "owner": (r.get("owner", {}).get("name", "") if isinstance(r.get("owner"), dict) else r.get("owner", "")) or "",
            })
        signature_execs = sig[0] if isinstance(sig, tuple) and len(sig) == 2 else None
        signature_last = sig[1] if isinstance(sig, tuple) and len(sig) == 2 else ""
        _primary_path = primary.get("folderPath", "") or primary.get("path", "") or ""
        if not _primary_path:
            for mm in members_for_ui:
                if mm.get("folderPath"):
                    _primary_path = mm["folderPath"]
                    break
        collision_groups.append({
            "pass": 1,
            "executions": signature_execs,
            "lastExec": signature_last,
            "canonicalId": primary["id"],
            "canonicalName": primary.get("name", ""),
            "canonicalPath": _primary_path,
            "size": len(recs),
            "collapsed": len(recs) - 1,
            "members": members_for_ui,
        })
        for r, _ in recs_sorted[1:]:
            active_lookup.pop(r["id"], None)
            if r.get("_telemetry") is not None:
                r["_telemetry"] = None
            collapsed_count += 1
    combined = canonical
    print(f"  {collisions} telemetry rows had collisions; collapsed {collapsed_count} MSTR objects -> {len(combined)} unique")

    # Second pass: same normalized name + same execution count (signature pass
    # misses these when lastExec timestamps drift by a day).
    name_exec_groups = defaultdict(list)
    for r in combined:
        act = active_lookup.get(r["id"], {})
        e = act.get("totalExecutions") or (r.get("_telemetry") or {}).get("executions", 0)
        if e:
            name_exec_groups[(_norm(r.get("name", "")), e)].append(r)
    pass2_removed = 0
    keep_ids = {r["id"] for r in combined}
    for (nm, e), recs in name_exec_groups.items():
        if len(recs) < 2:
            continue
        def _score2(r):
            tier = active_lookup.get(r["id"], {}).get("matchTier", "") or (r.get("_telemetry") or {}).get("tier", "")
            return (0 if tier == "exact" else 1, r["id"])
        recs_sorted = sorted(recs, key=_score2)
        primary = recs_sorted[0]
        # Record this pass-2 collision
        members_for_ui = []
        for rr in recs_sorted:
            tier = active_lookup.get(rr["id"], {}).get("matchTier", "") or (rr.get("_telemetry") or {}).get("tier", "")
            members_for_ui.append({
                "id": rr["id"],
                "name": rr.get("name", ""),
                "matchTier": tier,
                "folderPath": rr.get("folderPath", "") or rr.get("path", "") or "",
                "owner": (rr.get("owner", {}).get("name", "") if isinstance(rr.get("owner"), dict) else rr.get("owner", "")) or "",
            })
        _primary_path = primary.get("folderPath", "") or primary.get("path", "") or ""
        if not _primary_path:
            for mm in members_for_ui:
                if mm.get("folderPath"):
                    _primary_path = mm["folderPath"]
                    break
        collision_groups.append({
            "pass": 2,
            "executions": e,
            "normalizedName": nm,
            "canonicalId": primary["id"],
            "canonicalName": primary.get("name", ""),
            "canonicalPath": _primary_path,
            "size": len(recs),
            "collapsed": len(recs) - 1,
            "members": members_for_ui,
        })
        for r in recs_sorted[1:]:
            keep_ids.discard(r["id"])
            active_lookup.pop(r["id"], None)
            if r.get("_telemetry") is not None:
                r["_telemetry"] = None
            pass2_removed += 1
    combined = [r for r in combined if r["id"] in keep_ids]
    print(f"  Second pass (name+execs): collapsed {pass2_removed} more -> {len(combined)} unique")

    # Sort collision groups by collapsed count descending for the UI
    collision_groups.sort(key=lambda g: (-g["collapsed"], -g["size"]))
    # NOTE: collisions.json is written later — after path_lookup is built
    # so we can backfill folderPath on members.

    # Compute fingerprints
    print("Computing fingerprints...")
    fingerprints = {}
    for r in combined:
        fingerprints[r["id"]] = rich_fingerprint(r)

    # Fingerprint exact-dedup pass (provable duplicates)
    print("Fingerprint dedup (exact match)...")
    fp_groups = defaultdict(list)
    for rid, fp in fingerprints.items():
        fp_groups[fp].append(rid)
    fp_total_groups = len(fp_groups)
    fp_multi_groups = [g for g in fp_groups.values() if len(g) >= 2]
    fp_reports_in_multi = sum(len(g) for g in fp_multi_groups)
    fp_removable = sum(len(g) - 1 for g in fp_multi_groups)
    after_fingerprint = len(combined) - fp_removable

    # Store reverse lookup for later writing of fingerprints.json
    combined_by_id = {r["id"]: r for r in combined}
    fp_multi_groups_with_sig = [
        (fp, ids) for fp, ids in fp_groups.items() if len(ids) >= 2
    ]

    # AST similarity (new axis — SQL structural hash)
    print("Computing AST fingerprints...")
    import sys
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    from sql_ast_sim import compute_ast_hashes, jaccard as ast_jaccard
    # Look up SQL from the merged UI data so we reuse the extracted corpus
    ui_reports_path = OUTPUT_DIR / "reports.json"
    ui_sql_by_id: dict[str, str] = {}
    if ui_reports_path.exists():
        for r in json.loads(ui_reports_path.read_text(encoding="utf-8")):
            if r.get("sql"):
                ui_sql_by_id[r["id"]] = r["sql"]
    ast_input = [{"id": rid, "sql": sql} for rid, sql in ui_sql_by_id.items()]
    ast_hashes, ast_errors = compute_ast_hashes(ast_input, dialect="redshift", verbose=False)
    print(f"  AST parsed {len(ast_hashes)} / {len(ast_input)} SQLs "
          f"({len(ast_errors)} failures)")

    # Similarity clustering
    print("Clustering (threshold 0.80)...")
    THRESHOLD = 0.80
    ids = list(fingerprints.keys())
    n = len(ids)
    adjacency = defaultdict(set)
    similarity_pairs = {}
    for i in range(n):
        fp1 = fingerprints[ids[i]]
        a1 = ast_hashes.get(ids[i])
        for j in range(i + 1, n):
            fp2 = fingerprints[ids[j]]
            a2 = ast_hashes.get(ids[j])
            m_sim = jaccard(fp1[0], fp2[0])
            t_sim = jaccard(fp1[1], fp2[1])
            f_sim = jaccard(fp1[2], fp2[2])
            bag_sim = 0.4 * m_sim + 0.4 * t_sim + 0.2 * f_sim
            if a1 and a2:
                ast_sim = ast_jaccard(a1, a2)
                # AST is the stronger signal when both sides have SQL
                s = 0.6 * ast_sim + 0.25 * m_sim + 0.15 * t_sim
            else:
                ast_sim = None
                s = bag_sim
            if s >= THRESHOLD:
                adjacency[ids[i]].add(ids[j])
                adjacency[ids[j]].add(ids[i])
                pair = {
                    "combined": round(s, 3),
                    "metric": round(m_sim, 3),
                    "table": round(t_sim, 3),
                    "filter": round(f_sim, 3),
                }
                if ast_sim is not None:
                    pair["ast"] = round(ast_sim, 3)
                similarity_pairs[(ids[i], ids[j])] = pair

    visited = set()
    components = []
    for start in ids:
        if start in visited: continue
        stack = [start]
        comp = []
        while stack:
            x = stack.pop()
            if x in visited: continue
            visited.add(x)
            comp.append(x)
            stack.extend(adjacency[x] - visited)
        components.append(comp)

    multi = sorted([c for c in components if len(c) >= 2], key=lambda c: -len(c))
    singletons = [c[0] for c in components if len(c) == 1]
    print(f"  {len(components)} components, {len(multi)} multi-member, {len(singletons)} singletons")

    # ---- AST-only clustering (standalone, for the Rationalization AST tab) ----
    print("AST clustering (threshold 0.80)...")
    ast_ids = list(ast_hashes.keys())
    ast_adj: dict[str, set[str]] = defaultdict(set)
    for i in range(len(ast_ids)):
        a = ast_hashes[ast_ids[i]]
        for j in range(i + 1, len(ast_ids)):
            if ast_jaccard(a, ast_hashes[ast_ids[j]]) >= THRESHOLD:
                ast_adj[ast_ids[i]].add(ast_ids[j])
                ast_adj[ast_ids[j]].add(ast_ids[i])
    ast_visited: set[str] = set()
    ast_components: list[list[str]] = []
    for start in ast_ids:
        if start in ast_visited:
            continue
        stack = [start]
        comp: list[str] = []
        while stack:
            x = stack.pop()
            if x in ast_visited:
                continue
            ast_visited.add(x)
            comp.append(x)
            stack.extend(ast_adj[x] - ast_visited)
        ast_components.append(comp)
    ast_multi = sorted(
        [c for c in ast_components if len(c) >= 2], key=lambda c: -len(c)
    )
    ast_reducible = sum(len(c) - 1 for c in ast_multi)
    print(
        f"  AST: {len(ast_components)} components, "
        f"{len(ast_multi)} multi-member, {ast_reducible} reducible"
    )

    # Exact-AST groups (a stricter subset of the 0.80 fuzzy grouping)
    ast_exact_groups: dict[frozenset[str], list[str]] = defaultdict(list)
    for rid, h in ast_hashes.items():
        ast_exact_groups[frozenset(h)].append(rid)
    ast_exact_multi = [g for g in ast_exact_groups.values() if len(g) >= 2]
    ast_exact_removable = sum(len(g) - 1 for g in ast_exact_multi)

    # ---- Sequential dedup pipeline on top of fingerprint canonicals ----
    # Step 1: fingerprint canonicals (one representative per fp group)
    # Step 2: SQL Hash dedup — merge fp canonicals with identical normalized SQL
    # Step 3: AST dedup — merge post-SQL-hash canonicals by AST structural similarity
    print("Sequential dedup pipeline (Fingerprint -> SQL Hash -> AST)...")
    fp_canonical_ids: set[str] = set()
    for fp, rids in fp_groups.items():
        if len(rids) == 1:
            fp_canonical_ids.add(rids[0])
        else:
            best = max(rids, key=lambda rid: active_lookup.get(rid, {}).get("totalExecutions", 0))
            fp_canonical_ids.add(best)

    # ---- Sequential SQL Hash dedup on fp canonicals ----
    sql_hash_canon_groups: dict[str, list[str]] = defaultdict(list)
    for rid in fp_canonical_ids:
        r = combined_by_id.get(rid, {})
        h = r.get("sqlHash")
        if h:
            sql_hash_canon_groups[h].append(rid)
    sqlhash_sequential_multi = [g for g in sql_hash_canon_groups.values() if len(g) >= 2]
    sqlhash_sequential_removable = sum(len(g) - 1 for g in sqlhash_sequential_multi)
    after_sqlhash_sequential = after_fingerprint - sqlhash_sequential_removable
    # Build the post-SQL-hash canonical set: keep the highest-execs one per hash group
    sqlhash_collapsed_ids: set[str] = set()
    for g in sqlhash_sequential_multi:
        best = max(g, key=lambda rid: active_lookup.get(rid, {}).get("totalExecutions", 0))
        for rid in g:
            if rid != best:
                sqlhash_collapsed_ids.add(rid)
    post_sqlhash_ids = fp_canonical_ids - sqlhash_collapsed_ids
    print(
        f"  Sequential SQL Hash: {len(sqlhash_sequential_multi)} groups, "
        f"{sqlhash_sequential_removable} additional reducible "
        f"-> after_sqlhash = {after_sqlhash_sequential}"
    )

    # ---- Sequential AST dedup on post-SQL-hash canonicals ----
    fp_canon_with_sql = [rid for rid in post_sqlhash_ids if rid in ast_hashes]
    fp_canon_ast_adj: dict[str, set[str]] = defaultdict(set)
    for i in range(len(fp_canon_with_sql)):
        a = ast_hashes[fp_canon_with_sql[i]]
        for j in range(i + 1, len(fp_canon_with_sql)):
            if ast_jaccard(a, ast_hashes[fp_canon_with_sql[j]]) >= THRESHOLD:
                fp_canon_ast_adj[fp_canon_with_sql[i]].add(fp_canon_with_sql[j])
                fp_canon_ast_adj[fp_canon_with_sql[j]].add(fp_canon_with_sql[i])
    fp_canon_visited: set[str] = set()
    fp_canon_ast_components: list[list[str]] = []
    for start in fp_canon_with_sql:
        if start in fp_canon_visited:
            continue
        stack = [start]
        comp: list[str] = []
        while stack:
            x = stack.pop()
            if x in fp_canon_visited:
                continue
            fp_canon_visited.add(x)
            comp.append(x)
            stack.extend(fp_canon_ast_adj[x] - fp_canon_visited)
        fp_canon_ast_components.append(comp)
    fp_canon_ast_multi = [c for c in fp_canon_ast_components if len(c) >= 2]
    ast_sequential_removable = sum(len(c) - 1 for c in fp_canon_ast_multi)
    after_ast_sequential = after_sqlhash_sequential - ast_sequential_removable
    print(
        f"  Sequential AST: {len(fp_canon_ast_multi)} groups on post-SQL-hash canonicals, "
        f"{ast_sequential_removable} additional reducible"
    )
    print(f"  After AST dedup (sequential): {after_ast_sequential}")

    # Build the post-AST canonical set: for each AST cluster, keep the one
    # with the most executions; all singletons (reports not in any AST cluster)
    # are also kept. This is the 260-report population that feeds similarity.
    ast_collapsed_ids: set[str] = set()
    for g in fp_canon_ast_multi:
        best = max(g, key=lambda rid: active_lookup.get(rid, {}).get("totalExecutions", 0))
        for rid in g:
            if rid != best:
                ast_collapsed_ids.add(rid)
    post_ast_ids = post_sqlhash_ids - ast_collapsed_ids
    print(f"  post_ast_ids: {len(post_ast_ids)}")

    # ---- Rerun similarity clustering on post-AST canonicals only ----
    # This makes the funnel truly sequential: similarity is applied on top
    # of the 260-report population, not on the full 994.
    print("Re-clustering (similarity on post-AST canonicals)...")
    _seq_adjacency: dict[str, set[str]] = defaultdict(set)
    _seq_similarity_pairs: dict[tuple[str, str], dict] = {}
    post_ast_list = list(post_ast_ids)
    for i in range(len(post_ast_list)):
        id_i = post_ast_list[i]
        fp1 = fingerprints[id_i]
        a1 = ast_hashes.get(id_i)
        for j in range(i + 1, len(post_ast_list)):
            id_j = post_ast_list[j]
            fp2 = fingerprints[id_j]
            a2 = ast_hashes.get(id_j)
            m_sim = jaccard(fp1[0], fp2[0])
            t_sim = jaccard(fp1[1], fp2[1])
            f_sim = jaccard(fp1[2], fp2[2])
            bag_sim = 0.4 * m_sim + 0.4 * t_sim + 0.2 * f_sim
            if a1 and a2:
                ast_sim = ast_jaccard(a1, a2)
                s = 0.6 * ast_sim + 0.25 * m_sim + 0.15 * t_sim
            else:
                ast_sim = None
                s = bag_sim
            if s >= THRESHOLD:
                _seq_adjacency[id_i].add(id_j)
                _seq_adjacency[id_j].add(id_i)
                pair = {
                    "combined": round(s, 3),
                    "metric": round(m_sim, 3),
                    "table": round(t_sim, 3),
                    "filter": round(f_sim, 3),
                }
                if ast_sim is not None:
                    pair["ast"] = round(ast_sim, 3)
                _seq_similarity_pairs[(id_i, id_j)] = pair

    _seq_visited: set[str] = set()
    _seq_components: list[list[str]] = []
    for start in post_ast_list:
        if start in _seq_visited:
            continue
        stack = [start]
        comp: list[str] = []
        while stack:
            x = stack.pop()
            if x in _seq_visited:
                continue
            _seq_visited.add(x)
            comp.append(x)
            stack.extend(_seq_adjacency[x] - _seq_visited)
        _seq_components.append(comp)
    _seq_multi = sorted([c for c in _seq_components if len(c) >= 2], key=lambda c: -len(c))
    print(
        f"  Sequential similarity: {len(_seq_components)} components, "
        f"{len(_seq_multi)} multi-member"
    )

    # Replace `components`/`multi`/`singletons` with the sequential results so
    # downstream cluster output reflects the post-AST view.
    #
    # IMPORTANT: keep the full `similarity_pairs` (from the earlier all-994
    # pass) — the compare modal uses it to look up pair scores between ANY
    # two reports, including collapsed duplicates. Merge the sequential pairs
    # in so newly computed post-AST pairs are available too.
    components = _seq_components
    multi = _seq_multi
    singletons = [c[0] for c in _seq_components if len(c) == 1]
    similarity_pairs.update(_seq_similarity_pairs)

    # ---- Build reports.json (per-report full enrichment) ----
    print("Building reports.json...")
    report_id_to_cluster = {}
    for i, comp in enumerate(multi):
        cluster_id = f"C{i+1:03d}"
        for rid in comp:
            report_id_to_cluster[rid] = cluster_id

    reports_out = []
    for r in combined:
        rid = r["id"]
        fp = fingerprints[rid]
        act = active_lookup.get(rid, {})
        owner_raw = r.get("owner")
        owner = owner_raw if isinstance(owner_raw, str) else (owner_raw.get("name", "") if isinstance(owner_raw, dict) else "")
        reports_out.append({
            "id": rid,
            "name": r.get("name", ""),
            "owner": owner,
            "path": r.get("folderPath", "") or r.get("path", "") or "",
            "executions": act.get("totalExecutions", 0),
            "users": act.get("totalUsers", 0),
            "lastExec": act.get("lastExecTs", ""),
            "matchTier": act.get("matchTier", ""),
            "metrics": sorted(fp[0]),
            "tables": sorted(fp[1]),
            "filters": sorted(fp[2]),
            "metricCount": len(fp[0]),
            "tableCount": len(fp[1]),
            "filterCount": len(fp[2]),
            "clusterId": report_id_to_cluster.get(rid),
            "familyBase": family_base(r.get("name", "")),
            "dateCreated": r.get("dateCreated"),
            "dateModified": r.get("dateModified"),
        })
    with open(OUTPUT_DIR / "reports.json", "w", encoding="utf-8") as f:
        json.dump(reports_out, f)
    print(f"  Wrote {len(reports_out)} reports")

    # ---- Build clusters.json (metadata only, no full member data) ----
    print("Building clusters.json...")
    report_lookup = {r["id"]: r for r in reports_out}

    clusters_out = []
    for i, comp in enumerate(multi):
        cluster_id = f"C{i+1:03d}"
        # Sort by executions
        members = sorted(comp, key=lambda rid: -report_lookup[rid]["executions"])
        primary = report_lookup[members[0]]

        # Union of all metrics/tables/filters across members (most common)
        all_metrics = Counter()
        all_tables = Counter()
        all_filters = Counter()
        for rid in comp:
            r = report_lookup[rid]
            for m in r["metrics"]: all_metrics[m] += 1
            for t in r["tables"]: all_tables[t] += 1
            for fa in r["filters"]: all_filters[fa] += 1

        total_execs = sum(report_lookup[rid]["executions"] for rid in comp)
        total_users = max((report_lookup[rid]["users"] for rid in comp), default=0)

        clusters_out.append({
            "id": cluster_id,
            "size": len(comp),
            "primaryReportId": members[0],
            "primaryName": primary["name"],
            "totalExecutions": total_execs,
            "totalUsers": total_users,
            "commonMetrics": [m for m, _ in all_metrics.most_common(10)],
            "commonTables": [t for t, _ in all_tables.most_common(10)],
            "commonFilters": [f for f, _ in all_filters.most_common(10)],
            "metricCount": len(all_metrics),
            "tableCount": len(all_tables),
            "filterCount": len(all_filters),
            "memberIds": members,
        })
    with open(OUTPUT_DIR / "clusters.json", "w", encoding="utf-8") as f:
        json.dump(clusters_out, f)
    print(f"  Wrote {len(clusters_out)} clusters")

    # ---- Build similarities.json (pairs with their detailed scores) ----
    print("Building similarities.json...")
    sim_out = {}
    for (a, b), scores in similarity_pairs.items():
        key = f"{a}|{b}"
        sim_out[key] = scores
    with open(OUTPUT_DIR / "similarities.json", "w", encoding="utf-8") as f:
        json.dump(sim_out, f)
    print(f"  Wrote {len(sim_out)} pair similarities")

    # ---- Build families.json (for UI drill-down) ----
    print("Building families.json...")
    fam_groups = defaultdict(list)
    for r in combined:
        fam_groups[family_base(r["name"])].append(r)
    fam_multi = [g for g in fam_groups.values() if len(g) >= 2]
    fam_reducible = sum(len(g) for g in fam_multi) - len(fam_multi)

    # Sort families by size desc - fam_multi is list of lists, need to track base from members
    families_out = []
    # Rebuild as list of (base, members) tuples
    fam_tuples = [(family_base(members[0]["name"]), members) for members in fam_multi]
    for base, members in sorted(fam_tuples, key=lambda x: -len(x[1])):
        member_details = []
        for m in members:
            mid = m["id"]
            suffix = m["name"][len(base):].lstrip(" -").strip() if m["name"].startswith(base) else ""
            act = active_lookup.get(mid, {})
            member_details.append({
                "id": mid,
                "name": m["name"],
                "variantSuffix": suffix or "(base)",
                "executions": act.get("totalExecutions", 0),
                "users": act.get("totalUsers", 0),
                "lastExec": act.get("lastExecTs", ""),
            })
        # Sort members by executions desc
        member_details.sort(key=lambda x: -x["executions"])
        families_out.append({
            "base": base,
            "size": len(members),
            "reducible": len(members) - 1,
            "totalExecutions": sum(d["executions"] for d in member_details),
            "members": member_details,
        })
    with open(OUTPUT_DIR / "families.json", "w", encoding="utf-8") as f:
        json.dump(families_out, f)
    print(f"  Wrote {len(families_out)} families")

    # ---- Build fingerprints.json (exact-match groups for UI drill-down) ----
    print("Building fingerprints.json...")
    fingerprints_out = []
    for idx, (fp, ids) in enumerate(
        sorted(fp_multi_groups_with_sig, key=lambda x: -len(x[1]))
    ):
        metrics_set, tables_set, filters_set = fp
        member_details = []
        for rid in ids:
            src = combined_by_id.get(rid, {})
            act = active_lookup.get(rid, {})
            member_details.append({
                "id": rid,
                "name": src.get("name", ""),
                "path": src.get("path", ""),
                "executions": act.get("totalExecutions", 0),
                "users": act.get("totalUsers", 0),
                "lastExec": act.get("lastExecTs", ""),
            })
        member_details.sort(key=lambda x: -x["executions"])
        fingerprints_out.append({
            "id": f"FP{idx+1:04d}",
            "size": len(ids),
            "reducible": len(ids) - 1,
            "metricCount": len(metrics_set),
            "tableCount": len(tables_set),
            "filterCount": len(filters_set),
            "metrics": sorted(metrics_set),
            "tables": sorted(tables_set),
            "filters": sorted(filters_set),
            "totalExecutions": sum(d["executions"] for d in member_details),
            "members": member_details,
        })
    with open(OUTPUT_DIR / "fingerprints.json", "w", encoding="utf-8") as f:
        json.dump(fingerprints_out, f)
    print(f"  Wrote {len(fingerprints_out)} fingerprint groups")

    # ---- Build ast_clusters.json ----
    print("Building ast_clusters.json...")
    # Build a quick lookup of inner pairwise AST scores for each cluster
    ast_score_lookup: dict[tuple[str, str], float] = {}
    for i in range(len(ast_ids)):
        a = ast_hashes[ast_ids[i]]
        for j in range(i + 1, len(ast_ids)):
            score = ast_jaccard(a, ast_hashes[ast_ids[j]])
            if score >= THRESHOLD:
                ast_score_lookup[(ast_ids[i], ast_ids[j])] = score
    exact_hashes_set: dict[str, frozenset[str]] = {
        rid: frozenset(h) for rid, h in ast_hashes.items()
    }

    # Pre-compute SQL hash member sets so we can flag AST-exclusive clusters
    _sqlhash_to_rids: dict[str, list[str]] = defaultdict(list)
    for r in combined:
        h = r.get("sqlHash")
        if h:
            _sqlhash_to_rids[h].append(r["id"])
    _sqlhash_member_sets = [frozenset(rids) for rids in _sqlhash_to_rids.values() if len(rids) >= 2]

    # Emit AST clusters from the SEQUENTIAL view (on post-SQL-hash fp canonicals)
    # so the per-cluster reducible counts actually add up to the lineage delta.
    # The old "all 857 SQLs" view inflated cluster sizes with fingerprint-duplicate
    # members that the earlier pipeline steps would already have caught.
    seq_multi = sorted(
        [c for c in fp_canon_ast_multi], key=lambda c: -len(c)
    )
    print(f"  AST cluster emission: using sequential view ({len(seq_multi)} groups on fp canonicals)")

    ast_clusters_out = []
    for idx, comp in enumerate(seq_multi):
        member_details = []
        for rid in comp:
            src = combined_by_id.get(rid, {})
            act = active_lookup.get(rid, {})
            member_details.append({
                "id": rid,
                "name": src.get("name", ""),
                "path": src.get("folderPath", "") or src.get("path", "") or "",
                "executions": act.get("totalExecutions", 0),
                "users": act.get("totalUsers", 0),
                "lastExec": act.get("lastExecTs", ""),
            })
        member_details.sort(key=lambda x: -x["executions"])
        first_hash = exact_hashes_set[comp[0]]
        all_exact = all(exact_hashes_set[r] == first_hash for r in comp[1:])
        scores = []
        for i in range(len(comp)):
            for j in range(i + 1, len(comp)):
                a, b = sorted((comp[i], comp[j]))
                scores.append(ast_score_lookup.get((a, b), 1.0 if all_exact else 0.0))
        avg_score = sum(scores) / len(scores) if scores else 1.0
        min_score = min(scores) if scores else 1.0
        rid_set = frozenset(comp)
        covered_by_sqlhash = any(rid_set == hs or rid_set.issubset(hs) for hs in _sqlhash_member_sets)
        ast_clusters_out.append({
            "id": f"AST{idx+1:04d}",
            "size": len(comp),
            "reducible": len(comp) - 1,
            "allExact": all_exact,
            "avgScore": round(avg_score, 3),
            "minScore": round(min_score, 3),
            "totalExecutions": sum(d["executions"] for d in member_details),
            "members": member_details,
            "inSqlHash": covered_by_sqlhash,
            "astExclusive": not covered_by_sqlhash,
        })
    with open(OUTPUT_DIR / "ast_clusters.json", "w", encoding="utf-8") as f:
        json.dump(ast_clusters_out, f)

    # Persist raw AST hashes so the UI can compute pair similarity on-demand
    # for ANY two reports, not just those above the clustering threshold.
    ast_hashes_out = {rid: sorted(h) for rid, h in ast_hashes.items()}
    with open(OUTPUT_DIR / "ast_hashes.json", "w", encoding="utf-8") as f:
        json.dump(ast_hashes_out, f)
    print(f"  Wrote ast_hashes.json ({len(ast_hashes_out)} reports)")
    print(
        f"  Wrote {len(ast_clusters_out)} AST clusters "
        f"({len(ast_exact_multi)} exact, {ast_reducible} reducible, "
        f"{ast_exact_removable} provable)"
    )

    # ---- Build sql_hash_groups.json ----
    print("Building sql_hash_groups.json...")
    sqlhash_out = []
    sql_hash_to_reports: dict[str, list[str]] = defaultdict(list)
    for r in combined:
        h = r.get("sqlHash")
        if h:
            sql_hash_to_reports[h].append(r["id"])

    sql_by_id = {r["id"]: r for r in combined}
    cid = 0
    for h, rids in sql_hash_to_reports.items():
        if len(rids) < 2:
            continue
        members = []
        total_exec = 0
        for rid in rids:
            r = sql_by_id.get(rid, {})
            act = active_lookup.get(rid, {})
            execs = act.get("totalExecutions", 0)
            total_exec += execs
            members.append({
                "id": rid,
                "name": r.get("name", ""),
                "path": r.get("folderPath", "") or r.get("path", "") or "",
                "executions": execs,
                "users": act.get("totalUsers", 0),
                "lastExec": act.get("lastExecTs", ""),
            })
        first_sql = (sql_by_id.get(rids[0], {}).get("sql") or "")[:300]
        sqlhash_out.append({
            "id": f"H{cid:04d}",
            "size": len(rids),
            "reducible": len(rids) - 1,
            "totalExecutions": total_exec,
            "sqlHash": h,
            "sqlPreview": first_sql,
            "members": members,
        })
        cid += 1
    sqlhash_out.sort(key=lambda g: -g["size"])
    with open(OUTPUT_DIR / "sql_hash_groups.json", "w", encoding="utf-8") as f:
        json.dump(sqlhash_out, f, default=str)
    print(f"  Wrote {len(sqlhash_out)} SQL hash groups")

    # ---- Build summary.json (KPIs, funnel, tier breakdown, top lists) ----
    print("Building summary.json...")

    # Tier breakdown
    old_tier_counts = Counter(r.get("matchTier") for r in old_active)
    new_tier_counts = Counter((r.get("_telemetry") or {}).get("tier") for r in new_enriched)
    added_tier_counts = Counter((r.get("_telemetry") or {}).get("tier") for r in added_unique)
    tier_breakdown = {
        "exact": (old_tier_counts.get("exact_name", 0)
                  + new_tier_counts.get("exact", 0)
                  + added_tier_counts.get("exact", 0)),
        "fuzzy": (old_tier_counts.get("fuzzy", 0)
                  + new_tier_counts.get("fuzzy", 0)
                  + added_tier_counts.get("fuzzy", 0)),
        "noMatch": len(old_retire) + len(new_retire) + len(added_no_tel),
    }

    # Top metrics/tables/filters
    metric_freq = Counter()
    table_freq = Counter()
    filter_freq = Counter()
    for r in reports_out:
        for m in r["metrics"]: metric_freq[m] += 1
        for t in r["tables"]: table_freq[t] += 1
        for fa in r["filters"]: filter_freq[fa] += 1

    # Top executed reports (dedup by name)
    top_exec_raw = sorted(reports_out, key=lambda r: -r["executions"])
    seen = set()
    top_exec = []
    for r in top_exec_raw:
        if r["name"] not in seen:
            seen.add(r["name"])
            top_exec.append({
                "id": r["id"],
                "name": r["name"],
                "executions": r["executions"],
                "users": r["users"],
                "lastExec": r["lastExec"],
            })
        if len(top_exec) >= 25:
            break

    # Total inventory = sum of all reports we ever inventoried
    # old (1347) + new (1177) + added (5091) = 7615
    # NOTE: if reports_fresh.json exists, use that for the canonical count
    total_inventory_path = PROJECT_DIR / "inventory" / "reports_fresh.json"
    inventory_all_records = []
    if total_inventory_path.exists():
        with open(total_inventory_path, "r", encoding="utf-8") as f:
            inventory_all_records = json.load(f)
        total_inventory = len(inventory_all_records)
    else:
        total_inventory = len(old_reports) + len(new_enriched) + len(new_retire) + len(added_enriched) + len(added_no_tel)

    # Build a comprehensive id -> folderPath lookup from ALL enriched sources
    # so the Original Inventory + Retired drill-downs can show paths even for
    # reports that weren't enriched in the current pipeline run.
    path_lookup = {}
    for src in (old_reports, new_enriched, added_enriched, combined):
        for r in src:
            rid = r.get("id")
            if not rid:
                continue
            p = r.get("folderPath") or r.get("path") or ""
            if isinstance(p, list):
                # Some records store path as a list of ancestors
                p = "/".join(
                    (a.get("name", "") if isinstance(a, dict) else str(a)) for a in p
                )
            if p and rid not in path_lookup:
                path_lookup[rid] = p

    # Also merge in paths fetched by the out-of-band fetch_all_missing_paths.py
    # script (which covers the reports that were never enriched in-line).
    fetched_paths_file = PROJECT_DIR / "inventory" / "fetched_paths.json"
    if fetched_paths_file.exists():
        fetched = json.loads(fetched_paths_file.read_text(encoding="utf-8"))
        added_from_fetched = 0
        for rid, v in fetched.items():
            p = (v or {}).get("path") if isinstance(v, dict) else v
            if p and rid not in path_lookup:
                path_lookup[rid] = p
                added_from_fetched += 1
        print(f"  Merged {added_from_fetched} paths from fetched_paths.json")

    # Build an id -> {metrics,tables,filters} lookup covering ALL enriched
    # sources (canonicals, collapsed siblings, retired, etc.) so the
    # ReportComparison modal can diff any two members of a collision group.
    def _extract_fp(r):
        def _norm_tables(ts):
            out = []
            for t in (ts or []):
                if not t:
                    continue
                out.append(str(t).strip('"').lower().split(".")[-1])
            return sorted(set(out))
        def _norm_metrics(ms):
            return sorted({
                (m.get("name", "") if isinstance(m, dict) else str(m)).lower()
                for m in (ms or [])
                if (m.get("name") if isinstance(m, dict) else m)
            })
        def _norm_filters(f):
            attrs = set()
            def walk(node):
                if isinstance(node, dict):
                    n = node.get("name")
                    if n:
                        attrs.add(str(n).lower())
                    for v in node.values():
                        walk(v)
                elif isinstance(node, list):
                    for x in node:
                        walk(x)
            if f:
                walk(f.get("tree") if isinstance(f, dict) else f)
            return sorted(attrs)
        return {
            "metrics": _norm_metrics(r.get("metrics")),
            "tables": _norm_tables(r.get("sourceTables")),
            "filters": _norm_filters(r.get("filter")),
        }

    fingerprint_lookup = {}
    for src in (old_reports, new_enriched, added_enriched, combined):
        for r in src:
            rid = r.get("id")
            if not rid or rid in fingerprint_lookup:
                continue
            fp = _extract_fp(r)
            # Only store if at least one dimension has data (avoid noise)
            if fp["metrics"] or fp["tables"] or fp["filters"]:
                fingerprint_lookup[rid] = fp

    # Backfill folderPath on collision members + canonicalPath on groups,
    # and fingerprint data (metrics/tables/filters) on each member so the
    # comparison modal works for collapsed reports too.
    _common_enrich_collisions(collision_groups, path_lookup)
    _common_enrich_coll_fps(collision_groups, fingerprint_lookup)
    print(f"  fingerprint_lookup covers {len(fingerprint_lookup)} reports")
    _common_emit_collisions(OUTPUT_DIR, collision_groups)

    # Annotate each record with its final classification: active/retired/collapsed
    active_ids = {r["id"] for r in combined}
    retired_ids = {r.get("id") for r in (list(old_retire) + list(new_retire) + list(added_no_tel))}

    _common_emit_inventory_all(
        OUTPUT_DIR,
        inventory_all_records,
        active_ids,
        retired_ids,
        path_lookup=path_lookup,
    )

    _common_emit_retired(
        OUTPUT_DIR,
        [("original", old_retire), ("new", new_retire), ("added", added_no_tel)],
        path_lookup=path_lookup,
    )

    after_telemetry = pre_collision_count  # reports with ANY telemetry (before collision collapse)
    after_collision_collapse = len(combined)  # canonical unique reports after row-collision dedup
    collision_collapsed = pre_collision_count - after_collision_collapse
    after_family = after_collision_collapse - fam_reducible
    after_similarity = len(components)
    total_retired = len(old_retire) + len(new_retire) + len(added_no_tel)

    # ---- LLM review (classification layer) ----
    # llm_reviews.json is produced by llm_review_go_clusters.py. Similarity
    # clustering already collapses each cluster to 1 canonical — so the LLM's
    # removable_count per cluster doesn't further reduce the funnel. Instead,
    # the LLM classifies each cluster's action (PARAMETERIZE / MERGE_IMMEDIATE
    # / REVIEW_WITH_OWNER / KEEP_SEPARATE) with confidence and a business
    # rationale. Track action distribution and a confidence-adjusted count of
    # clusters the LLM says are SAFE to auto-consolidate.
    llm_reviews_path = OUTPUT_DIR / "llm_reviews.json"
    llm_reviews: list[dict] = []
    llm_removable_raw = 0
    llm_actions: dict[str, int] = {}
    llm_safe_clusters = 0  # clusters LLM confidently says can be auto-consolidated
    if llm_reviews_path.exists():
        try:
            llm_reviews = json.loads(llm_reviews_path.read_text(encoding="utf-8"))
            for rv in llm_reviews:
                if "error" in rv:
                    continue
                llm_removable_raw += int(rv.get("removable_count", 0) or 0)
                a = rv.get("consolidation_action", "UNKNOWN")
                llm_actions[a] = llm_actions.get(a, 0) + 1
                if a in ("MERGE_IMMEDIATE", "PARAMETERIZE") and rv.get("confidence") == "HIGH":
                    llm_safe_clusters += 1
        except Exception as e:
            print(f"  WARNING: could not read llm_reviews.json: {e}")
            llm_reviews = []

    reduction_total = total_inventory - after_similarity
    reduction_pct = round((reduction_total / total_inventory) * 100, 1) if total_inventory else 0.0

    funnel = [
        {
            "label": "Original Inventory",
            "value": total_inventory,
            "detail": "All reports enumerated via REST API across multiple permission waves",
        },
        {
            "label": "After Telemetry Retirement",
            "value": after_telemetry,
            "detail": f"{total_retired} reports with no usage in 7 months removed",
        },
        {
            "label": "After Collision Collapse",
            "value": after_collision_collapse,
            "detail": f"{collision_collapsed} reports collapsed by telemetry-row signature (same telemetry row attributed to multiple objects by fuzzy matcher)",
        },
        {
            "label": "After Fingerprint Dedup",
            "value": after_fingerprint,
            "detail": f"{fp_removable} provable duplicates removed ({len(fp_multi_groups)} exact-match fingerprint groups covering {fp_reports_in_multi} reports)",
        },
        {
            "label": "After SQL Hash Dedup",
            "value": after_sqlhash_sequential,
            "detail": f"{sqlhash_sequential_removable} exact-SQL duplicates removed ({len(sqlhash_sequential_multi)} SQL-hash groups on fingerprint canonicals)",
        },
        {
            "label": "After AST Dedup",
            "value": after_ast_sequential,
            "detail": f"{ast_sequential_removable} AST-structural duplicates removed ({len(fp_canon_ast_multi)} AST clusters on post-SQL-hash canonicals)",
        },
        {
            "label": "After Similarity Clustering",
            "value": after_similarity,
            "detail": f"{after_ast_sequential - after_similarity} reports grouped into {len(multi)} clusters on post-AST canonicals (\u22650.80 weighted Jaccard)",
        },
    ]

    summary = {
        "projectName": "Global Operational",
        "projectId": "E77B77894C04BF0E6D244F9363CFAF64",
        "totalInventory": total_inventory,
        "totalObjects": analysis["totalObjects"],
        "afterTelemetry": after_telemetry,
        "retired": total_retired,
        "afterCollisionCollapse": after_collision_collapse,
        "collisionCollapsed": collision_collapsed,
        "afterFingerprint": after_fingerprint,
        "fingerprintGroups": fp_total_groups,
        "fingerprintMultiGroups": len(fp_multi_groups),
        "fingerprintReportsInGroups": fp_reports_in_multi,
        "fingerprintRemovable": fp_removable,
        "astParsed": len(ast_hashes),
        # These counts now reflect the SEQUENTIAL view (AST clusters among
        # post-SQL-hash fp canonicals) so the per-cluster reducible numbers
        # sum to the lineage delta.
        "astClusters": len(fp_canon_ast_components),
        "astMultiClusters": len(fp_canon_ast_multi),
        "astReducible": ast_sequential_removable,
        "astExactGroups": len(ast_exact_multi),
        "astExactRemovable": ast_exact_removable,
        "astFinalToMigrate": after_ast_sequential,
        # Sequential AST dedup — applied ON TOP of fingerprint dedup.
        # This is the number the funnel should use (always <= afterFingerprint).
        "afterSqlHash": after_sqlhash_sequential,
        "sqlHashSequentialRemovable": sqlhash_sequential_removable,
        "sqlHashSequentialGroups": len(sqlhash_sequential_multi),
        "afterAst": after_ast_sequential,
        "astSequentialRemovable": ast_sequential_removable,
        "astSequentialGroups": len(fp_canon_ast_multi),
        "afterFamily": after_family,
        "familyReducible": fam_reducible,
        "afterSimilarity": after_similarity,
        "clustersTotal": len(components),
        "clustersMulti": len(multi),
        "clustersSingleton": len(singletons),
        "llmReviewsTotal": len(llm_reviews),
        "llmReviewsByAction": llm_actions,
        "llmSafeClusters": llm_safe_clusters,
        "llmRemovableRaw": llm_removable_raw,
        "reductionTotal": reduction_total,
        "reductionPct": reduction_pct,
        "funnel": funnel,
        "alternativeFamilyMethod": {
            "label": "Alternative: Family Name Dedup",
            "value": after_family,
            "detail": f"Grouping {fam_reducible} reports into {len(fam_multi)} name-pattern families (less aggressive than similarity)",
        },
        "tierBreakdown": tier_breakdown,
        "objectTypes": [{"name": k, "count": v} for k, v in sorted(analysis.get("byCategory", {}).items(), key=lambda x: -x[1]) if v > 0],
        "topMetrics": [{"name": n, "count": c} for n, c in metric_freq.most_common(15)],
        "topTables": [{"name": n, "count": c} for n, c in table_freq.most_common(15)],
        "topFilters": [{"name": n, "count": c} for n, c in filter_freq.most_common(15)],
        "topExecuted": top_exec,
        "scenarios": [
            {
                "name": "Aggressive (Automated)",
                "count": after_similarity,
                "approach": "Trust similarity clustering — 1 parameterized report per cluster",
                "confidence": "Medium",
            },
            {
                "name": "Moderate (Recommended)",
                "count": 400,
                "approach": "Allow 2-3 reports per large cluster for legitimate brand/season variants",
                "confidence": "High",
            },
            {
                "name": "Conservative (Family-Only)",
                "count": after_family,
                "approach": "Only collapse name-pattern variants, keep distinct fingerprints",
                "confidence": "Very High",
            },
        ],
    }
    with open(OUTPUT_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"  Summary: {after_similarity} final unique from {total_inventory}")

    print(f"\nAll data written to: {OUTPUT_DIR}/")

    # Merge extracted SQL back into reports.json (both public and dist)
    print("\nMerging extracted SQL into reports.json...")
    try:
        import subprocess, sys
        subprocess.run(
            [sys.executable, str(PROJECT_DIR / "scripts" / "merge_sql_into_ui.py")],
            check=True,
        )
    except Exception as e:
        print(f"  WARNING: SQL merge step failed: {e}")


if __name__ == "__main__":
    main()
