"""Search all 3 MSTR projects for 'DTC Management Report' across object types.

Reimer, Deborah (US) has 49,586 executions against this object in
UserActivity.csv but it's not in our inventory. Try:
  - type=3  Report
  - type=14 Document
  - type=55 Dossier

Prints hits with id, type, project, name, path.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

# Load .env.local
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
from mstr_report_inventory import MstrClient

BASE_URL = os.environ.get(
    "MSTR_BASE_URL", "https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api"
)
USERNAME = os.environ["MSTR_USERNAME"]
PASSWORD = os.environ["MSTR_PASSWORD"]

PROJECTS = [
    ("global-operational", "Global Operational", "E77B77894C04BF0E6D244F9363CFAF64"),
    ("global-insight",     "Global Insight",     "C4F6C04843C6B8DD1C36BD905889C0B3"),
    ("insight",            "INSIGHT",            "45E54E0C4B25FFD69A4EE4B4E1EFCD69"),
]

TYPES = [(3, "Report"), (14, "Document"), (55, "Dossier")]
SEARCH = "DTC Management"


def search(client: MstrClient, obj_type: int, query: str) -> list[dict]:
    """MSTR REST: /searches/results?type=T&name=Q&pattern=4 (contains).

    pattern codes: 1=starts, 2=ends, 3=exact, 4=contains.
    """
    resp = client.session.get(
        client._url("/searches/results"),
        headers=client._headers(),
        params={"type": obj_type, "name": query, "pattern": 4, "limit": 200},
        verify=client.verify_ssl,
        timeout=60,
    )
    if resp.status_code != 200:
        return []
    data = resp.json()
    items: list[dict] = []
    if isinstance(data, list):
        items = data
    else:
        for key in ("result", "results", "items", "objects"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    # Defensive client-side filter in case the server ignores the filter
    q = query.lower()
    return [r for r in items if q in (r.get("name") or "").lower()]


def main() -> int:
    print(f"Searching for '{SEARCH}' in 3 projects × 3 object types...\n")
    hits = []
    for pid, pname, proj_guid in PROJECTS:
        print(f"=== {pname} ===")
        try:
            client = MstrClient(
                base_url=BASE_URL,
                username=USERNAME,
                password=PASSWORD,
                verify_ssl=True,
                timeout=120,
            )
            client.login()
            client.project_id = proj_guid
        except Exception as e:
            print(f"  LOGIN FAILED: {e}")
            continue
        for t, tname in TYPES:
            results = search(client, t, SEARCH)
            if results:
                for r in results:
                    hits.append({
                        "project": pname,
                        "type": tname,
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "path": r.get("path") or r.get("ancestors"),
                        "subtype": r.get("subtype"),
                        "owner": (r.get("owner") or {}).get("name"),
                    })
                print(f"  {tname:10} -> {len(results)} hits")
            else:
                print(f"  {tname:10} -> 0")
    print()
    if not hits:
        print("No matches in any project for any type.")
        return 0
    print(f"=== {len(hits)} total hits ===")
    for h in hits:
        print(f"  [{h['project']:18} {h['type']:8}] id={h['id']}  subtype={h['subtype']}")
        print(f"       name: {h['name']}")
        if h.get("path"):
            print(f"       path: {h['path']}")
        if h.get("owner"):
            print(f"       owner: {h['owner']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
