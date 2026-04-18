#!/usr/bin/env python3
"""Extract SQL for the combined list of newly accessible + previously failed reports."""

import os
import json
import sys
from pathlib import Path

from mstr_report_inventory import MstrClient
from mstr_project_rationalize import hash_sql, parse_table_names_from_sql

DATA_DIR = Path("Global Operational")
INPUT_FILE = DATA_DIR / "inventory" / "reports_to_extract_sql.json"
OUTPUT_FILE = DATA_DIR / "inventory" / "reports_sql_extracted.json"


def get_sql(client, report_id):
    """Try direct SQL extraction, fall back to prompt resolution if needed."""
    instance_id = None
    try:
        resp = client.session.post(
            client._url(f"/v2/reports/{report_id}/instances"),
            headers=client._headers(),
            params={"executionStage": "resolve_prompts"},
            verify=False, timeout=120,
        )
        if resp.status_code == 400 and "prompt" in resp.text.lower():
            return try_with_prompts(client, report_id)
        if not resp.ok:
            return None, f"POST failed: HTTP {resp.status_code}"

        data = resp.json()
        instance_id = data.get("instanceId") or data.get("id")
        if not instance_id:
            return None, "No instance ID"

        resp = client.session.get(
            client._url(f"/v2/reports/{report_id}/instances/{instance_id}/sqlView"),
            headers=client._headers(),
            verify=False, timeout=120,
        )
        # If 406 with "open prompts" message, fall back to prompt resolution
        if resp.status_code == 406 and "prompt" in resp.text.lower():
            # Clean up this instance first (we'll create a fresh one with prompt resolution)
            try:
                client.session.delete(
                    client._url(f"/v2/reports/{report_id}/instances/{instance_id}"),
                    headers=client._headers(), verify=False, timeout=30,
                )
            except:
                pass
            instance_id = None  # Prevent double cleanup in finally block
            return try_with_prompts(client, report_id)
        if not resp.ok:
            return None, f"sqlView: HTTP {resp.status_code}"
        return resp.json().get("sqlStatement", ""), None
    except Exception as e:
        return None, str(e)[:100]
    finally:
        if instance_id:
            try:
                client.session.delete(
                    client._url(f"/v2/reports/{report_id}/instances/{instance_id}"),
                    headers=client._headers(),
                    verify=False, timeout=30,
                )
            except:
                pass


def try_with_prompts(client, report_id):
    instance_id = None
    try:
        resp = client.session.post(
            client._url(f"/v2/reports/{report_id}/instances"),
            headers=client._headers(),
            verify=False, timeout=120,
        )
        if not resp.ok:
            return None, f"prompt POST: HTTP {resp.status_code}"
        data = resp.json()
        instance_id = data.get("instanceId") or data.get("id")
        if not instance_id:
            return None, "prompted: no instance"

        resp2 = client.session.get(
            client._url(f"/reports/{report_id}/instances/{instance_id}/prompts"),
            headers=client._headers(),
            verify=False, timeout=60,
        )
        if not resp2.ok:
            return None, "prompted: no prompt list"
        prompts = resp2.json()
        if not isinstance(prompts, list) or not prompts:
            return None, "prompted: empty"

        answer_body = {"prompts": []}
        can_answer = True
        for p in prompts:
            has_answers = bool(p.get("answers") and p["answers"] != {})
            has_default = bool(p.get("defaultAnswer") and p["defaultAnswer"] != {})
            entry = {"key": p["key"], "id": p["id"], "type": p["type"]}
            if has_answers:
                entry["answers"] = p["answers"]
            elif has_default:
                entry["useDefault"] = True
            elif not p.get("required"):
                continue
            else:
                can_answer = False
                break
            answer_body["prompts"].append(entry)

        if not can_answer:
            return None, "prompted: required, no answer"

        resp3 = client.session.put(
            client._url(f"/reports/{report_id}/instances/{instance_id}/prompts/answers"),
            headers=client._headers(),
            json=answer_body,
            verify=False, timeout=60,
        )
        if not resp3.ok:
            return None, f"prompted answer: HTTP {resp3.status_code}"

        resp4 = client.session.get(
            client._url(f"/v2/reports/{report_id}/instances/{instance_id}/sqlView"),
            headers=client._headers(),
            verify=False, timeout=120,
        )
        if not resp4.ok:
            return None, f"prompted sqlView: HTTP {resp4.status_code}"
        return resp4.json().get("sqlStatement", ""), None
    except Exception as e:
        return None, f"prompted: {str(e)[:80]}"
    finally:
        if instance_id:
            try:
                client.session.delete(
                    client._url(f"/v2/reports/{report_id}/instances/{instance_id}"),
                    headers=client._headers(),
                    verify=False, timeout=30,
                )
            except:
                pass


def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        reports = json.load(f)

    # Resume support
    existing = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_list = json.load(f)
        existing = {r["id"]: r for r in existing_list}
        done = sum(1 for r in existing.values() if r.get("sql") or r.get("errors", {}).get("sql"))
        print(f"Resuming: {done} already processed")

    client = MstrClient(
        base_url="https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api",
        username=os.environ["MSTR_USERNAME"],
        password=os.environ["MSTR_PASSWORD"],
        login_mode=1, verify_ssl=False, timeout=120,
    )
    client.login()
    project = client.resolve_project(project_id=None, project_name="Global Operational")
    print(f"Project: {project.name}")
    print(f"Reports to process: {len(reports)}\n", flush=True)

    success = 0
    errors = 0

    for i, rec in enumerate(reports, 1):
        rid = rec["id"]

        if rid in existing and (existing[rid].get("sql") or existing[rid].get("errors", {}).get("sql")):
            rec.update(existing[rid])
            if existing[rid].get("sql"):
                success += 1
            else:
                errors += 1
            continue

        sql, err = get_sql(client, rid)

        if sql:
            rec["sql"] = sql
            rec["sqlHash"] = hash_sql(sql)
            rec["sourceTables"] = parse_table_names_from_sql(sql)
            success += 1
            status = f"OK ({len(sql):,}ch)"
        else:
            rec["errors"] = rec.get("errors", {})
            rec["errors"]["sql"] = err or "unknown"
            errors += 1
            status = f"ERR: {(err or '')[:60]}"

        print(f"  [{i}/{len(reports)}] {rec.get('name','')[:50]:50s} -> {status} (ok={success}, err={errors})", flush=True)

        if i % 25 == 0:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(reports, f, indent=2)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(reports, f, indent=2)

    print(f"\nDone. Success: {success}, Errors: {errors}")
    print(f"Saved to: {OUTPUT_FILE}")
    client.logout()


if __name__ == "__main__":
    main()
