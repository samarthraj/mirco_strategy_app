"""Run LLM review on a Playground experiment's clusters.

Takes an experiment ID (density, cosine, or domain — anything with a
clusters[] of memberIds), loads the experiment JSON, and for each
multi-member cluster asks GPT for a business consolidation label.

Writes the LLM response back onto each cluster as cluster["llm"] in the
camelCase shape the UI's SemanticCluster type expects:

    llm: {
        label, businessFunction, relationship, action, confidence,
        detail, keepReport, removableCount, error
    }

Cached per-cluster by content hash of (primaryName, commonMetrics,
commonTables, commonFilters, memberIds), so re-running an already-reviewed
experiment is free.

Same prompt shape as llm_review_go_clusters.py so the output plugs into
the Semantic Clusters / LLM Review tabs unchanged.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.db import connect, get_project
from db.compute.semantic import _load_env_local
from db.compute.playground import PLAYGROUND_DIR, _resolve_project_id


LLM_MODEL = "gpt-5.4"
MAX_MEMBERS_IN_PROMPT = 25


def _cluster_cache_key(cluster: dict) -> str:
    """Stable hash over the fields the LLM actually sees. If they change,
    we re-review; if not, cached result stands."""
    payload = {
        "primary": cluster.get("primaryName", ""),
        "size": cluster.get("size", 0),
        "members": sorted(cluster.get("memberIds", []))[:MAX_MEMBERS_IN_PROMPT],
        "metrics": sorted(cluster.get("commonMetrics") or [])[:15],
        "tables": sorted(cluster.get("commonTables") or [])[:15],
        "filters": sorted(cluster.get("commonFilters") or [])[:15],
        "domain": cluster.get("domain", ""),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _load_reports_for_project(project_id: str) -> dict[str, dict]:
    """Quick lookup: report_id -> {name, path, metrics[], tables[], filters[]}.
    Reads from SQLite so we don't depend on public/data JSONs being current."""
    conn = connect()
    try:
        out: dict[str, dict] = {}
        for r in conn.execute(
            "SELECT report_id, name, path FROM report WHERE project_id=?",
            (project_id,),
        ):
            out[r[0]] = {
                "id": r[0], "name": r[1] or "", "path": r[2] or "",
                "metrics": [], "tables": [], "filters": [],
            }
        # Lightweight enrichment — just counts so the prompt stays small.
        for tbl, col, key in [
            ("report_metric", "metric_name", "metrics"),
            ("report_table", "table_name", "tables"),
            ("report_filter", "filter_name", "filters"),
        ]:
            for rid, name in conn.execute(
                f"SELECT report_id, {col} FROM {tbl} WHERE project_id=?",
                (project_id,),
            ):
                rec = out.get(rid)
                if rec is not None:
                    rec[key].append(name)
        return out
    finally:
        conn.close()


def _build_prompt(cluster: dict, by_id: dict[str, dict]) -> str:
    """Same prompt shape as llm_review_go_clusters.py — GPT already knows
    how to answer it. Only diff: we don't always have commonMetrics/Tables
    for non-Jaccard experiments, so we aggregate from member metadata when
    the cluster record doesn't carry them."""
    member_ids = cluster.get("memberIds", [])[:MAX_MEMBERS_IN_PROMPT]
    member_lines = []
    agg_metrics: set[str] = set()
    agg_tables: set[str] = set()
    agg_filters: set[str] = set()
    for mid in member_ids:
        r = by_id.get(mid) or {}
        name = r.get("name", "")
        path = r.get("path", "")
        metrics = r.get("metrics") or []
        tables = r.get("tables") or []
        filters = r.get("filters") or []
        agg_metrics.update(metrics)
        agg_tables.update(tables)
        agg_filters.update(filters)
        member_lines.append(
            f"- {name[:70]} | metrics:{len(metrics)} tables:{len(tables)} filters:{len(filters)}"
            f"{f' | path: {path}' if path else ''}"
        )
    total = cluster.get("size", len(cluster.get("memberIds", [])))
    if total > MAX_MEMBERS_IN_PROMPT:
        member_lines.append(f"... +{total - MAX_MEMBERS_IN_PROMPT} more members")

    # Prefer cluster's own aggregates if it carries them (Jaccard-style),
    # else fall back to union across members we looked up.
    common_metrics = cluster.get("commonMetrics") or sorted(agg_metrics)[:15]
    common_tables = cluster.get("commonTables") or sorted(agg_tables)[:15]
    common_filters = cluster.get("commonFilters") or sorted(agg_filters)[:15]

    domain_line = ""
    if cluster.get("domain"):
        # For domain-classified experiments, give GPT the assigned bucket as
        # a hint.
        domain_line = f"\nAssigned business domain: {cluster['domain']}"

    # Emit numbered members so GPT can cite them by id in the removable list.
    numbered_lines = []
    for i, mid in enumerate(member_ids, 1):
        r = by_id.get(mid) or {}
        name = (r.get("name") or "")[:70]
        numbered_lines.append(f"  {i}. id={mid} | name={name}")

    return f"""Analyze this cluster of {total} MicroStrategy reports grouped by semantic similarity.

Cluster {cluster.get('id')} — primary: {cluster.get('primaryName', '')} (id={cluster.get('primaryReportId','')})
Common metrics: {', '.join(common_metrics[:8])}
Common tables: {', '.join(common_tables[:8])}
Common filter attrs: {', '.join(common_filters[:8])}
Total executions across members: {cluster.get('totalExecutions', 0):,}{domain_line}

Visible members (up to {MAX_MEMBERS_IN_PROMPT}):
{chr(10).join(numbered_lines)}

For `removable_member_ids`, include the ID of every visible member that is
safely consolidatable into the primary (or parameterizable). Do NOT include
the primary in that list. If a member is business-distinct enough to keep
separate, exclude it. Only cite IDs from the visible-members block above.

Respond with ONLY valid JSON:
{{
    "label": "Short descriptive business label",
    "business_function": "What these reports do for the business",
    "relationship": "EXACT_DUPLICATE | NEAR_DUPLICATE | PARAMETERIZED_VARIANTS | SIMILAR_DOMAIN | LOOSELY_RELATED",
    "consolidation_action": "MERGE_IMMEDIATE | PARAMETERIZE | REVIEW_WITH_OWNER | KEEP_SEPARATE",
    "confidence": "HIGH | MEDIUM | LOW",
    "consolidation_detail": "Specific migration recommendation",
    "keep_report": "Name of primary report to keep",
    "removable_count": (int — total removable across the WHOLE cluster; for clusters > {MAX_MEMBERS_IN_PROMPT} you're estimating from the visible sample),
    "removable_member_ids": ["id1", "id2", ...]  // visible members that can be removed
}}"""


def _parse_llm_response(text: str) -> dict:
    """Strip fences if present and parse JSON. Maps snake_case keys from
    the prompt to the camelCase our UI expects."""
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    raw_ids = data.get("removable_member_ids") or []
    # Be defensive — GPT sometimes returns non-strings or duplicates.
    removable_ids = sorted({str(x) for x in raw_ids if x})
    return {
        "label": data.get("label", ""),
        "businessFunction": data.get("business_function", ""),
        "relationship": data.get("relationship", ""),
        "action": data.get("consolidation_action", ""),
        "confidence": data.get("confidence", ""),
        "detail": data.get("consolidation_detail", ""),
        "keepReport": data.get("keep_report", ""),
        "removableCount": int(data.get("removable_count") or 0),
        "removableIds": removable_ids,
        "error": None,
    }


def run_experiment_llm_review(
    config: dict, verbose: bool = True,
) -> dict:
    """Review every multi-member cluster in an experiment and persist the
    LLM payloads back into the experiment JSON.

    config expects:
      - id        (exp_id)
      - project   (short project id or name)
      - model     (optional; defaults to gpt-5.4)
    """
    exp_id = config.get("id") or config.get("expId")
    if not exp_id:
        return {"error": "missing exp_id"}
    project_id = _resolve_project_id(config["project"])

    exp_path = PLAYGROUND_DIR / f"{exp_id}.json"
    if not exp_path.exists():
        return {"error": f"experiment {exp_id} not found"}

    exp = json.loads(exp_path.read_text(encoding="utf-8"))
    clusters = exp.get("clusters") or []
    multi = [c for c in clusters if c.get("size", 0) >= 2 and not c.get("isSingletons")]
    if not multi:
        return {"reviewed": 0, "skipped": 0, "note": "no multi-member clusters"}

    project_name = get_project(project_id)["name"]
    if verbose:
        print(f"\n=== LLM review of experiment {exp_id} — project {project_name} ===")
        print(f"  multi-clusters to review: {len(multi):,}")

    # Build existing-reviews cache — key by cluster cache-hash so we can
    # skip re-reviewing unchanged clusters.
    existing_by_cid: dict[str, dict] = {}
    existing_by_hash: dict[str, dict] = {}
    for c in clusters:
        cid = c.get("id")
        llm = c.get("llm")
        if not cid or not llm:
            continue
        existing_by_cid[cid] = llm
        # Check if this llm still applies — match on stored cache key if any
        stored_hash = llm.get("_cacheKey")
        if stored_hash:
            existing_by_hash[stored_hash] = llm

    # `dict.get(k, default)` only returns default when k is MISSING, not when
    # the value is None / empty. Use `or` so a None/empty config value still
    # falls back to the default model — this was the source of the original
    # "you must provide a model parameter" failures.
    model = config.get("model") or LLM_MODEL

    _load_env_local()
    if not os.environ.get("OPENAI_API_KEY"):
        return {"error": "OPENAI_API_KEY not set"}
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    by_id = _load_reports_for_project(project_id)

    reviewed = 0
    cached = 0
    errors = 0
    t0 = time.time()

    for i, cluster in enumerate(multi, 1):
        cid = cluster.get("id")
        cache_key = _cluster_cache_key(cluster)

        # Cached (same inputs) → reuse
        if cache_key in existing_by_hash and not existing_by_hash[cache_key].get("error"):
            cluster["llm"] = existing_by_hash[cache_key]
            cached += 1
            if verbose and i % 25 == 0:
                print(f"  [{i}/{len(multi)}] cached")
            continue

        prompt = _build_prompt(cluster, by_id)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=2000,
                temperature=0.1,
            )
            text = (resp.choices[0].message.content or "").strip()
            parsed = _parse_llm_response(text)
            parsed["_cacheKey"] = cache_key
            cluster["llm"] = parsed
            reviewed += 1
            if verbose:
                print(
                    f"  [{i}/{len(multi)}] {cid} size={cluster.get('size'):3} -> "
                    f"{parsed['action']} removable={parsed['removableCount']} "
                    f"({parsed['label'][:40]})"
                )
        except Exception as e:
            cluster["llm"] = {
                "label": "", "businessFunction": "", "relationship": "",
                "action": "", "confidence": "", "detail": "",
                "keepReport": "", "removableCount": 0,
                "error": str(e)[:200], "_cacheKey": cache_key,
            }
            errors += 1
            if verbose:
                print(f"  [{i}/{len(multi)}] {cid} — ERROR: {str(e)[:120]}")

        # Save after every 10 to preserve progress on interrupt
        if reviewed % 10 == 0 and reviewed > 0:
            exp["llmReviewedAt"] = datetime.now(timezone.utc).isoformat()
            exp_path.write_text(json.dumps(exp, indent=2, default=str), encoding="utf-8")

        # Gentle rate limit
        time.sleep(0.3)

    # Final save
    exp["llmReviewedAt"] = datetime.now(timezone.utc).isoformat()
    exp["llmReviewSummary"] = _summarize(multi)
    exp_path.write_text(json.dumps(exp, indent=2, default=str), encoding="utf-8")

    # Also update the index so the UI can show "reviewed" at a glance
    idx_path = PLAYGROUND_DIR / "index.json"
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            for e in idx:
                if e.get("id") == exp_id:
                    e["llmReviewedAt"] = exp["llmReviewedAt"]
                    e["llmReviewSummary"] = exp["llmReviewSummary"]
                    break
            idx_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")
        except Exception:
            pass

    elapsed = time.time() - t0
    if verbose:
        print(f"\n  reviewed={reviewed} cached={cached} errors={errors} in {elapsed:.1f}s")
    return {
        "expId": exp_id,
        "reviewed": reviewed,
        "cached": cached,
        "errors": errors,
        "elapsedSec": round(elapsed, 1),
        "summary": exp["llmReviewSummary"],
    }


def _summarize(clusters: list[dict]) -> dict:
    """Aggregate action histogram + total removable across reviewed clusters."""
    actions: dict[str, int] = {}
    removable = 0
    reviewed = 0
    for c in clusters:
        llm = c.get("llm") or {}
        if llm and not llm.get("error"):
            reviewed += 1
            a = llm.get("action") or "UNKNOWN"
            actions[a] = actions.get(a, 0) + 1
            removable += int(llm.get("removableCount") or 0)
    safe = actions.get("MERGE_IMMEDIATE", 0) + actions.get("PARAMETERIZE", 0)
    return {
        "reviewed": reviewed,
        "removable": removable,
        "safeToAuto": safe,
        "byAction": actions,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM-review a Playground experiment's clusters")
    ap.add_argument("--exp-id", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--model", default=LLM_MODEL)
    args = ap.parse_args()
    _load_env_local()
    result = run_experiment_llm_review({
        "id": args.exp_id, "project": args.project, "model": args.model,
    })
    print(json.dumps(result, indent=2))
    return 0 if result.get("reviewed") is not None else 1


if __name__ == "__main__":
    sys.exit(main())
