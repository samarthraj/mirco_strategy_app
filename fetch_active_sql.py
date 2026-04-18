#!/usr/bin/env python3
"""Extract SQL for active reports only. Writes to a separate output file."""

from __future__ import annotations
import json, sys, time
from collections import defaultdict
from pathlib import Path
from mstr_project_rationalize import (
    RationalizationClient, write_json, hash_sql, parse_table_names_from_sql,
    _extract_sql_for_report, _extract_sql_for_cube,
)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract SQL for active reports only")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default="")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--login-mode", type=int, default=1)
    parser.add_argument("--verify-ssl", action="store_true", default=False)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--sql-batch-size", type=int, default=10)
    parser.add_argument("--sql-batch-delay", type=float, default=5.0)
    parser.add_argument("--save-interval", type=int, default=50)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    enriched_path = output_dir / "inventory" / "active_reports_enriched.json"
    out_path = output_dir / "inventory" / "active_reports_sql.json"

    if not enriched_path.exists():
        print(f"ERROR: {enriched_path} not found.")
        return 1

    # Load enriched active reports
    print("Loading enriched active reports...")
    enriched = json.loads(enriched_path.read_text(encoding="utf-8"))
    print(f"  {len(enriched)} active reports")

    # De-duplicate by definition fingerprint: reports with identical
    # attributes, metrics, filters, sourceType and sourceCubeId are copies.
    # Keep the one with highest executions as the parent.
    import hashlib

    active_tel_path = output_dir / "telemetry" / "active_reports.json"
    exec_by_id = {}
    if active_tel_path.exists():
        active_tel = json.loads(active_tel_path.read_text(encoding="utf-8"))
        exec_by_id = {r["id"]: r.get("totalExecutions", 0) for r in active_tel}

    def def_fingerprint(rec):
        attrs = sorted([a.get("id", "") for a in rec.get("attributes", []) if a.get("id")])
        metrics = sorted([m.get("id", "") for m in rec.get("metrics", []) if m.get("id")])
        filt_text = (rec.get("filter") or {}).get("text") or ""
        source_type = rec.get("sourceType", "")
        cube_id = rec.get("sourceCubeId", "")
        sig = json.dumps({
            "attrs": attrs, "metrics": metrics, "filter": filt_text,
            "sourceType": source_type, "cubeId": cube_id,
        }, sort_keys=True)
        return hashlib.md5(sig.encode()).hexdigest()

    fp_groups = defaultdict(list)
    no_def = []
    for rec in enriched:
        if not rec.get("definition") and not rec.get("attributes") and not rec.get("metrics"):
            no_def.append(rec)
            continue
        fp = def_fingerprint(rec)
        fp_groups[fp].append(rec)

    # Skip reports that had 400/500 definition errors — they won't succeed for SQL either
    def_error_skip = []
    def_error_retry = []
    for rec in no_def:
        err = rec.get("errors", {}).get("definition", "")
        if "400" in err or "500" in err or "403" in err or "404" in err:
            def_error_skip.append(rec)
        else:
            def_error_retry.append(rec)  # connection timeouts — worth retrying

    print(f"  Definition errors skipped (400/500/403/404): {len(def_error_skip)}")
    print(f"  Definition errors retrying (timeouts): {len(def_error_retry)}")

    deduped = list(def_error_retry)  # only include retryable errors
    skipped_dupes = []
    families = []
    for fp, recs in fp_groups.items():
        parent = max(recs, key=lambda r: exec_by_id.get(r["id"], 0))
        deduped.append(parent)
        children = [r for r in recs if r["id"] != parent["id"]]
        skipped_dupes.extend(children)
        if len(recs) > 1:
            families.append({
                "fingerprint": fp,
                "parentId": parent["id"],
                "parentName": parent.get("name", ""),
                "familySize": len(recs),
                "children": [{"id": r["id"], "name": r.get("name", "")} for r in children],
            })

    print(f"  No definition (included as-is): {len(no_def)}")
    print(f"  Unique fingerprints: {len(fp_groups)}")
    print(f"  Unique after dedup: {len(deduped)}")
    print(f"  Duplicates skipped: {len(skipped_dupes)}")
    print(f"  Families with copies: {len(families)}")

    # Save dedup mapping with family details
    dedup_path = output_dir / "inventory" / "active_reports_dedup.json"
    write_json(dedup_path, {
        "method": "definition_fingerprint",
        "uniqueCount": len(deduped),
        "duplicateCount": len(skipped_dupes),
        "noDefinitionCount": len(no_def),
        "familyCount": len(families),
        "families": sorted(families, key=lambda f: -f["familySize"]),
    })
    print(f"  Dedup mapping saved to {dedup_path.name}")

    # Load existing output if resuming
    done_ids = set()
    results = []
    if out_path.exists():
        results = json.loads(out_path.read_text(encoding="utf-8"))
        done_ids = {r["id"] for r in results}
        print(f"  Resuming: {len(done_ids)} already processed")

    # Group reports — skip cube-sourced ones
    individual = []
    skip_count = 0
    cube_skip = 0

    for rec in deduped:
        if rec["id"] in done_ids:
            skip_count += 1
            continue
        if rec.get("sourceCubeId"):
            cube_skip += 1
            rec.setdefault("errors", {})["sql"] = "Skipped: cube-sourced report"
            results.append(rec)
            continue
        individual.append(rec)

    total_calls = len(individual)
    print(f"\n  Cube-sourced (skipped): {cube_skip}")
    print(f"  Individual reports:     {len(individual)}")
    print(f"  Already done:           {skip_count}")
    print(f"  Total API calls:        {total_calls}")
    est_min = total_calls * 5 / 60
    print(f"  Estimated time:         ~{est_min:.0f} minutes")

    if total_calls == 0:
        print("\nAll SQL already extracted!")
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
        sql_batch_size=args.sql_batch_size,
        sql_batch_delay=args.sql_batch_delay,
    )
    client.login()
    project = client.resolve_project(project_id=None, project_name=args.project_name)
    print(f"\nProject: {project.name} ({project.id})")

    t0 = time.time()
    extracted = 0
    errors = 0

    try:
        # Individual reports only (cube-sourced already skipped)
        if individual:
            print(f"\n[sql] Extracting SQL for {len(individual)} individual reports...")
            for idx, rec in enumerate(individual, 1):
                if "errors" not in rec:
                    rec["errors"] = {}

                _extract_sql_for_report(client, rec)
                results.append(rec)

                if rec.get("sql"):
                    extracted += 1
                    status = "OK"
                else:
                    errors += 1
                    status = "ERR"

                elapsed = time.time() - t0
                rate = (extracted + errors) / elapsed if elapsed > 0 else 0
                remaining = len(individual) - idx
                eta = remaining / rate if rate > 0 else 0

                if idx % 50 == 0 or idx == 1:
                    print(f"  [{idx}/{len(individual)}] {rec.get('name','')} -> {status} "
                          f"({extracted} ok, {errors} err, ETA: {int(eta//60)}m {int(eta%60)}s)", flush=True)

                if idx % args.save_interval == 0:
                    write_json(out_path, results)
                    print(f"  [save] progress saved", flush=True)

    except KeyboardInterrupt:
        print("\nInterrupted! Saving progress...")
    finally:
        write_json(out_path, results)
        client.logout()

    elapsed = time.time() - t0
    has_sql = sum(1 for r in results if r.get("sql"))
    print(f"\nDone! {has_sql} reports with SQL, {errors} errors [{elapsed:.1f}s]")
    print(f"Output: {out_path}")

    # Update manifest
    manifest_path = output_dir / "run_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if "sql" not in manifest.get("completedPhases", []):
            manifest["completedPhases"].append("sql")
        write_json(manifest_path, manifest)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
