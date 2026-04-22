"""One-shot test of batch4's prompt auto-answer flow on 5 prompted reports.

Flow (ported from extract_in_batch4.py):
  1. POST /v2/reports/{id}/instances?executionStage=RESOLVE_PROMPTS  -> instanceId
  2. GET  /v2/reports/{id}/instances/{iid}/sqlView   -> may 406 "open prompts"
  3. GET  /reports/{id}/instances/{iid}/prompts      -> prompt list
  4. Build answers from each prompt's `answers` or `defaultAnswer`
  5. PUT  /reports/{id}/instances/{iid}/prompts/answers  -> submit
  6. GET  /v2/reports/{id}/instances/{iid}/sqlView   -> retry
  7. DELETE /v2/reports/{id}/instances/{iid}         -> cleanup
"""
from __future__ import annotations
import json
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
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from mstr_report_inventory import MstrClient


GI_PROJECT_ID = "07E2CE9311EB6800B59F0080EF050FB2"

REPORTS_TO_TRY = [
    ("024066706C41CC7688B71FA42FC48EB5", "Blank Report", 4200),
    ("06308B0DBB49518085F878BB6B1EFA99", "Sourcing_Config_Dataset_V01", 2913),
    ("A2FAEEDB6D4C09824FABCAACAF4A5DFA", "Pin of Solidarity Tracker Report", 2197),
    ("18DED152437119DF873974BDE8A1AFC9", "LY by Class - Date Prompt", 1733),
    ("7B41232E724DFE4C801D73BD8FA30DC1", "Material L4 Finder", 1654),
]


def build_prompt_answers(prompts: list) -> list[dict]:
    """Ported verbatim from extract_in_batch4.build_prompt_answers."""
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


def try_extract(client: MstrClient, rid: str) -> dict:
    """Return a verbose result dict: {stage, iid, prompts_n, defaulted_n,
    answered, sql_len, sql, scalar_len, passes_n, error}."""
    out = {"stage": None, "iid": None, "prompts_n": 0, "defaulted_n": 0,
           "answered": False, "sql_len": 0, "scalar_len": 0, "passes_n": 0,
           "error": None}

    # 1. Create instance
    try:
        r = client.session.post(
            client._url(f"/v2/reports/{rid}/instances"),
            headers=client._headers(),
            params={"executionStage": "RESOLVE_PROMPTS"},
            verify=client.verify_ssl, timeout=client.timeout,
        )
    except Exception as e:
        out["stage"] = "instance_post"
        out["error"] = f"exception: {e}"
        return out
    if not r.ok:
        out["stage"] = "instance_post"
        out["error"] = f"http={r.status_code} {r.text[:180]}"
        return out
    try:
        instance = r.json() or {}
    except Exception:
        out["stage"] = "instance_post"
        out["error"] = "non-JSON instance response"
        return out
    iid = instance.get("instanceId") or instance.get("id")
    out["iid"] = iid
    if not iid:
        out["stage"] = "instance_post"
        out["error"] = "no instanceId"
        return out

    def _cleanup():
        try:
            client.session.delete(
                client._url(f"/v2/reports/{rid}/instances/{iid}"),
                headers=client._headers(), verify=client.verify_ssl, timeout=client.timeout,
            )
        except Exception:
            pass

    # 2. First sqlView attempt
    try:
        r2 = client.session.get(
            client._url(f"/v2/reports/{rid}/instances/{iid}/sqlView"),
            headers=client._headers(), verify=client.verify_ssl, timeout=client.timeout,
        )
    except Exception as e:
        _cleanup()
        out["stage"] = "first_sqlview"
        out["error"] = f"exception: {e}"
        return out

    first_ok = r2.ok
    first_msg = r2.text[:200] if not r2.ok else ""
    if first_ok:
        try:
            data = r2.json() or {}
            scalar = data.get("sqlStatement") or data.get("sql") or ""
            stmts = data.get("sqlStatements") or []
            out["scalar_len"] = len(scalar)
            out["passes_n"] = len(stmts)
            out["sql_len"] = len(scalar)
            out["sql"] = scalar
            out["stage"] = "first_sqlview_ok"
            _cleanup()
            return out
        except Exception:
            pass

    # 3. Fetch prompts
    try:
        r3 = client.session.get(
            client._url(f"/reports/{rid}/instances/{iid}/prompts"),
            headers=client._headers(), verify=client.verify_ssl, timeout=client.timeout,
        )
    except Exception as e:
        _cleanup()
        out["stage"] = "prompts_get"
        out["error"] = f"exception: {e} (first sqlView: {first_msg})"
        return out
    if not r3.ok:
        _cleanup()
        out["stage"] = "prompts_get"
        out["error"] = f"http={r3.status_code} {r3.text[:180]} (first sqlView: {first_msg})"
        return out
    try:
        pdata = r3.json()
    except Exception:
        _cleanup()
        out["stage"] = "prompts_get"
        out["error"] = "non-JSON prompts"
        return out
    prompts = pdata if isinstance(pdata, list) else (pdata.get("prompts") or [])
    out["prompts_n"] = len(prompts)
    out["defaulted_n"] = sum(
        1 for p in prompts
        if isinstance(p, dict) and (p.get("answers") or p.get("defaultAnswer"))
    )

    answers = build_prompt_answers(prompts)
    if not answers:
        _cleanup()
        out["stage"] = "build_answers"
        out["error"] = (
            f"{len(prompts)} prompt(s) but none have defaultAnswer / existing answers"
        )
        return out

    # 4. Submit answers
    try:
        r4 = client.session.put(
            client._url(f"/reports/{rid}/instances/{iid}/prompts/answers"),
            headers=client._headers(), json={"prompts": answers},
            verify=client.verify_ssl, timeout=client.timeout,
        )
    except Exception as e:
        _cleanup()
        out["stage"] = "put_answers"
        out["error"] = f"exception: {e}"
        return out
    if not r4.ok:
        _cleanup()
        out["stage"] = "put_answers"
        out["error"] = f"http={r4.status_code} {r4.text[:180]}"
        return out
    out["answered"] = True

    # 5. Retry sqlView
    try:
        r5 = client.session.get(
            client._url(f"/v2/reports/{rid}/instances/{iid}/sqlView"),
            headers=client._headers(), verify=client.verify_ssl, timeout=client.timeout,
        )
    except Exception as e:
        _cleanup()
        out["stage"] = "retry_sqlview"
        out["error"] = f"exception: {e}"
        return out
    if not r5.ok:
        _cleanup()
        out["stage"] = "retry_sqlview"
        out["error"] = f"http={r5.status_code} {r5.text[:180]}"
        return out
    try:
        data = r5.json() or {}
    except Exception:
        _cleanup()
        out["stage"] = "retry_sqlview"
        out["error"] = "non-JSON sqlView"
        return out

    scalar = data.get("sqlStatement") or data.get("sql") or ""
    stmts = data.get("sqlStatements") or []
    out["scalar_len"] = len(scalar)
    out["passes_n"] = len(stmts)
    out["sql_len"] = len(scalar)
    out["sql"] = scalar
    out["stage"] = "retry_sqlview_ok"
    _cleanup()
    return out


def main() -> int:
    base_url = os.environ["MSTR_BASE_URL"].rstrip("/")
    if not base_url.endswith("/api"):
        base_url = base_url + "/api"
    client = MstrClient(
        base_url=base_url, username=os.environ["MSTR_USERNAME"],
        password=os.environ["MSTR_PASSWORD"],
        login_mode=int(os.environ.get("MSTR_LOGIN_MODE", "1")),
        verify_ssl=False, timeout=60,
    )
    client.login()
    client.session.headers["X-MSTR-ProjectID"] = GI_PROJECT_ID
    print(f"Logged in. Project: Global Insight\n")

    results = []
    for i, (rid, name, execs) in enumerate(REPORTS_TO_TRY, 1):
        print(f"=> [{i}] {rid}  {name!r}  (execs={execs:,})")
        t0 = time.time()
        res = try_extract(client, rid)
        dt = time.time() - t0
        if res["sql_len"] > 0:
            tag = ("NO-PROMPT" if res["stage"] == "first_sqlview_ok"
                   else f"AUTO-ANSWER ({res['defaulted_n']}/{res['prompts_n']} defaults)")
            print(f"   OK  ({res['sql_len']:,} chars, {res['passes_n']} passes) — {tag} — {dt:.1f}s")
            preview = res["sql"][:180].replace("\n", " ")
            print(f"   preview: {preview}...")
        else:
            print(f"   FAIL at {res['stage']}: {res['error']} ({dt:.1f}s)")
            if res["prompts_n"]:
                print(f"   (had {res['prompts_n']} prompt(s), {res['defaulted_n']} with defaultAnswer)")
        print()
        results.append({"id": rid, "name": name, **res})
        time.sleep(0.5)

    try:
        client.logout()
    except Exception:
        pass

    print("=== SUMMARY ===")
    ok = [r for r in results if r["sql_len"] > 0]
    auto = [r for r in results if r["stage"] == "retry_sqlview_ok"]
    first = [r for r in results if r["stage"] == "first_sqlview_ok"]
    failed = [r for r in results if r["sql_len"] == 0]
    print(f"  SQL recovered:           {len(ok)}/{len(results)}")
    print(f"    via first sqlView:     {len(first)}")
    print(f"    via prompt auto-answer:{len(auto)}")
    print(f"  Still failing:           {len(failed)}")
    for r in results:
        tag = "OK  " if r["sql_len"] else "FAIL"
        note = (f"{r['sql_len']:,} chars" if r["sql_len"]
                else f"stage={r['stage']} {r['error'] or ''}"[:100])
        print(f"  {tag}  {r['id'][:16]}  prompts={r['prompts_n']}/defaulted={r['defaulted_n']}  {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
