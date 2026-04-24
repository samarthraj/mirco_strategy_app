"""Named embedding sets — the asset layer that clustering methods build on.

The shift: before, "Semantic / Density / AI Playground" each did
embed+cluster in one step. That hid the cache and made cost unpredictable.
This module surfaces embedding as a first-class named artefact:

  Embedding (asset)              Clustering (strategy on top)
  ──────────────────             ────────────────────────────
  • gi-metrics-lg-3072      →    Semantic / Density / ...
  • gi-full-lg-3072
  • go-full-lg-3072

Each embedding set is:
  - A name chosen by the user
  - A project + scope + source filter + fields + model + dim
  - Metadata pointing to vectors already stored in `semantic_embedding`
  - Re-running doesn't duplicate vectors; content-hash-based cache still
    shared across sets and clustering methods.

Storage: `embedding_set` table holds metadata only. Vectors continue to
live in the existing `semantic_embedding` table keyed by content_hash, so
two sets with identical text blobs share the same bytes.
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
from db.compute.semantic import _load_env_local
from db.compute.playground import (
    _resolve_project_id, _resolve_scope, _apply_source_filter,
    _build_text, _load_report_data, _embed_all, _content_hash,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_set (
    name          TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    scope         TEXT NOT NULL,
    source_filter TEXT NOT NULL,
    model         TEXT NOT NULL,
    dim           INTEGER NOT NULL,
    fields_json   TEXT NOT NULL,
    report_count  INTEGER NOT NULL,
    token_estimate INTEGER,
    created_at    TEXT NOT NULL,
    refreshed_at  TEXT
);

CREATE INDEX IF NOT EXISTS ix_embedding_set_project
    ON embedding_set(project_id);

-- Per-report membership. Lets us quickly answer "which reports are in
-- this embedding set?" and "which sets cover report X?" without redoing
-- the scope+text computation.
CREATE TABLE IF NOT EXISTS embedding_set_member (
    name         TEXT NOT NULL,
    report_id    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (name, report_id),
    FOREIGN KEY (name) REFERENCES embedding_set(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_embedding_set_member_report
    ON embedding_set_member(report_id);
"""


def _ensure_schema(conn) -> None:
    with conn:
        conn.executescript(_SCHEMA)


# ---------------- Build text plan -------------------------------

def _plan_texts(conn, config: dict) -> tuple[str, list[str], dict[str, str], dict[str, str]]:
    """Resolve scope, build text blob per report, compute content hashes.

    Returns: (project_id, ordered_rids, text_by_rid, hash_by_rid).
    No API calls — purely local.
    """
    project_id = _resolve_project_id(config["project"])
    scope = config.get("scope", "post-family")
    source_filter = config.get("sourceFilter", "all")
    fields = config.get("fields") or {}
    model = config.get("model", "text-embedding-3-large")
    dim = int(config.get("dimensions") or 3072)

    rids = _resolve_scope(conn, project_id, scope)
    rids = _apply_source_filter(conn, project_id, rids, source_filter)
    # Optional business-domain filter from report_domain. "all"/None = no filter.
    domain_filter = config.get("domainFilter")
    if domain_filter and domain_filter.lower() != "all":
        domain_rids = {
            r[0] for r in conn.execute(
                "SELECT report_id FROM report_domain WHERE project_id=? AND domain=?",
                (project_id, domain_filter),
            )
        }
        rids = rids & domain_rids if isinstance(rids, set) else {r for r in rids if r in domain_rids}
    rids = sorted(rids)
    if config.get("limit"):
        rids = rids[: int(config["limit"])]

    recs = _load_report_data(conn, project_id, rids)
    text_by_rid: dict[str, str] = {}
    hash_by_rid: dict[str, str] = {}
    for rid in rids:
        r = recs.get(rid)
        if not r:
            continue
        t = _build_text(r, fields)
        if not t:
            continue
        text_by_rid[rid] = t
        hash_by_rid[rid] = _content_hash(t, model, dim)
    return project_id, rids, text_by_rid, hash_by_rid


def preview_embedding_set(config: dict) -> dict:
    """Report how many reports + cache hits vs new without embedding anything.
    Fast: one bulk SELECT via a temp table instead of per-report queries."""
    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        _ensure_schema(conn)
        project_id, rids, text_by_rid, hash_by_rid = _plan_texts(conn, config)
        model = config.get("model", "text-embedding-3-large")
        total = len(text_by_rid)
        if total == 0:
            return {"totalReports": 0, "cached": 0, "new": 0,
                    "tokenEstimate": 0, "estimatedCostUsd": 0.0}

        # Compute cached set in ONE pass using a temp table. Prior code ran a
        # SELECT per report (3k+ queries on INSIGHT) plus called
        # _already_cached_set once per iteration inside a generator — O(n²) on
        # a "preview" endpoint that needs to finish in <1s.
        cached_ids = _already_cached_set(conn, project_id, hash_by_rid, model)
        cached = len(cached_ids)
        new_count = total - cached

        # Cost estimate based on new (uncached) reports only.
        new_chars = sum(
            len(text_by_rid[rid])
            for rid in text_by_rid
            if rid not in cached_ids
        )
        token_est = max(1, new_chars // 4)  # ~1 token per 4 chars
        price_per_1k = {
            "text-embedding-3-large": 0.00013,
            "text-embedding-3-small": 0.00002,
        }.get(model, 0.00013)
        cost = round((token_est / 1000.0) * price_per_1k, 4)
        return {
            "totalReports": total,
            "cached": cached,
            "new": new_count,
            "tokenEstimate": token_est,
            "estimatedCostUsd": cost,
            "model": model,
            "projectId": project_id,
        }
    finally:
        conn.close()


def _already_cached_set(conn, project_id: str, hash_by_rid: dict[str, str], model: str) -> set[str]:
    """report_ids whose (rid, content_hash) pair is cached. Used for
    'new chars' estimate in preview.

    Uses a single temp-table JOIN so previews stay sub-second even on 10k+
    reports — the OR-clause-chunked version was ~1-2s per call and this is
    called inside hot paths."""
    if not hash_by_rid:
        return set()
    # Use a temp table instead of thousands of bound params.
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _emb_probe "
        "(report_id TEXT, content_hash TEXT, PRIMARY KEY(report_id, content_hash))"
    )
    conn.execute("DELETE FROM _emb_probe")
    conn.executemany(
        "INSERT OR IGNORE INTO _emb_probe (report_id, content_hash) VALUES (?, ?)",
        list(hash_by_rid.items()),
    )
    rows = conn.execute(
        """SELECT se.report_id
             FROM semantic_embedding se
             JOIN _emb_probe p
               ON p.report_id = se.report_id
              AND p.content_hash = se.content_hash
            WHERE se.project_id = ? AND se.model = ?""",
        (project_id, model),
    ).fetchall()
    return {r[0] for r in rows}


def run_embedding_set(config: dict, verbose: bool = True) -> dict:
    """Create or refresh a named embedding set.

    config:
      - name:     string (primary key)
      - project:  short project id (or display name / GUID)
      - scope:    post-family / post-ast / active / final-kept / post-collision
      - sourceFilter: all / normal / cube / exclude-cube / custom_sql_free_form
      - fields:   dict of which fields go into the text blob
      - model:    text-embedding-3-large (default) / text-embedding-3-small
      - dimensions: 3072 (default) / 1536
      - limit:    optional int — cap report count (useful for testing)
    """
    name = (config.get("name") or "").strip()
    if not name:
        raise ValueError("embedding set name is required")

    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    _ensure_schema(conn)
    try:
        project_id, rids, text_by_rid, hash_by_rid = _plan_texts(conn, config)
        model = config.get("model", "text-embedding-3-large")
        dim = int(config.get("dimensions") or 3072)

        if not text_by_rid:
            return {"error": "no reports in scope produced a text blob"}

        cfg_name = get_project(project_id)["name"]
        print(f"\n=== Embedding set '{name}' — project {cfg_name} ===")
        print(f"  scope={config.get('scope')} src={config.get('sourceFilter')}")
        print(f"  model={model} dim={dim} reports={len(text_by_rid):,}")

        t0 = time.time()
        vectors = _embed_all(conn, project_id, text_by_rid, model, dim, verbose)
        elapsed = time.time() - t0

        # Estimate tokens for reporting (rough)
        total_chars = sum(len(t) for t in text_by_rid.values())
        token_est = max(1, total_chars // 4)

        now = datetime.now(timezone.utc).isoformat()
        with conn:
            # Upsert the set metadata
            existing = conn.execute(
                "SELECT created_at FROM embedding_set WHERE name=?", (name,)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE embedding_set
                          SET project_id=?, scope=?, source_filter=?, model=?,
                              dim=?, fields_json=?, report_count=?,
                              token_estimate=?, refreshed_at=?
                        WHERE name=?""",
                    (project_id, config.get("scope", "post-family"),
                     config.get("sourceFilter", "all"), model, dim,
                     json.dumps(config.get("fields") or {}),
                     len(vectors), token_est, now, name),
                )
                created_at = existing[0]
            else:
                conn.execute(
                    """INSERT INTO embedding_set
                        (name, project_id, scope, source_filter, model, dim,
                         fields_json, report_count, token_estimate, created_at, refreshed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, project_id, config.get("scope", "post-family"),
                     config.get("sourceFilter", "all"), model, dim,
                     json.dumps(config.get("fields") or {}),
                     len(vectors), token_est, now, now),
                )
                created_at = now

            # Rewrite the membership rows
            conn.execute("DELETE FROM embedding_set_member WHERE name=?", (name,))
            rows = [(name, rid, hash_by_rid[rid]) for rid in vectors]
            conn.executemany(
                "INSERT INTO embedding_set_member (name, report_id, content_hash) VALUES (?, ?, ?)",
                rows,
            )

        print(f"\nwrote embedding_set '{name}' ({len(vectors):,} reports) in {elapsed:.1f}s")
        return {
            "name": name,
            "projectId": project_id,
            "reportCount": len(vectors),
            "model": model,
            "dim": dim,
            "createdAt": created_at,
            "refreshedAt": now,
            "elapsedSec": round(elapsed, 1),
        }
    finally:
        conn.close()


def list_embedding_sets(project_filter: str | None = None) -> list[dict]:
    init()
    conn = connect()
    _ensure_schema(conn)
    try:
        if project_filter:
            pid = _resolve_project_id(project_filter)
            rows = conn.execute(
                """SELECT name, project_id, scope, source_filter, model, dim,
                          fields_json, report_count, token_estimate,
                          created_at, refreshed_at
                     FROM embedding_set WHERE project_id=?
                     ORDER BY refreshed_at DESC, created_at DESC""",
                (pid,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT name, project_id, scope, source_filter, model, dim,
                          fields_json, report_count, token_estimate,
                          created_at, refreshed_at
                     FROM embedding_set
                     ORDER BY refreshed_at DESC, created_at DESC"""
            ).fetchall()
        out = []
        for r in rows:
            try:
                fields = json.loads(r[6]) if r[6] else {}
            except Exception:
                fields = {}
            out.append({
                "name": r[0],
                "projectId": r[1],
                "scope": r[2],
                "sourceFilter": r[3],
                "model": r[4],
                "dim": r[5],
                "fields": fields,
                "reportCount": r[7],
                "tokenEstimate": r[8],
                "createdAt": r[9],
                "refreshedAt": r[10],
            })
        return out
    finally:
        conn.close()


def fetch_embedding_set(name: str) -> dict | None:
    """Return a single embedding-set record (or None). Used by clustering
    modules to resolve an `embeddingSetName` into the concrete scope +
    filter + fields + model + dim config."""
    init()
    conn = connect()
    _ensure_schema(conn)
    try:
        row = conn.execute(
            """SELECT name, project_id, scope, source_filter, model, dim,
                      fields_json, report_count, token_estimate,
                      created_at, refreshed_at
                 FROM embedding_set WHERE name=?""",
            (name,),
        ).fetchone()
        if not row:
            return None
        try:
            fields = json.loads(row[6]) if row[6] else {}
        except Exception:
            fields = {}
        return {
            "name": row[0],
            "projectId": row[1],
            "scope": row[2],
            "sourceFilter": row[3],
            "model": row[4],
            "dim": row[5],
            "fields": fields,
            "reportCount": row[7],
            "tokenEstimate": row[8],
            "createdAt": row[9],
            "refreshedAt": row[10],
        }
    finally:
        conn.close()


def resolve_config_from_set(config: dict) -> dict:
    """If `config['embeddingSetName']` is set, merge the named set's
    scope/sourceFilter/fields/model/dim into the config (the clustering
    caller's values are overwritten). Other fields (threshold, UMAP params,
    etc.) are kept. Returns the merged config. If the set name is missing
    or not found, returns the config unchanged."""
    name = (config.get("embeddingSetName") or "").strip()
    if not name:
        return config
    es = fetch_embedding_set(name)
    if not es:
        # Unknown set name — leave config alone; the caller's scope/fields
        # are used as a fallback. Log in the runner's verbose output.
        return config
    merged = dict(config)
    merged["project"] = es["projectId"]
    merged["scope"] = es["scope"]
    merged["sourceFilter"] = es["sourceFilter"]
    merged["model"] = es["model"]
    merged["dimensions"] = es["dim"]
    merged["fields"] = es["fields"]
    merged["_resolvedFromEmbeddingSet"] = name
    return merged


def delete_embedding_set(name: str) -> dict:
    """Delete the set's metadata + membership. Does NOT remove the
    underlying vectors from `semantic_embedding` — they may be shared with
    other sets, the pipeline, or future experiments."""
    init()
    conn = connect()
    _ensure_schema(conn)
    try:
        with conn:
            rm = conn.execute("SELECT COUNT(*) FROM embedding_set_member WHERE name=?", (name,)).fetchone()[0]
            conn.execute("DELETE FROM embedding_set_member WHERE name=?", (name,))
            d = conn.execute("DELETE FROM embedding_set WHERE name=?", (name,))
            return {"deleted": name, "removedMembers": rm, "existed": d.rowcount > 0}
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Named embedding sets")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Create/refresh a named embedding set")
    run.add_argument("--name", required=True)
    run.add_argument("--project", required=True)
    run.add_argument("--scope", default="post-family")
    run.add_argument("--source-filter", default="all")
    run.add_argument("--model", default="text-embedding-3-large")
    run.add_argument("--dim", type=int, default=3072)
    run.add_argument("--fields", default="name,path,metrics,tables,filters,sql",
                     help="Comma-separated field names to enable")
    run.add_argument("--limit", type=int, default=None)

    ls = sub.add_parser("list", help="List all named embedding sets")
    ls.add_argument("--project", default=None)

    rm = sub.add_parser("delete", help="Remove a named embedding set (metadata only)")
    rm.add_argument("--name", required=True)

    pv = sub.add_parser("preview", help="Cost preview — no API calls")
    pv.add_argument("--project", required=True)
    pv.add_argument("--scope", default="post-family")
    pv.add_argument("--source-filter", default="all")
    pv.add_argument("--model", default="text-embedding-3-large")
    pv.add_argument("--dim", type=int, default=3072)
    pv.add_argument("--fields", default="name,path,metrics,tables,filters,sql")

    args = ap.parse_args()
    _load_env_local()

    if args.cmd == "run":
        fields = {k: True for k in args.fields.split(",") if k.strip()}
        fields.setdefault("normalizeSql", True)
        fields.setdefault("maxSqlChars", 2000)
        result = run_embedding_set({
            "name": args.name, "project": args.project,
            "scope": args.scope, "sourceFilter": args.source_filter,
            "model": args.model, "dimensions": args.dim,
            "fields": fields, "limit": args.limit,
        })
        print(json.dumps(result, indent=2))
    elif args.cmd == "list":
        rows = list_embedding_sets(args.project)
        print(json.dumps(rows, indent=2))
    elif args.cmd == "delete":
        print(json.dumps(delete_embedding_set(args.name), indent=2))
    elif args.cmd == "preview":
        fields = {k: True for k in args.fields.split(",") if k.strip()}
        fields.setdefault("normalizeSql", True)
        fields.setdefault("maxSqlChars", 2000)
        result = preview_embedding_set({
            "project": args.project, "scope": args.scope,
            "sourceFilter": args.source_filter,
            "model": args.model, "dimensions": args.dim,
            "fields": fields,
        })
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
