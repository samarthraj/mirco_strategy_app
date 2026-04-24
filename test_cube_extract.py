"""Probe MSTR endpoints for feature extraction on 3 cube-sourced INSIGHT
reports that currently have zero metrics/tables/filters (the /model/
endpoint returned 500s during the original ingest).

Tries multiple endpoint variants to see which one yields a usable
definition we could backfill into raw_inventory.
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

BASE_URL   = os.environ.get("MSTR_BASE_URL", "").rstrip("/")
USERNAME   = os.environ["MSTR_USERNAME"]
PASSWORD   = os.environ["MSTR_PASSWORD"]
PROJECT_ID = "6104D29041297D66C6BD16B602F2705F"  # INSIGHT

CANDIDATES = [
    ("586132FB44D8CB00C7712E94A4C0D6B6", "CEL Weekly Sales Goals",              66_593),
    ("0D6142EA4238FA60CC8402B5BBE2E2CB", "CEL Tracker Plan Only",               63_434),
    ("0734FE65F447E14B8F0759989FDFC322", "Mad Mobile  EU Sales Txn Report",     26_884),
]

ENDPOINTS = [
    ("GET",  "/v2/cubes/{id}"),
    ("GET",  "/v2/reports/{id}"),
    ("GET",  "/model/cubes/{id}"),
    ("GET",  "/model/reports/{id}"),
    ("GET",  "/objects/{id}?type=3"),  # generic object lookup
]


def login(session: requests.Session) -> None:
    r = session.post(
        f"{BASE_URL}/auth/login",
        json={"username": USERNAME, "password": PASSWORD, "loginMode": 1},
    )
    r.raise_for_status()
    token = r.headers["X-MSTR-AuthToken"]
    session.headers.update({
        "X-MSTR-AuthToken": token,
        "X-MSTR-ProjectID": PROJECT_ID,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })


def count_features(defn: dict) -> dict:
    """Count attributes / metrics / filters / tables in a definition blob,
    trying the various shapes MSTR returns."""
    attrs = metrics = filters = tables = 0
    if not isinstance(defn, dict):
        return {"attrs": 0, "metrics": 0, "filters": 0, "tables": 0}

    # /v2/cubes + /v2/reports shape
    d = defn.get("definition", {}) or {}
    avail = d.get("availableObjects", {}) or {}
    attrs_list = avail.get("attributes") or d.get("attributes") or defn.get("attributes") or []
    mets_list = avail.get("metrics") or d.get("metrics") or defn.get("metrics") or []
    attrs = len(attrs_list)
    metrics = len(mets_list)

    # /model shape
    ds = defn.get("dataSource", {}) or {}
    units = (ds.get("dataTemplate", {}) or {}).get("units", []) or []
    if units:
        attrs_from_model = [u for u in units if u.get("type") == "attribute"]
        met_elems = [e for u in units if u.get("type") == "metrics" for e in u.get("elements", [])]
        attrs = max(attrs, len(attrs_from_model))
        metrics = max(metrics, len(met_elems))

    # Tables (cubes often list in dataSource or tables key)
    tables_list = (
        defn.get("tables")
        or (defn.get("dataSource") or {}).get("tables")
        or (defn.get("definition") or {}).get("tables")
        or []
    )
    tables = len(tables_list) if isinstance(tables_list, list) else 0

    # Filters
    filt = defn.get("filter") or ds.get("filter") or {}
    filters = 1 if (isinstance(filt, dict) and (filt.get("text") or filt.get("tree"))) else 0

    return {"attrs": attrs, "metrics": metrics, "filters": filters, "tables": tables}


def probe(session: requests.Session, rid: str) -> None:
    print(f"\n{'='*72}\nREPORT {rid}\n{'='*72}")
    for method, tmpl in ENDPOINTS:
        url = f"{BASE_URL}{tmpl.format(id=rid)}"
        try:
            resp = session.get(url, timeout=30)
        except Exception as e:
            print(f"  {method} {tmpl:<35} -> ERROR {e}")
            continue
        print(f"  {method} {tmpl:<35} -> HTTP {resp.status_code}", end="")
        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                print("  (not JSON)")
                continue
            counts = count_features(data)
            info = data.get("information") or {}
            name = info.get("name") or data.get("name") or "?"
            print(f"  name={name!r} attrs={counts['attrs']} metrics={counts['metrics']} tables={counts['tables']} filters={counts['filters']}")
            # Save first success for inspection
            safe = tmpl.replace("/", "_").replace("{", "").replace("}", "").replace("?", "-").replace("=", "_").strip("_")
            out = Path(f"output_cube_probe_{rid}_{safe}.json")
            out.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            print(f"    saved full response -> {out.name}")
        elif resp.status_code == 500:
            # Log body snippet for the 500 so we can see why
            body = resp.text[:200].replace("\n", " ")
            print(f"  body: {body}")
        else:
            body = resp.text[:200].replace("\n", " ")
            print(f"  body: {body}")
        time.sleep(0.5)


def main() -> int:
    if not BASE_URL:
        print("MSTR_BASE_URL not set")
        return 1
    session = requests.Session()
    print(f"Logging in to {BASE_URL} as {USERNAME}...")
    login(session)
    print("Login OK")

    for rid, name, execs in CANDIDATES:
        print(f"\n### {name} ({execs:,} executions)")
        probe(session, rid)

    return 0


if __name__ == "__main__":
    sys.exit(main())
