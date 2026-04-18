#!/usr/bin/env python3
"""Re-fetch definitions for reports where extract_units returned 0 attrs/metrics
(flat-shape definitions missed by the old extraction logic).
"""

import os
import json
import time
from pathlib import Path
from mstr_project_rationalize import RationalizationClient, enrich_report


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="INSIGHT/rationalization")
    parser.add_argument("--save-interval", type=int, default=100)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    enriched_path = output_dir / "inventory" / "active_reports_enriched.json"

    enriched = json.loads(enriched_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(enriched)} enriched reports")

    # Find reports with definition but 0 attributes/metrics
    targets = [
        r for r in enriched
        if r.get("definition") and not r.get("attributes") and not r.get("metrics")
    ]
    print(f"Targets (flat-shape, need re-extract): {len(targets)}")
    if not targets:
        print("Nothing to do.")
        return 0

    client = RationalizationClient(
        base_url="https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api",
        username=os.environ["MSTR_USERNAME"],
        password=os.environ["MSTR_PASSWORD"],
        login_mode=1,
        verify_ssl=False,
        timeout=60,
        delay=0.1,
    )
    client.login()
    client.project_id = "6104D29041297D66C6BD16B602F2705F"

    enriched_by_id = {r["id"]: r for r in enriched}

    t0 = time.time()
    ok = err = 0

    try:
        for i, rec in enumerate(targets, 1):
            if "errors" not in rec:
                rec["errors"] = {}

            # Clear prior data and re-enrich
            rec.pop("attributes", None)
            rec.pop("metrics", None)
            rec.pop("filter", None)
            rec.pop("definition", None)

            try:
                enrich_report(client, rec, include_definitions=True, include_sql=False)
                if rec.get("attributes") or rec.get("metrics"):
                    ok += 1
                else:
                    err += 1
            except Exception as e:
                rec.setdefault("errors", {})["definition"] = str(e)
                err += 1

            enriched_by_id[rec["id"]] = rec

            if i % 20 == 0 or i == 1:
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(targets) - i) / rate if rate > 0 else 0
                print(f"  [{i}/{len(targets)}] ok={ok} err={err} "
                      f"rate={rate:.1f}/s eta={int(eta//60)}m{int(eta%60)}s",
                      flush=True)

            if i % args.save_interval == 0:
                _save(enriched_path, list(enriched_by_id.values()))
                print(f"  [save] progress saved", flush=True)

    except KeyboardInterrupt:
        print("\nInterrupted! Saving...")
    finally:
        _save(enriched_path, list(enriched_by_id.values()))
        client.logout()

    print(f"\nDone. Recovered attributes for {ok} reports, {err} still empty [{time.time()-t0:.1f}s]")
    return 0


def _save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
