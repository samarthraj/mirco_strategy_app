#!/usr/bin/env python3
"""
Step 4: LLM review of top semantic clusters.

For each cluster, sends representative SQL samples to the LLM and asks it to classify:
  - MERGE_IMMEDIATE: identical/near-identical, remove duplicates
  - PARAMETERIZE: same structure, different filters — consolidate into one prompted report
  - REVIEW_WITH_OWNER: similar but different enough to need business confirmation
  - KEEP_SEPARATE: share tables but serve different purposes

Uses OpenAI GPT-4o for analysis.
"""

import json
import os
import time
from pathlib import Path
from openai import OpenAI


OUTPUT_DIR = Path("INSIGHT/rationalization")
SQL_DIR = OUTPUT_DIR / "sql"
RAW_DIR = SQL_DIR / "raw"
NORM_DIR = SQL_DIR / "normalized"

SYSTEM_PROMPT = """You are a MicroStrategy report rationalization expert. You analyze SQL queries from reports to determine if they can be consolidated.

Given a cluster of SQL queries that are semantically similar, classify the cluster and provide a recommendation.

Respond ONLY with valid JSON in this exact format:
{
  "action": "MERGE_IMMEDIATE|PARAMETERIZE|REVIEW_WITH_OWNER|KEEP_SEPARATE",
  "confidence": "HIGH|MEDIUM|LOW",
  "relationship": "exact_duplicate|near_duplicate|parameterized_variants|similar_domain|loosely_related",
  "business_function": "Brief description of what these reports do",
  "label": "Short 3-5 word label for this cluster",
  "consolidation_detail": "Specific recommendation for what to do",
  "keep_report": "Name of the report to keep as primary (or 'any' if identical)",
  "removable_count": <number of reports that can be removed>
}

Action definitions:
- MERGE_IMMEDIATE: SQL is identical or differs only in cosmetic ways. Remove duplicates, keep one.
- PARAMETERIZE: Same query structure but different WHERE filters (brand, season, region, date). Consolidate into one report with prompts.
- REVIEW_WITH_OWNER: Reports appear similar but may serve different purposes. Need business confirmation.
- KEEP_SEPARATE: Reports share some tables but serve fundamentally different business functions."""


def build_cluster_prompt(cluster, max_sql_samples=5):
    """Build a prompt showing SQL samples from a cluster."""
    members = cluster["members"]

    lines = [
        f"Cluster with {cluster['uniqueSqlCount']} unique SQL statements, "
        f"{cluster['totalReportCount']} total reports, "
        f"average similarity: {cluster['avgSimilarity']:.4f}",
        ""
    ]

    # Show SQL samples (up to max_sql_samples)
    samples = members[:max_sql_samples]
    for i, member in enumerate(samples, 1):
        name = member["representative"]["name"]
        filename = member["representative"]["filename"]
        report_count = member["reportCount"]

        # Load raw SQL (more readable)
        raw_path = RAW_DIR / filename
        if raw_path.exists():
            sql = raw_path.read_text(encoding="utf-8")
        else:
            norm_path = NORM_DIR / filename
            sql = norm_path.read_text(encoding="utf-8") if norm_path.exists() else "(SQL not available)"

        # Truncate long SQL
        if len(sql) > 1500:
            sql = sql[:1500] + "\n... (truncated)"

        lines.append(f"--- Report {i}: \"{name}\" ({report_count} copies) ---")
        lines.append(sql)
        lines.append("")

    if len(members) > max_sql_samples:
        remaining_names = [m["representative"]["name"] for m in members[max_sql_samples:]]
        lines.append(f"--- Additional reports not shown ({len(remaining_names)}): ---")
        lines.append(", ".join(remaining_names[:10]))
        if len(remaining_names) > 10:
            lines.append(f"... and {len(remaining_names) - 10} more")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="LLM review of semantic clusters")
    parser.add_argument("--output-dir", default="INSIGHT/rationalization")
    parser.add_argument("--top-n", type=int, default=50,
                        help="Number of top clusters to review (by similarity)")
    parser.add_argument("--min-similarity", type=float, default=0.90,
                        help="Minimum average similarity to review")
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--resume", action="store_true",
                        help="Skip clusters already reviewed")
    parser.add_argument("--save-interval", type=int, default=5)
    args = parser.parse_args()

    os.environ.setdefault("OPENAI_API_KEY",
        os.environ["OPENAI_API_KEY"])

    output_dir = Path(args.output_dir)
    clusters_path = output_dir / "sql" / "semantic_clusters.json"
    review_path = output_dir / "sql" / "cluster_reviews.json"

    if not clusters_path.exists():
        print(f"ERROR: {clusters_path} not found.")
        return 1

    clusters_data = json.loads(clusters_path.read_text(encoding="utf-8"))
    all_clusters = clusters_data["clusters"]

    # Filter and sort by similarity
    candidates = [c for c in all_clusters if c["avgSimilarity"] >= args.min_similarity]
    candidates.sort(key=lambda c: -c["avgSimilarity"])
    candidates = candidates[:args.top_n]

    print(f"Clusters to review: {len(candidates)}")
    print(f"  Similarity range: {candidates[-1]['avgSimilarity']:.4f} - {candidates[0]['avgSimilarity']:.4f}")
    total_reports = sum(c["totalReportCount"] for c in candidates)
    print(f"  Total reports covered: {total_reports}")

    # Load existing reviews if resuming
    existing_reviews = {}
    if args.resume and review_path.exists():
        existing = json.loads(review_path.read_text(encoding="utf-8"))
        for r in existing.get("reviews", []):
            existing_reviews[r["clusterId"]] = r
        print(f"  Resuming: {len(existing_reviews)} already reviewed")

    client = OpenAI()
    reviews = list(existing_reviews.values())
    reviewed = 0
    errors = 0

    t0 = time.time()

    for i, cluster in enumerate(candidates, 1):
        cid = cluster["clusterId"]
        if cid in existing_reviews:
            continue

        prompt = build_cluster_prompt(cluster)

        try:
            response = client.chat.completions.create(
                model=args.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=500,
            )

            reply = response.choices[0].message.content.strip()
            # Parse JSON from reply (handle markdown code blocks)
            if reply.startswith("```"):
                reply = reply.split("```")[1]
                if reply.startswith("json"):
                    reply = reply[4:]

            analysis = json.loads(reply)

            review = {
                "clusterId": cid,
                "uniqueSqlCount": cluster["uniqueSqlCount"],
                "totalReportCount": cluster["totalReportCount"],
                "avgSimilarity": cluster["avgSimilarity"],
                "sampleReports": [m["representative"]["name"] for m in cluster["members"][:5]],
                "analysis": analysis,
            }
            reviews.append(review)
            reviewed += 1

            action = analysis.get("action", "?")
            label = analysis.get("label", "?")
            print(f"  [{i}/{len(candidates)}] Cluster {cid} ({cluster['uniqueSqlCount']} SQL, "
                  f"{cluster['totalReportCount']} reports): {action} - {label}", flush=True)

        except Exception as e:
            errors += 1
            print(f"  [{i}/{len(candidates)}] Cluster {cid}: ERROR - {e}", flush=True)
            reviews.append({
                "clusterId": cid,
                "uniqueSqlCount": cluster["uniqueSqlCount"],
                "totalReportCount": cluster["totalReportCount"],
                "avgSimilarity": cluster["avgSimilarity"],
                "sampleReports": [m["representative"]["name"] for m in cluster["members"][:5]],
                "analysis": {"error": str(e)},
            })

        # Save periodically
        if (reviewed + errors) % args.save_interval == 0:
            _save_reviews(review_path, reviews, clusters_data["summary"])

        # Rate limit
        time.sleep(0.5)

    # Final save
    _save_reviews(review_path, reviews, clusters_data["summary"])

    # Summary
    actions = {}
    total_removable = 0
    for r in reviews:
        a = r.get("analysis", {})
        action = a.get("action", "error")
        actions[action] = actions.get(action, 0) + 1
        total_removable += a.get("removable_count", 0)

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  LLM CLUSTER REVIEW COMPLETE [{elapsed:.1f}s]")
    print(f"{'='*60}")
    print(f"  Clusters reviewed:   {reviewed}")
    print(f"  Errors:              {errors}")
    print(f"  Actions:")
    for action, count in sorted(actions.items()):
        print(f"    {action}: {count}")
    print(f"  Total removable reports: {total_removable}")
    print(f"{'='*60}")
    print(f"\nOutput: {review_path}")
    return 0


def _save_reviews(path, reviews, clustering_summary):
    output = {
        "clusteringSummary": clustering_summary,
        "reviewCount": len(reviews),
        "reviews": reviews,
    }
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
