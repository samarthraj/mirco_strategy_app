"""Excel export — per-report rationalization passport for one project.

Walks every report through every dedup stage and records:
  - The outcome at each stage (kept / collapsed / primary / member / singleton)
  - Which report ABSORBED it at that stage (the survivor's id + name)
  - The "ultimately survived by" chain (terminal canonical after walking
    forward through all stages)
  - Telemetry, features, domain, LLM verdict, user retain/retire overrides
  - A one-line "why final" narrative per report

Usage:
    python export_rationalization_xlsx.py --project global-insight
    python export_rationalization_xlsx.py --project insight --out my_export.xlsx
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db.db import DB_PATH, init, get_project


PLAYGROUND_DIR = Path(__file__).resolve().parent / "data" / "_playground"


# ----------------------------------------------------------------------
# Stage walkers — each returns {report_id: (outcome, survivor_id, group_id)}
# ----------------------------------------------------------------------

def _telemetry_outcomes(conn, pid: str) -> dict[str, tuple[str, str | None, str | None]]:
    """Telemetry retirement: report retired iff total_executions == 0 OR row missing.
    Survivor = None (telemetry retirement isn't a merge into another report)."""
    out: dict[str, tuple[str, str | None, str | None]] = {}
    for rid, execs in conn.execute(
        "SELECT report_id, total_executions FROM raw_telemetry WHERE project_id=?",
        (pid,),
    ):
        if (execs or 0) > 0:
            out[rid] = ("kept", None, None)
        else:
            out[rid] = ("retired (zero execs)", None, None)
    return out


def _collision_outcomes(conn, pid: str) -> dict[str, tuple[str, str | None, str | None]]:
    """collision_member.report_id != collision_group.canonical_id => collapsed."""
    out: dict[str, tuple[str, str | None, str | None]] = {}
    for r in conn.execute(
        """SELECT cm.report_id, cm.group_id, cg.canonical_id
             FROM collision_member cm
             JOIN collision_group cg
               ON cg.project_id = cm.project_id AND cg.group_id = cm.group_id
            WHERE cm.project_id = ?""",
        (pid,),
    ):
        rid, gid, canon = r
        if rid == canon:
            out[rid] = ("kept (canonical)", None, gid)
        else:
            out[rid] = ("collapsed", canon, gid)
    return out


def _group_canonical_outcomes(
    conn, pid: str, group_table: str, member_table: str,
) -> dict[str, tuple[str, str | None, str | None]]:
    """Generic walker for fingerprint / sql_hash / ast where the canonical
    isn't stored — pick the highest-execs member per group."""
    # group sizes for member-table queries
    members_by_group: dict[str, list[str]] = {}
    for gid, rid in conn.execute(
        f"SELECT group_id, report_id FROM {member_table} WHERE project_id = ?"
        if "cluster" not in member_table
        else f"SELECT cluster_id, report_id FROM {member_table} WHERE project_id = ?",
        (pid,),
    ):
        members_by_group.setdefault(gid, []).append(rid)

    # report executions for tie-break
    execs_by_rid: dict[str, int] = {
        r[0]: (r[1] or 0)
        for r in conn.execute(
            "SELECT report_id, total_executions FROM raw_telemetry WHERE project_id=?",
            (pid,),
        )
    }

    out: dict[str, tuple[str, str | None, str | None]] = {}
    for gid, rids in members_by_group.items():
        if not rids:
            continue
        # multi-member groups — pick canonical by max execs
        if len(rids) >= 2:
            canon = max(rids, key=lambda r: execs_by_rid.get(r, 0))
            for rid in rids:
                if rid == canon:
                    out[rid] = ("kept (canonical)", None, gid)
                else:
                    out[rid] = ("collapsed", canon, gid)
        else:
            # singleton group — passes through
            out[rids[0]] = ("singleton (passed through)", None, gid)
    return out


def _fingerprint_outcomes(conn, pid: str):
    return _group_canonical_outcomes(conn, pid, "fingerprint_group", "fingerprint_member")


def _sql_hash_outcomes(conn, pid: str):
    return _group_canonical_outcomes(conn, pid, "sql_hash_group", "sql_hash_member")


def _ast_outcomes(conn, pid: str):
    """ast_cluster_member uses cluster_id (not group_id) — walker handles that."""
    return _group_canonical_outcomes(conn, pid, "ast_cluster", "ast_cluster_member")


def _family_outcomes(conn, pid: str) -> dict[str, tuple[str, str | None, str | None]]:
    """post_ast_family is the family-collapse stage's output. canonical_id
    is stored directly on the family row."""
    out: dict[str, tuple[str, str | None, str | None]] = {}
    canonical_by_base = {
        r[0]: r[1] for r in conn.execute(
            "SELECT base, canonical_id FROM post_ast_family WHERE project_id = ?",
            (pid,),
        )
    }
    for base, rid, is_canon in conn.execute(
        """SELECT base, report_id, is_canonical
             FROM post_ast_family_member WHERE project_id = ?""",
        (pid,),
    ):
        canon = canonical_by_base.get(base)
        if is_canon or rid == canon:
            out[rid] = ("kept (canonical)", None, base)
        else:
            out[rid] = ("collapsed", canon, base)
    return out


def _similarity_outcomes(conn, pid: str) -> dict[str, tuple[str, str | None, str | None]]:
    """similarity_cluster.primary_report_id is the survivor for each
    multi-cluster. Members with primary_report_id == self → primary."""
    out: dict[str, tuple[str, str | None, str | None]] = {}
    primary_by_cluster: dict[str, str] = {
        r[0]: r[1] for r in conn.execute(
            "SELECT cluster_id, primary_report_id FROM similarity_cluster WHERE project_id=?",
            (pid,),
        ) if r[1]
    }
    for cid, rid in conn.execute(
        "SELECT cluster_id, report_id FROM similarity_cluster_member WHERE project_id=?",
        (pid,),
    ):
        primary = primary_by_cluster.get(cid)
        if rid == primary:
            out[rid] = ("primary", None, cid)
        else:
            out[rid] = ("member (collapsed)", primary, cid)
    return out


# ----------------------------------------------------------------------
# Active semantic experiment (Playground) — outcomes by report id
# ----------------------------------------------------------------------

def _semantic_outcomes(project_id: str) -> tuple[
    dict[str, tuple[str, str | None, str | None]],
    dict[str, dict],   # cluster_id → {primaryName, size, llm:{...}}
    str | None,        # active expId
]:
    active_path = PLAYGROUND_DIR / "active.json"
    if not active_path.exists():
        return {}, {}, None
    active = json.loads(active_path.read_text(encoding="utf-8"))
    exp_id = active.get(project_id)
    if not exp_id:
        return {}, {}, None
    exp_path = PLAYGROUND_DIR / f"{exp_id}.json"
    if not exp_path.exists():
        return {}, {}, exp_id
    exp = json.loads(exp_path.read_text(encoding="utf-8"))

    cluster_meta: dict[str, dict] = {}
    out: dict[str, tuple[str, str | None, str | None]] = {}

    for c in exp.get("clusters", []):
        cid = c.get("id")
        primary = c.get("primaryReportId")
        cluster_meta[cid] = {
            "primaryReportId": primary,
            "primaryName":     c.get("primaryName") or "",
            "size":            c.get("size") or len(c.get("memberIds") or []),
            "llm":             c.get("llm") or {},
        }
        for mid in c.get("memberIds") or []:
            if mid == primary:
                out[mid] = ("primary", None, cid)
            else:
                out[mid] = ("member (collapsed)", primary, cid)

    # Singletons in scatter (clusterId = null)
    scatter = (exp.get("viz") or {}).get("scatter") or []
    for p in scatter:
        if p.get("clusterId") is None and p["id"] not in out:
            out[p["id"]] = ("singleton", None, None)

    return out, cluster_meta, exp_id


# ----------------------------------------------------------------------
# Domain classification + retain/retire overrides
# ----------------------------------------------------------------------

def _domains(conn, pid: str) -> dict[str, tuple[str, float | None]]:
    out: dict[str, tuple[str, float | None]] = {}
    for rid, dom, conf in conn.execute(
        "SELECT report_id, domain, confidence FROM report_domain WHERE project_id=?",
        (pid,),
    ):
        out[rid] = (dom or "", conf)
    return out


def _user_overrides(conn, pid: str) -> tuple[set[str], set[str]]:
    retained: set[str] = set()
    retired: set[str] = set()
    try:
        for r in conn.execute(
            "SELECT report_id FROM retained_report WHERE project_id=?", (pid,)
        ):
            retained.add(r[0])
    except sqlite3.OperationalError:
        pass
    try:
        for r in conn.execute(
            "SELECT report_id FROM retired_override WHERE project_id=?", (pid,)
        ):
            retired.add(r[0])
    except sqlite3.OperationalError:
        pass
    return retained, retired


# ----------------------------------------------------------------------
# Survivor chain — given per-stage outcomes, walk forward to find terminal
# canonical for every report that got collapsed somewhere.
# ----------------------------------------------------------------------

STAGE_ORDER = [
    "telemetry", "collision", "fingerprint", "sql_hash",
    "ast", "family", "similarity", "semantic",
]


def _ultimately_survived_by(
    rid: str,
    stage_outcomes: dict[str, dict[str, tuple[str, str | None, str | None]]],
) -> tuple[str | None, str | None]:
    """Walk the chain. Returns (terminal_survivor_id, first_collapse_stage).
    A 'collapsed' or 'member (collapsed)' outcome at any stage points to a
    survivor; we follow that survivor forward through subsequent stages."""
    current = rid
    first_collapse: str | None = None
    visited: set[str] = {current}
    for stage in STAGE_ORDER:
        outcome_map = stage_outcomes.get(stage, {})
        info = outcome_map.get(current)
        if not info:
            continue
        outcome, survivor, _gid = info
        if survivor and survivor != current and survivor not in visited:
            if first_collapse is None:
                first_collapse = stage
            current = survivor
            visited.add(current)
    return (current if current != rid else None, first_collapse)


# ----------------------------------------------------------------------
# Why-final narrative
# ----------------------------------------------------------------------

def _why_final(
    rid: str, name: str, stage_outcomes: dict, sem_meta: dict,
    user_retained: set[str], user_retired: set[str],
    isFinalCanonical: bool,
) -> str:
    if rid in user_retired:
        return "Retired by user override"
    if rid in user_retained:
        return "Retained by user override"
    # Stage-by-stage check, latest decisive stage wins
    sem = stage_outcomes.get("semantic", {}).get(rid)
    if sem:
        outcome, survivor, cid = sem
        if outcome == "primary":
            meta = sem_meta.get(cid, {})
            return f"Primary of semantic cluster {cid} ({meta.get('size','?')} reports collapsed under it)"
        if outcome == "member (collapsed)":
            meta = sem_meta.get(cid, {})
            return f"Member of semantic cluster {cid} — primary is {meta.get('primaryName','?')!r}"
        if outcome == "singleton":
            return "Singleton in semantic experiment — no duplicates found"
    sim = stage_outcomes.get("similarity", {}).get(rid)
    if sim:
        outcome, survivor, cid = sim
        if outcome == "primary":
            return f"Primary of Jaccard cluster {cid}"
        if outcome == "member (collapsed)":
            return f"Member of Jaccard cluster {cid} — primary survives"
    fam = stage_outcomes.get("family", {}).get(rid)
    if fam and fam[0] == "collapsed":
        return f"Collapsed at Family stage under base {fam[2]!r}"
    ast = stage_outcomes.get("ast", {}).get(rid)
    if ast and ast[0] == "collapsed":
        return f"Collapsed at AST stage in cluster {ast[2]}"
    sh = stage_outcomes.get("sql_hash", {}).get(rid)
    if sh and sh[0] == "collapsed":
        return f"Collapsed at SQL-hash stage in group {sh[2]}"
    fp = stage_outcomes.get("fingerprint", {}).get(rid)
    if fp and fp[0] == "collapsed":
        return f"Collapsed at Fingerprint stage in group {fp[2]}"
    coll = stage_outcomes.get("collision", {}).get(rid)
    if coll and coll[0] == "collapsed":
        return f"Collapsed at Collision stage (telemetry-row signature match)"
    tel = stage_outcomes.get("telemetry", {}).get(rid)
    if tel and tel[0].startswith("retired"):
        return "Retired at Telemetry stage (zero executions in window)"
    if isFinalCanonical:
        return "Survived all stages — kept as canonical"
    return "Pipeline-collapsed at an earlier stage; not final-canonical"


# ----------------------------------------------------------------------
# Build the dataframe
# ----------------------------------------------------------------------

def build_export(project_id: str) -> tuple[pd.DataFrame, dict, dict]:
    init()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Pull all reports for this project (status = 'active' is the audit scope —
    # this includes reports that survived AND were collapsed/retired along the
    # way). For pure final-kept set, filter on isFinalCanonical downstream.
    rows = conn.execute(
        """SELECT r.report_id, r.name, r.owner, r.path,
                  r.metric_count, r.table_count, r.filter_count, r.attribute_count,
                  r.status, r.cluster_id, r.match_tier,
                  rt.total_executions, rt.total_users, rt.last_exec_ts,
                  ri.source_type
             FROM report r
             LEFT JOIN raw_telemetry rt
               ON rt.project_id = r.project_id AND rt.report_id = r.report_id
             LEFT JOIN raw_inventory ri
               ON ri.project_id = r.project_id AND ri.report_id = r.report_id
            WHERE r.project_id = ?""",
        (project_id,),
    ).fetchall()
    print(f"  loaded {len(rows):,} reports for {project_id}")

    print("  computing stage outcomes...")
    stage_outcomes = {
        "telemetry":   _telemetry_outcomes(conn, project_id),
        "collision":   _collision_outcomes(conn, project_id),
        "fingerprint": _fingerprint_outcomes(conn, project_id),
        "sql_hash":    _sql_hash_outcomes(conn, project_id),
        "ast":         _ast_outcomes(conn, project_id),
        "family":      _family_outcomes(conn, project_id),
        "similarity":  _similarity_outcomes(conn, project_id),
    }
    sem_outcomes, sem_meta, active_exp_id = _semantic_outcomes(project_id)
    stage_outcomes["semantic"] = sem_outcomes
    print(f"  active semantic experiment: {active_exp_id or '(none)'}")

    domains = _domains(conn, project_id)
    user_retained, user_retired = _user_overrides(conn, project_id)

    # Lookup helpers — get canonical name for a survivor id
    name_by_id: dict[str, str] = {r["report_id"]: (r["name"] or "") for r in rows}
    path_by_id: dict[str, str] = {r["report_id"]: (r["path"] or "") for r in rows}
    execs_by_id: dict[str, int] = {
        r["report_id"]: (r["total_executions"] or 0) for r in rows
    }
    isFinalCanonical_set: set[str] = set()
    # Compute isFinalCanonical from the active experiment (or fallback to similarity primaries + post-fam singletons)
    if sem_outcomes:
        for rid, (outcome, _, _cid) in sem_outcomes.items():
            if outcome in ("primary", "singleton"):
                isFinalCanonical_set.add(rid)
    else:
        # fallback: similarity primaries + post-family singletons
        sim_members = {
            r[0] for r in conn.execute(
                "SELECT report_id FROM similarity_cluster_member WHERE project_id=?",
                (project_id,),
            )
        }
        for rid, primary in {
            r[0]: r[1] for r in conn.execute(
                "SELECT cluster_id, primary_report_id FROM similarity_cluster WHERE project_id=?",
                (project_id,),
            )
        }.items():
            if primary:
                isFinalCanonical_set.add(primary)
        for r in conn.execute(
            "SELECT report_id FROM post_ast_family_member WHERE project_id=?",
            (project_id,),
        ):
            if r[0] not in sim_members:
                isFinalCanonical_set.add(r[0])

    def _survivor_label(survivor_id: str | None, group_id: str | None = None) -> str:
        if not survivor_id:
            return ""
        nm = name_by_id.get(survivor_id, "?")
        if group_id:
            return f"{group_id} → {nm} · {survivor_id}"
        return f"{nm} · {survivor_id}"

    print(f"  building {len(rows):,} rows...")
    records = []
    for r in rows:
        rid = r["report_id"]
        # Per-stage outcome + survivor
        stage_cols: dict[str, str] = {}
        for stage in STAGE_ORDER:
            info = stage_outcomes.get(stage, {}).get(rid)
            if info:
                outcome, survivor, gid = info
                stage_cols[f"{stage}_outcome"] = outcome
                stage_cols[f"{stage}_survivor"] = _survivor_label(survivor, gid)
            else:
                # Report wasn't an input to this stage (either pre-stage
                # filtered out, or stage didn't run for some reason)
                stage_cols[f"{stage}_outcome"] = "n/a"
                stage_cols[f"{stage}_survivor"] = ""

        # Ultimately survived by — walk the chain
        terminal_id, first_collapse = _ultimately_survived_by(rid, stage_outcomes)
        survivor_name = name_by_id.get(terminal_id, "") if terminal_id else ""
        survivor_path = path_by_id.get(terminal_id, "") if terminal_id else ""
        survivor_execs = execs_by_id.get(terminal_id, 0) if terminal_id else 0
        # How many reports the survivor absorbed = the cluster size where
        # this report ended up (the deepest cluster in the chain)
        absorbed_count = 0
        if first_collapse:
            sem_info = stage_outcomes["semantic"].get(rid) or stage_outcomes["semantic"].get(terminal_id) if terminal_id else None
            if sem_info and sem_info[2]:
                absorbed_count = sem_meta.get(sem_info[2], {}).get("size", 0)

        # Domain
        dom_name, dom_conf = domains.get(rid, ("", None))

        # LLM verdict (from semantic cluster the report ended up in)
        llm = {}
        sem_info = stage_outcomes["semantic"].get(rid)
        if sem_info and sem_info[2]:
            llm = sem_meta.get(sem_info[2], {}).get("llm", {}) or {}
        is_in_removable = ""
        if llm and llm.get("removableIds"):
            is_in_removable = "Yes" if rid in (llm.get("removableIds") or []) else "No"

        # Disposition
        pipeline_says = "Retain" if rid in isFinalCanonical_set else "Retire"
        override = "retain" if rid in user_retained else ("retire" if rid in user_retired else "")
        if override == "retain":
            effective = "Retain"
        elif override == "retire":
            effective = "Retire"
        else:
            effective = pipeline_says

        why = _why_final(rid, r["name"] or "", stage_outcomes, sem_meta,
                          user_retained, user_retired, rid in isFinalCanonical_set)

        rec = {
            # A. Identity
            "report_id":   rid,
            "name":        r["name"] or "",
            "owner":       r["owner"] or "",
            "path":        r["path"] or "",
            "source_type": r["source_type"] or "",
            # B. Telemetry
            "executions":  r["total_executions"] or 0,
            "users":       r["total_users"] or 0,
            "last_exec":   r["last_exec_ts"] or "",
            "match_tier":  r["match_tier"] or "",
            # C. Features
            "attributes":  r["attribute_count"] or 0,
            "metrics":     r["metric_count"] or 0,
            "tables":      r["table_count"] or 0,
            "filters":     r["filter_count"] or 0,
            # D. Per-stage outcomes (16 cols)
            **stage_cols,
            # E. LLM verdict
            "llm_action":             llm.get("action", ""),
            "llm_confidence":         llm.get("confidence", ""),
            "llm_recommended_keep":   llm.get("keepReport", ""),
            "llm_removable_for_this": is_in_removable,
            # F. Domain
            "domain":            dom_name,
            "domain_confidence": (round(dom_conf, 3) if dom_conf is not None else ""),
            # G. Final disposition
            "pipeline_says":        pipeline_says,
            "user_override":        override,
            "effective_disposition": effective,
            "why_final":            why,
            # H. Survivor chain
            "first_collapse_stage":            first_collapse or "",
            "ultimately_survived_by_id":       terminal_id or "",
            "ultimately_survived_by_name":     survivor_name,
            "ultimately_survived_by_path":     survivor_path,
            "ultimately_survived_by_execs":    survivor_execs,
            "reports_absorbed_into_survivor":  absorbed_count,
        }
        records.append(rec)

    df = pd.DataFrame.from_records(records)

    # Stage-funnel summary (one row per stage)
    summary_rows = []
    for stage in STAGE_ORDER:
        outcomes = stage_outcomes.get(stage, {})
        kept = sum(1 for v in outcomes.values() if "kept" in v[0] or "primary" in v[0] or "singleton" in v[0])
        collapsed = sum(1 for v in outcomes.values() if "collapsed" in v[0] or "member" in v[0])
        retired = sum(1 for v in outcomes.values() if "retired" in v[0])
        summary_rows.append({
            "stage": stage,
            "input_count": len(outcomes),
            "kept": kept,
            "collapsed": collapsed,
            "retired": retired,
        })
    summary_df = pd.DataFrame(summary_rows)

    # Active experiment metadata
    exp_meta_rows = []
    if active_exp_id:
        exp_path = PLAYGROUND_DIR / f"{active_exp_id}.json"
        if exp_path.exists():
            exp = json.loads(exp_path.read_text(encoding="utf-8"))
            cfg = exp.get("config") or {}
            stats = exp.get("stats") or {}
            exp_meta_rows = [
                {"key": "experiment_id", "value": active_exp_id},
                {"key": "name",          "value": exp.get("name", "")},
                {"key": "scope",         "value": cfg.get("scope", "")},
                {"key": "sourceFilter",  "value": cfg.get("sourceFilter", "")},
                {"key": "model",         "value": cfg.get("model", "")},
                {"key": "dimensions",    "value": cfg.get("dimensions", "")},
                {"key": "threshold",     "value": cfg.get("threshold", "")},
                {"key": "fields",        "value": ", ".join([k for k, v in (cfg.get("fields") or {}).items() if v is True])},
                {"key": "embeddingSetName", "value": cfg.get("embeddingSetName", "")},
                {"key": "createdAt",     "value": exp.get("createdAt", "")},
                {"key": "stats.inputReports", "value": stats.get("inputReports", "")},
                {"key": "stats.multiClusters", "value": stats.get("multiClusters", "")},
                {"key": "stats.singletons", "value": stats.get("singletons", "")},
                {"key": "stats.finalUnique", "value": stats.get("finalUnique", "")},
                {"key": "llmReviewedAt", "value": exp.get("llmReviewedAt", "")},
            ]
    exp_meta_df = pd.DataFrame(exp_meta_rows) if exp_meta_rows else pd.DataFrame(
        [{"key": "experiment_id", "value": "(no active experiment)"}]
    )

    conn.close()
    return df, summary_df.to_dict(), exp_meta_df.to_dict()


def _write_xlsx_to(
    target, df: pd.DataFrame, summary: dict, exp_meta: dict,
) -> None:
    """Common writer used by both the CLI (writes to a Path) and the
    sidecar's streaming endpoint (writes to a BytesIO buffer)."""
    summary_df = pd.DataFrame(summary)
    exp_meta_df = pd.DataFrame(exp_meta)
    with pd.ExcelWriter(target, engine="xlsxwriter") as xw:
        df.to_excel(xw, sheet_name="Reports", index=False, freeze_panes=(1, 0))
        summary_df.to_excel(xw, sheet_name="Stage Summary", index=False)
        exp_meta_df.to_excel(xw, sheet_name="Active Experiment", index=False)

        # Format the Reports sheet
        wb = xw.book
        ws = xw.sheets["Reports"]
        header_fmt = wb.add_format({
            "bold": True, "bg_color": "#1F2D50", "font_color": "white",
            "align": "left", "valign": "vcenter", "border": 1,
        })
        retain_fmt = wb.add_format({"bg_color": "#D6F5DA", "bold": True})
        retire_fmt = wb.add_format({"bg_color": "#F8D7DA", "bold": True})
        for col_num, name in enumerate(df.columns):
            ws.write(0, col_num, name, header_fmt)
            # rough auto-width
            max_len = max(
                len(str(name)),
                int(df[name].astype(str).map(len).quantile(0.95)) if len(df) else 8,
            )
            ws.set_column(col_num, col_num, min(max(max_len + 2, 12), 60))
        # Conditional format on effective_disposition
        if "effective_disposition" in df.columns:
            col_idx = df.columns.get_loc("effective_disposition")
            n_rows = len(df) + 1
            ws.conditional_format(1, col_idx, n_rows, col_idx, {
                "type": "cell", "criteria": "==", "value": '"Retain"', "format": retain_fmt
            })
            ws.conditional_format(1, col_idx, n_rows, col_idx, {
                "type": "cell", "criteria": "==", "value": '"Retire"', "format": retire_fmt
            })
        ws.autofilter(0, 0, len(df), len(df.columns) - 1)


def write_xlsx(
    df: pd.DataFrame, summary: dict, exp_meta: dict, out_path: Path,
) -> None:
    print(f"  writing {out_path} ...")
    _write_xlsx_to(out_path, df, summary, exp_meta)


def build_xlsx_bytes(project_id: str) -> tuple[bytes, dict]:
    """Build the xlsx in-memory and return (bytes, stats). Used by the
    sidecar's streaming download endpoint — no temp files."""
    import io
    df, summary, exp_meta = build_export(project_id)
    buf = io.BytesIO()
    _write_xlsx_to(buf, df, summary, exp_meta)
    buf.seek(0)
    return buf.getvalue(), {
        "rows": len(df),
        "cols": len(df.columns),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True,
                    help="Project short id (global-insight, insight, global-operational)")
    ap.add_argument("--out", default=None,
                    help="Output xlsx path (default: <project>_rationalization_<ts>.xlsx)")
    args = ap.parse_args()

    if args.out:
        out_path = Path(args.out)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_path = Path(f"{args.project}_rationalization_{ts}.xlsx")

    print(f"Building rationalization passport for {args.project}...")
    t0 = time.time()
    df, summary, exp_meta = build_export(args.project)
    write_xlsx(df, summary, exp_meta, out_path)
    print(f"Done in {time.time() - t0:.1f}s. → {out_path} ({len(df):,} reports, {len(df.columns)} columns)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
