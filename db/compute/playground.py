"""AI Playground — run experimental semantic clustering with custom config.

Each experiment is isolated from the production semantic stage. Results
are written as self-contained JSON under `public/data/_playground/` so
the UI can load and compare them without touching the real pipeline.

Workflow:
  1. UI generates a config JSON file
  2. User runs:  python -m db.compute.playground <config_path>
  3. Script embeds + clusters per config, writes
     `public/data/_playground/<exp_id>.json` and updates
     `public/data/_playground/index.json`.
  4. UI loads the result.

Embeddings are cached in the same `semantic_embedding` table keyed by
content_hash — so re-running an experiment with text that overlaps a
prior one is free.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.db import connect, init, get_project
from db.compute.semantic import (
    normalize_sql_for_embed, pack_vector, unpack_vector,
    _load_env_local, _openai_client, EMBED_DIM as DEFAULT_EMBED_DIM,
)
from db.compute.pipeline import (
    _ast_canonical_ids, _post_ast_family_canonical_ids,
    _canonical_ids_after_collisions,
)


# Keep playground output OUTSIDE public/ so Vite's file watcher doesn't
# full-page-reload the UI (and drop the user's auth state) every time an
# experiment finishes. The UI reads these via the sidecar endpoint.
PLAYGROUND_DIR = Path(__file__).resolve().parents[2] / "data" / "_playground"


# ---------------- Scope resolution --------------------------------

def _resolve_scope(conn, project_id: str, scope: str) -> set[str]:
    if scope == "post-family":
        return _post_ast_family_canonical_ids(conn, project_id)
    if scope == "post-ast":
        return _ast_canonical_ids(conn, project_id)
    if scope == "active":
        return {r[0] for r in conn.execute(
            "SELECT report_id FROM report WHERE project_id=? AND status='active'",
            (project_id,),
        )}
    if scope == "final-kept":
        # primaries of sim clusters + post-family singletons
        primaries = {r[0] for r in conn.execute(
            "SELECT primary_report_id FROM similarity_cluster WHERE project_id=?",
            (project_id,),
        ) if r[0]}
        members = {r[0] for r in conn.execute(
            "SELECT report_id FROM similarity_cluster_member WHERE project_id=?",
            (project_id,),
        )}
        post_fam = _post_ast_family_canonical_ids(conn, project_id)
        return primaries | (post_fam - members)
    if scope == "post-collision":
        return _canonical_ids_after_collisions(conn, project_id)
    sys.exit(f"unknown scope {scope}")


def _apply_source_filter(conn, project_id: str, rids: set[str], src: str) -> set[str]:
    if src == "all":
        return rids
    if src == "exclude-cube":
        cubes = {r[0] for r in conn.execute(
            "SELECT DISTINCT report_id FROM raw_inventory "
            "WHERE project_id=? AND source_type='cube'", (project_id,),
        )}
        return rids - cubes
    wanted = {r[0] for r in conn.execute(
        "SELECT DISTINCT report_id FROM raw_inventory "
        "WHERE project_id=? AND source_type=?", (project_id, src),
    )}
    return rids & wanted


# ---------------- Text builder ------------------------------------

def _build_text(rec: dict, fields: dict) -> str:
    max_sql = int(fields.get("maxSqlChars") or 2000)
    parts = []
    if fields.get("name") and rec.get("name"):
        parts.append(f"NAME: {rec['name']}")
    if fields.get("path") and rec.get("path"):
        parts.append(f"PATH: {rec['path']}")
    if fields.get("owner") and rec.get("owner"):
        parts.append(f"OWNER: {rec['owner']}")
    if fields.get("attributes") and rec.get("attributes"):
        parts.append(f"ATTRIBUTES: {'; '.join(rec['attributes'])}")
    if fields.get("metrics") and rec.get("metrics"):
        parts.append(f"METRICS: {'; '.join(rec['metrics'])}")
    if fields.get("tables") and rec.get("tables"):
        parts.append(f"TABLES: {'; '.join(rec['tables'])}")
    if fields.get("filters") and rec.get("filters"):
        parts.append(f"FILTERS: {'; '.join(rec['filters'])}")
    if fields.get("sql") and rec.get("sql"):
        sql = normalize_sql_for_embed(rec["sql"], max_chars=max_sql) \
              if fields.get("normalizeSql", True) else rec["sql"][:max_sql]
        if sql:
            parts.append(f"SQL: {sql}")
    text = "\n".join(parts)
    if not text:
        text = f"ID: {rec.get('id')}"
    return text


def _content_hash(text: str, model: str, dim: int) -> str:
    # Include model + dim so cached embeddings don't collide across model choices
    h = hashlib.sha256(f"{model}|{dim}|{text}".encode("utf-8")).hexdigest()
    return h[:32]


def _load_report_data(conn, project_id: str, rids: list[str]) -> dict[str, dict]:
    placeholders = ",".join("?" * len(rids))
    out: dict[str, dict] = {}
    for r in conn.execute(
        f"""SELECT r.report_id, r.name, r.path, r.owner,
                   rs.sql_text, t.total_executions
              FROM report r
              LEFT JOIN report_sql rs USING (project_id, report_id)
              LEFT JOIN telemetry_match t USING (project_id, report_id)
             WHERE r.project_id = ? AND r.report_id IN ({placeholders})""",
        [project_id, *rids],
    ):
        out[r[0]] = {
            "id": r[0], "name": r[1] or "", "path": r[2] or "",
            "owner": r[3] or "", "sql": r[4] or "",
            "executions": r[5] or 0,
            "attributes": [], "metrics": [], "tables": [], "filters": [],
        }
    for tbl, col, key in [
        ("report_attribute", "attribute_name", "attributes"),
        ("report_metric", "metric_name", "metrics"),
        ("report_table", "table_name", "tables"),
        ("report_filter", "filter_name", "filters"),
    ]:
        for r in conn.execute(
            f"""SELECT report_id, {col} FROM {tbl}
                 WHERE project_id = ? AND report_id IN ({placeholders})
                 ORDER BY {col}""",
            [project_id, *rids],
        ):
            rec = out.get(r[0])
            if rec:
                rec[key].append(r[1])
    return out


# ---------------- Embed + Cluster ---------------------------------

def _embed_all(conn, project_id: str, texts: dict[str, str], model: str, dim: int,
               verbose: bool = True) -> dict[str, list[float]]:
    """Fetch cached or create embeddings. Returns {rid: vector}."""
    hashes = {rid: _content_hash(t, model, dim) for rid, t in texts.items()}
    # Check cache (semantic_embedding table), join on (model, content_hash)
    cached: dict[str, tuple[str, bytes, int]] = {}
    for rid, text in texts.items():
        row = conn.execute(
            """SELECT content_hash, vector, dim FROM semantic_embedding
                WHERE project_id=? AND report_id=? AND model=? AND content_hash=?""",
            (project_id, rid, model, hashes[rid]),
        ).fetchone()
        if row:
            cached[rid] = (row[0], row[1], row[2])
    to_embed = [rid for rid in texts if rid not in cached]
    if verbose:
        print(f"[embed] cache hits: {len(cached):,}   new: {len(to_embed):,}")
    vectors: dict[str, list[float]] = {
        rid: unpack_vector(blob, d) for rid, (_h, blob, d) in cached.items()
    }
    if to_embed:
        client = _openai_client()
        batch_size = 64
        done = 0
        for i in range(0, len(to_embed), batch_size):
            batch = to_embed[i:i + batch_size]
            inputs = [texts[r] for r in batch]
            kwargs = {"model": model, "input": inputs}
            if "text-embedding-3" in model:
                kwargs["dimensions"] = dim
            resp = client.embeddings.create(**kwargs)
            rows = []
            for rid, d in zip(batch, resp.data):
                vec = d.embedding
                vectors[rid] = vec
                rows.append((
                    project_id, rid, model, dim, hashes[rid], pack_vector(vec),
                ))
            with conn:
                conn.executemany(
                    """INSERT OR REPLACE INTO semantic_embedding
                       (project_id, report_id, model, dim, content_hash, vector)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    rows,
                )
            done += len(batch)
            if verbose:
                print(f"[embed] progress: {done}/{len(to_embed)}")
    return vectors


def _project_2d(ids: list[str], vectors: dict[str, list[float]], dim: int,
                 verbose: bool = True) -> list[list[float]]:
    """Compute a fast 2D projection via truncated SVD (PCA) so the UI can
    scatter-plot the embedding space. Returns [[x, y] per id in order]."""
    import numpy as np
    if not ids:
        return []
    M = np.asarray([vectors[r] for r in ids], dtype=np.float32)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Mn = M / norms
    mean = Mn.mean(axis=0)
    Xc = Mn - mean
    # Truncated SVD: take top 2 principal components
    try:
        U, S, _ = np.linalg.svd(Xc, full_matrices=False)
        coords = U[:, :2] * S[:2]
    except Exception:
        if verbose:
            print("[project_2d] SVD failed, falling back to random")
        rng = np.random.default_rng(42)
        coords = rng.standard_normal((len(ids), 2))
    # Normalize to a reasonable display range
    lo = coords.min(axis=0)
    hi = coords.max(axis=0)
    scale = (hi - lo)
    scale[scale == 0] = 1.0
    coords = (coords - lo) / scale  # now in [0, 1]
    if verbose:
        print(f"[project_2d] projected {len(ids):,} vectors to 2D")
    return coords.tolist()


def _cluster(ids: list[str], vectors: dict[str, list[float]], dim: int,
             threshold: float, min_size: int, verbose: bool = True) -> list[list[str]]:
    import numpy as np
    from collections import defaultdict
    n = len(ids)
    if verbose:
        print(f"[cluster] {n:,} reports -> {n*(n-1)//2:,} pairs (thr {threshold})")
    if n == 0:
        return []
    M = np.asarray([vectors[r] for r in ids], dtype=np.float32)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    M /= norms
    adj: dict[str, set[str]] = defaultdict(set)
    pair_rows = []
    block = max(256, min(2048, 50_000_000 // max(1, n)))
    t0 = time.time()
    for bstart in range(0, n, block):
        bend = min(n, bstart + block)
        sim = M[bstart:bend] @ M[bstart:].T
        idx_i, idx_j = np.where(sim >= threshold)
        for li, lj in zip(idx_i, idx_j):
            gi = bstart + int(li); gj = bstart + int(lj)
            if gj <= gi:
                continue
            s = float(sim[li, lj])
            adj[ids[gi]].add(ids[gj]); adj[ids[gj]].add(ids[gi])
            pair_rows.append((ids[gi], ids[gj], s))
    if verbose:
        print(f"[cluster] edges: {len(pair_rows):,} ({time.time()-t0:.1f}s)")

    visited: set[str] = set()
    components: list[list[str]] = []
    for start in ids:
        if start in visited:
            continue
        stack = [start]; comp = []
        while stack:
            x = stack.pop()
            if x in visited:
                continue
            visited.add(x); comp.append(x)
            stack.extend(adj[x] - visited)
        components.append(comp)
    multi = [c for c in components if len(c) >= min_size]
    multi.sort(key=lambda c: -len(c))
    return multi, pair_rows


# ---------------- Main --------------------------------------------

# Hardcoded GUIDs because db.db.PROJECTS doesn't have mstr_project_id
# populated for GI or INSIGHT (legacy inventory was ingested from JSON, not
# the MSTR server). These match what the live /projects endpoint returned.
_MSTR_GUIDS = {
    "global-operational": "E77B77894C04BF0E6D244F9363CFAF64",
    "global-insight":     "07E2CE9311EB6800B59F0080EF050FB2",
    "insight":            "6104D29041297D66C6BD16B602F2705F",
}


def _resolve_project_id(project_ref: str) -> str:
    """Accept either the short project_id ('global-operational'), the MSTR GUID
    ('E77B7789...'), or the display name ('Global Operational'). Return the
    short project_id used everywhere else in the pipeline."""
    from db.db import PROJECTS
    ref = (project_ref or "").strip()
    if not ref:
        sys.exit("project is required")
    # Exact short id match
    for p in PROJECTS:
        if p["project_id"] == ref:
            return ref
    # MSTR GUID match — DB first, then hardcoded backfill
    for p in PROJECTS:
        if p.get("mstr_project_id") == ref:
            return p["project_id"]
    for short, guid in _MSTR_GUIDS.items():
        if guid == ref:
            return short
    # Display-name match (case-insensitive)
    for p in PROJECTS:
        if p["name"].lower() == ref.lower():
            return p["project_id"]
    known = ", ".join(p["project_id"] for p in PROJECTS) + \
            " (or any matching MSTR GUID / display name)"
    sys.exit(f"Unknown project_id '{ref}'. Known: {known}")


def run_experiment(config: dict, verbose: bool = True) -> dict:
    # Phase 2: if the user picked a saved embedding set, resolve its
    # scope/sourceFilter/model/dim/fields into the config so this run
    # hits the cache for all vectors.
    from db.compute.embedding_set import resolve_config_from_set
    config = resolve_config_from_set(config)

    exp_id = config.get("id") or f"exp-{int(time.time())}"
    name = config.get("name") or exp_id
    project_id = _resolve_project_id(config["project"])
    model = config.get("model", "text-embedding-3-large")
    dim = int(config.get("dimensions") or (DEFAULT_EMBED_DIM if "large" in model else 1536))
    threshold = float(config.get("threshold", 0.85))
    min_size = int(config.get("minClusterSize", 2))
    scope = config.get("scope", "post-family")
    src_filter = config.get("sourceFilter", "all")
    fields = config.get("fields", {})

    cfg_name = get_project(project_id)["name"]
    print(f"\n=== Experiment {exp_id} ({name}) — project {cfg_name} ===")
    resolved_es = config.get("_resolvedFromEmbeddingSet")
    if resolved_es:
        print(f"  [using embedding set '{resolved_es}']")
    print(f"  scope={scope} src={src_filter} model={model} dim={dim} thr={threshold}")

    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        rids = _resolve_scope(conn, project_id, scope)
        rids = _apply_source_filter(conn, project_id, rids, src_filter)
        rids = sorted(rids)
        if not rids:
            print("  no reports in scope; aborting")
            return {"error": "empty scope"}
        if verbose:
            print(f"  reports in scope: {len(rids):,}")

        # Limit: allow cap for cheap testing
        if config.get("limit"):
            rids = rids[: int(config["limit"])]
            print(f"  limited to {len(rids):,}")

        recs = _load_report_data(conn, project_id, rids)
        texts: dict[str, str] = {}
        for rid in rids:
            r = recs.get(rid)
            if not r:
                continue
            t = _build_text(r, fields)
            if t:
                texts[rid] = t

        vectors = _embed_all(conn, project_id, texts, model, dim, verbose)
        ids_in_order = sorted(vectors.keys())
        multi, pairs = _cluster(ids_in_order, vectors, dim, threshold, min_size, verbose)

        # Build a 2D projection for the Graph View in the UI. PCA is deterministic,
        # <1s for ~6k vectors. Captures the "gist" of similarity in 2D.
        coords_2d = _project_2d(ids_in_order, vectors, dim, verbose)

        members_in_multi = {r for comp in multi for r in comp}
        singletons = [r for r in ids_in_order if r not in members_in_multi]

        # Build result
        clusters_out = []
        for i, comp in enumerate(multi, 1):
            primary = max(comp, key=lambda r: recs.get(r, {}).get("executions", 0))
            tot_exec = sum(recs.get(r, {}).get("executions", 0) for r in comp)
            pair_lookup = {}
            for a, b, s in pairs:
                key = (min(a, b), max(a, b))
                pair_lookup[key] = s
            in_cluster_pairs = []
            for x in range(len(comp)):
                for y in range(x + 1, len(comp)):
                    k = (min(comp[x], comp[y]), max(comp[x], comp[y]))
                    if k in pair_lookup:
                        in_cluster_pairs.append(pair_lookup[k])
            avg_cos = round(sum(in_cluster_pairs) / len(in_cluster_pairs), 4) if in_cluster_pairs else None
            min_cos = round(min(in_cluster_pairs), 4) if in_cluster_pairs else None
            clusters_out.append({
                "id": f"XC{i:04d}",
                "size": len(comp),
                "primaryReportId": primary,
                "primaryName": recs.get(primary, {}).get("name", ""),
                "totalExecutions": tot_exec,
                "avgCosine": avg_cos,
                "minCosine": min_cos,
                "memberIds": comp,
            })

        # Build the viz payload. `scatter` is a flat array aligned with
        # ids_in_order so the UI can map (reportId -> x,y,clusterId).
        # `topPairs` holds the strongest cosine edges (capped) so the UI can
        # re-cluster locally at a different threshold without round-tripping.
        cluster_by_rid: dict[str, str] = {}
        for i, comp in enumerate(multi, 1):
            cid = f"XC{i:04d}"
            for rid in comp:
                cluster_by_rid[rid] = cid
        scatter = []
        for idx, rid in enumerate(ids_in_order):
            rec = recs.get(rid, {})
            scatter.append({
                "id": rid,
                "x": coords_2d[idx][0] if idx < len(coords_2d) else 0.0,
                "y": coords_2d[idx][1] if idx < len(coords_2d) else 0.0,
                "clusterId": cluster_by_rid.get(rid, None),
                "name": rec.get("name", "")[:80],
                "executions": rec.get("executions", 0),
            })
        # Pair selection for the UI graph view. Naive "top-K by cosine" is
        # wrong: the strongest pairs are mostly redundant within already-merged
        # clusters, while the marginal pairs (near the run threshold) are the
        # ones that actually perform merges. The graph view then re-runs
        # union-find on this subset, and misses most of the true merges.
        #
        # Instead, build a maximum spanning forest (MSF) via Kruskal's on
        # edges sorted by cosine descending. One tree per connected component,
        # n-1 edges per component. Property: at any threshold t >= run_threshold,
        # union-find on the MSF produces the same cluster assignments as
        # union-find on the full edge set. Then fill the remaining budget with
        # the strongest non-MSF edges for edge-density visualization.
        PAIR_CAP = 50000
        sorted_pairs = sorted(pairs, key=lambda p: -p[2])
        _p: dict[str, str] = {}
        def _find(x: str) -> str:
            while _p.setdefault(x, x) != x:
                _p[x] = _p[_p[x]]
                x = _p[x]
            return x
        msf: list[tuple[str, str, float]] = []
        extras: list[tuple[str, str, float]] = []
        for a, b, s in sorted_pairs:
            ra, rb = _find(a), _find(b)
            if ra != rb:
                _p[ra] = rb
                msf.append((a, b, s))
            else:
                extras.append((a, b, s))
        top_pairs = msf[:PAIR_CAP]
        if len(top_pairs) < PAIR_CAP:
            top_pairs += extras[:PAIR_CAP - len(top_pairs)]
        if verbose:
            print(f"[viz] MSF edges: {len(msf):,}   extras kept: {max(0, len(top_pairs) - len(msf)):,}   total pairs: {len(pairs):,}")
        top_pairs_ser = [[a, b, s] for (a, b, s) in top_pairs]

        result = {
            "id": exp_id,
            "name": name,
            "config": config,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "stats": {
                "inputReports": len(rids),
                "reportsEmbedded": len(vectors),
                "edges": len(pairs),
                "multiClusters": len(multi),
                "singletons": len(singletons),
                "finalUnique": len(multi) + len(singletons),
            },
            "clusters": clusters_out,
            "singletonIds": singletons[:500],  # cap for file size
            "viz": {
                "scatter": scatter,
                "topPairs": top_pairs_ser,
                "thresholdUsed": threshold,
            },
        }

        PLAYGROUND_DIR.mkdir(parents=True, exist_ok=True)
        out_path = PLAYGROUND_DIR / f"{exp_id}.json"
        out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {out_path}")

        # Update index
        idx_path = PLAYGROUND_DIR / "index.json"
        idx: list[dict] = []
        if idx_path.exists():
            try:
                idx = json.loads(idx_path.read_text(encoding="utf-8"))
            except Exception:
                idx = []
        idx = [e for e in idx if e.get("id") != exp_id]
        idx.insert(0, {
            "id": exp_id, "name": name, "project": project_id,
            "createdAt": result["createdAt"],
            "stats": result["stats"],
            "config": {
                "scope": scope, "sourceFilter": src_filter,
                "model": model, "dim": dim, "threshold": threshold,
                "minClusterSize": min_size, "fields": fields,
            },
        })
        idx_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")
        print(f"updated {idx_path} ({len(idx)} experiments)")
        return result
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Run an AI Playground experiment")
    ap.add_argument("config_path", nargs="?",
                    help="Path to experiment config JSON. If omitted, reads from stdin.")
    ap.add_argument("--stdin", action="store_true",
                    help="Read config JSON from stdin even if path was given")
    args = ap.parse_args()

    _load_env_local()

    if args.stdin or not args.config_path or args.config_path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(args.config_path).read_text(encoding="utf-8")
    cfg = json.loads(raw)
    run_experiment(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
