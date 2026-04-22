"""Semantic (embedding-based) clustering of post-AST canonicals.

Pipeline:
  1. Build a normalized text representation per post-AST survivor report
     (name, path, metrics, tables, filters, normalized SQL excerpt).
  2. Embed each text via OpenAI text-embedding-3-large (3072-dim).
     Cached in `semantic_embedding` keyed by content_hash so re-runs only
     re-embed reports whose content has changed.
  3. Compute cosine similarity between every pair. Edges with cosine >=
     threshold (default 0.85) form a graph; connected components are
     semantic clusters.
  4. LLM-review each multi-member cluster via GPT — same prompt shape as
     llm_review_go_clusters.py.
  5. Store clusters + members + LLM results in semantic_* tables.

Run:
    python -m db.compute.semantic --project global-operational
    python -m db.compute.semantic --project all
    python -m db.compute.semantic --project insight --skip-llm
    python -m db.compute.semantic --project global-operational --limit 50  # dry run
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.db import connect, init, get_project, PROJECTS
from db.compute.pipeline import _ast_canonical_ids, _post_ast_family_canonical_ids


# =========================================================================
# Config
# =========================================================================

EMBED_MODEL = "text-embedding-3-large"
EMBED_DIM = 3072
LLM_MODEL = "gpt-5.4"
DEFAULT_THRESHOLD = 0.85
MAX_SQL_CHARS = 2000
MAX_TOTAL_CHARS = 6000  # safety cap on the embedded text


# =========================================================================
# SQL normalizer (same logic as scratch_normalize_sanity.py, validated)
# =========================================================================

_LITERAL_STR = re.compile(r"'(?:''|[^'])*'")
_LITERAL_NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_TMP_NAME = re.compile(
    r"\b(?:"
    r"zz[a-z]{2}\d+(?:_\w+)?"
    r"|dss_int\w*"
    r"|tmp_\w+|temp_\w+"
    r"|#?[a-z]*_temp_[a-z0-9_]+"
    r")\b",
    re.IGNORECASE,
)
_HASH_TOKEN = re.compile(r"\b[a-f0-9]{16,}\b", re.IGNORECASE)
_WS = re.compile(r"\s+")


def normalize_sql_for_embed(sql: str, max_chars: int = MAX_SQL_CHARS) -> str:
    if not sql:
        return ""
    s = sql
    s = _BLOCK_COMMENT.sub(" ", s)
    s = _LITERAL_STR.sub("?", s)          # must precede line-comment strip
    s = _LINE_COMMENT.sub(" ", s)
    s = s.lower()
    s = _TMP_NAME.sub("tmp_tbl", s)
    s = _HASH_TOKEN.sub("hashtok", s)
    s = _LITERAL_NUM.sub("?", s)
    s = _WS.sub(" ", s).strip()
    if len(s) > max_chars:
        s = s[:max_chars]
    return s


# =========================================================================
# Per-report text builder
# =========================================================================

def _load_report_features(conn, project_id: str, rids: list[str]) -> dict[str, dict]:
    """For each report id, assemble the fields we'll embed."""
    placeholders = ",".join("?" * len(rids))
    out: dict[str, dict] = {}
    # Base report
    for r in conn.execute(
        f"""SELECT r.report_id, r.name, r.path, r.object_type, r.subtype, r.owner,
                   rs.sql_text
              FROM report r
              LEFT JOIN report_sql rs
                ON rs.project_id = r.project_id AND rs.report_id = r.report_id
             WHERE r.project_id = ? AND r.report_id IN ({placeholders})""",
        [project_id, *rids],
    ):
        out[r[0]] = {
            "name": r[1] or "",
            "path": r[2] or "",
            "object_type": str(r[3] or ""),
            "subtype": str(r[4] or ""),
            "owner": r[5] or "",
            "sql": r[6] or "",
            "metrics": [],
            "tables": [],
            "filters": [],
        }
    # Lists
    for tbl, col, key in [
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


def build_report_text(rec: dict) -> str:
    """Compose the string we hand to the embedding model."""
    parts = [
        f"NAME: {rec['name']}",
        f"PATH: {rec['path']}",
        f"TYPE: {rec['object_type']} {rec['subtype']}".strip(),
        f"OWNER: {rec['owner']}",
        f"METRICS: {'; '.join(rec['metrics'])}",
        f"TABLES: {'; '.join(rec['tables'])}",
        f"FILTERS: {'; '.join(rec['filters'])}",
    ]
    sql_norm = normalize_sql_for_embed(rec["sql"])
    if sql_norm:
        parts.append(f"SQL: {sql_norm}")
    text = "\n".join(parts)
    if len(text) > MAX_TOTAL_CHARS:
        # Drop SQL first, then filters, then tables until under cap
        parts_no_sql = parts[:-1] if sql_norm else parts
        text = "\n".join(parts_no_sql)
        if len(text) > MAX_TOTAL_CHARS:
            text = text[:MAX_TOTAL_CHARS]
    return text


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


# =========================================================================
# Vector packing
# =========================================================================

def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"{dim}f", blob))


# =========================================================================
# Embed
# =========================================================================

def _load_env_local() -> None:
    """Lightweight .env.local loader (no python-dotenv dep)."""
    env = Path(__file__).resolve().parents[2] / ".env.local"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def _openai_client():
    _load_env_local()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        sys.exit("OPENAI_API_KEY missing (set in env or .env.local).")
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("pip install openai")
    return OpenAI(api_key=key)


def embed_reports(conn, project_id: str, *, verbose: bool = True, limit: int | None = None) -> dict:
    """Generate or reuse cached embeddings for each post-AST survivor.
    Returns {report_id: vector}.
    """
    survivors = sorted(_post_ast_family_canonical_ids(conn, project_id))
    if limit:
        survivors = survivors[:limit]
    if verbose:
        print(f"[embed] post-AST survivors: {len(survivors):,}")

    features = _load_report_features(conn, project_id, survivors)

    # Build text + hash per report; decide who needs re-embedding.
    texts: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for rid in survivors:
        rec = features.get(rid)
        if not rec:
            continue
        t = build_report_text(rec)
        texts[rid] = t
        hashes[rid] = content_hash(t)

    # Check cache
    cached_rows = conn.execute(
        f"""SELECT report_id, content_hash, vector FROM semantic_embedding
             WHERE project_id = ? AND model = ?""",
        (project_id, EMBED_MODEL),
    ).fetchall()
    cached: dict[str, tuple[str, bytes]] = {r[0]: (r[1], r[2]) for r in cached_rows}

    reuse: dict[str, list[float]] = {}
    to_embed: list[str] = []
    for rid in survivors:
        if rid not in texts:
            continue
        c = cached.get(rid)
        if c and c[0] == hashes[rid]:
            reuse[rid] = unpack_vector(c[1], EMBED_DIM)
        else:
            to_embed.append(rid)
    if verbose:
        print(f"[embed] cache hits: {len(reuse):,}   new/changed: {len(to_embed):,}")

    # Batch-embed new/changed
    if to_embed:
        client = _openai_client()
        batch_size = 64
        done = 0
        for i in range(0, len(to_embed), batch_size):
            batch = to_embed[i : i + batch_size]
            inputs = [texts[rid] for rid in batch]
            try:
                resp = client.embeddings.create(
                    model=EMBED_MODEL,
                    input=inputs,
                    dimensions=EMBED_DIM,
                )
            except Exception as e:
                print(f"[embed] batch {i//batch_size + 1} FAILED: {e}")
                raise
            rows = []
            for rid, d in zip(batch, resp.data):
                vec = d.embedding
                reuse[rid] = vec
                rows.append((
                    project_id, rid, EMBED_MODEL, EMBED_DIM, hashes[rid], pack_vector(vec),
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
    if verbose:
        print(f"[embed] total embeddings ready: {len(reuse):,}")
    return reuse


# =========================================================================
# Cluster via cosine similarity + connected components
# =========================================================================

def cluster_embeddings(conn, project_id: str, vectors: dict[str, list[float]], *,
                       threshold: float = DEFAULT_THRESHOLD, verbose: bool = True) -> list[list[str]]:
    from collections import defaultdict
    import numpy as np
    ids = list(vectors.keys())
    n = len(ids)
    if verbose:
        print(f"[cluster] computing {n*(n-1)//2:,} pairwise cosines (threshold {threshold})")

    # Stack to matrix, L2-normalize each row so cosine = matrix product
    t0 = time.time()
    M = np.asarray([vectors[rid] for rid in ids], dtype=np.float32)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    M /= norms
    if verbose:
        print(f"[cluster] matrix ready: {M.shape}  (normalize: {time.time()-t0:.1f}s)")

    # Similarity matrix via BLAS. Process in row-blocks to cap peak memory.
    t0 = time.time()
    adj: dict[str, set[str]] = defaultdict(set)
    pair_rows = []
    block = max(256, min(2048, 50_000_000 // max(1, n)))  # aim for ~50M floats per block
    for bstart in range(0, n, block):
        bend = min(n, bstart + block)
        # Only upper triangle: compare rows [bstart:bend] against rows [bstart:n]
        sim = M[bstart:bend] @ M[bstart:].T  # shape (bend-bstart, n-bstart)
        # Mask below-diagonal within the square sub-block and diagonal itself
        idx_i, idx_j = np.where(sim >= threshold)
        for local_i, local_j in zip(idx_i, idx_j):
            gi = bstart + int(local_i)
            gj = bstart + int(local_j)
            if gj <= gi:
                continue  # skip diag + lower
            s = float(sim[local_i, local_j])
            adj[ids[gi]].add(ids[gj])
            adj[ids[gj]].add(ids[gi])
            pair_rows.append((project_id, ids[gi], ids[gj], round(s, 4)))
        if verbose:
            print(f"[cluster] rows {bstart}-{bend}  elapsed={time.time()-t0:.1f}s  edges={len(pair_rows):,}")

    if verbose:
        print(f"[cluster] total edges >= {threshold}: {len(pair_rows):,}")

    # Connected components via DFS
    visited: set[str] = set()
    components: list[list[str]] = []
    for start in ids:
        if start in visited:
            continue
        stack = [start]
        comp: list[str] = []
        while stack:
            x = stack.pop()
            if x in visited:
                continue
            visited.add(x)
            comp.append(x)
            stack.extend(adj[x] - visited)
        components.append(comp)
    multi = [c for c in components if len(c) >= 2]
    multi.sort(key=lambda c: -len(c))
    if verbose:
        singletons = len(components) - len(multi)
        print(f"[cluster] multi-member clusters: {len(multi):,}   singletons: {singletons:,}")

    # Snapshot existing LLM reviews keyed by their cluster's member-set so we
    # can re-attach them after clustering if the same membership reappears
    # (avoids burning $$ re-reviewing unchanged clusters).
    old_members_by_cid: dict[str, frozenset[str]] = {}
    for row in conn.execute(
        "SELECT cluster_id FROM semantic_cluster WHERE project_id = ?",
        (project_id,),
    ):
        cid = row[0]
        members = frozenset(
            r[0] for r in conn.execute(
                "SELECT report_id FROM semantic_cluster_member WHERE project_id = ? AND cluster_id = ?",
                (project_id, cid),
            )
        )
        if members:
            old_members_by_cid[cid] = members

    old_reviews: dict[frozenset[str], dict] = {}
    for r in conn.execute(
        """SELECT cluster_id, label, business_function, relationship, action,
                  confidence, consolidation_detail, keep_report, removable_count, error
             FROM semantic_llm_review WHERE project_id = ?""",
        (project_id,),
    ):
        cid = r[0]
        member_set = old_members_by_cid.get(cid)
        if member_set:
            old_reviews[member_set] = {
                "label": r[1], "business_function": r[2], "relationship": r[3],
                "action": r[4], "confidence": r[5], "consolidation_detail": r[6],
                "keep_report": r[7], "removable_count": r[8], "error": r[9],
            }

    # Persist pairs + clusters
    with conn:
        conn.execute("DELETE FROM semantic_pair WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM semantic_cluster_member WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM semantic_cluster WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM semantic_llm_review WHERE project_id = ?", (project_id,))
        if pair_rows:
            conn.executemany(
                "INSERT INTO semantic_pair (project_id, id_a, id_b, cosine) VALUES (?, ?, ?, ?)",
                pair_rows,
            )

    # Look up name + telemetry for primary selection
    tel_rows = conn.execute(
        """SELECT r.report_id, r.name, t.total_executions, t.total_users
             FROM report r
             LEFT JOIN telemetry_match t USING (project_id, report_id)
            WHERE r.project_id = ?""",
        (project_id,),
    ).fetchall()
    rec_by_id: dict[str, dict] = {
        r[0]: {"name": r[1] or "", "exec": r[2] or 0, "users": r[3] or 0} for r in tel_rows
    }

    cluster_rows, member_rows = [], []
    new_members_by_cid: dict[str, frozenset[str]] = {}
    # Build cosine lookup for in-cluster avg/min
    cos_lookup: dict[tuple[str, str], float] = {}
    for r in pair_rows:
        _, a, b, s = r
        key = (min(a, b), max(a, b))
        cos_lookup[key] = s

    for i, comp in enumerate(multi, 1):
        cid = f"SC{i:04d}"
        primary = max(comp, key=lambda rid: rec_by_id.get(rid, {}).get("exec", 0))
        primary_name = rec_by_id.get(primary, {}).get("name", "")
        tot_exec = sum(rec_by_id.get(rid, {}).get("exec", 0) for rid in comp)
        tot_users = sum(rec_by_id.get(rid, {}).get("users", 0) for rid in comp)
        # avg/min pairwise cosine inside the component
        pair_ss: list[float] = []
        for x in range(len(comp)):
            for y in range(x + 1, len(comp)):
                key = (min(comp[x], comp[y]), max(comp[x], comp[y]))
                if key in cos_lookup:
                    pair_ss.append(cos_lookup[key])
        avg_cos = round(sum(pair_ss) / len(pair_ss), 4) if pair_ss else None
        min_cos = round(min(pair_ss), 4) if pair_ss else None
        cluster_rows.append((
            project_id, cid, len(comp), primary, primary_name,
            tot_exec, tot_users, avg_cos, min_cos,
        ))
        for rid in comp:
            key = (min(rid, primary), max(rid, primary))
            c_to_p = cos_lookup.get(key) if rid != primary else 1.0
            member_rows.append((project_id, cid, rid, c_to_p))
        new_members_by_cid[cid] = frozenset(comp)

    with conn:
        if cluster_rows:
            conn.executemany(
                """INSERT INTO semantic_cluster
                   (project_id, cluster_id, size, primary_report_id, primary_name,
                    total_executions, total_users, avg_cosine, min_cosine)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                cluster_rows,
            )
        if member_rows:
            conn.executemany(
                """INSERT INTO semantic_cluster_member
                   (project_id, cluster_id, report_id, cosine_to_primary)
                   VALUES (?, ?, ?, ?)""",
                member_rows,
            )

        # Re-attach preserved LLM reviews whose old member-set exactly
        # matches a new cluster's member-set.
        restored = 0
        for new_cid, new_set in new_members_by_cid.items():
            review = old_reviews.get(new_set)
            if not review:
                continue
            conn.execute(
                """INSERT INTO semantic_llm_review
                   (project_id, cluster_id, label, business_function, relationship,
                    action, confidence, consolidation_detail, keep_report,
                    removable_count, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id, new_cid,
                    review["label"], review["business_function"], review["relationship"],
                    review["action"], review["confidence"], review["consolidation_detail"],
                    review["keep_report"], review["removable_count"], review["error"],
                ),
            )
            conn.execute(
                """UPDATE semantic_cluster
                   SET llm_label = ?, llm_action = ?, llm_confidence = ?, llm_removable = ?
                   WHERE project_id = ? AND cluster_id = ?""",
                (
                    review["label"], review["action"], review["confidence"],
                    review["removable_count"], project_id, new_cid,
                ),
            )
            restored += 1
        if verbose and old_reviews:
            total_old = len(old_reviews)
            print(f"[cluster] LLM reviews: restored {restored}/{total_old} "
                  f"(clusters with identical membership); "
                  f"{total_old - restored} orphaned by membership shift")
    return multi


# =========================================================================
# LLM review per semantic cluster
# =========================================================================

LLM_PROMPT_TEMPLATE = """Analyze this semantic cluster of {size} MicroStrategy reports. These reports
were grouped because their embedded representations (name + path + metrics +
tables + filters + normalized SQL) share cosine similarity >= {threshold} in
vector space — i.e. they are *semantically* similar, even if their exact
metric/table/filter sets differ.

Cluster {cid} — primary (highest-execution) member: {primary_name}
Avg cosine within cluster: {avg_cos}   Min cosine: {min_cos}
Total executions across members: {total_exec:,}

Members (up to 25 shown):
{member_lines}

Respond with ONLY valid JSON:
{{
  "label": "Short descriptive business label",
  "business_function": "What these reports do for the business",
  "relationship": "EXACT_DUPLICATE | NEAR_DUPLICATE | PARAMETERIZED_VARIANTS | SIMILAR_DOMAIN | LOOSELY_RELATED",
  "consolidation_action": "MERGE_IMMEDIATE | PARAMETERIZE | REVIEW_WITH_OWNER | KEEP_SEPARATE",
  "confidence": "HIGH | MEDIUM | LOW",
  "consolidation_detail": "Specific recommendation",
  "keep_report": "Name of primary report to keep",
  "removable_count": (int)
}}"""


def llm_review_clusters(conn, project_id: str, *, threshold: float = DEFAULT_THRESHOLD,
                        verbose: bool = True, force: bool = False) -> int:
    """Review multi-member semantic clusters with an LLM.

    By default, skips clusters that already have a successful review (only
    re-reviews clusters whose review is missing or errored). Pass force=True
    to re-review every cluster.
    """
    clusters = conn.execute(
        """SELECT cluster_id, size, primary_report_id, primary_name,
                  total_executions, avg_cosine, min_cosine
             FROM semantic_cluster WHERE project_id = ?
            ORDER BY size DESC, cluster_id""",
        (project_id,),
    ).fetchall()
    if not clusters:
        if verbose:
            print("[llm] no multi-member clusters to review")
        return 0

    # Existing successful reviews — skip unless force=True
    already_reviewed: set[str] = set()
    if not force:
        for row in conn.execute(
            """SELECT cluster_id FROM semantic_llm_review
               WHERE project_id = ? AND label IS NOT NULL AND label != ''
                 AND (error IS NULL OR error = '')""",
            (project_id,),
        ):
            already_reviewed.add(row[0])
        if verbose and already_reviewed:
            skipped = len(already_reviewed)
            total = len(clusters)
            print(f"[llm] {skipped}/{total} clusters already reviewed — will review {total - skipped} new/orphaned")

    # Load member names per cluster
    members_by_cluster: dict[str, list[str]] = {}
    for r in conn.execute(
        """SELECT scm.cluster_id, scm.report_id, r.name, r.path,
                  r.metric_count, r.table_count, r.filter_count
             FROM semantic_cluster_member scm
             JOIN report r USING (project_id, report_id)
            WHERE scm.project_id = ?
            ORDER BY scm.cluster_id, r.name""",
        (project_id,),
    ):
        cid, rid, name, path, mc, tc, fc = r
        line = f"- {(name or '')[:70]} | metrics:{mc or 0} tables:{tc or 0} filters:{fc or 0}"
        if path:
            line += f" | path: {path[:80]}"
        members_by_cluster.setdefault(cid, []).append(line)

    client = _openai_client()
    if verbose:
        print(f"[llm] reviewing {len(clusters)} semantic clusters with {LLM_MODEL}")

    reviewed = 0
    for i, c in enumerate(clusters, 1):
        cid, size, pid_rep, primary_name, tot_exec, avg_cos, min_cos = c
        if cid in already_reviewed:
            continue
        mem_lines = members_by_cluster.get(cid, [])
        if len(mem_lines) > 25:
            shown = mem_lines[:25]
            shown.append(f"... +{len(mem_lines) - 25} more members")
            mem_lines = shown

        prompt = LLM_PROMPT_TEMPLATE.format(
            size=size, threshold=threshold, cid=cid,
            primary_name=primary_name or "(unknown)",
            avg_cos=avg_cos if avg_cos is not None else "?",
            min_cos=min_cos if min_cos is not None else "?",
            total_exec=tot_exec or 0,
            member_lines="\n".join(mem_lines),
        )

        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=1500,
                temperature=0.1,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            result = json.loads(text)
            with conn:
                conn.execute(
                    """INSERT OR REPLACE INTO semantic_llm_review
                       (project_id, cluster_id, label, business_function, relationship,
                        action, confidence, consolidation_detail, keep_report, removable_count)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id, cid,
                        result.get("label", ""),
                        result.get("business_function", ""),
                        result.get("relationship", ""),
                        result.get("consolidation_action", ""),
                        result.get("confidence", ""),
                        result.get("consolidation_detail", ""),
                        result.get("keep_report", ""),
                        int(result.get("removable_count", 0) or 0),
                    ),
                )
                # Also denormalize onto the cluster row for simple emits
                conn.execute(
                    """UPDATE semantic_cluster SET
                         llm_label = ?, llm_action = ?, llm_confidence = ?, llm_removable = ?
                       WHERE project_id = ? AND cluster_id = ?""",
                    (
                        result.get("label", ""),
                        result.get("consolidation_action", ""),
                        result.get("confidence", ""),
                        int(result.get("removable_count", 0) or 0),
                        project_id, cid,
                    ),
                )
            reviewed += 1
            if verbose:
                # Guard against cp1252 console choking on unicode labels.
                msg = (f"  [{i}/{len(clusters)}] {cid} size={size} -> "
                       f"{result.get('consolidation_action','?')} rem={result.get('removable_count',0)} "
                       f"({result.get('label','')[:50]})")
                try:
                    print(msg)
                except UnicodeEncodeError:
                    print(msg.encode("ascii", "replace").decode("ascii"))
            time.sleep(0.3)
        except Exception as e:
            with conn:
                conn.execute(
                    """INSERT OR REPLACE INTO semantic_llm_review
                       (project_id, cluster_id, error) VALUES (?, ?, ?)""",
                    (project_id, cid, str(e)[:300]),
                )
            if verbose:
                print(f"  [{i}/{len(clusters)}] {cid} ERROR: {str(e)[:100]}")
    return reviewed


# =========================================================================
# Orchestrator + CLI
# =========================================================================

def run_semantic(project_id: str, *, threshold: float = DEFAULT_THRESHOLD,
                 skip_llm: bool = False, limit: int | None = None) -> dict:
    cfg = get_project(project_id)
    print(f"\n============================================================")
    print(f"Semantic clustering — {cfg['name']} ({project_id})")
    print(f"============================================================")

    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        vectors = embed_reports(conn, project_id, limit=limit)
        if not vectors:
            print("No vectors — nothing to cluster.")
            return {"clusters": 0, "reviewed": 0}
        clusters = cluster_embeddings(conn, project_id, vectors, threshold=threshold)
        reviewed = 0
        if not skip_llm:
            reviewed = llm_review_clusters(conn, project_id, threshold=threshold)
        return {"clusters": len(clusters), "reviewed": reviewed}
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser()
    project_ids = [p["project_id"] for p in PROJECTS]
    p.add_argument("--project", choices=project_ids + ["all"], default="global-operational")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p.add_argument("--skip-llm", action="store_true", help="Skip LLM review step")
    p.add_argument("--limit", type=int, default=None,
                   help="Dry-run limit on post-AST survivors (caps the embed set)")
    args = p.parse_args()

    targets = (project_ids if args.project == "all" else [args.project])
    for tgt in targets:
        run_semantic(tgt, threshold=args.threshold, skip_llm=args.skip_llm, limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
