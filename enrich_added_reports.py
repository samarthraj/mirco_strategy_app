#!/usr/bin/env python3
"""Enrich the 3,166 newly-visible reports that have telemetry usage."""

import os
import json
import sys
from pathlib import Path

from mstr_report_inventory import MstrClient, extract_filter, extract_units

DATA_DIR = Path("Global Operational")
INPUT_FILE = DATA_DIR / "inventory" / "reports_added_in_telemetry.json"
OUTPUT_FILE = DATA_DIR / "inventory" / "reports_added_enriched.json"


def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        reports = json.load(f)

    existing = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_list = json.load(f)
        existing = {r["id"]: r for r in existing_list}
        done = sum(1 for r in existing.values() if r.get("definition") or r.get("errors", {}).get("definition"))
        print(f"Resuming: {done} already enriched")

    client = MstrClient(
        base_url="https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api",
        username=os.environ["MSTR_USERNAME"],
        password=os.environ["MSTR_PASSWORD"],
        login_mode=1, verify_ssl=False, timeout=120,
    )
    client.login()
    project = client.resolve_project(project_id=None, project_name="Global Operational")
    print(f"Project: {project.name}")
    print(f"Enriching {len(reports)} reports...\n", flush=True)

    success = 0
    errors = 0

    for i, rec in enumerate(reports, 1):
        rid = rec["id"]

        if rid in existing and (existing[rid].get("definition") or existing[rid].get("errors", {}).get("definition")):
            rec.update(existing[rid])
            if existing[rid].get("definition"):
                success += 1
            else:
                errors += 1
            continue

        try:
            resp = client.session.get(
                client._url(f"/model/reports/{rid}"),
                headers=client._headers(),
                params={"showExpressionAs": "tree"},
                verify=False, timeout=60,
            )
            if not resp.ok:
                rec.setdefault("errors", {})["definition"] = f"HTTP {resp.status_code}"
                errors += 1
                status = f"ERR HTTP {resp.status_code}"
            else:
                report_def = resp.json()
                info = report_def.get("information", {})
                rec["definition"] = {
                    "id": info.get("objectId"),
                    "name": info.get("name"),
                    "subType": info.get("subType"),
                    "dateCreated": info.get("dateCreated"),
                    "dateModified": info.get("dateModified"),
                }
                rec["filter"] = extract_filter(report_def)
                attrs, metrics = extract_units(report_def)
                rec["attributes"] = attrs
                rec["metrics"] = metrics
                rec["sourceType"] = report_def.get("sourceType")
                cube_ref = report_def.get("dataSource", {}).get("cube")
                if cube_ref:
                    rec["sourceCubeId"] = cube_ref.get("objectId")
                success += 1
                status = f"OK (m:{len(metrics)}, f:{'Y' if rec.get('filter') else 'N'})"
        except Exception as e:
            rec.setdefault("errors", {})["definition"] = str(e)[:100]
            errors += 1
            status = f"EXC: {str(e)[:50]}"

        if i % 25 == 0 or i == len(reports):
            print(f"  [{i}/{len(reports)}] {rec.get('name','')[:50]:50s} -> {status} (ok={success}, err={errors})", flush=True)

        if i % 100 == 0:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(reports, f, indent=2)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(reports, f, indent=2)

    print(f"\nDone. Success: {success}, Errors: {errors}")
    print(f"Saved to: {OUTPUT_FILE}")
    client.logout()


if __name__ == "__main__":
    main()
