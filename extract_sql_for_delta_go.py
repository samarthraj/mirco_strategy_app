#!/usr/bin/env python3
"""Extract SQL for Global Operational delta-added reports, record errors, merge to UI."""

import os
import json
import sys
import time
from pathlib import Path
from mstr_project_rationalize import RationalizationClient, _extract_sql_for_report


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--save-interval", type=int, default=100)
    parser.add_argument("--resume", action="store_true", default=True)
    args = parser.parse_args()

    project_dir = Path("Global Operational")
    enriched_path = project_dir / "inventory" / "reports_added_enriched.json"
    out_path = project_dir / "inventory" / "reports_added_sql.json"

    enriched = json.loads(enriched_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(enriched)} delta-added reports")

    # Load already-processed
    done_ids = set()
    results = []
    if args.resume and out_path.exists():
        results = json.loads(out_path.read_text(encoding="utf-8"))
        done_ids = {r["id"] for r in results}
        print(f"Resuming: {len(done_ids)} already processed")

    # Filter to reports needing SQL (not cube-sourced, no errors yet)
    to_process = []
    for r in enriched:
        if r["id"] in done_ids:
            continue
        if r.get("sql"):
            continue
        if r.get("sourceCubeId"):
            # Skip cube-sourced
            r.setdefault("errors", {})["sql"] = "Skipped: cube-sourced report"
            results.append(r)
            continue
        to_process.append(r)

    print(f"To process: {len(to_process)}")

    client = RationalizationClient(
        base_url="https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api",
        username=os.environ["MSTR_USERNAME"],
        password=os.environ["MSTR_PASSWORD"],
        login_mode=1,
        verify_ssl=False,
        timeout=60,
        delay=0.1,
        sql_batch_size=10,
        sql_batch_delay=5.0,
    )
    client.login()
    client.project_id = "E77B77894C04BF0E6D244F9363CFAF64"

    t0 = time.time()
    ok = err = 0
    try:
        for i, rec in enumerate(to_process, 1):
            if "errors" not in rec:
                rec["errors"] = {}
            _extract_sql_for_report(client, rec)
            if rec.get("sql"):
                ok += 1
            else:
                err += 1
            results.append(rec)

            if i % 20 == 0 or i == 1:
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(to_process) - i) / rate if rate > 0 else 0
                print(f"  [{i}/{len(to_process)}] ok={ok} err={err} "
                      f"rate={rate:.1f}/s eta={int(eta//60)}m{int(eta%60)}s",
                      flush=True)

            if i % args.save_interval == 0:
                out_path.write_text(
                    json.dumps(results, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8"
                )
                print(f"  [save] progress saved", flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted! Saving...")
    finally:
        out_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8"
        )
        client.logout()

    print(f"\nDone. {ok} with SQL, {err} errors [{time.time()-t0:.1f}s]")
    print(f"Output: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
