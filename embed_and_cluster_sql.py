#!/usr/bin/env python3
"""
Step 2 & 3 of Semantic Analysis: Embed normalized SQL and cluster near-duplicates.

1. Loads unique normalized SQL (one per hash from sql_index.json)
2. Embeds with all-MiniLM-L6-v2 (local, best separation score)
3. Computes cosine similarity matrix
4. Agglomerative clustering at configurable threshold
5. Saves clusters, embeddings, and similarity data

Output:
  sql/embeddings.npy          — embedding vectors (n_unique x 384)
  sql/embedding_index.json    — maps embedding row to report info
  sql/similarity_matrix.npy   — cosine similarity matrix
  sql/semantic_clusters.json  — cluster assignments and members
"""

import json
import hashlib
import numpy as np
import time
from pathlib import Path
from collections import defaultdict


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Embed and cluster normalized SQL")
    parser.add_argument("--output-dir", default="INSIGHT/rationalization")
    parser.add_argument("--threshold", type=float, default=0.25,
                        help="Distance threshold for clustering (1 - similarity). "
                             "0.25 = 75%% similarity minimum. Lower = stricter.")
    parser.add_argument("--model", default="all-MiniLM-L6-v2")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--skip-embed", action="store_true",
                        help="Skip embedding, load existing embeddings.npy")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    sql_dir = output_dir / "sql"
    norm_dir = sql_dir / "normalized"
    index_path = sql_dir / "sql_index.json"

    if not index_path.exists():
        print(f"ERROR: {index_path} not found. Run normalize_sql_insight.py first.")
        return 1

    t0 = time.time()

    # ── Step 1: Load unique SQL (one per hash) ──
    print("Loading SQL index...")
    index = json.loads(index_path.read_text(encoding="utf-8"))

    hash_groups = defaultdict(list)
    for entry in index["files"]:
        hash_groups[entry["normalizedHash"]].append(entry)

    # Pick one representative per hash
    unique_entries = []
    hash_to_row = {}
    for h, members in hash_groups.items():
        rep = members[0]  # pick first as representative
        hash_to_row[h] = len(unique_entries)
        unique_entries.append({
            "row": len(unique_entries),
            "normalizedHash": h,
            "representative": {
                "id": rep["id"],
                "name": rep["name"],
                "filename": rep["filename"],
            },
            "allMembers": [{"id": m["id"], "name": m["name"], "filename": m["filename"]}
                           for m in members],
            "memberCount": len(members),
        })

    print(f"  {len(index['files'])} total SQL files")
    print(f"  {len(unique_entries)} unique hashes to embed")

    # Save embedding index
    embed_index_path = sql_dir / "embedding_index.json"
    embed_index_path.write_text(json.dumps(unique_entries, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Step 2: Embed ──
    embeddings_path = sql_dir / "embeddings.npy"

    if args.skip_embed and embeddings_path.exists():
        print(f"\nLoading existing embeddings from {embeddings_path}...")
        embeddings = np.load(str(embeddings_path))
        print(f"  Shape: {embeddings.shape}")
    else:
        print(f"\nLoading model: {args.model}...")
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(args.model)

        # Load SQL texts
        print("Loading normalized SQL texts...")
        texts = []
        for entry in unique_entries:
            filepath = norm_dir / entry["representative"]["filename"]
            sql = filepath.read_text(encoding="utf-8")
            # Truncate to model's max sequence length (~256 tokens for MiniLM)
            texts.append(sql[:2000])

        print(f"Embedding {len(texts)} SQL statements...")
        embeddings = model.encode(texts, batch_size=args.batch_size, show_progress_bar=True,
                                   normalize_embeddings=True)
        embeddings = np.array(embeddings, dtype=np.float32)
        print(f"  Embedding shape: {embeddings.shape}")

        # Save embeddings
        np.save(str(embeddings_path), embeddings)
        print(f"  Saved to {embeddings_path}")

    # ── Step 3: Cosine similarity matrix ──
    print("\nComputing cosine similarity matrix...")
    from sklearn.metrics.pairwise import cosine_similarity
    sim_matrix = cosine_similarity(embeddings)
    print(f"  Shape: {sim_matrix.shape}")

    sim_path = sql_dir / "similarity_matrix.npy"
    np.save(str(sim_path), sim_matrix)
    print(f"  Saved to {sim_path}")

    # Stats
    upper_tri = sim_matrix[np.triu_indices_from(sim_matrix, k=1)]
    print(f"  Mean similarity: {upper_tri.mean():.4f}")
    print(f"  Median similarity: {np.median(upper_tri):.4f}")
    print(f"  >0.90 pairs: {(upper_tri > 0.90).sum():,}")
    print(f"  >0.80 pairs: {(upper_tri > 0.80).sum():,}")
    print(f"  >0.75 pairs: {(upper_tri > 0.75).sum():,}")

    # ── Step 4: Agglomerative clustering ──
    print(f"\nClustering with distance threshold={args.threshold} (similarity>={1-args.threshold:.2f})...")
    from sklearn.cluster import AgglomerativeClustering

    distance_matrix = 1 - sim_matrix
    np.fill_diagonal(distance_matrix, 0)
    # Clip small negatives from floating point
    distance_matrix = np.clip(distance_matrix, 0, 2)

    clustering = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=args.threshold,
        metric="precomputed",
        linkage="average",
    )
    labels = clustering.fit_predict(distance_matrix)

    n_clusters = len(set(labels))
    print(f"  Total clusters: {n_clusters}")

    # Group by cluster
    cluster_groups = defaultdict(list)
    for i, label in enumerate(labels):
        cluster_groups[int(label)].append(i)

    multi_member = {k: v for k, v in cluster_groups.items() if len(v) >= 2}
    singletons = {k: v for k, v in cluster_groups.items() if len(v) == 1}

    # Count total reports in multi-member clusters (including hash duplicates)
    reports_in_clusters = 0
    for cid, rows in multi_member.items():
        for row_idx in rows:
            reports_in_clusters += unique_entries[row_idx]["memberCount"]

    singleton_reports = 0
    for cid, rows in singletons.items():
        singleton_reports += unique_entries[rows[0]]["memberCount"]

    print(f"  Multi-member clusters: {len(multi_member)}")
    print(f"  Singletons: {len(singletons)}")
    print(f"  Reports in multi-member clusters: {reports_in_clusters:,}")
    print(f"  Singleton reports: {singleton_reports:,}")

    # ── Build output ──
    clusters_output = []
    for cid, row_indices in sorted(multi_member.items(), key=lambda x: -len(x[1])):
        # Compute intra-cluster similarity
        if len(row_indices) > 1:
            sub_sim = sim_matrix[np.ix_(row_indices, row_indices)]
            upper = sub_sim[np.triu_indices_from(sub_sim, k=1)]
            avg_sim = float(upper.mean()) if len(upper) > 0 else 1.0
            min_sim = float(upper.min()) if len(upper) > 0 else 1.0
        else:
            avg_sim = 1.0
            min_sim = 1.0

        members = []
        for row_idx in row_indices:
            entry = unique_entries[row_idx]
            members.append({
                "embeddingRow": row_idx,
                "normalizedHash": entry["normalizedHash"],
                "representative": entry["representative"],
                "allReports": entry["allMembers"],
                "reportCount": entry["memberCount"],
            })

        total_reports = sum(m["reportCount"] for m in members)
        clusters_output.append({
            "clusterId": int(cid),
            "uniqueSqlCount": len(row_indices),
            "totalReportCount": total_reports,
            "avgSimilarity": round(avg_sim, 4),
            "minSimilarity": round(min_sim, 4),
            "members": members,
        })

    # Summary
    summary = {
        "model": args.model,
        "distanceThreshold": args.threshold,
        "similarityMinimum": 1 - args.threshold,
        "totalUniqueSQL": len(unique_entries),
        "totalReports": len(index["files"]),
        "totalClusters": n_clusters,
        "multiMemberClusters": len(multi_member),
        "singletonClusters": len(singletons),
        "reportsInMultiMemberClusters": reports_in_clusters,
        "singletonReports": singleton_reports,
        "highSimilarityPairs_gt90": int((upper_tri > 0.90).sum()),
        "highSimilarityPairs_gt80": int((upper_tri > 0.80).sum()),
        "highSimilarityPairs_gt75": int((upper_tri > 0.75).sum()),
    }

    result = {
        "summary": summary,
        "clusters": clusters_output,
    }

    clusters_path = sql_dir / "semantic_clusters.json"
    clusters_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  EMBEDDING & CLUSTERING COMPLETE [{elapsed:.1f}s]")
    print(f"{'='*60}")
    print(f"  Model:                  {args.model}")
    print(f"  Distance threshold:     {args.threshold} (similarity >= {1-args.threshold:.2f})")
    print(f"  Unique SQL embedded:    {len(unique_entries):,}")
    print(f"  Multi-member clusters:  {len(multi_member):,}")
    print(f"  Reports in clusters:    {reports_in_clusters:,}")
    print(f"  Singleton reports:      {singleton_reports:,}")
    print(f"{'='*60}")
    print(f"\nOutputs:")
    print(f"  {embeddings_path}")
    print(f"  {embed_index_path}")
    print(f"  {sim_path}")
    print(f"  {clusters_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
