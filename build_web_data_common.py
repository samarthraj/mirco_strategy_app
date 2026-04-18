"""Shared helpers for project-specific build_web_data_*.py scripts.

Contains the logic that should be identical across projects:
- telemetry-row collision collapse
- inventory_all.json / retired.json / collisions.json emission
- summary field builders (afterCollisionCollapse, afterAst, funnel stages)

Each project script does its own data loading, normalizes inputs to a
common shape, then calls these functions. This keeps the UI at parity
across Global Operational, Global Insight, and INSIGHT.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def family_base(name: str) -> str:
    parts = name.rsplit(" - ", 1)
    if len(parts) == 2 and len(parts[0]) >= 10:
        return parts[0].strip()
    return name.strip()


def jaccard(a: set | frozenset, b: set | frozenset) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _norm_name(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def _slim_record(r: dict) -> dict:
    """Lightweight record (id/name/owner/path/dates) for list endpoints."""
    owner = r.get("owner")
    if isinstance(owner, dict):
        owner = owner.get("name", "")
    return {
        "id": r.get("id"),
        "name": r.get("name", ""),
        "owner": owner or "",
        "path": r.get("folderPath", "") or r.get("path", "") or "",
        "dateCreated": r.get("dateCreated"),
        "dateModified": r.get("dateModified"),
    }


# ---------------------------------------------------------------------------
# Telemetry-row collision collapse
# ---------------------------------------------------------------------------

def collapse_telemetry_collisions(
    combined: list[dict],
    active_lookup: dict[str, dict],
    collision_min: int = 3,
    verbose: bool = True,
) -> tuple[list[dict], list[dict], int]:
    """Collapse MSTR objects that were attributed to the same telemetry row.

    Two passes:
      1. Signature = (executions, lastExecTs) — the telemetry-row primary key.
      2. Name + execution count — catches drift in lastExec timestamps.

    Mutates ``active_lookup`` to remove collapsed IDs, and mutates ``combined``
    records' ``_telemetry`` field to None where collapsed.

    Returns:
        (canonical_combined_list, collision_groups_for_ui, total_collapsed_count)
    """
    def _tel_sig(rid: str, rec: dict):
        act = active_lookup.get(rid)
        if act:
            e = act.get("totalExecutions", 0)
            ts = act.get("lastExecTs", "")
            tier = act.get("matchTier", "")
            return (e, ts) if e else None, tier
        t = rec.get("_telemetry") or {}
        e = t.get("executions", 0)
        return ((e, t.get("lastExec", "")) if e else None, t.get("tier", ""))

    # Pass 1 ────────────────────────────────────────────────────────────
    tel_groups = defaultdict(list)
    for r in combined:
        sig, tier = _tel_sig(r["id"], r)
        if sig is None:
            tel_groups[("__solo__", r["id"])].append((r, tier))
        else:
            tel_groups[sig].append((r, tier))

    canonical: list[dict] = []
    collapsed_count = 0
    collisions_found = 0
    collision_groups: list[dict] = []

    for sig, recs in tel_groups.items():
        is_solo = isinstance(sig, tuple) and sig[0] == "__solo__"
        same_name_pair = (
            len(recs) == 2
            and _norm_name(recs[0][0].get("name", ""))
            == _norm_name(recs[1][0].get("name", ""))
        )
        if is_solo or (len(recs) < collision_min and not same_name_pair):
            canonical.extend(r for r, _ in recs)
            continue
        collisions_found += 1

        def _score(item):
            r, tier = item
            name = _norm_name(r.get("name", ""))
            return (0 if tier == "exact" else 1, len(name), r["id"])

        recs_sorted = sorted(recs, key=_score)
        primary = recs_sorted[0][0]
        canonical.append(primary)

        members_for_ui = []
        for r, tier in recs_sorted:
            owner = r.get("owner")
            if isinstance(owner, dict):
                owner = owner.get("name", "")
            members_for_ui.append({
                "id": r["id"],
                "name": r.get("name", ""),
                "matchTier": tier,
                "folderPath": r.get("folderPath", "") or r.get("path", "") or "",
                "owner": owner or "",
            })
        sig_execs = sig[0] if isinstance(sig, tuple) and len(sig) == 2 else None
        sig_last = sig[1] if isinstance(sig, tuple) and len(sig) == 2 else ""
        _primary_path = (
            primary.get("folderPath", "") or primary.get("path", "") or ""
        )
        # If the primary has no path, fall back to any member that does —
        # collapsed siblings often carry the folder metadata.
        if not _primary_path:
            for mm in members_for_ui:
                if mm.get("folderPath"):
                    _primary_path = mm["folderPath"]
                    break
        collision_groups.append({
            "pass": 1,
            "executions": sig_execs,
            "lastExec": sig_last,
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

    if verbose:
        print(f"  Pass 1: {collisions_found} telemetry rows had collisions; "
              f"collapsed {collapsed_count} objects -> {len(canonical)} unique")

    # Pass 2 ────────────────────────────────────────────────────────────
    name_exec_groups = defaultdict(list)
    for r in canonical:
        act = active_lookup.get(r["id"], {})
        e = act.get("totalExecutions") or (r.get("_telemetry") or {}).get("executions", 0)
        if e:
            name_exec_groups[(_norm_name(r.get("name", "")), e)].append(r)

    pass2_removed = 0
    keep_ids = {r["id"] for r in canonical}
    for (nm, e), recs in name_exec_groups.items():
        if len(recs) < 2:
            continue

        def _score2(r):
            tier = (active_lookup.get(r["id"], {}).get("matchTier", "")
                    or (r.get("_telemetry") or {}).get("tier", ""))
            return (0 if tier == "exact" else 1, r["id"])

        recs_sorted = sorted(recs, key=_score2)
        primary = recs_sorted[0]

        members_for_ui = []
        for rr in recs_sorted:
            tier = (active_lookup.get(rr["id"], {}).get("matchTier", "")
                    or (rr.get("_telemetry") or {}).get("tier", ""))
            owner = rr.get("owner")
            if isinstance(owner, dict):
                owner = owner.get("name", "")
            members_for_ui.append({
                "id": rr["id"],
                "name": rr.get("name", ""),
                "matchTier": tier,
                "folderPath": rr.get("folderPath", "") or rr.get("path", "") or "",
                "owner": owner or "",
            })
        _primary_path = (
            primary.get("folderPath", "") or primary.get("path", "") or ""
        )
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

    canonical = [r for r in canonical if r["id"] in keep_ids]
    if verbose:
        print(f"  Pass 2: +{pass2_removed} collapsed -> {len(canonical)} unique")

    collision_groups.sort(key=lambda g: (-g["collapsed"], -g["size"]))
    return canonical, collision_groups, collapsed_count + pass2_removed


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------

def enrich_collisions_with_paths(
    collision_groups: list[dict],
    path_lookup: dict[str, str],
) -> None:
    """Backfill folderPath on collision members and canonicalPath on groups.

    Mutates the collision_groups in place.
    """
    for g in collision_groups:
        # Fill member folderPaths from lookup when empty
        for m in g.get("members", []):
            if not m.get("folderPath") and path_lookup.get(m["id"]):
                m["folderPath"] = path_lookup[m["id"]]
        # Fill canonicalPath from the canonical member's newly filled path,
        # or from any member that now has a path
        if not g.get("canonicalPath"):
            canonical_member = next(
                (m for m in g.get("members", []) if m["id"] == g.get("canonicalId")),
                None,
            )
            if canonical_member and canonical_member.get("folderPath"):
                g["canonicalPath"] = canonical_member["folderPath"]
            else:
                for m in g.get("members", []):
                    if m.get("folderPath"):
                        g["canonicalPath"] = m["folderPath"]
                        break


def enrich_collisions_with_fingerprints(
    collision_groups: list[dict],
    fingerprint_lookup: dict[str, dict],
) -> None:
    """Attach metrics/tables/filters to each collision member from a lookup.

    ``fingerprint_lookup`` is id -> {metrics: [...], tables: [...], filters: [...]}.
    Only added if the member doesn't already have them. This lets the UI's
    ReportComparison modal compute real fingerprint diffs between any two
    members of a collision group — including collapsed ones that aren't in
    the canonical reports.json.
    """
    for g in collision_groups:
        for m in g.get("members", []):
            fp = fingerprint_lookup.get(m["id"])
            if not fp:
                continue
            if "metrics" not in m:
                m["metrics"] = fp.get("metrics", [])
            if "tables" not in m:
                m["tables"] = fp.get("tables", [])
            if "filters" not in m:
                m["filters"] = fp.get("filters", [])


def emit_collisions(output_dir: Path, collision_groups: list[dict]) -> None:
    with open(output_dir / "collisions.json", "w", encoding="utf-8") as f:
        json.dump(collision_groups, f)
    # Report path coverage for sanity
    total_members = sum(len(g.get("members", [])) for g in collision_groups)
    with_path = sum(1 for g in collision_groups for m in g.get("members", []) if m.get("folderPath"))
    canon_with_path = sum(1 for g in collision_groups if g.get("canonicalPath"))
    print(
        f"  Wrote collisions.json ({len(collision_groups)} groups, "
        f"{canon_with_path} with canonicalPath, {with_path}/{total_members} members with folderPath)"
    )


def emit_inventory_all(
    output_dir: Path,
    inventory_records: list[dict],
    active_ids: set[str],
    retired_ids: set[str],
    path_lookup: dict[str, str] | None = None,
) -> int:
    """Emit inventory_all.json with status tags and best-available folder path.

    ``path_lookup`` (id -> folderPath) is merged over the inventory records.
    Use it to inject paths from enriched sources (active reports, retired
    reports, enriched batches) since the raw inventory often lacks paths.

    Status values:
      - 'active'    : survived telemetry + collision collapse
      - 'retired'   : in a retire bucket
      - 'collapsed' : inventoried but not in active or retired (usually collision-collapsed)
    """
    path_lookup = path_lookup or {}
    out = []
    for r in inventory_records:
        rec = _slim_record(r)
        rid = rec["id"]
        # Fill in path from the lookup if the record itself doesn't have one
        if not rec.get("path") and rid in path_lookup:
            rec["path"] = path_lookup[rid]
        if rid in active_ids:
            rec["status"] = "active"
        elif rid in retired_ids:
            rec["status"] = "retired"
        else:
            rec["status"] = "collapsed"
        out.append(rec)
    filled = sum(1 for r in out if r.get("path"))
    with open(output_dir / "inventory_all.json", "w", encoding="utf-8") as f:
        json.dump(out, f)
    print(f"  Wrote inventory_all.json ({len(out)} reports, {filled} with path)")
    return len(out)


def emit_retired_list(
    output_dir: Path,
    retired_buckets: list[tuple[str, Iterable[dict]]],
    path_lookup: dict[str, str] | None = None,
) -> int:
    """Emit retired.json.

    ``retired_buckets`` is a list of (bucket_name, records) tuples. Bucket
    name is stored on each record so the UI can filter. ``path_lookup``
    (id -> folderPath) is used to fill in missing paths from other sources.
    """
    path_lookup = path_lookup or {}
    out = []
    for bucket_name, bucket in retired_buckets:
        for r in bucket:
            rec = _slim_record(r)
            rid = rec["id"]
            if not rec.get("path") and rid in path_lookup:
                rec["path"] = path_lookup[rid]
            rec["bucket"] = bucket_name
            out.append(rec)
    filled = sum(1 for r in out if r.get("path"))
    with open(output_dir / "retired.json", "w", encoding="utf-8") as f:
        json.dump(out, f)
    print(f"  Wrote retired.json ({len(out)} reports, {filled} with path)")
    return len(out)


# ---------------------------------------------------------------------------
# Sequential AST dedup on fingerprint canonicals
# ---------------------------------------------------------------------------

def sequential_ast_dedup(
    fingerprint_groups: dict[Any, list[str]],
    active_lookup: dict[str, dict],
    ast_hashes: dict[str, list[str]],
    ast_jaccard_fn: Callable[[list[str], list[str]], float],
    threshold: float = 0.80,
    verbose: bool = True,
) -> tuple[int, int]:
    """Compute how many fingerprint canonicals can be further merged via AST.

    Returns (reducible_count, group_count).
    """
    fp_canonical_ids: set[str] = set()
    for fp, rids in fingerprint_groups.items():
        if len(rids) == 1:
            fp_canonical_ids.add(rids[0])
        else:
            best = max(rids, key=lambda rid: active_lookup.get(rid, {}).get("totalExecutions", 0))
            fp_canonical_ids.add(best)

    fp_canon_with_sql = [rid for rid in fp_canonical_ids if rid in ast_hashes]
    fp_canon_ast_adj: dict[str, set[str]] = defaultdict(set)
    for i in range(len(fp_canon_with_sql)):
        a = ast_hashes[fp_canon_with_sql[i]]
        for j in range(i + 1, len(fp_canon_with_sql)):
            if ast_jaccard_fn(a, ast_hashes[fp_canon_with_sql[j]]) >= threshold:
                fp_canon_ast_adj[fp_canon_with_sql[i]].add(fp_canon_with_sql[j])
                fp_canon_ast_adj[fp_canon_with_sql[j]].add(fp_canon_with_sql[i])

    visited: set[str] = set()
    components: list[list[str]] = []
    for start in fp_canon_with_sql:
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
            stack.extend(fp_canon_ast_adj[x] - visited)
        components.append(comp)

    multi = [c for c in components if len(c) >= 2]
    reducible = sum(len(c) - 1 for c in multi)
    if verbose:
        print(f"  Sequential AST: {len(multi)} groups, {reducible} additional reducible")
    return reducible, len(multi)
