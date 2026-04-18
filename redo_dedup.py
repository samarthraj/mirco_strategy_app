#!/usr/bin/env python3
"""Re-run definition-based fingerprint dedup on the updated enriched data."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="INSIGHT/rationalization")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    enriched_path = output_dir / "inventory" / "active_reports_enriched.json"
    tel_path = output_dir / "telemetry" / "active_reports.json"
    dedup_path = output_dir / "inventory" / "active_reports_dedup.json"

    enriched = json.loads(enriched_path.read_text(encoding="utf-8"))
    active_tel = json.loads(tel_path.read_text(encoding="utf-8"))
    exec_by_id = {r["id"]: r.get("totalExecutions", 0) for r in active_tel}

    print(f"Enriched reports: {len(enriched)}")

    def fingerprint(rec):
        attrs = sorted(a.get("id", "") for a in rec.get("attributes", []) if a.get("id"))
        metrics = sorted(m.get("id", "") for m in rec.get("metrics", []) if m.get("id"))
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
        fp = fingerprint(rec)
        fp_groups[fp].append(rec)

    deduped = list(no_def)
    skipped = []
    families = []
    for fp, recs in fp_groups.items():
        parent = max(recs, key=lambda r: exec_by_id.get(r["id"], 0))
        deduped.append(parent)
        children = [r for r in recs if r["id"] != parent["id"]]
        skipped.extend(children)
        if len(recs) > 1:
            families.append({
                "fingerprint": fp,
                "parentId": parent["id"],
                "parentName": parent.get("name", ""),
                "familySize": len(recs),
                "children": [{"id": r["id"], "name": r.get("name", "")} for r in children],
            })

    print(f"Unique fingerprints: {len(fp_groups)}")
    print(f"Reports with no definition: {len(no_def)}")
    print(f"Families (2+ copies): {len(families)}")
    print(f"Duplicate copies removed: {len(skipped)}")
    print(f"Unique after dedup: {len(deduped)}")
    print(f"  Math check: {len(enriched)} - {len(skipped)} = {len(enriched) - len(skipped)} (should equal {len(deduped)})")

    output = {
        "method": "definition_fingerprint",
        "uniqueCount": len(deduped),
        "duplicateCount": len(skipped),
        "noDefinitionCount": len(no_def),
        "familyCount": len(families),
        "families": sorted(families, key=lambda f: -f["familySize"]),
    }
    dedup_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\nSaved: {dedup_path}")


if __name__ == "__main__":
    main()
