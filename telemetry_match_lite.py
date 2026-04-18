#!/usr/bin/env python3
"""Lightweight telemetry matcher — streams inventory JSON to avoid loading 732MB into memory."""

from __future__ import annotations
import argparse, csv, json, os, re, sys, time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Telemetry loader (from rationalize_with_telemetry.py)
# ---------------------------------------------------------------------------

def load_telemetry_csv(csv_path: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            if len(row) < 12:
                continue
            last_exec_ts = row[11].strip()
            last_exec_dt = None
            if last_exec_ts:
                try:
                    last_exec_dt = datetime.strptime(last_exec_ts, "%m/%d/%Y %H:%M").replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            executions = 0
            try:
                executions = int(row[5].strip().replace(",", ""))
            except ValueError:
                pass
            records.append({
                "name": row[0].strip(),
                "path": row[1].strip(),
                "user": row[2].strip(),
                "objectType": row[3].strip(),
                "project": row[4].strip(),
                "executions": executions,
                "lastExecTs": last_exec_ts,
                "lastExecDt": last_exec_dt,
            })
    return records

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    n = name.lower().strip().lstrip("*")
    return re.sub(r"\s+", " ", n).strip()

def _normalize_path(path: str) -> str:
    p = path.lower().strip().strip("/")
    return re.sub(r"\s+", " ", p)

def _token_overlap_score(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]+", a.lower()))
    tb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def _aggregate(recs):
    recs_with_dt = [r for r in recs if r["lastExecDt"] is not None]
    best = dict(max(recs_with_dt, key=lambda r: r["lastExecDt"]) if recs_with_dt else recs[0])
    best["totalExecutions"] = sum(r["executions"] for r in recs)
    best["totalUsers"] = len(set(r["user"] for r in recs if r["user"]))
    return best

def build_telemetry_index(telemetry):
    by_name = defaultdict(list)
    by_fullpath = defaultdict(list)
    by_path = defaultdict(list)
    for rec in telemetry:
        name, path = rec["name"], rec.get("path", "")
        if name:
            by_name[_normalize_name(name)].append(rec)
        if path and name:
            by_fullpath[_normalize_path(path.rstrip("/") + "/" + name)].append(rec)
        if path:
            by_path[_normalize_path(path)].append(rec)
    best_by_name = {k: _aggregate(v) for k, v in by_name.items()}
    best_by_fullpath = {k: _aggregate(v) for k, v in by_fullpath.items()}
    return {"by_name": best_by_name, "by_fullpath": best_by_fullpath, "by_path": by_path, "all_records": telemetry}

def _find_best_fuzzy(inv_name, inv_path, idx, threshold=0.6):
    norm = _normalize_name(inv_name)
    norm_path = _normalize_path(inv_path) if inv_path else ""
    inv_tokens = set(re.findall(r"[a-z0-9]+", norm))
    if not inv_tokens or len(inv_tokens) < 2:
        return None
    best, best_score = None, 0.0
    for tel_name, tel_rec in idx["by_name"].items():
        if len(norm) >= 8 and len(tel_name) >= 8:
            if norm in tel_name or tel_name in norm:
                score = max(_token_overlap_score(norm, tel_name), 0.7)
                if score > best_score:
                    best_score, best = score, tel_rec
                continue
        score = _token_overlap_score(norm, tel_name)
        if score > best_score:
            best_score, best = score, tel_rec
    if norm_path and norm_path in idx["by_path"]:
        for rec in idx["by_path"][norm_path]:
            tn = _normalize_name(rec.get("name", ""))
            boosted = _token_overlap_score(norm, tn) + 0.15
            if boosted > best_score:
                best_score, best = boosted, rec
    if best_score >= threshold and best is not None:
        best = dict(best)
        best["_matchScore"] = round(best_score, 3)
        return best
    return None

# ---------------------------------------------------------------------------
# Lightweight inventory loader — only extract needed fields
# ---------------------------------------------------------------------------

def load_reports_lite(reports_json_path: str) -> List[Dict[str, Any]]:
    """Stream-parse reports.json extracting only fields needed for telemetry matching."""
    import ijson
    reports = []
    with open(reports_json_path, "rb") as f:
        for obj in ijson.items(f, "item"):
            reports.append({
                "id": obj.get("id", ""),
                "name": obj.get("name", ""),
                "path": obj.get("folderPath") or obj.get("path", ""),
                "owner": obj.get("owner"),
                "dateCreated": obj.get("dateCreated"),
                "dateModified": obj.get("dateModified"),
            })
            if len(reports) % 5000 == 0:
                print(f"  loaded {len(reports)} reports...", flush=True)
    return reports

def load_reports_lite_stdlib(reports_json_path: str) -> List[Dict[str, Any]]:
    """Fallback if ijson not installed — use json but only keep needed fields."""
    print("  loading reports.json (this may take a minute for large files)...", flush=True)
    reports = []
    with open(reports_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for obj in data:
        reports.append({
            "id": obj.get("id", ""),
            "name": obj.get("name", ""),
            "path": obj.get("folderPath") or obj.get("path", ""),
            "owner": obj.get("owner"),
            "dateCreated": obj.get("dateCreated"),
            "dateModified": obj.get("dateModified"),
        })
    del data  # free memory
    return reports

def load_reports(reports_json_path: str) -> List[Dict[str, Any]]:
    try:
        import ijson
        print("  using ijson streaming parser", flush=True)
        return load_reports_lite(reports_json_path)
    except ImportError:
        print("  ijson not installed, using stdlib json", flush=True)
        return load_reports_lite_stdlib(reports_json_path)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

def main():
    parser = argparse.ArgumentParser(description="Lightweight telemetry matcher")
    parser.add_argument("--telemetry-csv", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--retire-months", type=int, default=7)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    reports_path = output_dir / "inventory" / "reports.json"
    if not reports_path.exists():
        print(f"ERROR: {reports_path} not found"); return 1

    t0 = time.time()

    # Load telemetry
    print(f"Loading telemetry from {args.telemetry_csv}...", flush=True)
    telemetry = load_telemetry_csv(args.telemetry_csv)
    print(f"  {len(telemetry)} telemetry records loaded", flush=True)
    idx = build_telemetry_index(telemetry)
    print(f"  {len(idx['by_name'])} unique names indexed", flush=True)

    # Load inventory (lightweight)
    print(f"Loading inventory from {reports_path}...", flush=True)
    reports = load_reports(str(reports_path))
    print(f"  {len(reports)} reports loaded [{time.time()-t0:.1f}s]", flush=True)

    # Match
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.retire_months * 30)
    print(f"Matching (cutoff: {cutoff.strftime('%Y-%m-%d')})...", flush=True)

    matched, unmatched, active, retire = [], [], [], []
    tiers = {"exact_name": 0, "full_path": 0, "fuzzy": 0, "none": 0}

    for i, rec in enumerate(reports):
        name = rec.get("name", "")
        path = rec.get("path", "")
        entry = dict(rec)

        name_key = _normalize_name(name)
        tel = idx["by_name"].get(name_key)
        tier = None
        if tel:
            tier = "exact_name"

        if tel and tier:
            tiers[tier] += 1
            entry["telemetryMatch"] = True
            entry["matchTier"] = tier
            entry["matchScore"] = tel.get("_matchScore", 1.0)
            entry["matchedTelemetryName"] = tel.get("name", "")
            entry["lastExecTs"] = tel.get("lastExecTs")
            entry["lastExecDt"] = tel["lastExecDt"].isoformat() if tel.get("lastExecDt") else None
            entry["totalExecutions"] = tel.get("totalExecutions", 0)
            entry["totalUsers"] = tel.get("totalUsers", 0)
            entry["telemetryPath"] = tel.get("path", "")
            matched.append(entry)
            if tel.get("lastExecDt") and tel["lastExecDt"] < cutoff:
                entry["retireReason"] = f"Last used {tel['lastExecTs']}"
                retire.append(entry)
            elif tel.get("lastExecDt") is None:
                entry["retireReason"] = "No valid execution timestamp"
                retire.append(entry)
            else:
                active.append(entry)
        else:
            tiers["none"] += 1
            entry["telemetryMatch"] = False
            entry["matchTier"] = "none"
            entry["retireReason"] = "No telemetry data found"
            unmatched.append(entry)
            retire.append(entry)

        if (i + 1) % 5000 == 0:
            print(f"  matched {i+1}/{len(reports)}...", flush=True)

    summary = {
        "totalReports": len(reports),
        "matchedInTelemetry": len(matched),
        "unmatchedInTelemetry": len(unmatched),
        "activeReports": len(active),
        "retireReports": len(retire),
        "cutoffDate": cutoff.isoformat(),
        "cutoffMonths": args.retire_months,
        "matchTiers": tiers,
    }

    # Save
    tel_dir = output_dir / "telemetry"
    tel_dir.mkdir(parents=True, exist_ok=True)
    write_json(tel_dir / "telemetry_match_result.json", {"summary": summary, "matched": matched, "unmatched": unmatched})
    write_json(tel_dir / "active_reports.json", active)
    write_json(tel_dir / "retire_reports.json", retire)

    # CSV
    with open(tel_dir / "retire_reports.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Report ID", "Report Name", "Path", "Owner", "Date Created", "Date Modified",
                     "Telemetry Match", "Match Tier", "Match Score", "Matched Telemetry Name",
                     "Last Execution", "Total Executions", "Total Users", "Retire Reason"])
        for r in retire:
            w.writerow([r.get("id",""), r.get("name",""), r.get("path",""), r.get("owner",""),
                         r.get("dateCreated",""), r.get("dateModified",""),
                         "Yes" if r.get("telemetryMatch") else "No", r.get("matchTier",""),
                         r.get("matchScore",""), r.get("matchedTelemetryName",""),
                         r.get("lastExecTs",""), r.get("totalExecutions",""),
                         r.get("totalUsers",""), r.get("retireReason","")])

    # Update manifest
    manifest_path = output_dir / "run_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if "telemetry" not in manifest.get("completedPhases", []):
            manifest["completedPhases"].append("telemetry")
        manifest["telemetrySummary"] = summary
        write_json(manifest_path, manifest)

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  TELEMETRY MATCH COMPLETE [{elapsed:.1f}s]")
    print(f"  Total reports:   {summary['totalReports']}")
    print(f"  Matched:         {summary['matchedInTelemetry']}")
    print(f"    Exact name:    {tiers['exact_name']}")
    print(f"    Full path:     {tiers['full_path']}")
    print(f"    Fuzzy:         {tiers['fuzzy']}")
    print(f"  Unmatched:       {summary['unmatchedInTelemetry']}")
    print(f"  Active (keep):   {summary['activeReports']}")
    print(f"  Retire:          {summary['retireReports']}")
    print(f"{'='*60}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
