#!/usr/bin/env python3
"""Fetch paths for enumerated GO dossiers and merge them into the dashboard
inventory so the 219 (of 281) UserActivity-known-missing dossiers show up.

Inputs:
  - Global Operational/inventory/dossiers.json  (from enumerate_go_dossiers.py)
  - UserActivity.csv                            (for telemetry match + path fallback)

Outputs (both written in place):
  - Global Operational/inventory/dossiers_enriched.json (paths + telemetry flags)
  - public/data/Global Operational/inventory_all.json   (dossiers appended with status='dossier')
  - public/data/Global Operational/summary.json         (totalInventory + totalDossiers bumped)
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

PROJECT_DIR = Path("Global Operational")
DOSSIERS_IN = PROJECT_DIR / "inventory" / "dossiers.json"
DOSSIERS_OUT = PROJECT_DIR / "inventory" / "dossiers_enriched.json"
INVENTORY_ALL = Path("public/data/Global Operational/inventory_all.json")
SUMMARY = Path("public/data/Global Operational/summary.json")
UA_CSV = Path("UserActivity.csv")


def build_path_from_ancestors(ancestors: list) -> str:
    if not ancestors:
        return ""
    srt = sorted(ancestors, key=lambda a: a.get("level", 0), reverse=True)
    return "/".join(a.get("name", "") for a in srt if a.get("name"))


def main() -> int:
    dossiers = json.loads(DOSSIERS_IN.read_text(encoding="utf-8"))
    print(f"Loaded {len(dossiers):,} dossiers")

    # Collect UserActivity-known paths + telemetry by Object ID (GO-scoped only)
    ua_info: dict[str, dict] = {}
    with UA_CSV.open(encoding="utf-8-sig", errors="replace") as f:
        for row in csv.DictReader(f):
            path = (row.get("Folder Path") or "").strip()
            oid = (row.get("Object ID") or "").strip()
            if not oid or len(oid) != 32:
                continue
            if not path.lstrip("/").lower().startswith("global operational"):
                continue
            ex = int((row.get("Executions") or "0").strip() or 0)
            le = row.get("Last Execution", "")
            info = ua_info.setdefault(oid, {"path": path.lstrip("/"), "execs": 0, "last_exec": ""})
            info["execs"] += ex
            if le > info["last_exec"]:
                info["last_exec"] = le

    # Resume cache (id -> path) so reruns skip fetched dossiers
    cache_path = PROJECT_DIR / "inventory" / "fetched_dossier_paths.json"
    cache: dict[str, str] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    need_fetch = []
    for d in dossiers:
        oid = d["id"]
        if oid in cache and cache[oid]:
            continue
        if oid in ua_info:
            cache[oid] = ua_info[oid]["path"]
            continue
        need_fetch.append(oid)
    print(f"  Paths from UserActivity + cache: {len(cache):,}")
    print(f"  Need to fetch via ancestors:     {len(need_fetch):,}")

    if need_fetch:
        client = MstrClient(
            base_url=BASE_URL, username=USERNAME, password=PASSWORD, login_mode=1,
        )
        client.login()
        client.resolve_project(project_id=PROJECT_ID, project_name=None)
        print("  Fetching ancestors for remaining dossiers...")
        t0 = time.time()
        for i, oid in enumerate(need_fetch, 1):
            resp = client.session.get(
                client._url(f"/objects/{oid}"),
                headers=client._headers(),
                params={"type": 55},
                verify=client.verify_ssl,
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                anc = data.get("ancestors") or []
                p = build_path_from_ancestors(anc)
                if p and data.get("name"):
                    p = f"{p}/{data['name']}".rstrip("/")
                cache[oid] = p or ""
            else:
                cache[oid] = ""
            if i % 50 == 0:
                rate = i / (time.time() - t0)
                cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
                print(f"    {i}/{len(need_fetch)}  ({rate:.1f}/s)")
            time.sleep(0.05)
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        try:
            client.logout()
        except Exception:
            pass

    # Enrich dossier records
    enriched = []
    for d in dossiers:
        oid = d["id"]
        ua = ua_info.get(oid) or {}
        enriched.append({
            "id": oid,
            "name": d.get("name", ""),
            "owner": (d.get("owner") or {}).get("name", "") if isinstance(d.get("owner"), dict) else "",
            "path": cache.get(oid, ""),
            "dateCreated": d.get("dateCreated"),
            "dateModified": d.get("dateModified"),
            "type": 55,
            "subtype": d.get("subtype"),
            "hasTelemetry": oid in ua_info,
            "totalExecutions": ua.get("execs", 0),
            "lastExec": ua.get("last_exec", ""),
        })
    DOSSIERS_OUT.write_text(json.dumps(enriched, indent=2, default=str), encoding="utf-8")
    print(f"\n  Wrote {DOSSIERS_OUT}")

    tel_count = sum(1 for e in enriched if e["hasTelemetry"])
    active_tel = sum(1 for e in enriched if e["totalExecutions"] > 0)
    print(f"  Dossiers with UserActivity match: {tel_count}")
    print(f"  Dossiers with non-zero executions: {active_tel}")

    # Merge into inventory_all.json (only if not already present)
    inv = json.loads(INVENTORY_ALL.read_text(encoding="utf-8"))
    existing_ids = {x["id"] for x in inv}
    added = 0
    for e in enriched:
        if e["id"] in existing_ids:
            continue
        inv.append({
            "id": e["id"],
            "name": e["name"],
            "owner": e["owner"],
            "path": e["path"],
            "dateCreated": e["dateCreated"],
            "dateModified": e["dateModified"],
            "status": "dossier",
        })
        added += 1
    INVENTORY_ALL.write_text(json.dumps(inv, indent=2, default=str), encoding="utf-8")
    print(f"  Added {added} dossiers to inventory_all.json (total now {len(inv):,})")

    # Update summary.json with totalDossiers + bump totalInventory
    s = json.loads(SUMMARY.read_text(encoding="utf-8"))
    s["totalDossiers"] = len(enriched)
    s["dossiersWithTelemetry"] = tel_count
    s["totalInventory"] = s.get("totalInventory", 0) + added
    # Update funnel "Original Inventory" value if present
    for stage in s.get("funnel", []):
        if stage.get("label") == "Original Inventory":
            stage["value"] = s["totalInventory"]
            stage["detail"] = (stage.get("detail", "") +
                               f" (includes {len(enriched)} dossiers/documents, type=55)")
            break
    # Recompute reduction
    after_final = s.get("afterSimilarity", 0)
    s["reductionTotal"] = s["totalInventory"] - after_final
    s["reductionPct"] = round((s["reductionTotal"] / s["totalInventory"]) * 100, 1) if s["totalInventory"] else 0
    SUMMARY.write_text(json.dumps(s, indent=2, default=str), encoding="utf-8")
    print(f"  Updated summary.json: totalInventory={s['totalInventory']:,}, totalDossiers={s['totalDossiers']:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
