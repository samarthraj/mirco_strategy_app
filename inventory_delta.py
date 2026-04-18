#!/usr/bin/env python3
"""Delta inventory — find reports added since the last inventory pull.

Telemetry-first mode: only enrich new reports that match telemetry (are active).
Unmatched new reports are added with metadata only, saving ~70% enrichment time.
"""

from __future__ import annotations
import argparse, csv, json, os, re, sys, time
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from pathlib import Path

from mstr_project_rationalize import RationalizationClient, enrich_report


def load_existing_ids(inventory_path: Path) -> set:
    if not inventory_path.exists():
        return set()
    print(f"Loading existing inventory from {inventory_path}...", flush=True)
    with open(inventory_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    ids = {r["id"] for r in data if r.get("id")}
    print(f"  {len(ids):,} existing report IDs", flush=True)
    return ids


def _normalize_name(name: str) -> str:
    n = name.lower().strip().lstrip("*")
    return re.sub(r"\s+", " ", n).strip()


def load_telemetry_names(csv_path: str) -> set:
    """Load set of normalized telemetry report names."""
    names = set()
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 1:
                continue
            n = _normalize_name(row[0])
            if n:
                names.add(n)
    return names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default=os.getenv("MSTR_PASSWORD", ""))
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--project-id")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--existing-inventory", help="Path to existing reports.json (to diff against)")
    parser.add_argument("--telemetry-csv", help="If provided, only enrich new reports matching telemetry")
    parser.add_argument("--include-definitions", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    inv_dir = output_dir / "inventory"
    inv_dir.mkdir(parents=True, exist_ok=True)

    existing_path = Path(args.existing_inventory) if args.existing_inventory else inv_dir / "reports.json"
    existing_ids = load_existing_ids(existing_path)

    client = RationalizationClient(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        login_mode=1,
        verify_ssl=False,
        timeout=60,
        delay=0.1,
    )
    client.login()
    project = client.resolve_project(project_id=args.project_id, project_name=args.project_name)
    print(f"Project: {project.name} ({project.id})", flush=True)

    # Enumerate all reports (type 3) — returns id, name, dateCreated, dateModified, etc.
    print(f"\nEnumerating all reports via search API...", flush=True)
    t0 = time.time()
    all_reports = client.search_objects_all(3, page_size=1000)
    print(f"  {len(all_reports):,} reports found [{time.time()-t0:.1f}s]", flush=True)

    # Diff
    new_reports = [r for r in all_reports if r["id"] not in existing_ids]
    print(f"\nDelta: {len(new_reports):,} new reports", flush=True)

    if not new_reports:
        print("Nothing to do.")
        return 0

    # Telemetry-first filter
    to_enrich = new_reports
    not_enriched = []
    if args.telemetry_csv:
        print(f"\nLoading telemetry names from {args.telemetry_csv}...", flush=True)
        tel_names = load_telemetry_names(args.telemetry_csv)
        print(f"  {len(tel_names):,} unique normalized telemetry names", flush=True)

        matched = []
        unmatched = []
        for r in new_reports:
            if _normalize_name(r.get("name", "")) in tel_names:
                matched.append(r)
            else:
                r.setdefault("errors", {})
                r["category"] = "reports"
                unmatched.append(r)
        to_enrich = matched
        not_enriched = unmatched
        print(f"  Matching telemetry (active):   {len(matched):,} -> will enrich", flush=True)
        print(f"  No telemetry match (retire):   {len(unmatched):,} -> metadata only", flush=True)

    # Enrich active new reports
    print(f"\nEnriching {len(to_enrich):,} active new reports...", flush=True)
    t0 = time.time()
    for i, rec in enumerate(to_enrich, 1):
        rec.setdefault("errors", {})
        rec["category"] = "reports"
        enrich_report(client, rec, include_definitions=args.include_definitions, include_sql=False)
        if args.verbose and i % 100 == 0:
            elapsed = time.time() - t0
            rate = i / elapsed
            remaining = (len(to_enrich) - i) / rate
            print(f"  [{i}/{len(to_enrich)}] {rec.get('name','')[:50]} — rate {rate:.1f}/s, ETA {remaining/60:.1f}m", flush=True)

    new_reports = to_enrich + not_enriched

    # Merge with existing and save
    if existing_path.exists():
        print(f"\nMerging with existing inventory...", flush=True)
        with open(existing_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        merged = existing + new_reports
    else:
        merged = new_reports

    out_path = inv_dir / "reports.json"
    print(f"Writing {len(merged):,} reports to {out_path}...", flush=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, default=str)

    print(f"\nDone. Total reports: {len(merged):,} (added {len(new_reports):,})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
