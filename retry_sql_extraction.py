#!/usr/bin/env python3
"""Retry SQL extraction for reports the inventory pipeline couldn't get SQL for.

Original behavior: tries the execution-path endpoints for reports listed in
`retry_sql_candidates.json`.

This version also ports three strategies from extract_in_batch4.py:

  1. **Prompt auto-answer** — when `GET .../sqlView` returns 406 "open prompts",
     fetch the prompts via `GET /reports/{id}/instances/{iid}/prompts`, build
     answers from each prompt's `defaultAnswer` (or existing `answers`), PUT
     them via `.../prompts/answers`, and retry sqlView.

  2. **Cube SQL fallback** — when the report-level path fails AND the report
     is cube-sourced (has `dataSource.datasets[]`), fetch SQL from each source
     cube via `POST /cubes/{id}/instances` + `GET /v2/cubes/{id}/sqlView` and
     join multi-pass statements with a PASS_BREAK marker. Cubes that aren't
     published (MSTR iServerCode -2147072488) are logged and skipped.

  3. **Instance cleanup** — DELETE each report/cube instance after use so we
     don't leak server resources on long runs.

Scopes:

  --scope candidates    (default)  reads retry_sql_candidates.json — the
                                   original narrow-scan list
  --scope missing-sql              queries SQLite for every active report in
                                   the project(s) that has no sql_text yet —
                                   full refresh mode

Usage:
    python retry_sql_extraction.py                              # candidates, all projects
    python retry_sql_extraction.py --scope missing-sql --project global-insight
    python retry_sql_extraction.py --scope missing-sql --limit 50
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mstr_report_inventory import MstrClient

BASE_URL = os.environ.get("MSTR_BASE_URL",
                          "https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary/api")

PROJECT_MAP = {
    "global-operational": {
        "mstr_id": "E77B77894C04BF0E6D244F9363CFAF64",
        "name": "Global Operational",
    },
    "global-insight": {
        "mstr_id": "",
        "name": "Global Insight",
    },
    "insight": {
        "mstr_id": "",
        "name": "INSIGHT",
    },
}

RESULTS_FILE = Path("retry_sql_results.json")
CANDIDATES_FILE = Path("retry_sql_candidates.json")
DB_PATH = Path("data.db")

PASS_BREAK = "\n\n-- [ PASS BREAK ] --\n\n"


def _load_env_local() -> None:
    """Load .env.local so MSTR_* is available even when not exported."""
    p = Path(__file__).resolve().parent / ".env.local"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v:
                os.environ.setdefault(k, v)


def _norm_sql_hash(sql: str) -> str:
    return "sha256:" + hashlib.sha256(sql.encode("utf-8")).hexdigest()


# ---------------- Building blocks --------------------------------

def _err_of(where: str, resp) -> str:
    try:
        err = resp.json()
        code = err.get("iServerCode") or err.get("code") or resp.status_code
        msg = (err.get("message") or "")[:100]
        return f"{where} http={resp.status_code} iserver={code} {msg}"
    except Exception:
        return f"{where} http={resp.status_code}"


def _build_prompt_answers(prompts: list) -> list[dict]:
    """Port of extract_in_batch4.build_prompt_answers."""
    answers = []
    for p in prompts:
        if not isinstance(p, dict):
            continue
        ptype = p.get("type", "")
        pkey = p.get("key", p.get("id"))
        answer_val = p.get("answers") or p.get("defaultAnswer")
        if not answer_val:
            continue
        answers.append({"key": pkey, "type": ptype, "answers": answer_val})
    return answers


def _delete_report_instance(client: MstrClient, rid: str, iid: str) -> None:
    try:
        client.session.delete(
            client._url(f"/v2/reports/{rid}/instances/{iid}"),
            headers=client._headers(), verify=client.verify_ssl, timeout=client.timeout,
        )
    except Exception:
        pass


def _delete_cube_instance(client: MstrClient, cid: str, iid: str) -> None:
    try:
        client.session.delete(
            client._url(f"/cubes/{cid}/instances/{iid}"),
            headers=client._headers(), verify=client.verify_ssl, timeout=client.timeout,
        )
    except Exception:
        pass


def _extract_sql_from_response(data: dict) -> str:
    """Pull SQL out of a sqlView response. Prefers `sqlStatements[]` (multi-
    pass) joined with PASS_BREAK; falls back to scalar `sqlStatement` / `sql`."""
    stmts = data.get("sqlStatements") or []
    if stmts:
        joined = PASS_BREAK.join(
            s.get("statement", "") for s in stmts if isinstance(s, dict)
        )
        if joined.strip():
            return joined
    return data.get("sqlStatement") or data.get("sql") or ""


# ---------------- Report-level strategies -----------------------

def _try_report_path(
    client: MstrClient, rid: str, timeout: int = 30,
) -> tuple[str | None, str | None, str | None]:
    """Try the full report-level ladder:
      1. Create instance (resolve_prompts) -> sqlView
      2. If 406/open-prompts: answer prompts from defaults -> retry
      3. If still failing: fresh instance (full execute) -> sqlView
    Returns (sql, error, path_taken).
    """
    attempts: list[str] = []
    iid: str | None = None

    # Step 1: create instance with resolve_prompts
    try:
        resp = client.session.post(
            client._url(f"/v2/reports/{rid}/instances"),
            headers=client._headers(),
            params={"executionStage": "resolve_prompts"},
            verify=client.verify_ssl, timeout=timeout,
        )
        if not resp.ok:
            attempts.append(_err_of("v2-prompts-instance", resp))
        else:
            iid = (resp.json() or {}).get("instanceId")
    except Exception as e:
        attempts.append(f"v2-prompts exception: {str(e)[:80]}")

    # Step 2: first sqlView
    if iid:
        try:
            r = client.session.get(
                client._url(f"/v2/reports/{rid}/instances/{iid}/sqlView"),
                headers=client._headers(), verify=client.verify_ssl, timeout=timeout,
            )
            if r.ok:
                sql = _extract_sql_from_response(r.json() or {})
                if sql.strip():
                    _delete_report_instance(client, rid, iid)
                    return sql, None, "report_sqlview"
            else:
                attempts.append(_err_of("first-sqlview", r))
                # Prompt-blocked? If so, try auto-answer on THIS instance.
                is_prompt_blocked = (r.status_code == 406) or (
                    "open prompts" in (r.text or "").lower()
                )
                if is_prompt_blocked:
                    sql, err_ans = _try_prompt_autoanswer(client, rid, iid, timeout)
                    if sql:
                        _delete_report_instance(client, rid, iid)
                        return sql, None, "report_autoanswer"
                    if err_ans:
                        attempts.append(err_ans)
        except Exception as e:
            attempts.append(f"first-sqlview exception: {str(e)[:80]}")
        _delete_report_instance(client, rid, iid)

    # Step 3: full execute (no resolve_prompts — MSTR may still block on prompts,
    # but in some environments this auto-runs with cached defaults).
    iid2: str | None = None
    try:
        resp = client.session.post(
            client._url(f"/v2/reports/{rid}/instances"),
            headers=client._headers(), verify=client.verify_ssl, timeout=timeout,
        )
        if not resp.ok:
            attempts.append(_err_of("v2-execute", resp))
        else:
            iid2 = (resp.json() or {}).get("instanceId")
    except Exception as e:
        attempts.append(f"v2-execute exception: {str(e)[:80]}")

    if iid2:
        try:
            r = client.session.get(
                client._url(f"/v2/reports/{rid}/instances/{iid2}/sqlView"),
                headers=client._headers(), verify=client.verify_ssl, timeout=timeout,
            )
            if r.ok:
                sql = _extract_sql_from_response(r.json() or {})
                if sql.strip():
                    _delete_report_instance(client, rid, iid2)
                    return sql, None, "report_execute"
            else:
                attempts.append(_err_of("execute-sqlview", r))
        except Exception as e:
            attempts.append(f"execute-sqlview exception: {str(e)[:80]}")
        _delete_report_instance(client, rid, iid2)

    return None, " || ".join(attempts) if attempts else "unknown", None


def _try_prompt_autoanswer(
    client: MstrClient, rid: str, iid: str, timeout: int,
) -> tuple[str | None, str | None]:
    """Given an existing report instance blocked on prompts, fetch prompt
    definitions, build answers from their defaults, submit, retry sqlView."""
    try:
        rp = client.session.get(
            client._url(f"/reports/{rid}/instances/{iid}/prompts"),
            headers=client._headers(), verify=client.verify_ssl, timeout=timeout,
        )
        if not rp.ok:
            return None, _err_of("prompts-get", rp)
        pdata = rp.json() if rp.text else []
    except Exception as e:
        return None, f"prompts-get exception: {str(e)[:80]}"

    prompts = pdata if isinstance(pdata, list) else (pdata.get("prompts") or [])
    if not prompts:
        return None, "no prompts returned"

    answers = _build_prompt_answers(prompts)
    if not answers:
        return None, f"{len(prompts)} prompts without defaults"

    try:
        rput = client.session.put(
            client._url(f"/reports/{rid}/instances/{iid}/prompts/answers"),
            headers=client._headers(), json={"prompts": answers},
            verify=client.verify_ssl, timeout=timeout,
        )
        if not rput.ok:
            return None, _err_of("prompts-put", rput)
    except Exception as e:
        return None, f"prompts-put exception: {str(e)[:80]}"

    try:
        r = client.session.get(
            client._url(f"/v2/reports/{rid}/instances/{iid}/sqlView"),
            headers=client._headers(), verify=client.verify_ssl, timeout=timeout,
        )
        if not r.ok:
            return None, _err_of("retry-sqlview", r)
        sql = _extract_sql_from_response(r.json() or {})
        return (sql if sql.strip() else None), (None if sql.strip() else "empty sqlStatement after auto-answer")
    except Exception as e:
        return None, f"retry-sqlview exception: {str(e)[:80]}"


# ---------------- Cube-level strategy ---------------------------

def _resolve_source_cubes(
    client: MstrClient, rid: str, timeout: int,
) -> list[dict]:
    """Look up the report's source cube(s). Tries /model/reports first, then
    /v2/reports. Returns [{id, name}, ...] (empty if not cube-sourced)."""
    cubes: list[dict] = []
    try:
        r = client.session.get(
            client._url(f"/model/reports/{rid}"),
            headers=client._headers(), verify=client.verify_ssl, timeout=timeout,
        )
        if r.ok:
            defn = r.json() or {}
            datasets = (defn.get("dataSource") or {}).get("datasets") or []
            for d in datasets:
                if d.get("id"):
                    cubes.append({"id": d["id"], "name": d.get("name", "")})
    except Exception:
        pass

    if cubes:
        return cubes

    try:
        r = client.session.get(
            client._url(f"/v2/reports/{rid}"),
            headers=client._headers(), verify=client.verify_ssl, timeout=timeout,
        )
        if r.ok:
            defn = r.json() or {}
            src = defn.get("source") or {}
            if src.get("id"):
                cubes.append({"id": src["id"], "name": src.get("name", "")})
    except Exception:
        pass

    return cubes


def _get_cube_sql(
    client: MstrClient, cube_id: str, timeout: int = 30,
) -> tuple[str | None, str | None]:
    """POST /cubes/{id}/instances + GET /v2/cubes/{id}/sqlView. Multi-pass
    aware. Returns (sql, error)."""
    iid: str | None = None
    try:
        r = client.session.post(
            client._url(f"/cubes/{cube_id}/instances"),
            headers=client._headers(),
            params={"executionStage": "RESOLVE_PROMPTS"},
            verify=client.verify_ssl, timeout=timeout,
        )
        if not r.ok:
            return None, _err_of("cube-instance", r)
        iid = (r.json() or {}).get("instanceId") or (r.json() or {}).get("id")
    except Exception as e:
        return None, f"cube-instance exception: {str(e)[:80]}"

    try:
        sr = client.session.get(
            client._url(f"/v2/cubes/{cube_id}/sqlView"),
            headers=client._headers(), verify=client.verify_ssl, timeout=timeout,
        )
        if not sr.ok:
            return None, _err_of("cube-sqlview", sr)
        sql = _extract_sql_from_response(sr.json() or {})
        return (sql if sql.strip() else None), (None if sql.strip() else "empty cube sql")
    except Exception as e:
        return None, f"cube-sqlview exception: {str(e)[:80]}"
    finally:
        if iid:
            _delete_cube_instance(client, cube_id, iid)


def _try_cube_fallback(
    client: MstrClient, rid: str, timeout: int,
) -> tuple[str | None, str | None, str | None]:
    """When report path fails, try cube-SQL extraction. Two sub-cases:
      (a) report is cube-SOURCED — resolve its source cube(s) via /model/reports
      (b) the object ID is itself a pure CUBE — try cube endpoints with that ID
    Returns (sql, err, path).
    """
    cubes = _resolve_source_cubes(client, rid, timeout)

    # (b) If not cube-sourced, try treating the ID itself as a cube. This
    # catches pure-cube objects (type 776) that MSTR rejects on /v2/reports/.
    if not cubes:
        sql, err = _get_cube_sql(client, rid, timeout)
        if sql:
            return sql, None, "cube_direct"
        # If cube endpoint also errors with "not a cube", just no-op
        if err and "not a cube" in err.lower():
            return None, None, None
        return None, f"cube_direct: {err}" if err else None, None

    ok_parts: list[str] = []
    errs: list[str] = []
    for cube in cubes:
        sql, err = _get_cube_sql(client, cube["id"], timeout)
        if sql:
            ok_parts.append(f"-- cube: {cube.get('name','')} ({cube['id']})\n{sql}")
        else:
            errs.append(f"{cube['id'][:16]}:{err}")

    if ok_parts:
        joined = (PASS_BREAK).join(ok_parts)
        return joined, None, "cube"
    return None, f"cubes=[{','.join(errs)}]", None


# ---------------- Top-level extractor ----------------------------

def try_extract_sql(
    client: MstrClient, rid: str, timeout: int = 30,
) -> tuple[str | None, str | None, str | None]:
    """Top-level extractor. Returns (sql, error, path_taken)."""
    # Report path (with auto-answer + full-execute fallback)
    sql, err, path = _try_report_path(client, rid, timeout=timeout)
    if sql:
        return sql, None, path
    # Cube fallback
    csql, cerr, cpath = _try_cube_fallback(client, rid, timeout=timeout)
    if csql:
        return csql, None, cpath
    merged_err = err or ""
    if cerr:
        merged_err = (merged_err + " || cube: " + cerr).strip(" |")
    return None, merged_err or "unknown", None


# ---------------- Scope resolvers --------------------------------

def _load_candidates(scope: str, project: str, limit: int | None) -> dict[str, list[str]]:
    """Build {project_id: [report_id, ...]} for the selected scope."""
    if scope == "candidates":
        if not CANDIDATES_FILE.exists():
            sys.exit(f"Run the scan first: {CANDIDATES_FILE} missing")
        data = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
        if project != "all":
            data = {project: data.get(project, [])}
        return data

    # scope == "missing-sql": query the DB
    if not DB_PATH.exists():
        sys.exit(f"SQLite DB missing at {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        out: dict[str, list[str]] = {}
        projects = [project] if project != "all" else list(PROJECT_MAP.keys())
        for pid in projects:
            q = (
                "SELECT r.report_id "
                "  FROM report r "
                "  LEFT JOIN report_sql rs USING (project_id, report_id) "
                " WHERE r.project_id=? AND r.status='active' "
                "   AND (rs.sql_text IS NULL OR rs.sql_text='') "
                " ORDER BY r.report_id"
            )
            ids = [row[0] for row in conn.execute(q, (pid,))]
            if limit is not None:
                ids = ids[:limit]
            out[pid] = ids
        return out
    finally:
        conn.close()


# ---------------- Main -------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="candidates",
                    choices=["candidates", "missing-sql"],
                    help="'candidates' reads retry_sql_candidates.json; "
                         "'missing-sql' queries the DB for all active reports "
                         "without sql_text yet")
    ap.add_argument("--project", default="all",
                    choices=["global-operational", "global-insight", "insight", "all"])
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap number of reports per project (useful for testing)")
    ap.add_argument("--delay", type=float, default=0.15)
    ap.add_argument("--save-every", type=int, default=25)
    ap.add_argument("--ids", nargs="+", default=None,
                    help="Override: only try these specific report IDs")
    ap.add_argument("--no-cube-fallback", action="store_true",
                    help="Skip the cube-SQL fallback (report-path only)")
    args = ap.parse_args()

    _load_env_local()

    # Build candidate set
    if args.ids:
        candidates = {args.project if args.project != "all" else "global-insight": list(args.ids)}
    else:
        candidates = _load_candidates(args.scope, args.project, args.limit)

    # Resume state
    results: dict[str, dict] = {}
    if RESULTS_FILE.exists():
        try:
            results = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
            print(f"Resuming with {len(results):,} already-attempted IDs")
        except Exception:
            results = {}

    ok_total = err_total = 0
    by_path: dict[str, int] = {}

    for pid, ids in candidates.items():
        if not ids:
            continue
        to_try = [r for r in ids if r not in results]
        if not to_try:
            print(f"\n{pid}: nothing to try (all already in results)")
            continue
        print(f"\n=== {pid}: {len(to_try):,} to try ===")

        cfg = PROJECT_MAP[pid]
        client = MstrClient(
            base_url=BASE_URL,
            username=os.environ["MSTR_USERNAME"],
            password=os.environ["MSTR_PASSWORD"],
            login_mode=1,
            verify_ssl=False,
            timeout=60,
        )
        client.login()
        client.resolve_project(
            project_id=cfg["mstr_id"] or None,
            project_name=None if cfg["mstr_id"] else cfg["name"],
        )

        t0 = time.time()
        ok = err = 0
        for i, rid in enumerate(to_try, 1):
            if args.no_cube_fallback:
                sql, error, path = _try_report_path(client, rid)
            else:
                sql, error, path = try_extract_sql(client, rid)

            if sql:
                results[rid] = {
                    "project_id": pid, "sql": sql,
                    "sql_hash": _norm_sql_hash(sql),
                    "path": path,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                ok += 1
                by_path[path or "?"] = by_path.get(path or "?", 0) + 1
            else:
                results[rid] = {
                    "project_id": pid, "error": error,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                err += 1

            if i % args.save_every == 0 or i == len(to_try):
                RESULTS_FILE.write_text(json.dumps(results, default=str), encoding="utf-8")
                rate = i / max(0.1, time.time() - t0)
                eta = (len(to_try) - i) / rate if rate > 0 else 0
                paths_summary = " ".join(f"{k}={v}" for k, v in sorted(by_path.items()))
                print(
                    f"  [{i:,}/{len(to_try):,}] ok={ok} err={err}  "
                    f"{rate:.1f}/s eta={int(eta//60)}m{int(eta%60)}s  "
                    f"[{paths_summary}]",
                    flush=True,
                )
            time.sleep(args.delay)

        try:
            client.logout()
        except Exception:
            pass
        print(f"  {pid} done: ok={ok} err={err}")
        ok_total += ok
        err_total += err

    RESULTS_FILE.write_text(json.dumps(results, default=str), encoding="utf-8")
    print(f"\n=== TOTAL ===  ok={ok_total:,}  errors={err_total:,}")
    print(f"Path breakdown: {by_path}")
    print(f"Results saved to {RESULTS_FILE}")

    # Persist successes into raw_inventory_sql
    print("\nWriting recovered SQL into raw_inventory_sql...")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = OFF")
    wrote = 0
    with conn:
        for rid, info in results.items():
            if not info.get("sql"):
                continue
            pid = info["project_id"]
            row = conn.execute(
                "SELECT batch FROM raw_inventory WHERE project_id=? AND report_id=? LIMIT 1",
                (pid, rid),
            ).fetchone()
            if not row:
                continue
            batch = row[0]
            conn.execute(
                """INSERT OR REPLACE INTO raw_inventory_sql
                   (project_id, report_id, batch, sql_text, sql_hash, sql_error)
                   VALUES (?, ?, ?, ?, ?, NULL)""",
                (pid, rid, batch, info["sql"], info["sql_hash"]),
            )
            conn.execute(
                """UPDATE raw_inventory SET has_sql=1, sql_hash=?
                    WHERE project_id=? AND report_id=? AND batch=?""",
                (info["sql_hash"], pid, rid, batch),
            )
            wrote += 1
    conn.close()
    print(f"  Wrote {wrote:,} SQL records to raw_inventory_sql")
    print("\nNext: run `python -m db.compute.build --project all --skip-ingest`"
          " then `python -m db.emit` to surface the new SQL in the UI.")


if __name__ == "__main__":
    sys.exit(main())
