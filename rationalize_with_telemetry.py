#!/usr/bin/env python3
"""
MicroStrategy Rationalization with Telemetry Matching.

Three-step process:
  1. Inventory  — Pull all reports from Global Operational project
  2. Telemetry  — Match inventory against telemetry CSV; flag reports
                  with last execution > 7 months ago (or no telemetry) for retirement
  3. Rationalize — Run dependencies + analysis, produce final report
                   with retirement recommendations

Usage:
  python rationalize_with_telemetry.py \
    --base-url https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api \
    --username "$MSTR_USERNAME" \
    --project-name "Global Operational" \
    --telemetry-csv report-telemetry-go.csv \
    --output-dir mstr_rationalization_apr09 \
    --include-definitions \
    --verbose
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from mstr_report_inventory import MstrClient, Project, write_json
from mstr_project_rationalize import (
    RationalizationClient,
    OBJECT_TYPES,
    build_inventory,
    fetch_folder_paths,
    build_dependency_graph,
    load_dependency_graph,
    run_analysis,
    load_manifest,
    save_manifest,
    build_sql_extraction_plan,
    execute_sql_phase,
)

# ---------------------------------------------------------------------------
# Telemetry loader
# ---------------------------------------------------------------------------

def load_telemetry_csv(csv_path: str) -> List[Dict[str, Any]]:
    """Load the telemetry CSV and parse into records."""
    records: List[Dict[str, Any]] = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)  # Object,,User,Object Type,Project,Executions,...

        # The header has an unnamed second column (the path)
        # Columns: Object, Path, User, Object Type, Project, Executions, Users,
        #          Errors, Error Rate, Avg Exec Time (s), Max Exec Time (s), Last Exec TS, Cubes
        for row in reader:
            if len(row) < 12:
                continue
            obj_name = row[0].strip()
            obj_path = row[1].strip()
            user = row[2].strip()
            obj_type = row[3].strip()
            project = row[4].strip()
            executions_str = row[5].strip().replace(",", "")
            last_exec_ts = row[11].strip()

            executions = 0
            try:
                executions = int(executions_str)
            except ValueError:
                pass

            last_exec_dt = None
            if last_exec_ts:
                try:
                    last_exec_dt = datetime.strptime(last_exec_ts, "%m/%d/%Y %H:%M")
                    last_exec_dt = last_exec_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

            records.append({
                "name": obj_name,
                "path": obj_path,
                "user": user,
                "objectType": obj_type,
                "project": project,
                "executions": executions,
                "lastExecTs": last_exec_ts,
                "lastExecDt": last_exec_dt,
            })

    return records


def _aggregate_telemetry_recs(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pick the record with the latest exec and aggregate stats across all records."""
    recs_with_dt = [r for r in recs if r["lastExecDt"] is not None]
    best_rec = max(recs_with_dt, key=lambda r: r["lastExecDt"]) if recs_with_dt else recs[0]
    best_rec = dict(best_rec)
    best_rec["totalExecutions"] = sum(r["executions"] for r in recs)
    best_rec["totalUsers"] = len(set(r["user"] for r in recs if r["user"]))
    return best_rec


def build_telemetry_index(telemetry: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build multiple lookup indexes from telemetry data.

    Returns a dict with:
      - by_name:     normalized name -> best telemetry record
      - by_fullpath: normalized (path + "/" + name) -> best telemetry record
      - by_path:     normalized folder path -> [telemetry records]
      - all_records: list of all telemetry records (for fuzzy matching)
    """
    by_name: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_fullpath: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_path: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for rec in telemetry:
        name = rec["name"]
        path = rec.get("path", "")

        if name:
            key = _normalize_name(name)
            by_name[key].append(rec)

        if path and name:
            full = _normalize_path(path.rstrip("/") + "/" + name)
            by_fullpath[full].append(rec)

        if path:
            norm_path = _normalize_path(path)
            by_path[norm_path].append(rec)

    # Aggregate each group
    best_by_name: Dict[str, Dict[str, Any]] = {}
    for key, recs in by_name.items():
        best_by_name[key] = _aggregate_telemetry_recs(recs)

    best_by_fullpath: Dict[str, Dict[str, Any]] = {}
    for key, recs in by_fullpath.items():
        best_by_fullpath[key] = _aggregate_telemetry_recs(recs)

    return {
        "by_name": best_by_name,
        "by_fullpath": best_by_fullpath,
        "by_path": by_path,
        "all_records": telemetry,
    }


def _normalize_name(name: str) -> str:
    """Normalize a report name for matching."""
    n = name.lower().strip()
    n = n.lstrip("*")  # telemetry sometimes has leading asterisks
    n = re.sub(r"\s+", " ", n)
    return n.strip()


def _normalize_path(path: str) -> str:
    """Normalize a folder path for matching."""
    p = path.lower().strip().strip("/")
    p = re.sub(r"\s+", " ", p)
    return p


def _token_overlap_score(name_a: str, name_b: str) -> float:
    """Compute Jaccard similarity on word tokens between two names."""
    tokens_a = set(re.findall(r"[a-z0-9]+", name_a.lower()))
    tokens_b = set(re.findall(r"[a-z0-9]+", name_b.lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _find_best_fuzzy_match(
    inv_name: str,
    inv_path: str,
    telemetry_index: Dict[str, Any],
    threshold: float = 0.6,
) -> Optional[Dict[str, Any]]:
    """Find the best fuzzy match for an inventory report in telemetry.

    Strategies tried in order:
    1. Substring match — telemetry name contained in inventory name or vice versa
    2. Same folder + high token overlap (>= threshold)
    3. High token overlap across all telemetry (>= 0.75)
    """
    norm_inv_name = _normalize_name(inv_name)
    norm_inv_path = _normalize_path(inv_path) if inv_path else ""
    inv_tokens = set(re.findall(r"[a-z0-9]+", norm_inv_name))

    if not inv_tokens or len(inv_tokens) < 2:
        return None

    best_match: Optional[Dict[str, Any]] = None
    best_score: float = 0.0

    # Strategy 1 & 3: scan all telemetry names
    for tel_name, tel_rec in telemetry_index["by_name"].items():
        # Substring containment (either direction, min length 8 to avoid false positives)
        if len(norm_inv_name) >= 8 and len(tel_name) >= 8:
            if norm_inv_name in tel_name or tel_name in norm_inv_name:
                score = _token_overlap_score(norm_inv_name, tel_name)
                # Boost substring matches
                score = max(score, 0.7)
                if score > best_score:
                    best_score = score
                    best_match = tel_rec
                continue

        # Token overlap
        score = _token_overlap_score(norm_inv_name, tel_name)
        if score > best_score:
            best_score = score
            best_match = tel_rec

    # Strategy 2: same folder path boost
    if norm_inv_path and norm_inv_path in telemetry_index["by_path"]:
        folder_recs = telemetry_index["by_path"][norm_inv_path]
        for rec in folder_recs:
            tel_name = _normalize_name(rec.get("name", ""))
            score = _token_overlap_score(norm_inv_name, tel_name)
            # Boost for same folder
            boosted = score + 0.15
            if boosted > best_score:
                best_score = boosted
                best_match = _aggregate_telemetry_recs(
                    [r for r in folder_recs if _normalize_name(r.get("name", "")) == tel_name]
                ) if tel_name else rec

    if best_score >= threshold and best_match is not None:
        best_match = dict(best_match)
        best_match["_matchScore"] = round(best_score, 3)
        return best_match

    return None


def match_inventory_with_telemetry(
    inventory_reports: List[Dict[str, Any]],
    telemetry_index: Dict[str, Any],
    cutoff_date: datetime,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Match inventory reports against telemetry and classify them.

    Multi-tier matching:
      Tier 1: Exact name match (normalized)
      Tier 2: Full path match (folder + name)
      Tier 3: Fuzzy — substring containment, same-folder + token overlap, high token overlap

    Returns:
        {
            "matched": [...],        # reports found in telemetry
            "unmatched": [...],       # reports NOT in telemetry (candidates for retirement)
            "active": [...],          # reports with recent usage (keep)
            "retire": [...],          # reports to retire (no use or old use)
            "summary": {...}
        }
    """
    matched = []
    unmatched = []
    active = []
    retire = []
    match_tier_counts = {"exact_name": 0, "full_path": 0, "fuzzy": 0, "none": 0}

    by_name = telemetry_index["by_name"]
    by_fullpath = telemetry_index["by_fullpath"]

    for rec in inventory_reports:
        report_name = rec.get("name", "")
        report_id = rec.get("id", "")
        report_path = rec.get("folderPath", "") or rec.get("path", "") or ""

        entry = {
            "id": report_id,
            "name": report_name,
            "path": report_path,
            "owner": rec.get("owner"),
            "dateCreated": rec.get("dateCreated"),
            "dateModified": rec.get("dateModified"),
        }

        # Tier 1: Exact name match
        name_key = _normalize_name(report_name)
        telemetry_rec = by_name.get(name_key)
        match_tier = None

        if telemetry_rec:
            match_tier = "exact_name"
        else:
            # Tier 2: Full path match (folder/name)
            if report_path:
                full_key = _normalize_path(report_path.rstrip("/") + "/" + report_name)
                telemetry_rec = by_fullpath.get(full_key)
                if telemetry_rec:
                    match_tier = "full_path"

        if not telemetry_rec:
            # Tier 3: Fuzzy matching
            telemetry_rec = _find_best_fuzzy_match(
                report_name, report_path, telemetry_index
            )
            if telemetry_rec:
                match_tier = "fuzzy"

        if telemetry_rec and match_tier:
            match_tier_counts[match_tier] += 1
            entry["telemetryMatch"] = True
            entry["matchTier"] = match_tier
            entry["matchScore"] = telemetry_rec.get("_matchScore", 1.0)
            entry["matchedTelemetryName"] = telemetry_rec.get("name", "")
            entry["lastExecTs"] = telemetry_rec.get("lastExecTs")
            entry["lastExecDt"] = telemetry_rec["lastExecDt"].isoformat() if telemetry_rec.get("lastExecDt") else None
            entry["totalExecutions"] = telemetry_rec.get("totalExecutions", 0)
            entry["totalUsers"] = telemetry_rec.get("totalUsers", 0)
            entry["telemetryPath"] = telemetry_rec.get("path", "")
            matched.append(entry)

            # Check if last execution is before the cutoff
            if telemetry_rec.get("lastExecDt") and telemetry_rec["lastExecDt"] < cutoff_date:
                entry["retireReason"] = f"Last used {telemetry_rec['lastExecTs']} (>{int((cutoff_date - telemetry_rec['lastExecDt']).days)} days ago)"
                retire.append(entry)
            elif telemetry_rec.get("lastExecDt") is None:
                entry["retireReason"] = "No valid execution timestamp in telemetry"
                retire.append(entry)
            else:
                active.append(entry)
        else:
            match_tier_counts["none"] += 1
            entry["telemetryMatch"] = False
            entry["matchTier"] = "none"
            entry["retireReason"] = "No telemetry data found (never executed or not tracked)"
            unmatched.append(entry)
            retire.append(entry)

    summary = {
        "totalReports": len(inventory_reports),
        "matchedInTelemetry": len(matched),
        "unmatchedInTelemetry": len(unmatched),
        "activeReports": len(active),
        "retireReports": len(retire),
        "cutoffDate": cutoff_date.isoformat(),
        "cutoffMonths": 7,
        "matchTiers": match_tier_counts,
    }

    return {
        "matched": matched,
        "unmatched": unmatched,
        "active": active,
        "retire": retire,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MicroStrategy rationalization with telemetry-based retirement."
    )
    # Connection
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default=os.getenv("MSTR_PASSWORD", ""))
    parser.add_argument("--login-mode", type=int, default=1)
    parser.add_argument("--project-id", help="Project GUID")
    parser.add_argument("--project-name", help="Project name")
    parser.add_argument("--verify-ssl", action="store_true", default=False)
    parser.add_argument("--timeout", type=int, default=60)

    # Telemetry
    parser.add_argument("--telemetry-csv", required=True,
                        help="Path to telemetry CSV (report-telemetry-go.csv)")
    parser.add_argument("--retire-months", type=int, default=7,
                        help="Reports not used in this many months are flagged for retirement (default: 7)")

    # Phases
    parser.add_argument("--phases", default="inventory,paths,telemetry,dependencies,analysis",
                        help="Comma-separated phases (inventory,paths,telemetry,dependencies,analysis,sql)")
    parser.add_argument("--skip-types", default="",
                        help="Comma-separated categories to skip")
    parser.add_argument("--limit", type=int, default=None)

    # Enrichment
    parser.add_argument("--include-sql", action="store_true")
    parser.add_argument("--include-definitions", action="store_true")

    # Analysis
    parser.add_argument("--stale-days", type=int, default=365)
    parser.add_argument("--top-impact", type=int, default=50)

    # Execution
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--sql-batch-size", type=int, default=10)
    parser.add_argument("--sql-batch-delay", type=float, default=5.0)
    parser.add_argument("--dep-batch-size", type=int, default=50)
    parser.add_argument("--dep-batch-delay", type=float, default=3.0)
    parser.add_argument("--output-dir", default="mstr_rationalization_apr09")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verbose", action="store_true")

    return parser.parse_args(argv)


def _fmt_elapsed(seconds: float) -> str:
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    skip_types = {s.strip() for s in args.skip_types.split(",") if s.strip()}
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(output_dir) if args.resume else {
        "completedPhases": [], "startedAt": None, "project": None
    }

    client = RationalizationClient(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        login_mode=args.login_mode,
        verify_ssl=args.verify_ssl,
        timeout=args.timeout,
        delay=args.delay,
        sql_batch_size=args.sql_batch_size,
        sql_batch_delay=args.sql_batch_delay,
    )

    # Phases that require API access
    api_phases = {"inventory", "paths", "dependencies", "sql"}
    needs_api = bool(set(phases) & api_phases)

    try:
        run_start = time.time()

        if needs_api:
            client.login()
            project = client.resolve_project(
                project_id=args.project_id, project_name=args.project_name
            )
            manifest["startedAt"] = manifest.get("startedAt") or datetime.now(timezone.utc).isoformat()
            manifest["project"] = {"id": project.id, "name": project.name}
            manifest["baseUrl"] = args.base_url
            save_manifest(output_dir, manifest)
        else:
            # For local-only phases (telemetry, analysis), load project info from manifest
            project_info = manifest.get("project") or {}
            project = Project(
                id=project_info.get("id", args.project_id or ""),
                name=project_info.get("name", args.project_name or ""),
            )

        print(f"\n{'='*60}")
        print(f"  RATIONALIZATION WITH TELEMETRY")
        print(f"  Project: {project.name} ({project.id})")
        print(f"  Output:  {output_dir}")
        print(f"{'='*60}\n")

        # ================================================================
        # PHASE 1: Inventory
        # ================================================================
        inventory: Dict[str, List[Dict[str, Any]]] = {}
        if "inventory" in phases:
            phase_start = time.time()
            if args.resume and "inventory" in manifest.get("completedPhases", []):
                print("[Phase 1] Inventory — loading from prior run...")
                inv_dir = output_dir / "inventory"
                for category in OBJECT_TYPES:
                    cat_file = inv_dir / f"{category}.json"
                    if cat_file.exists():
                        inventory[category] = json.loads(cat_file.read_text(encoding="utf-8"))
            else:
                print("[Phase 1] Inventory — enumerating all objects...")
                inventory = build_inventory(
                    client, output_dir,
                    include_definitions=args.include_definitions,
                    include_sql=args.include_sql,
                    skip_types=skip_types,
                    limit=args.limit,
                    resume=args.resume,
                    verbose=args.verbose,
                )
                manifest["completedPhases"] = list(set(manifest.get("completedPhases", []) + ["inventory"]))
                save_manifest(output_dir, manifest)

            total = sum(len(recs) for recs in inventory.values())
            report_count = len(inventory.get("reports", []))
            print(f"  -> {total} objects across {len(inventory)} categories ({report_count} reports) [{_fmt_elapsed(time.time() - phase_start)}]")
        else:
            inv_dir = output_dir / "inventory"
            if inv_dir.exists():
                for category in OBJECT_TYPES:
                    cat_file = inv_dir / f"{category}.json"
                    if cat_file.exists():
                        inventory[category] = json.loads(cat_file.read_text(encoding="utf-8"))

        # ================================================================
        # PHASE 1.5: Folder Paths
        # ================================================================
        if "paths" in phases:
            phase_start = time.time()
            if args.resume and "paths" in manifest.get("completedPhases", []):
                print("[Phase 1.5] Folder paths — loading from prior run...")
                inv_dir = output_dir / "inventory"
                for category in OBJECT_TYPES:
                    cat_file = inv_dir / f"{category}.json"
                    if cat_file.exists():
                        inventory[category] = json.loads(cat_file.read_text(encoding="utf-8"))
            else:
                print("[Phase 1.5] Folder paths — resolving...")
                fetch_folder_paths(
                    client, output_dir, inventory,
                    resume=args.resume,
                    verbose=args.verbose,
                )
                manifest["completedPhases"] = list(set(manifest.get("completedPhases", []) + ["paths"]))
                save_manifest(output_dir, manifest)

            paths_count = sum(1 for recs in inventory.values() for r in recs if r.get("folderPath"))
            print(f"  -> {paths_count} folder paths resolved [{_fmt_elapsed(time.time() - phase_start)}]")

        # ================================================================
        # PHASE 2: Telemetry Matching
        # ================================================================
        if "telemetry" in phases:
            phase_start = time.time()
            print(f"\n[Phase 2] Telemetry matching — loading {args.telemetry_csv}...")

            telemetry_raw = load_telemetry_csv(args.telemetry_csv)
            print(f"  -> {len(telemetry_raw)} telemetry records loaded")

            telemetry_index = build_telemetry_index(telemetry_raw)
            print(f"  -> {len(telemetry_index['by_name'])} unique report names indexed")
            print(f"  -> {len(telemetry_index['by_fullpath'])} unique full paths indexed")

            # Calculate cutoff date (7 months ago from today)
            now = datetime.now(timezone.utc)
            cutoff_date = now - timedelta(days=args.retire_months * 30)
            print(f"  -> Retirement cutoff: {cutoff_date.strftime('%Y-%m-%d')} ({args.retire_months} months ago)")

            reports = inventory.get("reports", [])
            result = match_inventory_with_telemetry(
                reports, telemetry_index, cutoff_date, verbose=args.verbose
            )

            # Save telemetry results
            telemetry_dir = output_dir / "telemetry"
            telemetry_dir.mkdir(parents=True, exist_ok=True)
            write_json(telemetry_dir / "telemetry_match_result.json", {
                "summary": result["summary"],
                "matched": result["matched"],
                "unmatched": result["unmatched"],
            })
            write_json(telemetry_dir / "active_reports.json", result["active"])
            write_json(telemetry_dir / "retire_reports.json", result["retire"])

            # Write a human-readable retirement report
            retire_csv_path = telemetry_dir / "retire_reports.csv"
            with open(retire_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Report ID", "Report Name", "Path", "Owner",
                    "Date Created", "Date Modified",
                    "Telemetry Match", "Match Tier", "Match Score",
                    "Matched Telemetry Name",
                    "Last Execution", "Total Executions",
                    "Total Users", "Retire Reason"
                ])
                for r in result["retire"]:
                    writer.writerow([
                        r.get("id", ""),
                        r.get("name", ""),
                        r.get("path", ""),
                        r.get("owner", ""),
                        r.get("dateCreated", ""),
                        r.get("dateModified", ""),
                        "Yes" if r.get("telemetryMatch") else "No",
                        r.get("matchTier", ""),
                        r.get("matchScore", ""),
                        r.get("matchedTelemetryName", ""),
                        r.get("lastExecTs", ""),
                        r.get("totalExecutions", ""),
                        r.get("totalUsers", ""),
                        r.get("retireReason", ""),
                    ])

            # Also write active reports CSV
            active_csv_path = telemetry_dir / "active_reports.csv"
            with open(active_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Report ID", "Report Name", "Path", "Owner",
                    "Last Execution", "Total Executions", "Total Users"
                ])
                for r in result["active"]:
                    writer.writerow([
                        r.get("id", ""),
                        r.get("name", ""),
                        r.get("path", ""),
                        r.get("owner", ""),
                        r.get("lastExecTs", ""),
                        r.get("totalExecutions", ""),
                        r.get("totalUsers", ""),
                    ])

            s = result["summary"]
            tiers = s.get("matchTiers", {})
            print(f"\n  {'-'*50}")
            print(f"  TELEMETRY MATCHING RESULTS")
            print(f"  {'-'*50}")
            print(f"  Total reports in inventory:  {s['totalReports']}")
            print(f"  Matched in telemetry:        {s['matchedInTelemetry']}")
            print(f"    Tier 1 (exact name):       {tiers.get('exact_name', 0)}")
            print(f"    Tier 2 (full path):        {tiers.get('full_path', 0)}")
            print(f"    Tier 3 (fuzzy):            {tiers.get('fuzzy', 0)}")
            print(f"  No telemetry data:           {s['unmatchedInTelemetry']}")
            print(f"  {'- '*15}")
            print(f"  ACTIVE (keep):               {s['activeReports']}")
            print(f"  RETIRE (recommend removal):  {s['retireReports']}")
            print(f"  {'-'*50}")
            print(f"  [{_fmt_elapsed(time.time() - phase_start)}]")

            manifest["completedPhases"] = list(set(manifest.get("completedPhases", []) + ["telemetry"]))
            manifest["telemetrySummary"] = result["summary"]
            save_manifest(output_dir, manifest)

        # ================================================================
        # PHASE 3: Dependencies
        # ================================================================
        graph: Dict[str, Any] = {}
        if "dependencies" in phases:
            phase_start = time.time()
            if args.resume and "dependencies" in manifest.get("completedPhases", []):
                print("\n[Phase 3] Dependencies — loading from prior run...")
                graph = load_dependency_graph(output_dir)
            else:
                print("\n[Phase 3] Dependencies — building dependency graph...")
                graph = build_dependency_graph(
                    client, output_dir, inventory,
                    skip_types=skip_types,
                    dep_batch_size=args.dep_batch_size,
                    dep_batch_delay=args.dep_batch_delay,
                    resume=args.resume,
                    verbose=args.verbose,
                )
                manifest["completedPhases"] = list(set(manifest.get("completedPhases", []) + ["dependencies"]))
                save_manifest(output_dir, manifest)

            print(f"  -> {len(graph.get('edges', []))} edges [{_fmt_elapsed(time.time() - phase_start)}]")
        else:
            graph = load_dependency_graph(output_dir)

        # ================================================================
        # PHASE 4: Analysis (rationalization)
        # ================================================================
        if "analysis" in phases:
            phase_start = time.time()
            print("\n[Phase 4] Analysis — running rationalization...")
            summary = run_analysis(
                output_dir, inventory, graph,
                stale_days=args.stale_days,
                top_impact=args.top_impact,
                verbose=args.verbose,
            )
            manifest["completedPhases"] = list(set(manifest.get("completedPhases", []) + ["analysis"]))
            manifest["finishedAt"] = datetime.now(timezone.utc).isoformat()
            save_manifest(output_dir, manifest)

            print(f"\n  {'-'*50}")
            print(f"  RATIONALIZATION SUMMARY")
            print(f"  {'-'*50}")
            print(f"  Total objects:        {summary['totalObjects']}")
            print(f"  Dependency edges:     {summary['totalEdges']}")
            print(f"  Orphans:              {summary['orphanCount']}")
            print(f"  Duplicate SQL groups: {summary['duplicateSqlGroupCount']}")
            print(f"  Stale objects:        {summary['staleObjectCount']}")
            print(f"  Unused metrics:       {summary['unusedMetricCount']}")
            print(f"  Errors:               {summary['errorCount']}")
            print(f"  {'-'*50}")
            print(f"  [{_fmt_elapsed(time.time() - phase_start)}]")

        # ================================================================
        # PHASE 5: SQL Extraction (optional)
        # ================================================================
        if "sql" in phases:
            phase_start = time.time()
            print("\n[Phase 5] SQL Extraction (retained reports only)...")

            # Load retained report IDs from telemetry results
            retain_ids: Set[str] = set()
            active_file = output_dir / "telemetry" / "active_reports.json"
            if active_file.exists():
                active_data = json.loads(active_file.read_text(encoding="utf-8"))
                retain_ids = {r["id"] for r in active_data}
                print(f"  -> {len(retain_ids):,} retained report IDs loaded from telemetry results")
            else:
                print("  WARNING: No telemetry results found, run telemetry phase first.", file=sys.stderr)
                print("  Falling back to all reports minus orphans.", file=sys.stderr)

            # Build skip set: orphans + retired reports
            skip_ids: Set[str] = set()

            # Add orphans
            orphans_file = output_dir / "analysis" / "orphans.json"
            if orphans_file.exists():
                orphan_data = json.loads(orphans_file.read_text(encoding="utf-8"))
                skip_ids.update(o["id"] for o in orphan_data)

            # Add retired reports (everything NOT in the retain list)
            if retain_ids:
                for rec in inventory.get("reports", []):
                    if rec["id"] not in retain_ids:
                        skip_ids.add(rec["id"])
                total_reports = len(inventory.get("reports", []))
                skipping = sum(1 for r in inventory.get("reports", []) if r["id"] in skip_ids)
                print(f"  -> Skipping {skipping:,} reports (retired/orphaned), extracting SQL for {total_reports - skipping:,}")

            plan = build_sql_extraction_plan(inventory, skip_ids, verbose=True)
            execute_sql_phase(
                client, output_dir, inventory, plan,
                skip_types=skip_types,
                verbose=args.verbose,
            )
            manifest["completedPhases"] = list(set(manifest.get("completedPhases", []) + ["sql"]))
            save_manifest(output_dir, manifest)
            print(f"  [{_fmt_elapsed(time.time() - phase_start)}]")

        # ================================================================
        # Final summary
        # ================================================================
        total_elapsed = time.time() - run_start
        print(f"\n{'='*60}")
        print(f"  COMPLETE — Total time: {_fmt_elapsed(total_elapsed)}")
        print(f"  Output directory: {output_dir}")
        print(f"  Key outputs:")
        print(f"    - inventory/reports.json")
        print(f"    - telemetry/retire_reports.csv")
        print(f"    - telemetry/active_reports.csv")
        print(f"    - analysis/summary.json")
        print(f"    - rationalization_report.json")
        print(f"{'='*60}\n")

        return 0

    except Exception as exc:
        print(f"\nFATAL: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    finally:
        try:
            client.logout()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
