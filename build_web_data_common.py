"""Shared compute helper used by the canonical Phase 3b pipeline.

Originally held many helpers used by three project-specific build scripts
(build_web_dashboard_data.py, build_web_data_gi.py, build_web_data_insight.py),
all of which have been retired in favor of `db/compute/build.py`. This module
now contains only the one helper the new pipeline still reuses:

    collapse_telemetry_collisions(combined, active_lookup, ...)

which performs the two-pass telemetry-row collision collapse. It's called
from stage_collisions in db/compute/pipeline.py.
"""

from __future__ import annotations

from collections import defaultdict


def _norm_name(s: str | None) -> str:
    return " ".join((s or "").lower().split())


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

        # Batch-schedule safeguard: when many reports share (execs, lastExecTs)
        # because a scheduler ran them together, they are NOT collisions — they
        # are distinct reports with independent names. Only collapse members
        # whose normalized names genuinely match OR that were fuzzy-matched
        # (where the same telemetry row was attributed to multiple inventory
        # objects by the matcher, which is the real collision case).
        distinct_names = {_norm_name(r.get("name", "")) for r, _ in recs}
        has_fuzzy = any(tier == "fuzzy" for _, tier in recs)
        if not has_fuzzy and len(distinct_names) > 1:
            # Scheduled batch — all exact-name hits with different names.
            # Keep every record as its own canonical.
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
            "lastExec": "",
            "canonicalId": primary["id"],
            "canonicalName": primary.get("name", ""),
            "canonicalPath": _primary_path,
            "normalizedName": nm,
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
