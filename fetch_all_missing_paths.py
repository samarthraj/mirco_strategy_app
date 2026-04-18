#!/usr/bin/env python3
"""Fetch folder paths for ACTIVE reports missing them in inventory_all.json.

Scope: only reports with status == 'active' are path-enriched. Retired and
collision-collapsed reports are skipped (they're typically not worth the API
calls, especially for large projects like INSIGHT with 87k retired reports).

Looks up each unpathed active report via GET /objects/{id}?type=3 and builds a
folder path by walking the `ancestors` array. Writes incrementally so
interrupted runs can resume.

Usage:
  python fetch_all_missing_paths.py \
    --base-url https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api \
    --username bourntec_mstr \
    --password "$MSTR_PASSWORD" \
    --project-name "Global Operational"

After it finishes, rerun the dashboard build script for that project to merge
the new paths into inventory_all.json / retired.json.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

from mstr_report_inventory import MstrClient


def build_path_from_ancestors(ancestors: list) -> str:
    """Sort by level desc (root first) and join names."""
    if not ancestors:
        return ""
    sorted_anc = sorted(ancestors, key=lambda a: a.get("level", 0), reverse=True)
    return "/".join(a.get("name", "") for a in sorted_anc if a.get("name"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch folder paths for all reports missing them"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default="")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--login-mode", type=int, default=1)
    parser.add_argument("--verify-ssl", action="store_true", default=False)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--delay", type=float, default=0.1,
                        help="Seconds between API calls (default 0.1)")
    parser.add_argument("--save-interval", type=int, default=200,
                        help="Save progress every N paths (default 200)")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument(
        "--statuses",
        default="active",
        help="Comma-separated list of statuses to fetch paths for "
             "(active, retired, collapsed, or 'all' for all three). Default: active",
    )
    args = parser.parse_args()

    # Resolve the project directory from the project name
    project_dir = Path(args.project_name)
    if not project_dir.exists():
        # Fallback for INSIGHT which lives under INSIGHT/rationalization/
        alt = Path(args.project_name) / "rationalization"
        if alt.exists():
            project_dir = alt
        else:
            print(f"ERROR: project directory not found for {args.project_name}")
            return 1

    # Read the public/data inventory file — that's the canonical list the UI uses
    public_inv_path = Path("public/data") / args.project_name / "inventory_all.json"
    if not public_inv_path.exists():
        print(f"ERROR: {public_inv_path} not found. Run the dashboard build script first.")
        return 1

    with open(public_inv_path, "r", encoding="utf-8") as f:
        inventory_all = json.load(f)

    # Decide which statuses to include
    if args.statuses.strip().lower() == "all":
        wanted = {"active", "retired", "collapsed"}
    else:
        wanted = {s.strip() for s in args.statuses.split(",") if s.strip()}
    print(f"Including statuses: {sorted(wanted)}")

    scoped_records = [r for r in inventory_all if r.get("status") in wanted]
    needs_path = [r["id"] for r in scoped_records if not r.get("path") and r.get("id")]
    # Breakdown by status
    from collections import Counter
    status_need = Counter(r.get("status") for r in scoped_records if not r.get("path") and r.get("id"))
    print(f"Inventory total: {len(inventory_all):,}")
    print(f"In-scope reports: {len(scoped_records):,}")
    print(f"In-scope with path already: {len(scoped_records) - len(needs_path):,}")
    print(f"Needing path fetch: {len(needs_path):,}")
    for s, n in status_need.most_common():
        print(f"  {s}: {n:,}")

    # Cache file — lets us resume after interruption
    cache_path = project_dir / "inventory" / "fetched_paths.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fetched_paths = {}
    if cache_path.exists():
        fetched_paths = json.loads(cache_path.read_text(encoding="utf-8"))
        print(f"Resuming: {len(fetched_paths):,} paths already cached")

    # Filter to what's actually left to fetch
    to_fetch = [rid for rid in needs_path if rid not in fetched_paths]
    print(f"Remaining to fetch: {len(to_fetch):,}")
    if not to_fetch:
        print("Nothing to fetch. Exiting.")
        return 0

    # Set up client
    client = MstrClient(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        login_mode=args.login_mode,
        verify_ssl=args.verify_ssl,
        timeout=args.timeout,
    )
    client.login()
    project = client.resolve_project(project_id=None, project_name=args.project_name)
    print(f"Project: {project.name} ({project.id})\n")

    ok = 0
    errors = 0
    empty = 0
    start = time.time()

    for i, rid in enumerate(to_fetch, 1):
        try:
            # Simple retry loop for transient errors
            resp = None
            for attempt in range(args.max_retries + 1):
                try:
                    resp = client.session.get(
                        client._url(f"/objects/{rid}"),
                        headers=client._headers(),
                        params={"type": 3},
                        verify=args.verify_ssl,
                        timeout=args.timeout,
                    )
                    if resp.status_code in (429, 500, 502, 503) and attempt < args.max_retries:
                        wait = min(2 ** attempt, 30)
                        print(f"  [retry] {resp.status_code} — waiting {wait}s", flush=True)
                        time.sleep(wait)
                        continue
                    break
                except Exception as e:
                    if attempt < args.max_retries:
                        time.sleep(2 ** attempt)
                        continue
                    raise

            if not resp or not resp.ok:
                fetched_paths[rid] = {"__error": f"HTTP {resp.status_code if resp else 'none'}"}
                errors += 1
            else:
                data = resp.json()
                path = build_path_from_ancestors(data.get("ancestors", []))
                fetched_paths[rid] = {"path": path}
                if path:
                    ok += 1
                else:
                    empty += 1
        except Exception as e:
            fetched_paths[rid] = {"__error": str(e)[:100]}
            errors += 1

        # Progress print + throttle + save
        if i % 50 == 0 or i == len(to_fetch):
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            remaining_sec = (len(to_fetch) - i) / rate if rate > 0 else 0
            mins = int(remaining_sec / 60)
            print(
                f"  [{i}/{len(to_fetch)}] ok={ok}, empty={empty}, err={errors}  "
                f"rate={rate:.1f}/s, ~{mins}m remaining",
                flush=True,
            )

        if i % args.save_interval == 0:
            cache_path.write_text(json.dumps(fetched_paths), encoding="utf-8")

        if args.delay > 0:
            time.sleep(args.delay)

    # Final save
    cache_path.write_text(json.dumps(fetched_paths), encoding="utf-8")
    print(
        f"\nDone. ok={ok}, empty={empty}, err={errors}"
        f"\nSaved cache to: {cache_path}"
        f"\n\nNext step: rerun the dashboard build script for this project to"
        f" merge these paths into inventory_all.json / retired.json."
    )

    client.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
