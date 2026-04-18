#!/usr/bin/env python3
"""Enumerate Global Operational dossiers (type=55) via REST API.

The primary inventory script (mstr_report_inventory.py) queries
/searches/results?type=3, which only returns reports. Dossiers / Documents
are type=55 and were never enumerated. UserActivity analysis found ~219
GO-scoped dossier Object IDs that weren't in our inventory.

This script:
  1. Logs in as bourntec_mstr
  2. Enumerates /searches/results?type=55 in Global Operational (with pagination)
  3. Writes results to Global Operational/inventory/dossiers.json
  4. Optionally cross-checks against the 219 dossier IDs known from UserActivity

Usage:
  python enumerate_go_dossiers.py
"""
from __future__ import annotations
import csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mstr_report_inventory import MstrClient

BASE_URL = os.environ.get("MSTR_BASE_URL", "https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api")
USERNAME = os.environ["MSTR_USERNAME"]
PASSWORD = os.environ["MSTR_PASSWORD"]
PROJECT_ID = "E77B77894C04BF0E6D244F9363CFAF64"
PROJECT_NAME = "Global Operational"
DOSSIER_TYPE = 55

PROJECT_DIR = Path("Global Operational")
OUT_FILE = PROJECT_DIR / "inventory" / "dossiers.json"


def fetch_all_dossiers(client: MstrClient) -> list[dict]:
    """Page through /searches/results?type=55 until exhausted."""
    results: list[dict] = []
    offset = 0
    limit = 500
    while True:
        resp = client.session.get(
            client._url("/searches/results"),
            headers=client._headers(),
            params={"type": DOSSIER_TYPE, "offset": offset, "limit": limit},
            verify=client.verify_ssl,
            timeout=client.timeout,
        )
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code} at offset={offset}: {resp.text[:300]}")
            break
        data = resp.json()
        # MSTR wraps results; try both shapes
        batch: list[dict] = []
        total = None
        if isinstance(data, list):
            batch = data
        elif isinstance(data, dict):
            for key in ("result", "results", "items", "objects"):
                if isinstance(data.get(key), list):
                    batch = data[key]
                    break
            # Extract total if available
            for key in ("totalSize", "total"):
                if isinstance(data.get(key), int):
                    total = data[key]
                    break
        print(f"  offset={offset:>5}  got {len(batch):>4} rows  total={total}")
        results.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.1)  # gentle on the server
    return results


def main() -> int:
    print(f"Enumerating dossiers (type={DOSSIER_TYPE}) in '{PROJECT_NAME}'...")

    client = MstrClient(
        base_url=BASE_URL,
        username=USERNAME,
        password=PASSWORD,
        login_mode=1,
    )
    client.login()
    client.resolve_project(project_id=PROJECT_ID, project_name=None)
    print(f"  Logged in; project_id={client.project_id}")

    dossiers = fetch_all_dossiers(client)
    print(f"\n  Total dossiers enumerated: {len(dossiers):,}")

    # Cross-check against the 219 dossier IDs we know from UserActivity
    ua_dossier_ids = set()
    ua_csv = Path("UserActivity.csv")
    if ua_csv.exists():
        # Load GO inventory IDs
        go_ids_file = Path("public/data/Global Operational/inventory_all.json")
        if go_ids_file.exists():
            go_ids = {x["id"] for x in json.loads(go_ids_file.read_text(encoding="utf-8"))}
            with ua_csv.open(encoding="utf-8-sig", errors="replace") as f:
                for row in csv.DictReader(f):
                    path = (row.get("Folder Path") or "").strip()
                    oid = (row.get("Object ID") or "").strip()
                    if not oid or len(oid) != 32 or oid in go_ids:
                        continue
                    if not path.lstrip("/").lower().startswith("global operational"):
                        continue
                    ua_dossier_ids.add(oid)

            enum_ids = {d.get("id") for d in dossiers}
            recovered = ua_dossier_ids & enum_ids
            still_missing = ua_dossier_ids - enum_ids
            print(f"\n  Known GO-scoped missing IDs from UserActivity: {len(ua_dossier_ids)}")
            print(f"  Recovered via type=55 enumeration: {len(recovered)}")
            print(f"  Still missing (likely deleted or ACL-hidden): {len(still_missing)}")

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(dossiers, indent=2, default=str), encoding="utf-8")
    print(f"\n  Wrote {OUT_FILE}")

    # Optional: logout
    try:
        client.logout()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
