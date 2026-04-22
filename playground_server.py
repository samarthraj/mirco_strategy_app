"""FastAPI sidecar for the AI Playground tab.

Runs alongside the Vite dev server. Vite proxies /playground_api/* to
this server on localhost:8899. The UI POSTs experiment configs here;
this server runs them via db.compute.playground and returns results.

Start:  python playground_server.py
Stop:   Ctrl-C

Requires OPENAI_API_KEY (read from .env.local at repo root).
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Load .env.local into process env so downstream modules see OPENAI_API_KEY
env_path = ROOT / ".env.local"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v

import threading
import traceback
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db.compute.playground import run_experiment, PLAYGROUND_DIR
from db.compute.domain_classify import (
    run_domain_classification, DEFAULT_DOMAINS,
)
from db.compute.density_cluster import (
    run_density_clustering, DEFAULTS as DENSITY_DEFAULTS,
)


app = FastAPI(title="AI Playground Sidecar")


# ============================================================
# Task registry — survives across UI tab switches.
# Keyed by task_id, persisted to disk at PLAYGROUND_DIR/tasks.json so tasks
# and their status are recoverable even if the user closes the browser or
# reloads the page mid-run.
# ============================================================

_TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()
_TASKS_FILE = PLAYGROUND_DIR / "tasks.json"


def _save_tasks() -> None:
    try:
        PLAYGROUND_DIR.mkdir(parents=True, exist_ok=True)
        serializable = {}
        for tid, t in _TASKS.items():
            # Don't persist the full result — it's already saved as its own
            # <exp_id>.json file and can get large. Just remember the pointer.
            serializable[tid] = {
                "id": t["id"],
                "expId": t.get("expId"),
                "status": t["status"],
                "error": t.get("error"),
                "startedAt": t.get("startedAt"),
                "endedAt": t.get("endedAt"),
                "name": t.get("name"),
                "config": t.get("config"),
            }
        _TASKS_FILE.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[tasks] save failed: {e}")


def _load_tasks() -> None:
    if not _TASKS_FILE.exists():
        return
    try:
        data = json.loads(_TASKS_FILE.read_text(encoding="utf-8"))
        # Any task that was 'running' when the server last exited is effectively lost.
        # Mark them as 'interrupted' so the UI can offer a retry.
        for tid, t in data.items():
            if t.get("status") == "running":
                t["status"] = "interrupted"
                t["error"] = "Server restarted while task was running"
            _TASKS[tid] = t
        print(f"[tasks] restored {len(_TASKS)} tasks from {_TASKS_FILE}")
    except Exception as e:
        print(f"[tasks] load failed: {e}")


def _run_in_background(task_id: str, config: dict, runner=run_experiment) -> None:
    """Run an experiment off the request thread. Updates _TASKS as it progresses.
    `runner` lets us dispatch to the domain classifier or other modes."""
    try:
        result = runner(config, verbose=True)
        with _TASKS_LOCK:
            _TASKS[task_id].update({
                "status": "completed",
                "expId": result.get("id"),
                "endedAt": datetime.now(timezone.utc).isoformat(),
            })
            _save_tasks()
    except SystemExit as e:
        with _TASKS_LOCK:
            _TASKS[task_id].update({
                "status": "failed",
                "error": str(e),
                "endedAt": datetime.now(timezone.utc).isoformat(),
            })
            _save_tasks()
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        with _TASKS_LOCK:
            _TASKS[task_id].update({
                "status": "failed",
                "error": f"{type(e).__name__}: {e}",
                "endedAt": datetime.now(timezone.utc).isoformat(),
            })
            _save_tasks()


_load_tasks()

# Allow the Vite dev server to call us directly in case proxy is bypassed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class PlaygroundConfig(BaseModel):
    id: str | None = None
    name: str | None = None
    project: str
    scope: str = "post-family"
    sourceFilter: str = "all"
    model: str = "text-embedding-3-large"
    dimensions: int = 3072
    threshold: float = 0.85
    minClusterSize: int = 2
    fields: dict[str, Any] = {}
    limit: int | None = None


@app.get("/playground_api/health")
def health() -> dict:
    return {"ok": True, "openai_key_set": bool(os.environ.get("OPENAI_API_KEY"))}


@app.get("/playground_api/index")
def get_index() -> list[dict]:
    path = PLAYGROUND_DIR / "index.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/playground_api/exp/{exp_id}")
def get_experiment(exp_id: str) -> dict:
    path = PLAYGROUND_DIR / f"{exp_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"experiment {exp_id} not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/playground_api/run")
def run(config: PlaygroundConfig) -> dict:
    """Start an experiment asynchronously. Returns a task_id immediately; the
    UI polls /playground_api/task/{task_id} until status is 'completed' or
    'failed', then fetches the experiment result.

    Async is mandatory because experiments on fresh embeddings can take
    several minutes — browsers will time out a blocking POST.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY not set on the server. Add it to .env.local.",
        )
    cfg = config.model_dump()
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    task = {
        "id": task_id,
        "status": "running",
        "name": cfg.get("name") or cfg.get("id") or task_id,
        "expId": cfg.get("id"),
        "config": cfg,
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "endedAt": None,
        "error": None,
    }
    with _TASKS_LOCK:
        _TASKS[task_id] = task
        _save_tasks()
    t = threading.Thread(target=_run_in_background, args=(task_id, cfg), daemon=True)
    t.start()
    # Return a SHALLOW COPY so the background thread mutating _TASKS[task_id]
    # doesn't race our response serialization. Without this, a fast-failing
    # task can flip status before FastAPI encodes the reply.
    return dict(task)


class DensityClusterConfig(BaseModel):
    id: str | None = None
    name: str | None = None
    project: str
    scope: str = "post-family"
    sourceFilter: str = "all"
    model: str = "text-embedding-3-large"
    dimensions: int = 3072
    umapNeighbors: int = DENSITY_DEFAULTS["umap_n_neighbors"]
    umapMinDist: float = DENSITY_DEFAULTS["umap_min_dist"]
    umapComponents: int = DENSITY_DEFAULTS["umap_n_components"]
    minClusterSize: int = DENSITY_DEFAULTS["hdbscan_min_cluster_size"]
    minSamples: int = DENSITY_DEFAULTS["hdbscan_min_samples"]
    clusterSelection: str = DENSITY_DEFAULTS["hdbscan_cluster_selection_method"]
    limit: int | None = None
    fields: dict[str, Any] | None = None


@app.get("/playground_api/density/defaults")
def density_defaults() -> dict:
    return DENSITY_DEFAULTS


@app.post("/playground_api/density/run")
def run_density(config: DensityClusterConfig) -> dict:
    """Kick off a UMAP + HDBSCAN density-clustering job. Task contract is the
    same as /playground_api/run — poll /playground_api/task/{id}."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY not set on the server. Add it to .env.local.",
        )
    cfg = config.model_dump()
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    task = {
        "id": task_id,
        "status": "running",
        "name": cfg.get("name") or cfg.get("id") or task_id,
        "expId": cfg.get("id"),
        "config": cfg,
        "mode": "density",
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "endedAt": None,
        "error": None,
    }
    with _TASKS_LOCK:
        _TASKS[task_id] = task
        _save_tasks()
    t = threading.Thread(
        target=_run_in_background,
        args=(task_id, cfg, run_density_clustering),
        daemon=True,
    )
    t.start()
    return dict(task)


class DomainClassifyConfig(BaseModel):
    id: str | None = None
    name: str | None = None
    project: str
    scope: str = "post-family"
    sourceFilter: str = "all"
    model: str = "gpt-5.4"
    taxonomy: list[str] | None = None
    limit: int | None = None


@app.get("/playground_api/domains/taxonomy")
def default_taxonomy() -> dict:
    """Return the fixed domain list the UI should show by default."""
    return {"domains": DEFAULT_DOMAINS}


@app.post("/playground_api/domains/run")
def run_domains(config: DomainClassifyConfig) -> dict:
    """Kick off an async domain classification. Same task-tracking contract
    as /playground_api/run — poll /playground_api/task/{id} until done."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY not set on the server. Add it to .env.local.",
        )
    cfg = config.model_dump()
    if not cfg.get("taxonomy"):
        cfg["taxonomy"] = DEFAULT_DOMAINS
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    task = {
        "id": task_id,
        "status": "running",
        "name": cfg.get("name") or cfg.get("id") or task_id,
        "expId": cfg.get("id"),
        "config": cfg,
        "mode": "domain",
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "endedAt": None,
        "error": None,
    }
    with _TASKS_LOCK:
        _TASKS[task_id] = task
        _save_tasks()
    t = threading.Thread(
        target=_run_in_background,
        args=(task_id, cfg, run_domain_classification),
        daemon=True,
    )
    t.start()
    return dict(task)


@app.get("/playground_api/task/{task_id}")
def get_task(task_id: str) -> dict:
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        return dict(task)


@app.get("/playground_api/tasks")
def list_tasks() -> list[dict]:
    """Return all known tasks, newest first. Lets the UI resume after a reload."""
    with _TASKS_LOCK:
        tasks = list(_TASKS.values())
    tasks.sort(key=lambda t: t.get("startedAt") or "", reverse=True)
    return tasks


# ============================================================
# AI Assistant — conversational Q&A over the rationalization data.
# Builds a compact data context from the project's emitted JSON files
# and asks GPT to answer the user's question grounded in that context.
# ============================================================

from openai import OpenAI as _OpenAI_for_assistant

ASSISTANT_MODEL = "gpt-5.4"
DATA_ROOT = ROOT / "public" / "data"

PROJECT_NAME_MAP = {
    "global-operational": "Global Operational",
    "global-insight": "Global Insight",
    "insight": "INSIGHT",
    "Global Operational": "Global Operational",
    "Global Insight": "Global Insight",
    "INSIGHT": "INSIGHT",
    "E77B77894C04BF0E6D244F9363CFAF64": "Global Operational",
    "07E2CE9311EB6800B59F0080EF050FB2": "Global Insight",
    "6104D29041297D66C6BD16B602F2705F": "INSIGHT",
}


def _resolve_project_name(ref: str) -> str | None:
    return PROJECT_NAME_MAP.get((ref or "").strip())


def _load_project_data(project_name: str) -> dict:
    """Load + condense the emitted JSON for one project into a token-efficient
    context blob the LLM can reason over."""
    pdir = DATA_ROOT / project_name
    if not pdir.exists():
        return {}
    out: dict = {"project": project_name}

    def _read(fname: str, fallback):
        p = pdir / fname
        if not p.exists():
            return fallback
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return fallback

    summary = _read("summary.json", {})
    out["summary"] = {
        k: summary.get(k) for k in [
            "totalInventory", "totalReportsInventory", "totalDossiers",
            "afterTelemetry", "retired", "afterCollisionCollapse",
            "afterFingerprint", "afterSqlHash", "afterAst",
            "afterFamily", "familyReducible",
            "afterSimilarity", "clustersTotal",
            "afterSemantic", "semanticClustersTotal", "semanticLlmRemovable",
            "reductionPct",
        ] if k in summary
    }

    reports = _read("reports.json", [])
    final_kept = [r for r in reports if r.get("isFinalCanonical")]
    final_kept.sort(key=lambda r: -(r.get("executions") or 0))
    out["finalKeptCount"] = len(final_kept)
    out["finalKeptTop"] = [
        {
            "id": r["id"], "name": r["name"], "owner": r.get("owner", ""),
            "path": r.get("path", ""), "executions": r.get("executions", 0),
            "users": r.get("users", 0), "sourceType": r.get("sourceType", ""),
            "status": r.get("status", ""),
            "clusterId": r.get("clusterId"),
            "metricCount": r.get("metricCount"), "tableCount": r.get("tableCount"),
            "filterCount": r.get("filterCount"), "attributeCount": r.get("attributeCount"),
        }
        for r in final_kept[:50]
    ]
    # Source-type breakdown, broken down by pipeline stage so the LLM can
    # answer "how many cubes survived?" without having to cross-join.
    all_counts: dict = {}
    final_counts: dict = {}
    active_counts: dict = {}
    status_breakdown: dict = {}
    for r in reports:
        s = (r.get("sourceType") or "unknown").strip() or "unknown"
        all_counts[s] = all_counts.get(s, 0) + 1
        if r.get("isFinalCanonical"):
            final_counts[s] = final_counts.get(s, 0) + 1
        if (r.get("status") or "active") == "active":
            active_counts[s] = active_counts.get(s, 0) + 1
        st = r.get("status") or "active"
        status_breakdown[st] = status_breakdown.get(st, 0) + 1
    out["sourceBreakdown"] = {
        "overall": all_counts,
        "active_after_telemetry": active_counts,
        "final_kept": final_counts,
    }
    out["statusBreakdown"] = status_breakdown

    # Top Jaccard clusters
    jac = _read("clusters.json", [])
    jac_multi = [c for c in jac if not c.get("isSingletons")]
    jac_multi.sort(key=lambda c: -(c.get("size") or 0))
    out["jaccardClusterCount"] = len(jac_multi)
    out["jaccardTopClusters"] = [
        {
            "id": c["id"], "size": c["size"], "primaryName": c.get("primaryName", ""),
            "totalExecutions": c.get("totalExecutions", 0),
            "dataQuality": c.get("dataQuality", "high"),
        }
        for c in jac_multi[:20]
    ]

    # Top Semantic clusters
    sem = _read("semantic_clusters.json", [])
    sem_multi = [c for c in sem if not c.get("isSingletons")]
    sem_multi.sort(key=lambda c: -(c.get("size") or 0))
    out["semanticClusterCount"] = len(sem_multi)
    out["semanticTopClusters"] = [
        {
            "id": c["id"], "size": c["size"], "primaryName": c.get("primaryName", ""),
            "totalExecutions": c.get("totalExecutions", 0),
            "dataQuality": c.get("dataQuality", "high"),
            "llmLabel": (c.get("llm") or {}).get("label", ""),
            "llmAction": (c.get("llm") or {}).get("action", ""),
            "llmRemovable": (c.get("llm") or {}).get("removableCount", 0),
        }
        for c in sem_multi[:20]
    ]

    # Families
    fams = _read("families.json", [])
    fams.sort(key=lambda f: -(f.get("size") or 0))
    out["familyCount"] = len(fams)
    out["familyTop"] = [
        {"base": f["base"], "size": f["size"], "reducible": f["reducible"],
         "totalExecutions": f.get("totalExecutions", 0)}
        for f in fams[:15]
    ]

    return out


def _build_system_prompt(ctx: dict, project_name: str) -> str:
    return f"""You are the AI Rationalization Assistant for Bourntec's MicroStrategy
rationalization platform. The user is viewing project "{project_name}". Your job is to
answer questions GROUNDED in the data provided below. Be concise, factual, and specific.

## Rules
- Cite specific numbers, report names, or cluster IDs from the data when answering.
- If asked about something not in the provided data, say so and suggest where in the UI to
  find it (Reports tab / Clusters tab / Semantic Clusters / Families / Heavy Users / AI Playground).
- Business users ask questions — avoid jargon when plain English works.
- For questions about "cubes", "SQL reports", or "free SQL": use the sourceBreakdown.
- When asked about the pipeline stages, reference the numbers:
  {ctx.get('summary', {}).get('totalReportsInventory') or ctx.get('summary', {}).get('totalInventory')} inventory →
  {ctx.get('summary', {}).get('afterTelemetry')} active (after retirement) →
  {ctx.get('summary', {}).get('afterCollisionCollapse')} post-collision →
  {ctx.get('summary', {}).get('afterFingerprint')} post-fingerprint →
  {ctx.get('summary', {}).get('afterSqlHash')} post-SQL-hash →
  {ctx.get('summary', {}).get('afterAst')} post-AST →
  {ctx.get('summary', {}).get('afterFamily')} post-family (feeds clustering) →
  {ctx.get('summary', {}).get('afterSimilarity')} after Jaccard / {ctx.get('summary', {}).get('afterSemantic')} after Semantic.
- The final-kept set is {ctx.get('finalKeptCount')} reports.
- Clusters flagged dataQuality="low" are data-quality artifacts — say so; don't treat them as real
  rationalization targets. They exist because inventory extraction failed for most of their members.

## Data Snapshot (compact)
{_render_context(ctx)}
"""


def _render_context(ctx: dict) -> str:
    """Render the context with small/critical sections first so they always
    appear in the prompt even if we hit a length cap."""
    parts = []
    for key in ["project", "finalKeptCount", "summary", "sourceBreakdown",
                "statusBreakdown", "jaccardClusterCount", "semanticClusterCount",
                "familyCount", "familyTop", "semanticTopClusters",
                "jaccardTopClusters", "finalKeptTop"]:
        if key in ctx:
            parts.append(f"\n### {key}\n{json.dumps(ctx[key], indent=2, default=str)}")
    full = "\n".join(parts)
    MAX = 28000
    if len(full) > MAX:
        full = full[:MAX] + "\n…(truncated)"
    return full


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str


class ChatRequest(BaseModel):
    project: str
    messages: list[ChatMessage]


@app.post("/playground_api/assistant/chat")
def assistant_chat(req: ChatRequest) -> dict:
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY not set on the server. Add it to .env.local.",
        )
    project_name = _resolve_project_name(req.project)
    if not project_name:
        raise HTTPException(status_code=400, detail=f"unknown project: {req.project!r}")
    ctx = _load_project_data(project_name)
    if not ctx:
        raise HTTPException(
            status_code=404,
            detail=f"no data found for project {project_name}. Has the pipeline been run + emitted?",
        )
    system = _build_system_prompt(ctx, project_name)
    msgs = [{"role": "system", "content": system}]
    for m in req.messages[-20:]:  # cap conversation history
        if m.role in ("user", "assistant") and m.content.strip():
            msgs.append({"role": m.role, "content": m.content})
    client = _OpenAI_for_assistant(api_key=os.environ["OPENAI_API_KEY"])
    try:
        resp = client.chat.completions.create(
            model=ASSISTANT_MODEL,
            messages=msgs,  # type: ignore
            max_completion_tokens=1200,
            temperature=0.2,
        )
        answer = (resp.choices[0].message.content or "").strip()
        return {
            "answer": answer,
            "model": ASSISTANT_MODEL,
            "usage": {
                "promptTokens": resp.usage.prompt_tokens if resp.usage else None,
                "completionTokens": resp.usage.completion_tokens if resp.usage else None,
            },
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


# ============================================================
# "Active experiment" promotion. Each project can designate one experiment
# as its primary semantic clustering result. When set, the UI's Semantic
# Clusters tab loads this experiment's clusters (transformed into the
# SemanticCluster shape the UI already understands) instead of the static
# pipeline-emitted semantic_clusters.json.
# Registry is a tiny JSON file keyed by short project_id.
# ============================================================

_ACTIVE_FILE = PLAYGROUND_DIR / "active.json"


def _load_active() -> dict:
    if not _ACTIVE_FILE.exists():
        return {}
    try:
        return json.loads(_ACTIVE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_active(data: dict) -> None:
    PLAYGROUND_DIR.mkdir(parents=True, exist_ok=True)
    _ACTIVE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _resolve_short_project(ref: str) -> str:
    """Accept display name / short id / GUID; return short id. Raises 400."""
    from db.compute.playground import _resolve_project_id
    try:
        return _resolve_project_id(ref)
    except SystemExit as e:
        raise HTTPException(status_code=400, detail=str(e))


def _exp_index_entry(exp_id: str) -> dict | None:
    idx_path = PLAYGROUND_DIR / "index.json"
    if not idx_path.exists():
        return None
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for e in idx:
        if e.get("id") == exp_id:
            return e
    return None


@app.get("/playground_api/active/{project}")
def get_active(project: str) -> dict:
    short = _resolve_short_project(project)
    data = _load_active()
    exp_id = data.get(short)
    if not exp_id:
        return {"project": short, "expId": None}
    entry = _exp_index_entry(exp_id) or {}
    return {
        "project": short,
        "expId": exp_id,
        "name": entry.get("name"),
        "createdAt": entry.get("createdAt"),
        "stats": entry.get("stats"),
    }


class ActiveBody(BaseModel):
    expId: str


@app.post("/playground_api/active/{project}")
def set_active(project: str, body: ActiveBody) -> dict:
    short = _resolve_short_project(project)
    exp_path = PLAYGROUND_DIR / f"{body.expId}.json"
    if not exp_path.exists():
        raise HTTPException(status_code=404, detail=f"experiment {body.expId} not found")
    # Sanity check: experiment must belong to the same project
    entry = _exp_index_entry(body.expId)
    if entry and entry.get("project") and entry["project"] != short:
        raise HTTPException(
            status_code=400,
            detail=f"experiment {body.expId} belongs to project {entry['project']}, "
                   f"not {short}",
        )
    data = _load_active()
    data[short] = body.expId
    _save_active(data)
    return {"project": short, "expId": body.expId}


@app.delete("/playground_api/active/{project}")
def clear_active(project: str) -> dict:
    short = _resolve_short_project(project)
    data = _load_active()
    existed = data.pop(short, None)
    _save_active(data)
    return {"project": short, "cleared": existed is not None, "previousExpId": existed}


def _transform_to_semantic_clusters(exp: dict, project_id: str) -> list[dict]:
    """Translate an experiment result into the SemanticCluster[] shape the UI
    expects. Missing per-member cosineToPrimary is None (we don't store it per
    member in experiments). Executions/users come from telemetry_match."""
    from db.db import connect

    scatter = (exp.get("viz") or {}).get("scatter") or []
    names_by_rid = {p["id"]: p.get("name") or "" for p in scatter}
    execs_by_rid = {p["id"]: p.get("executions") or 0 for p in scatter}

    # Enrich with user counts from telemetry_match (not in the experiment).
    users_by_rid: dict[str, int] = {}
    try:
        conn = connect()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(telemetry_match)")}
            user_col = next(
                (c for c in ("total_users", "unique_users", "users_count") if c in cols),
                None,
            )
            if user_col:
                for rid, u in conn.execute(
                    f"SELECT report_id, {user_col} FROM telemetry_match WHERE project_id=?",
                    (project_id,),
                ):
                    users_by_rid[rid] = u or 0
        finally:
            conn.close()
    except Exception as e:
        print(f"[transform] user-count query failed (non-fatal): {e}")

    out: list[dict] = []
    for c in exp.get("clusters", []):
        members = c.get("memberIds", [])
        total_users = sum(users_by_rid.get(m, 0) for m in members)
        total_execs = c.get("totalExecutions") or sum(execs_by_rid.get(m, 0) for m in members)
        out.append({
            "id": c["id"],
            "size": c["size"],
            "primaryReportId": c["primaryReportId"],
            "primaryName": c.get("primaryName") or names_by_rid.get(c["primaryReportId"], ""),
            "totalExecutions": total_execs,
            "totalUsers": total_users,
            "avgCosine": c.get("avgCosine"),
            "minCosine": c.get("minCosine"),
            "members": [{"id": m, "cosineToPrimary": None} for m in members],
            "memberIds": members,
            "isSingletons": False,
            "dataQuality": "unknown",
        })

    # One synthetic "singletons" bucket — reports outside any multi cluster.
    singleton_ids = [p["id"] for p in scatter if not p.get("clusterId")]
    if singleton_ids:
        out.append({
            "id": "XSING",
            "size": len(singleton_ids),
            "primaryReportId": singleton_ids[0],
            "primaryName": names_by_rid.get(singleton_ids[0], ""),
            "totalExecutions": sum(execs_by_rid.get(s, 0) for s in singleton_ids),
            "totalUsers": sum(users_by_rid.get(s, 0) for s in singleton_ids),
            "avgCosine": None,
            "minCosine": None,
            "members": [{"id": s, "cosineToPrimary": None} for s in singleton_ids],
            "memberIds": singleton_ids,
            "isSingletons": True,
            "topExampleName": names_by_rid.get(singleton_ids[0], ""),
        })

    return out


@app.get("/playground_api/active/{project}/semantic_clusters")
def get_active_semantic_clusters(project: str) -> list[dict]:
    short = _resolve_short_project(project)
    data = _load_active()
    exp_id = data.get(short)
    if not exp_id:
        raise HTTPException(status_code=404, detail=f"no active experiment for {short}")
    path = PLAYGROUND_DIR / f"{exp_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"experiment {exp_id} file missing")
    exp = json.loads(path.read_text(encoding="utf-8"))
    return _transform_to_semantic_clusters(exp, short)


@app.delete("/playground_api/exp/{exp_id}")
def delete_experiment(exp_id: str) -> dict:
    exp_path = PLAYGROUND_DIR / f"{exp_id}.json"
    if exp_path.exists():
        exp_path.unlink()
    # Remove from index
    idx_path = PLAYGROUND_DIR / "index.json"
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            idx = [e for e in idx if e.get("id") != exp_id]
            idx_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")
        except Exception:
            pass
    # If this exp was the active one for any project, clear it.
    active = _load_active()
    cleared = [proj for proj, eid in active.items() if eid == exp_id]
    for proj in cleared:
        active.pop(proj, None)
    if cleared:
        _save_active(active)
    return {"deleted": exp_id, "clearedActiveFor": cleared}


if __name__ == "__main__":
    import uvicorn
    print("Starting AI Playground sidecar on http://localhost:8899")
    print(f"  PLAYGROUND_DIR: {PLAYGROUND_DIR}")
    print(f"  OPENAI_API_KEY: {'set' if os.environ.get('OPENAI_API_KEY') else 'MISSING'}")
    uvicorn.run(app, host="127.0.0.1", port=8899, log_level="info")
