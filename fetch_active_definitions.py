#!/usr/bin/env python3
"""Fetch report definitions only for active (telemetry-matched) reports."""

from __future__ import annotations
import json, sys, time
from pathlib import Path
from mstr_project_rationalize import (
    RationalizationClient, enrich_report, _is_enriched
)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch definitions for active reports only")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default="")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--login-mode", type=int, default=1)
    parser.add_argument("--verify-ssl", action="store_true", default=False)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--save-interval", type=int, default=200)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    active_path = output_dir / "telemetry" / "active_reports.json"
    reports_path = output_dir / "inventory" / "reports.json"

    if not active_path.exists():
        print(f"ERROR: {active_path} not found. Run telemetry matching first.")
        return 1

    # Load active report IDs
    active = json.loads(active_path.read_text(encoding="utf-8"))
    active_ids = {r["id"] for r in active}
    print(f"Active reports to enrich: {len(active_ids)}")

    # Load full inventory
    print("Loading reports.json...")
    reports = json.loads(reports_path.read_text(encoding="utf-8"))
    print(f"Total reports in inventory: {len(reports)}")

    # Filter to active only
    active_reports = [r for r in reports if r.get("id") in active_ids]
    print(f"Matched active reports in inventory: {len(active_reports)}")

    # Check how many already enriched
    already = sum(1 for r in active_reports if _is_enriched(r))
    remaining = len(active_reports) - already
    print(f"Already enriched: {already}, Remaining: {remaining}")
    if remaining == 0:
        print("All active reports already enriched!")
        return 0

    # Connect
    client = RationalizationClient(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        login_mode=args.login_mode,
        verify_ssl=args.verify_ssl,
        timeout=args.timeout,
        delay=args.delay,
    )
    client.login()
    project = client.resolve_project(project_id=None, project_name=args.project_name)
    print(f"Project: {project.name} ({project.id})")

    # Output file — separate from the original inventory
    enriched_path = output_dir / "inventory" / "active_reports_enriched.json"

    # Load existing enriched file if resuming
    enriched_results = []
    enriched_ids = set()
    if enriched_path.exists():
        enriched_results = json.loads(enriched_path.read_text(encoding="utf-8"))
        enriched_ids = {r["id"] for r in enriched_results}
        print(f"Resuming: {len(enriched_results)} already in {enriched_path.name}")

    t0 = time.time()
    enriched_count = 0
    error_count = 0

    try:
        for i, rec in enumerate(active_reports):
            if rec["id"] in enriched_ids:
                continue

            if "errors" not in rec:
                rec["errors"] = {}

            try:
                enrich_report(client, rec, include_definitions=True, include_sql=False)
                enriched_count += 1
            except Exception as e:
                rec["errors"]["definition"] = str(e)
                error_count += 1

            enriched_results.append(rec)
            enriched_ids.add(rec["id"])

            total_done = len(enriched_ids)
            elapsed = time.time() - t0
            rate = (enriched_count + error_count) / elapsed if elapsed > 0 else 0
            eta = (len(active_reports) - total_done) / rate if rate > 0 else 0

            if (enriched_count + error_count) % 50 == 0 or (enriched_count + error_count) == 1:
                print(f"  [{total_done}/{len(active_reports)}] "
                      f"{rec.get('name','')} "
                      f"({enriched_count} ok, {error_count} err, "
                      f"ETA: {int(eta//60)}m {int(eta%60)}s)", flush=True)

            # Save periodically
            if (enriched_count + error_count) % args.save_interval == 0:
                enriched_path.write_text(json.dumps(enriched_results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
                print(f"  [save] progress saved to {enriched_path.name} ({total_done}/{len(active_reports)})", flush=True)

    except KeyboardInterrupt:
        print("\nInterrupted! Saving progress...")
    finally:
        # Final save
        enriched_path.write_text(json.dumps(enriched_results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        client.logout()

    elapsed = time.time() - t0
    print(f"\nDone! Enriched {enriched_count} reports, {error_count} errors [{elapsed:.1f}s]")
    print(f"Output: {enriched_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
