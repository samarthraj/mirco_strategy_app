#!/usr/bin/env python3
"""Fetch folder paths only for active (telemetry-matched) reports."""

from __future__ import annotations
import json, sys, time
from pathlib import Path
from mstr_project_rationalize import RationalizationClient, write_json

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch folder paths for active reports only")
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
    out_path = output_dir / "inventory" / "active_reports_paths.json"

    if not active_path.exists():
        print(f"ERROR: {active_path} not found. Run telemetry matching first.")
        return 1

    # Load active reports
    active_reports = json.loads(active_path.read_text(encoding="utf-8"))
    print(f"Active reports: {len(active_reports)}")

    # Load existing output if resuming
    done_ids = set()
    results = []
    if out_path.exists():
        results = json.loads(out_path.read_text(encoding="utf-8"))
        done_ids = {r["id"] for r in results}
        print(f"Resuming: {len(done_ids)} already fetched")

    remaining = [r for r in active_reports if r["id"] not in done_ids]
    print(f"Remaining: {len(remaining)}")
    if not remaining:
        print("All paths already fetched!")
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

    t0 = time.time()
    fetched = 0
    errors = 0

    try:
        for i, rec in enumerate(remaining):
            obj_id = rec.get("id")
            obj_type = rec.get("type", 3)  # default to report type

            try:
                path = client.get_object_folder_path(obj_id, obj_type)
                rec["folderPath"] = path or ""
                fetched += 1
            except Exception as e:
                rec["folderPath"] = ""
                rec.setdefault("errors", {})["path"] = str(e)
                errors += 1

            results.append(rec)
            done = len(done_ids) + fetched + errors
            elapsed = time.time() - t0
            rate = (fetched + errors) / elapsed if elapsed > 0 else 0
            eta = (len(remaining) - fetched - errors) / rate if rate > 0 else 0

            if (fetched + errors) % 100 == 0 or (fetched + errors) == 1:
                print(f"  [{done}/{len(active_reports)}] "
                      f"({fetched} ok, {errors} err, "
                      f"ETA: {int(eta//60)}m {int(eta%60)}s)", flush=True)

            if (fetched + errors) % args.save_interval == 0:
                write_json(out_path, results)
                print(f"  [save] progress saved ({done}/{len(active_reports)})", flush=True)

    except KeyboardInterrupt:
        print("\nInterrupted! Saving progress...")
    finally:
        write_json(out_path, results)
        client.logout()

    elapsed = time.time() - t0
    print(f"\nDone! {fetched} paths fetched, {errors} errors [{elapsed:.1f}s]")
    print(f"Output: {out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
