"""One-shot test: does extract_in_batch4.py's multi-pass SQL logic return
MORE than our current scalar `sqlStatement` path? Runs both extractions
side-by-side on 5 reports and diffs the output.

Current pipeline reads only `sqlStatement`. Batch4 reads `sqlStatements[]`
(a list of pass statements) and joins them with a PASS_BREAK separator.
The interesting cases are reports that actually have multi-pass SQL —
temp tables, intermediate aggregations, etc.
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

env_path = ROOT / ".env.local"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v:
                os.environ.setdefault(k, v)

from mstr_report_inventory import MstrClient
from db.db import connect


GI_PROJECT_ID = "07E2CE9311EB6800B59F0080EF050FB2"
PASS_BREAK = "\n\n-- [ PASS BREAK ] --\n\n"

REPORTS_TO_TRY = [
    ("334742BDB248163C1723C5BF7DDB6202", "BOM_Dataset_PO Creation_V02", 2877),
    ("02ADCB9FA841CFDC5EF380BBFC4F9969", "LY by Material - Date Prompt", 1724),
    ("39341AECD843D33EC062D0A3BDB22ADA", "TY by Class - Date Prompt", 1647),
    ("2BB9C784C94E51AD6FCBE8BCA5E2E7E3", "TY by Material - Date Prompt", 1591),
    ("61FD8F464B2F33F2BD67AFA09A14D05C", "NA DSF - Yesterday_TY", 1391),
]


def fetch_report_sql_raw(client: MstrClient, report_id: str) -> tuple[dict | None, str | None]:
    """Get the raw sqlView response so we can inspect both scalar and list
    shapes at the same time."""
    try:
        resp = client.session.post(
            client._url(f"/v2/reports/{report_id}/instances"),
            headers=client._headers(),
            params={"executionStage": "RESOLVE_PROMPTS"},
            verify=client.verify_ssl,
            timeout=client.timeout,
        )
    except Exception as e:
        return None, f"instance POST exception: {e}"
    if not resp.ok:
        return None, f"instance POST http={resp.status_code} body={resp.text[:200]}"
    try:
        instance = resp.json() or {}
    except Exception:
        return None, f"instance POST non-JSON: {resp.text[:200]}"
    iid = instance.get("instanceId") or instance.get("id")
    if not iid:
        return None, "no instanceId in instance response"

    try:
        sresp = client.session.get(
            client._url(f"/v2/reports/{report_id}/instances/{iid}/sqlView"),
            headers=client._headers(),
            verify=client.verify_ssl,
            timeout=client.timeout,
        )
    finally:
        # Clean up the instance even on failure
        try:
            client.session.delete(
                client._url(f"/v2/reports/{report_id}/instances/{iid}"),
                headers=client._headers(),
                verify=client.verify_ssl,
                timeout=client.timeout,
            )
        except Exception:
            pass

    if not sresp.ok:
        return None, f"sqlView GET http={sresp.status_code} body={sresp.text[:200]}"
    try:
        return sresp.json() or {}, None
    except Exception:
        return None, f"sqlView non-JSON: {sresp.text[:200]}"


def main() -> int:
    base_url = os.environ["MSTR_BASE_URL"].rstrip("/")
    if not base_url.endswith("/api"):
        base_url = base_url + "/api"
    client = MstrClient(
        base_url=base_url,
        username=os.environ["MSTR_USERNAME"],
        password=os.environ["MSTR_PASSWORD"],
        login_mode=int(os.environ.get("MSTR_LOGIN_MODE", "1")),
        verify_ssl=False,
        timeout=60,
    )
    client.login()
    client.session.headers["X-MSTR-ProjectID"] = GI_PROJECT_ID
    print(f"Logged in. Project: Global Insight ({GI_PROJECT_ID})\n")

    # Current stored (from our pipeline) to compare against
    conn = connect()
    stored = {}
    for rid, _n, _e in REPORTS_TO_TRY:
        row = conn.execute(
            "SELECT sql_text FROM report_sql WHERE project_id=? AND report_id=?",
            ("global-insight", rid),
        ).fetchone()
        stored[rid] = row[0] if row else ""
    conn.close()

    print(f"{'#':>2}  {'Report':40}  {'Stored(pipe)':>12}  {'Scalar':>8}  {'List passes':>11}  {'List total':>10}  Delta")
    print(f"{'-'*2}  {'-'*40}  {'-'*12}  {'-'*8}  {'-'*11}  {'-'*10}  -----")

    results = []
    for i, (rid, name, execs) in enumerate(REPORTS_TO_TRY, 1):
        data, err = fetch_report_sql_raw(client, rid)
        if err:
            print(f"{i:>2}  {name[:40]:40}  FAIL: {err}")
            results.append({"id": rid, "name": name, "ok": False, "error": err})
            time.sleep(0.4)
            continue

        scalar = (data.get("sqlStatement") or data.get("sql") or "") or ""
        statements = data.get("sqlStatements") or []
        passes = [s.get("statement", "") for s in statements if isinstance(s, dict)]
        joined = PASS_BREAK.join(p for p in passes if p.strip()) if passes else ""

        stored_sql = stored.get(rid) or ""
        scalar_len = len(scalar)
        passes_n = len(passes)
        joined_len = len(joined)
        delta = joined_len - scalar_len if joined else 0

        winner = "multi-pass" if (joined and joined_len > scalar_len) else (
                 "scalar" if scalar else "neither")
        print(f"{i:>2}  {name[:40]:40}  {len(stored_sql):>12,}  {scalar_len:>8,}  {passes_n:>11}  {joined_len:>10,}  +{delta:,} ({winner})")

        # Preview when multi-pass wins
        if joined and joined_len > scalar_len and passes_n > 1:
            print(f"    ↳ {passes_n} passes. First 2 lines of each:")
            for j, p in enumerate(passes, 1):
                first = (p.strip().splitlines() or [""])[0]
                print(f"       pass {j}: {first[:140]}")

        results.append({
            "id": rid, "name": name, "ok": True,
            "stored_len": len(stored_sql),
            "scalar_len": scalar_len,
            "passes": passes_n,
            "joined_len": joined_len,
        })
        time.sleep(0.4)

    try:
        client.logout()
    except Exception:
        pass

    print("\n=== SUMMARY ===")
    multi = [r for r in results if r.get("ok") and r.get("passes", 0) > 1]
    print(f"Reports with >1 SQL pass:   {len(multi)}/{len(results)}")
    for r in multi:
        print(f"  {r['id'][:16]}  passes={r['passes']}  scalar={r['scalar_len']:,}  joined={r['joined_len']:,}  stored={r['stored_len']:,}  delta-to-scalar=+{r['joined_len']-r['scalar_len']:,}")
    # Info on reports where stored != scalar (drift)
    drift = [r for r in results if r.get("ok") and r["stored_len"] != r["scalar_len"]]
    if drift:
        print("\nDrift between stored (pipeline) and freshly-fetched scalar:")
        for r in drift:
            print(f"  {r['id'][:16]}  stored={r['stored_len']:,}  scalar_now={r['scalar_len']:,}  {'+' if r['scalar_len']>r['stored_len'] else '-'}{abs(r['scalar_len']-r['stored_len']):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
