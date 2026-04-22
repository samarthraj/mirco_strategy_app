"""One-shot test: does extract_in_batch4.py's cube SQL logic actually work
against our MSTR for Global Insight? Tries it on 5 high-execution active
cube reports and reports success/failure per cube.

Reads MSTR_* from .env.local."""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Load .env.local so MSTR_BASE_URL / _USERNAME / _PASSWORD are available
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


GI_PROJECT_ID = "07E2CE9311EB6800B59F0080EF050FB2"

CUBES_TO_TRY = [
    ("F4421342454ED7FA73ABCC9862C91C76", "Allocated Buy Data", 3951),
    ("41A854C44178C58491CB1492003B233F", "Intelligent Cube Report", 3038),
    ("6DFD2ED39A4E5AB09CA36CA68DF708DB", "COPY Sell-Out Data (Week Filter) - by Store", 3003),
    ("19962ABF0C498624ADE06DBDE3952F1D", "COPY Sell-In Data (Week Filter) - by Store", 2859),
    ("539DCDBA4AB0F83472858D95A9C3A16B", "(ALL) Brand Metrics Report v03", 2506),
]


def get_cube_sql(client: MstrClient, cube_id: str) -> tuple[str | None, str | None]:
    """Ported from extract_in_batch4.py:get_cube_sql.
    Returns (sql_text, error_description)."""
    api = client.base_url.rstrip("/") + "/api" if not client.base_url.rstrip("/").endswith("/api") else client.base_url.rstrip("/")
    # Our MstrClient._url already appends /api — use it for consistency.

    # 1. Create an instance on the cube
    try:
        resp = client.session.post(
            client._url(f"/cubes/{cube_id}/instances"),
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
        return None, f"instance POST non-JSON body: {resp.text[:200]}"
    iid = instance.get("instanceId") or instance.get("id")

    # 2. Fetch the sqlView on the cube (note: not on the instance)
    try:
        sresp = client.session.get(
            client._url(f"/v2/cubes/{cube_id}/sqlView"),
            headers=client._headers(),
            verify=client.verify_ssl,
            timeout=client.timeout,
        )
    except Exception as e:
        # Best-effort instance cleanup before returning
        if iid:
            try:
                client.session.delete(
                    client._url(f"/cubes/{cube_id}/instances/{iid}"),
                    headers=client._headers(),
                    verify=client.verify_ssl,
                    timeout=client.timeout,
                )
            except Exception:
                pass
        return None, f"sqlView GET exception: {e}"

    # 3. Clean up
    if iid:
        try:
            client.session.delete(
                client._url(f"/cubes/{cube_id}/instances/{iid}"),
                headers=client._headers(),
                verify=client.verify_ssl,
                timeout=client.timeout,
            )
        except Exception:
            pass

    if not sresp.ok:
        return None, f"sqlView GET http={sresp.status_code} body={sresp.text[:200]}"
    try:
        data = sresp.json() or {}
    except Exception:
        return None, f"sqlView non-JSON body: {sresp.text[:200]}"

    passes = data.get("sqlStatements") or []
    if passes:
        joined = "\n\n-- [ PASS BREAK ] --\n\n".join(
            s.get("statement", "") for s in passes if isinstance(s, dict)
        )
        return (joined if joined.strip() else None), (None if joined.strip() else "sqlStatements all empty")
    sql = data.get("sqlStatement") or data.get("sql") or ""
    if sql.strip():
        return sql, None
    return None, f"no sqlStatements/sqlStatement in response keys={list(data.keys())[:10]}"


def main() -> int:
    base_url = os.environ["MSTR_BASE_URL"].rstrip("/")
    if base_url.endswith("/api"):
        base_url = base_url[:-4]
    # MstrClient expects base URL WITHOUT /api (it appends)
    client = MstrClient(
        base_url=base_url + "/api",
        username=os.environ["MSTR_USERNAME"],
        password=os.environ["MSTR_PASSWORD"],
        login_mode=int(os.environ.get("MSTR_LOGIN_MODE", "1")),
        verify_ssl=False,
        timeout=60,
    )
    client.login()
    # Set project header for this session
    client.session.headers["X-MSTR-ProjectID"] = GI_PROJECT_ID
    print(f"Logged in. Project: Global Insight ({GI_PROJECT_ID})\n")

    results = []
    for report_id, name, execs in CUBES_TO_TRY:
        print(f"=> REPORT {report_id}  {name!r}  (execs={execs:,})")
        t0 = time.time()

        # Step 1: fetch the report's source cube(s) via /model/reports/{id}
        source_cubes: list[dict] = []
        fetch_err: str | None = None
        try:
            r = client.session.get(
                client._url(f"/model/reports/{report_id}"),
                headers=client._headers(),
                verify=client.verify_ssl,
                timeout=client.timeout,
            )
            if r.ok:
                defn = r.json() or {}
                datasets = (defn.get("dataSource") or {}).get("datasets") or []
                source_cubes = [
                    {"id": d.get("id"), "name": d.get("name", "")}
                    for d in datasets if d.get("id")
                ]
            else:
                fetch_err = f"/model/reports http={r.status_code} body={r.text[:160]}"
        except Exception as e:
            fetch_err = f"/model/reports exception: {e}"

        if not source_cubes:
            # Fallback: try /v2/reports/{id} which exposes `source`
            try:
                r = client.session.get(
                    client._url(f"/v2/reports/{report_id}"),
                    headers=client._headers(),
                    verify=client.verify_ssl,
                    timeout=client.timeout,
                )
                if r.ok:
                    defn = r.json() or {}
                    src = defn.get("source") or {}
                    if src.get("id"):
                        source_cubes = [{"id": src["id"], "name": src.get("name", "")}]
            except Exception as e:
                fetch_err = (fetch_err or "") + f" || /v2/reports exception: {e}"

        if not source_cubes:
            dt = time.time() - t0
            msg = fetch_err or "no source datasets found in /model/reports or /v2/reports"
            print(f"   FAIL ({dt:.1f}s) — could not resolve source cube: {msg}\n")
            results.append({"id": report_id, "name": name, "ok": False, "sql_len": 0,
                            "error": f"no source cube: {msg}", "cubes": 0})
            time.sleep(0.5)
            continue

        print(f"   resolved {len(source_cubes)} source cube(s): "
              + ", ".join(f"{c['id'][:12]}…({c['name'][:25]})" for c in source_cubes))

        # Step 2: fetch SQL for each source cube
        any_ok = False
        total_len = 0
        per_cube: list[str] = []
        for cube in source_cubes:
            cid = cube["id"]
            sql, err = get_cube_sql(client, cid)
            if sql:
                any_ok = True
                total_len += len(sql)
                per_cube.append(f"OK {cid} ({len(sql):,} chars)")
                preview = sql.replace("\n", " ").strip()[:160]
                print(f"      OK cube {cid} — {len(sql):,} chars")
                print(f"         preview: {preview}...")
            else:
                per_cube.append(f"FAIL {cid} — {err}")
                print(f"      FAIL cube {cid} — {err}")
            time.sleep(0.3)

        dt = time.time() - t0
        if any_ok:
            print(f"   OK overall ({total_len:,} chars total across {len(source_cubes)} cube(s) in {dt:.1f}s)\n")
            results.append({"id": report_id, "name": name, "ok": True, "sql_len": total_len,
                            "error": None, "cubes": len(source_cubes)})
        else:
            print(f"   FAIL overall ({dt:.1f}s) — no cube returned SQL: {per_cube}\n")
            results.append({"id": report_id, "name": name, "ok": False, "sql_len": 0,
                            "error": "; ".join(per_cube)[:200], "cubes": len(source_cubes)})
        time.sleep(0.5)

    try:
        client.logout()
    except Exception:
        pass

    ok_n = sum(1 for r in results if r["ok"])
    print(f"=== SUMMARY: {ok_n}/{len(results)} cubes returned SQL ===")
    for r in results:
        marker = "OK  " if r["ok"] else "FAIL"
        extra = f"{r['sql_len']:,} chars" if r["ok"] else (r["error"] or "")[:120]
        print(f"  {marker}  {r['id']}  {r['name'][:40]!r}  {extra}")
    return 0 if ok_n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
