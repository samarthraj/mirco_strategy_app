"""Fetch definition of GO report C00A8E2D47FCB550E73B07A041524261 (CUSTOMER)."""
from __future__ import annotations
import json, os, sys
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
from mstr_report_inventory import MstrClient

BASE_URL = os.environ.get("MSTR_BASE_URL", "https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api")
GO_PROJECT_GUID = "E77B77894C04BF0E6D244F9363CFAF64"
RID = "C00A8E2D47FCB550E73B07A041524261"


def try_endpoint(client, path, params=None, label=""):
    try:
        resp = client.session.get(
            client._url(path),
            headers=client._headers(),
            params=params,
            verify=client.verify_ssl,
            timeout=60,
        )
        print(f"\n[{label or path}] HTTP {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(json.dumps(data, indent=2)[:3500])
            return data
        else:
            print(resp.text[:500])
    except Exception as e:
        print(f"  EXC {e}")


def main():
    client = MstrClient(
        base_url=BASE_URL,
        username=os.environ["MSTR_USERNAME"],
        password=os.environ["MSTR_PASSWORD"],
        verify_ssl=True, timeout=120,
    )
    client.login()
    client.project_id = GO_PROJECT_GUID

    # 1) /objects/{id} — generic object metadata (works for any object type)
    try_endpoint(client, f"/objects/{RID}", {"type": 3}, "OBJECT (type=3 report)")
    # 2) /model/reports/{id} — full definition with template + filter
    try_endpoint(client, f"/model/reports/{RID}",
                 {"showExpressionAs": "tree", "showFilterTokens": "true"},
                 "MODEL/REPORTS")
    # 3) /reports/{id}/instances — try executing
    # (skip — may have side effects; metadata should be enough)
    # 4) /reports/{id} — alternative definition endpoint
    try_endpoint(client, f"/reports/{RID}", None, "REPORTS (short)")


if __name__ == "__main__":
    main()
