#!/usr/bin/env python3
"""Extract SQL for specific report IDs and compare tables."""

import json
import os
import sys
from pathlib import Path

from mstr_report_inventory import parse_table_names_from_sql
from mstr_project_rationalize import (
    RationalizationClient,
    _extract_sql_for_report,
    hash_sql,
)

# Target report IDs
TARGETS = [
    ("DBCCA9B94ADF2A17BAE6C09A3E36EEB5", "GFE085a (copy 1)"),
    ("77E38D08F3446450C72F02823DDB8559", "GFE085a (copy 2)"),
    ("85A494E7794D39BC448375A05239B62F", "GFE085a - Projections by PD - Post Market Recap"),
]

BASE_URL = "https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api"
PROJECT_ID = "E77B77894C04BF0E6D244F9363CFAF64"
USERNAME = os.environ["MSTR_USERNAME"]
PASSWORD = os.getenv("MSTR_PASSWORD", "")


def main():
    if not PASSWORD:
        print("ERROR: MSTR_PASSWORD environment variable not set", file=sys.stderr)
        return 1

    client = RationalizationClient(
        base_url=BASE_URL,
        username=USERNAME,
        password=PASSWORD,
        login_mode=1,
        verify_ssl=False,
        timeout=60,
    )

    try:
        client.login()
        project = client.resolve_project(project_id=PROJECT_ID, project_name=None)
        print(f"Connected to project: {project.name}")

        results = {}
        for report_id, name in TARGETS:
            print(f"\n{'='*60}")
            print(f"  Extracting SQL for: {name}")
            print(f"  ID: {report_id}")
            print(f"{'='*60}")

            # Build a record dict matching what _extract_sql_for_report expects
            record = {"id": report_id, "name": name, "errors": {}}
            _extract_sql_for_report(client, record)

            if record.get("sql"):
                print(f"  -> SQL extracted: {len(record['sql'])} chars")
                if record.get("prompted"):
                    print(f"  -> (prompted report)")
                results[report_id] = {
                    "name": name,
                    "sql": record["sql"],
                    "sourceTables": record.get("sourceTables", []),
                    "prompted": record.get("prompted", False),
                }
            else:
                print(f"  -> FAILED: {record.get('errors', {})}")

        # Save results
        output_file = Path("extracted_sql_gfe085a.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\nResults saved to {output_file}")

        # Print comparison
        print(f"\n{'='*60}")
        print(f"  TABLE COMPARISON")
        print(f"{'='*60}")
        for rid, data in results.items():
            print(f"\n  {data['name']}:")
            print(f"    SQL length: {len(data['sql'])} chars")
            print(f"    Tables ({len(data['sourceTables'])}):")
            for t in sorted(data['sourceTables']):
                print(f"      - {t}")

        # Cross-compare if we have multiple results
        if len(results) >= 2:
            ids = list(results.keys())
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    t1 = set(results[ids[i]]["sourceTables"])
                    t2 = set(results[ids[j]]["sourceTables"])
                    common = t1 & t2
                    only1 = t1 - t2
                    only2 = t2 - t1
                    n1 = results[ids[i]]["name"]
                    n2 = results[ids[j]]["name"]
                    print(f"\n  --- {n1} vs {n2} ---")
                    print(f"    Common tables: {len(common)}")
                    if only1:
                        print(f"    Only in {n1}:")
                        for t in sorted(only1):
                            print(f"      - {t}")
                    if only2:
                        print(f"    Only in {n2}:")
                        for t in sorted(only2):
                            print(f"      - {t}")
                    if t1 == t2:
                        print(f"    -> IDENTICAL table sets!")

    finally:
        try:
            client.logout()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
