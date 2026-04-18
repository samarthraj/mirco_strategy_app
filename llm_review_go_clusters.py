#!/usr/bin/env python3
"""Run LLM review on Global Operational similarity clusters.

Reads the current clusters from public/data/Global Operational/clusters.json
and emits per-cluster consolidation recommendations to llm_reviews.json.

Uses GPT-5.4 via OpenAI API to classify each cluster:
  - MERGE_IMMEDIATE (provable duplicates, collapse now)
  - PARAMETERIZE (same query, different filter values)
  - REVIEW_WITH_OWNER (similar but need business confirmation)
  - KEEP_SEPARATE (legitimately different)

After running, re-run build_web_dashboard_data.py to merge into summary.
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    sys.exit("pip install openai")

MODEL = "gpt-5.4"
DATA_DIR = Path("public/data/Global Operational")
REPORTS_FILE = DATA_DIR / "reports.json"
CLUSTERS_FILE = DATA_DIR / "clusters.json"
OUT_FILE = DATA_DIR / "llm_reviews.json"
CACHE_FILE = Path("Global Operational") / "llm_reviews_cache.json"


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        sys.exit("Set OPENAI_API_KEY environment variable before running.")
    client = OpenAI(api_key=api_key)

    with open(CLUSTERS_FILE, "r", encoding="utf-8") as f:
        clusters = json.load(f)
    with open(REPORTS_FILE, "r", encoding="utf-8") as f:
        reports = json.load(f)
    by_id = {r["id"]: r for r in reports}

    # Resume cache
    results: dict[str, dict] = {}
    if CACHE_FILE.exists():
        results = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        print(f"Resuming with {len(results)} cached reviews")
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

    print(f"Reviewing {len(clusters)} clusters with {MODEL}")

    for i, c in enumerate(clusters, 1):
        cid = c["id"]
        if cid in results and "error" not in results[cid]:
            print(f"  [{i}/{len(clusters)}] {cid} — cached")
            continue

        # Build compact cluster description for the prompt
        member_lines = []
        for mid in c["memberIds"][:25]:  # cap at 25 to keep prompt small
            r = by_id.get(mid, {})
            name = r.get("name", "") if r else ""
            path = r.get("path", "") if r else ""
            metrics = r.get("metrics", []) or []
            tables = r.get("tables", []) or []
            filters = r.get("filters", []) or []
            member_lines.append(
                f"- {name[:70]} | metrics:{len(metrics)} tables:{len(tables)} filters:{len(filters)}"
                f"{f' | path: {path}' if path else ''}"
            )
        if len(c["memberIds"]) > 25:
            member_lines.append(f"... +{len(c['memberIds']) - 25} more members")

        prompt = f"""Analyze this cluster of {c['size']} MicroStrategy reports linked by fingerprint + SQL structural similarity (≥0.80 weighted Jaccard).

Cluster {cid} — primary: {c['primaryName']}
Common metrics: {', '.join(c.get('commonMetrics', [])[:8])}
Common tables: {', '.join(c.get('commonTables', [])[:8])}
Common filter attrs: {', '.join(c.get('commonFilters', [])[:8])}
Total executions across members: {c.get('totalExecutions', 0):,}

Members:
{chr(10).join(member_lines)}

Respond with ONLY valid JSON:
{{
    "label": "Short descriptive business label",
    "business_function": "What these reports do for the business",
    "relationship": "EXACT_DUPLICATE | NEAR_DUPLICATE | PARAMETERIZED_VARIANTS | SIMILAR_DOMAIN | LOOSELY_RELATED",
    "consolidation_action": "MERGE_IMMEDIATE | PARAMETERIZE | REVIEW_WITH_OWNER | KEEP_SEPARATE",
    "confidence": "HIGH | MEDIUM | LOW",
    "consolidation_detail": "Specific migration recommendation",
    "keep_report": "Name of primary report to keep",
    "removable_count": (int — how many reports in this cluster can safely be removed)
}}"""

        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=2000,
                temperature=0.1,
            )
            text = resp.choices[0].message.content.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            result = json.loads(text)
            result["clusterId"] = cid
            result["clusterSize"] = c["size"]
            result["primaryName"] = c["primaryName"]
            results[cid] = result
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
            action = result.get("consolidation_action", "?")
            rem = result.get("removable_count", 0)
            print(
                f"  [{i}/{len(clusters)}] {cid} size={c['size']:3} -> {action} remove={rem} ({result.get('label', '')[:40]})"
            )
            time.sleep(1)
        except Exception as e:
            print(f"  [{i}/{len(clusters)}] {cid} — ERROR: {e}")
            results[cid] = {"error": str(e)[:200], "clusterId": cid}

    # Write reviews to public/data for UI
    reviews_out = list(results.values())
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(reviews_out, f, indent=2)

    # Summarize
    actions: dict[str, int] = {}
    removable = 0
    for r in reviews_out:
        if "error" in r:
            continue
        a = r.get("consolidation_action", "?")
        actions[a] = actions.get(a, 0) + 1
        removable += r.get("removable_count", 0)
    print(f"\n=== Done ===")
    print(f"  Wrote {OUT_FILE}")
    for a, n in sorted(actions.items(), key=lambda x: -x[1]):
        print(f"  {a:22} {n}")
    print(f"  Total removable: {removable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
