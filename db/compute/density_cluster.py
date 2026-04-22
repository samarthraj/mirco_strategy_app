"""Density-based clustering — embed -> UMAP -> HDBSCAN.

Separate from the cosine AI Playground. Where the Playground uses a flat
cosine-threshold + union-find, this one:
  1. Embeds each report (reusing the cached OpenAI embeddings)
  2. UMAP-reduces to ~10 dims (preserves local structure, nonlinear)
  3. HDBSCAN on the reduced vectors (density-based, auto cluster count,
     explicit "noise" bucket for reports that don't belong anywhere)
  4. Scores the result with silhouette on the reduced space

At ~4k reports this is both tractable and high-quality: HDBSCAN explicitly
finds density peaks and labels edge cases as noise instead of forcing them
into a cluster.

Output is a Playground-format experiment JSON (mode="density") so the
existing "Make Primary" flow and Semantic Clusters view work unchanged.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.db import connect, init, get_project
from db.compute.semantic import (
    _load_env_local, _openai_client,
    EMBED_DIM as DEFAULT_EMBED_DIM,
)
from db.compute.playground import (
    PLAYGROUND_DIR, _resolve_project_id, _resolve_scope,
    _apply_source_filter, _build_text, _load_report_data,
    _embed_all, _content_hash,
)


# Defaults tuned for ~4k reports. For smaller populations (<1k) you'd drop
# n_neighbors and min_cluster_size; for larger (>10k) push them up.
DEFAULTS = {
    "umap_n_neighbors": 30,
    "umap_min_dist": 0.0,
    "umap_n_components": 10,
    "umap_metric": "cosine",
    "hdbscan_min_cluster_size": 15,
    "hdbscan_min_samples": 5,
    "hdbscan_cluster_selection_method": "eom",
    "hdbscan_metric": "euclidean",
}


def run_density_clustering(config: dict, verbose: bool = True) -> dict:
    import numpy as np
    import umap
    import hdbscan
    from sklearn.metrics import silhouette_score

    exp_id = config.get("id") or f"density-{int(time.time())}"
    name = config.get("name") or exp_id
    project_id = _resolve_project_id(config["project"])
    model = config.get("model", "text-embedding-3-large")
    embed_dim = int(config.get("dimensions") or DEFAULT_EMBED_DIM)
    scope = config.get("scope", "post-family")
    src_filter = config.get("sourceFilter", "all")
    fields = config.get("fields") or {
        "name": True, "path": True, "metrics": True,
        "tables": True, "attributes": True, "filters": True,
        "sql": True, "normalizeSql": True, "maxSqlChars": 1500,
    }

    umap_n_neighbors = int(config.get("umapNeighbors") or DEFAULTS["umap_n_neighbors"])
    umap_min_dist = float(config.get("umapMinDist") if config.get("umapMinDist") is not None else DEFAULTS["umap_min_dist"])
    umap_n_components = int(config.get("umapComponents") or DEFAULTS["umap_n_components"])
    min_cluster_size = int(config.get("minClusterSize") or DEFAULTS["hdbscan_min_cluster_size"])
    min_samples = int(config.get("minSamples") or DEFAULTS["hdbscan_min_samples"])
    selection = config.get("clusterSelection") or DEFAULTS["hdbscan_cluster_selection_method"]

    cfg_name = get_project(project_id)["name"]
    print(f"\n=== Density cluster {exp_id} ({name}) - project {cfg_name} ===")
    print(f"  scope={scope} src={src_filter} embed={model}")
    print(f"  UMAP: n_neighbors={umap_n_neighbors} min_dist={umap_min_dist} n_components={umap_n_components}")
    print(f"  HDBSCAN: min_cluster_size={min_cluster_size} min_samples={min_samples} selection={selection}")

    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        rids = _resolve_scope(conn, project_id, scope)
        rids = _apply_source_filter(conn, project_id, rids, src_filter)
        rids = sorted(rids)
        if config.get("limit"):
            rids = rids[: int(config["limit"])]
        if not rids:
            return {"error": "empty scope"}
        if verbose:
            print(f"  reports in scope: {len(rids):,}")

        recs = _load_report_data(conn, project_id, rids)
        texts: dict[str, str] = {}
        for rid in rids:
            r = recs.get(rid)
            if not r:
                continue
            t = _build_text(r, fields)
            if t:
                texts[rid] = t

        vectors = _embed_all(conn, project_id, texts, model, embed_dim, verbose)
        ids_in_order = sorted(vectors.keys())
        n = len(ids_in_order)
        if n < max(min_cluster_size * 2, 10):
            return {"error": f"too few reports ({n}) for HDBSCAN with min_cluster_size={min_cluster_size}"}

        X = np.asarray([vectors[r] for r in ids_in_order], dtype=np.float32)
        # Normalize for cosine — UMAP with metric="cosine" doesn't require it,
        # but normalized vectors also let the silhouette computation fall back
        # on euclidean in the reduced space cleanly.
        norms = np.linalg.norm(X, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        Xn = X / norms

        t0 = time.time()
        reducer = umap.UMAP(
            n_neighbors=umap_n_neighbors,
            min_dist=umap_min_dist,
            n_components=umap_n_components,
            metric="cosine",
            random_state=42,
        )
        X_red = reducer.fit_transform(Xn)
        if verbose:
            print(f"[umap] reduced {n:,} x {X.shape[1]}d -> {X_red.shape[1]}d in {time.time()-t0:.1f}s")

        # 2D projection for the UI. Cheap — run a second UMAP with 2 comps
        # using the same neighborhood.
        t1 = time.time()
        reducer_2d = umap.UMAP(
            n_neighbors=umap_n_neighbors,
            min_dist=umap_min_dist,
            n_components=2,
            metric="cosine",
            random_state=42,
        )
        X_2d = reducer_2d.fit_transform(Xn)
        if verbose:
            print(f"[umap-2d] in {time.time()-t1:.1f}s")

        # Normalize 2D to [0,1] for the canvas
        lo = X_2d.min(axis=0); hi = X_2d.max(axis=0)
        scale = (hi - lo); scale[scale == 0] = 1.0
        X_2d_n = (X_2d - lo) / scale

        t2 = time.time()
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            cluster_selection_method=selection,
            metric="euclidean",
            prediction_data=True,
        )
        labels = clusterer.fit_predict(X_red)
        if verbose:
            print(f"[hdbscan] in {time.time()-t2:.1f}s")

        unique_labels = sorted(set(int(l) for l in labels))
        n_noise = int((labels == -1).sum())
        n_clusters = sum(1 for l in unique_labels if l >= 0)
        sizes = {int(l): int((labels == l).sum()) for l in unique_labels}
        if verbose:
            print(f"[hdbscan] clusters={n_clusters} noise={n_noise} ({n_noise/n:.1%})")

        # Silhouette score on reduced space (excluding noise). Only meaningful
        # if we have >=2 clusters with non-trivial size.
        silhouette = None
        try:
            mask = labels != -1
            if mask.sum() > 10 and len(set(labels[mask])) >= 2:
                silhouette = float(silhouette_score(X_red[mask], labels[mask], metric="euclidean"))
        except Exception as e:
            if verbose:
                print(f"[silhouette] skipped: {e}")

        # Assemble clusters_out in Playground format
        label_to_members: dict[int, list[str]] = {}
        for rid, l in zip(ids_in_order, labels):
            label_to_members.setdefault(int(l), []).append(rid)
        # Real clusters, largest first
        real_labels = sorted([l for l in label_to_members if l >= 0],
                             key=lambda l: -len(label_to_members[l]))

        clusters_out = []
        cluster_by_rid: dict[str, str] = {}
        for idx, l in enumerate(real_labels, 1):
            members = label_to_members[l]
            cid = f"H{idx:03d}"
            for rid in members:
                cluster_by_rid[rid] = cid
            primary = max(members, key=lambda r: recs.get(r, {}).get("executions", 0))
            tot_exec = sum(recs.get(r, {}).get("executions", 0) for r in members)
            # Cluster persistence from HDBSCAN is roughly a "stability score"
            persistence = None
            try:
                if clusterer.cluster_persistence_ is not None:
                    persistence = float(clusterer.cluster_persistence_[l])
            except Exception:
                pass
            clusters_out.append({
                "id": cid,
                "size": len(members),
                "primaryReportId": primary,
                "primaryName": recs.get(primary, {}).get("name", ""),
                "totalExecutions": tot_exec,
                # Repurpose avg/min fields to hold cluster persistence so the
                # UI has something numeric per cluster.
                "avgCosine": round(persistence, 3) if persistence is not None else None,
                "minCosine": None,
                "memberIds": members,
                "hdbscanLabel": int(l),
                "persistence": persistence,
            })

        # Noise points become "singletons" in the UI's sense
        noise_ids = label_to_members.get(-1, [])

        # Scatter
        scatter = []
        for idx, rid in enumerate(ids_in_order):
            rec = recs.get(rid, {})
            scatter.append({
                "id": rid,
                "x": float(X_2d_n[idx][0]),
                "y": float(X_2d_n[idx][1]),
                "clusterId": cluster_by_rid.get(rid, None),
                "name": (rec.get("name") or "")[:80],
                "executions": rec.get("executions", 0),
            })

        avg_cluster_size = (
            sum(len(label_to_members[l]) for l in real_labels) / max(1, len(real_labels))
        )

        result = {
            "id": exp_id,
            "name": name,
            "config": {
                **config,
                "mode": "density",
                "project": project_id,
                "scope": scope,
                "sourceFilter": src_filter,
                "model": model,
                "dimensions": embed_dim,
                "threshold": 0.0,
                "umapNeighbors": umap_n_neighbors,
                "umapMinDist": umap_min_dist,
                "umapComponents": umap_n_components,
                "minClusterSize": min_cluster_size,
                "minSamples": min_samples,
                "clusterSelection": selection,
            },
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "stats": {
                "inputReports": len(rids),
                "reportsEmbedded": n,
                "edges": 0,
                "multiClusters": len(clusters_out),
                "singletons": len(noise_ids),
                "finalUnique": len(clusters_out) + len(noise_ids),
                "noisePct": round(n_noise / max(1, n), 4),
                "avgClusterSize": round(avg_cluster_size, 1),
                "silhouette": round(silhouette, 3) if silhouette is not None else None,
            },
            "clusters": clusters_out,
            "singletonIds": noise_ids[:500],
            "viz": {
                "scatter": scatter,
                "topPairs": [],
                "thresholdUsed": 0.0,
            },
            "mode": "density",
            "sizeDistribution": sizes,
        }

        PLAYGROUND_DIR.mkdir(parents=True, exist_ok=True)
        out_path = PLAYGROUND_DIR / f"{exp_id}.json"
        out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {out_path}")

        idx_path = PLAYGROUND_DIR / "index.json"
        existing: list[dict] = []
        if idx_path.exists():
            try:
                existing = json.loads(idx_path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing = [e for e in existing if e.get("id") != exp_id]
        existing.insert(0, {
            "id": exp_id, "name": name, "project": project_id,
            "createdAt": result["createdAt"],
            "stats": result["stats"],
            "config": {
                "scope": scope, "sourceFilter": src_filter,
                "model": model, "dim": embed_dim, "threshold": 0.0,
                "minClusterSize": min_cluster_size, "fields": fields,
                "mode": "density",
            },
        })
        idx_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        print(f"updated {idx_path} ({len(existing)} experiments)")
        return result
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Density clustering (UMAP + HDBSCAN)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--id", default=None)
    ap.add_argument("--scope", default="post-family")
    ap.add_argument("--source", dest="sourceFilter", default="all")
    ap.add_argument("--model", default="text-embedding-3-large")
    ap.add_argument("--dim", type=int, default=DEFAULT_EMBED_DIM)
    ap.add_argument("--umap-neighbors", type=int, default=DEFAULTS["umap_n_neighbors"])
    ap.add_argument("--umap-min-dist", type=float, default=DEFAULTS["umap_min_dist"])
    ap.add_argument("--umap-components", type=int, default=DEFAULTS["umap_n_components"])
    ap.add_argument("--min-cluster-size", type=int, default=DEFAULTS["hdbscan_min_cluster_size"])
    ap.add_argument("--min-samples", type=int, default=DEFAULTS["hdbscan_min_samples"])
    ap.add_argument("--selection", default=DEFAULTS["hdbscan_cluster_selection_method"])
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    _load_env_local()
    cfg = {
        "project": args.project,
        "name": args.name or f"density-{args.project}",
        "id": args.id,
        "scope": args.scope,
        "sourceFilter": args.sourceFilter,
        "model": args.model,
        "dimensions": args.dim,
        "umapNeighbors": args.umap_neighbors,
        "umapMinDist": args.umap_min_dist,
        "umapComponents": args.umap_components,
        "minClusterSize": args.min_cluster_size,
        "minSamples": args.min_samples,
        "clusterSelection": args.selection,
        "limit": args.limit,
    }
    run_density_clustering(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
