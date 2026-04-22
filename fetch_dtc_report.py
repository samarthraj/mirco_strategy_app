"""Fetch DTC Management Report directly by known object_id from INSIGHT."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

env = Path(__file__).resolve().parent / ".env.local"
if env.exists():
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v

sys.path.insert(0, str(Path(__file__).parent))
from mstr_report_inventory import MstrClient, extract_report_info

BASE_URL = os.environ.get(
    "MSTR_BASE_URL", "https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api"
)
USERNAME = os.environ["MSTR_USERNAME"]
PASSWORD = os.environ["MSTR_PASSWORD"]
INSIGHT_PROJECT_GUID = "45E54E0C4B25FFD69A4EE4B4E1EFCD69"
OBJECT_ID = "53630A16464958F8B144C38260BC5F9B"


def main() -> int:
    client = MstrClient(
        base_url=BASE_URL, username=USERNAME, password=PASSWORD,
        verify_ssl=True, timeout=120,
    )
    client.login()
    client.project_id = INSIGHT_PROJECT_GUID

    # 1. Try /model/reports/{id}
    print(f"Fetching /model/reports/{OBJECT_ID} ...")
    try:
        defn = client.get_report_definition(OBJECT_ID)
        print(f"  ok — keys: {list(defn.keys())[:12]}")
        info = extract_report_info(defn)
        print(f"  name: {info.get('name')}")
        print(f"  metrics: {len(info.get('metrics') or [])}")
        print(f"  attrs:   {len(info.get('attributes') or [])}")
        print(f"  filter:  {bool(info.get('filter'))}")
        out_path = Path("INSIGHT") / "dtc_management_report.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(defn, indent=2), encoding="utf-8")
        print(f"  saved full definition -> {out_path}")
    except Exception as e:
        print(f"  /model/reports failed: {e}")

    # 2. Also try /objects/{id}?type=55 (dossier) and /dossiers/{id}
    for path in (f"/objects/{OBJECT_ID}?type=55", f"/dossiers/{OBJECT_ID}/definition"):
        try:
            resp = client.session.get(
                client._url(path.split("?")[0]),
                headers=client._headers(),
                params=dict(kv.split("=") for kv in path.split("?")[1].split("&")) if "?" in path else None,
                verify=client.verify_ssl,
                timeout=60,
            )
            print(f"  {path}: HTTP {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                print(f"    name: {data.get('name')}  type: {data.get('type')} subtype: {data.get('subtype')}")
        except Exception as e:
            print(f"  {path}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
