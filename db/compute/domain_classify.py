"""Business-domain classification of reports.

Produces a Playground-style experiment JSON (same shape as
`db.compute.playground.run_experiment`) so that the existing Playground
index, "Make Primary" promotion, and Semantic Clusters tab all work
without change.

Strategy:
  1. Build classification text per report: NAME + PATH + top metrics +
     top attributes + top tables (small, cheap — not SQL).
  2. Send reports to GPT in batches of 25, asking it to pick ONE domain
     from a FIXED taxonomy per report. JSON response mode so parsing is
     reliable.
  3. Group reports by assigned domain — one cluster per domain.
  4. Emit the result under `data/_playground/<exp_id>.json` and append
     to `index.json`.

Cost notes:
  - ~5,000 reports / 25 per batch = 200 calls. gpt-5.4 at small prompts
    is maybe ~$0.02/call = ~$4 per full project run. Re-classifying the
    same report (by content hash) is free via SQLite cache.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.db import connect, init, get_project
from db.compute.semantic import _load_env_local, _openai_client
from db.compute.pipeline import (
    _ast_canonical_ids, _post_ast_family_canonical_ids,
    _canonical_ids_after_collisions,
)
from db.compute.playground import (
    PLAYGROUND_DIR, _resolve_project_id, _resolve_scope,
    _apply_source_filter,
)


# Fixed taxonomy. Keep broad, retail/enterprise-oriented. "Other" is the
# safety valve — if the LLM can't confidently place a report, it lands here
# rather than guessing.
DEFAULT_DOMAINS: list[str] = [
    "Inventory & Stock",
    "Purchase Orders",
    "Sales & Revenue",
    "Customer Analytics",
    "Product Catalog",
    "Order Management",
    "Shipping & Logistics",
    "Finance & Accounting",
    "HR & Workforce",
    "Marketing & Campaigns",
    "Returns & Refunds",
    "Supplier & Vendor",
    "Store Operations",
    "Pricing & Promotions",
    "Other",
]

CLASSIFY_MODEL = "gpt-5.4"
BATCH_SIZE = 25


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS report_domain (
    project_id   TEXT NOT NULL,
    report_id    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    domain       TEXT NOT NULL,
    confidence   REAL,
    classified_at TEXT NOT NULL,
    PRIMARY KEY (project_id, report_id, content_hash)
);
"""


def _ensure_schema(conn) -> None:
    with conn:
        conn.executescript(_SCHEMA_SQL)


# ---------------- Text builder ------------------------------------

def _build_classify_text(rec: dict, max_per_field: int = 10) -> str:
    """Short, cheap text: name, path, top attributes/metrics/tables. No SQL."""
    parts = []
    if rec.get("name"):
        parts.append(f"NAME: {rec['name']}")
    if rec.get("path"):
        parts.append(f"PATH: {rec['path']}")
    for field, label in [
        ("attributes", "ATTRIBUTES"),
        ("metrics", "METRICS"),
        ("tables", "TABLES"),
    ]:
        vals = rec.get(field) or []
        if vals:
            parts.append(f"{label}: {'; '.join(vals[:max_per_field])}")
    text = "\n".join(parts)
    return text or f"ID: {rec.get('id')}"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


# ---------------- Load report data --------------------------------

def _load_report_data(conn, project_id: str, rids: list[str]) -> dict[str, dict]:
    placeholders = ",".join("?" * len(rids))
    out: dict[str, dict] = {}
    for r in conn.execute(
        f"""SELECT r.report_id, r.name, r.path, t.total_executions
              FROM report r
              LEFT JOIN telemetry_match t USING (project_id, report_id)
             WHERE r.project_id = ? AND r.report_id IN ({placeholders})""",
        [project_id, *rids],
    ):
        out[r[0]] = {
            "id": r[0], "name": r[1] or "", "path": r[2] or "",
            "executions": r[3] or 0,
            "attributes": [], "metrics": [], "tables": [],
        }
    for tbl, col, key in [
        ("report_attribute", "attribute_name", "attributes"),
        ("report_metric", "metric_name", "metrics"),
        ("report_table", "table_name", "tables"),
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


# ---------------- LLM batch classify ------------------------------

def _classify_batch(client, model: str, taxonomy: list[str],
                    items: list[tuple[str, str]], verbose: bool = True
                    ) -> dict[str, tuple[str, float]]:
    """items: list of (report_id, classify_text). Returns {rid: (domain, confidence)}.
    Uses JSON response format so parsing is reliable."""
    domains_str = "\n".join(f"- {d}" for d in taxonomy)
    reports_json = json.dumps([
        {"id": rid, "text": text} for rid, text in items
    ], ensure_ascii=False)

    system = (
        "You classify enterprise BI/analytics reports into a FIXED list of "
        "business domains. For each report, pick the SINGLE best-fit domain "
        "from the provided list. If the report doesn't clearly fit any "
        "specific domain, use \"Other\". Respond ONLY as JSON of the shape "
        "{\"results\": [{\"id\": \"...\", \"domain\": \"...\", "
        "\"confidence\": 0.0-1.0}]}. The `domain` MUST be one of the "
        "allowed values verbatim."
    )
    user = (
        f"Allowed domains (pick exactly one, verbatim):\n{domains_str}\n\n"
        f"Reports to classify (JSON):\n{reports_json}"
    )

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=4000,
        temperature=0.1,
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        if verbose:
            print(f"[classify] bad JSON from LLM, falling back to Other: {raw[:200]!r}")
        return {rid: ("Other", 0.0) for rid, _ in items}

    taxonomy_set = set(taxonomy)
    out: dict[str, tuple[str, float]] = {}
    for r in parsed.get("results", []) or []:
        rid = r.get("id")
        dom = r.get("domain", "Other")
        conf = float(r.get("confidence") or 0.0)
        if dom not in taxonomy_set:
            dom = "Other"
        if rid:
            out[rid] = (dom, conf)
    # Safety: any missing rid -> Other
    for rid, _ in items:
        out.setdefault(rid, ("Other", 0.0))
    return out


# ---------------- Main driver -------------------------------------

def run_domain_classification(config: dict, verbose: bool = True) -> dict:
    """Classify reports into fixed-taxonomy domains and emit a
    Playground-compatible experiment JSON."""
    exp_id = config.get("id") or f"domain-{int(time.time())}"
    name = config.get("name") or exp_id
    project_id = _resolve_project_id(config["project"])
    scope = config.get("scope", "post-family")
    src_filter = config.get("sourceFilter", "all")
    taxonomy = config.get("taxonomy") or DEFAULT_DOMAINS
    model = config.get("model", CLASSIFY_MODEL)

    cfg_name = get_project(project_id)["name"]
    print(f"\n=== Domain classify {exp_id} ({name}) - project {cfg_name} ===")
    print(f"  scope={scope} src={src_filter} model={model}")
    print(f"  taxonomy ({len(taxonomy)}): {taxonomy}")

    init()
    conn = connect()
    conn.execute("PRAGMA foreign_keys = OFF")
    _ensure_schema(conn)
    try:
        rids = _resolve_scope(conn, project_id, scope)
        rids = _apply_source_filter(conn, project_id, rids, src_filter)
        rids = sorted(rids)
        if not rids:
            print("  no reports in scope; aborting")
            return {"error": "empty scope"}
        if config.get("limit"):
            rids = rids[: int(config["limit"])]
            print(f"  limited to {len(rids):,}")
        if verbose:
            print(f"  reports in scope: {len(rids):,}")

        recs = _load_report_data(conn, project_id, rids)

        # Per-report classification text + content hash
        text_by_rid: dict[str, str] = {}
        hash_by_rid: dict[str, str] = {}
        for rid in rids:
            r = recs.get(rid)
            if not r:
                continue
            t = _build_classify_text(r)
            text_by_rid[rid] = t
            hash_by_rid[rid] = _content_hash(t)

        # Cache lookup
        results: dict[str, tuple[str, float]] = {}
        to_classify: list[str] = []
        for rid in rids:
            h = hash_by_rid.get(rid)
            if not h:
                continue
            row = conn.execute(
                "SELECT domain, confidence FROM report_domain "
                "WHERE project_id=? AND report_id=? AND content_hash=?",
                (project_id, rid, h),
            ).fetchone()
            if row:
                results[rid] = (row[0], row[1] or 0.0)
            else:
                to_classify.append(rid)

        if verbose:
            print(f"[classify] cache hits: {len(results):,}   new: {len(to_classify):,}")

        if to_classify:
            client = _openai_client()
            done = 0
            for i in range(0, len(to_classify), BATCH_SIZE):
                batch_rids = to_classify[i:i + BATCH_SIZE]
                items = [(rid, text_by_rid[rid]) for rid in batch_rids]
                batch_out = _classify_batch(client, model, taxonomy, items, verbose)
                now = datetime.now(timezone.utc).isoformat()
                rows = []
                for rid, (dom, conf) in batch_out.items():
                    results[rid] = (dom, conf)
                    rows.append((project_id, rid, hash_by_rid[rid], dom, conf, now))
                with conn:
                    conn.executemany(
                        """INSERT OR REPLACE INTO report_domain
                            (project_id, report_id, content_hash, domain,
                             confidence, classified_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        rows,
                    )
                done += len(batch_rids)
                if verbose:
                    print(f"[classify] progress: {done}/{len(to_classify)}")

        # Group reports by domain -> clusters
        by_domain: dict[str, list[str]] = {d: [] for d in taxonomy}
        for rid in rids:
            dom = results.get(rid, ("Other", 0.0))[0]
            by_domain.setdefault(dom, []).append(rid)

        clusters_out = []
        scatter = []
        # Deterministic order: taxonomy order, then alphabetical for any extras
        ordered_domains = [d for d in taxonomy if d in by_domain] + \
                          [d for d in sorted(by_domain) if d not in taxonomy]
        idx = 1
        cluster_by_rid: dict[str, str] = {}
        singletons: list[str] = []
        for dom in ordered_domains:
            members = by_domain.get(dom, [])
            if not members:
                continue
            # Size=1 domain members are treated as singletons (no point in a
            # "cluster of one") EXCEPT we still want to surface them in the UI.
            # Mirror the Playground convention: clusters are size >= 2.
            if len(members) < 2:
                singletons.extend(members)
                continue
            cid = f"D{idx:03d}"
            idx += 1
            primary = max(members, key=lambda r: recs.get(r, {}).get("executions", 0))
            tot_exec = sum(recs.get(r, {}).get("executions", 0) for r in members)
            # Confidence stats per cluster
            confs = [results.get(m, (dom, 0.0))[1] for m in members]
            avg_conf = round(sum(confs) / len(confs), 3) if confs else None
            clusters_out.append({
                "id": cid,
                "size": len(members),
                "primaryReportId": primary,
                "primaryName": recs.get(primary, {}).get("name", ""),
                "totalExecutions": tot_exec,
                # These fields are cosine-specific; populate with confidence
                # so the existing UI has something to show.
                "avgCosine": avg_conf,
                "minCosine": round(min(confs), 3) if confs else None,
                "memberIds": members,
                "domain": dom,
            })
            for rid in members:
                cluster_by_rid[rid] = cid

        # Build scatter (no embeddings to project, so use a grid layout:
        # each domain gets a horizontal band, members spread across it).
        domain_y = {c["id"]: (i + 0.5) / max(1, len(clusters_out) + 1)
                    for i, c in enumerate(clusters_out)}
        for rid in rids:
            cid = cluster_by_rid.get(rid)
            if cid and cid in domain_y:
                # Spread x across the band by report-id hash
                x = (int(hashlib.md5(rid.encode()).hexdigest()[:8], 16) % 10000) / 10000
                y = domain_y[cid]
            else:
                # Singleton — park at the bottom
                x = (int(hashlib.md5(rid.encode()).hexdigest()[:8], 16) % 10000) / 10000
                y = 0.97
            rec = recs.get(rid, {})
            scatter.append({
                "id": rid,
                "x": x,
                "y": y,
                "clusterId": cid,
                "name": rec.get("name", "")[:80],
                "executions": rec.get("executions", 0),
            })

        result = {
            "id": exp_id,
            "name": name,
            "config": {**config, "mode": "domain", "taxonomy": taxonomy,
                       "project": project_id, "scope": scope,
                       "sourceFilter": src_filter, "model": model,
                       "dimensions": 0, "threshold": 0.0},
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "stats": {
                "inputReports": len(rids),
                "reportsEmbedded": len(rids),  # no embeddings, but UI expects this field
                "edges": 0,
                "multiClusters": len(clusters_out),
                "singletons": len(singletons),
                "finalUnique": len(clusters_out) + len(singletons),
            },
            "clusters": clusters_out,
            "singletonIds": singletons[:500],
            "viz": {
                "scatter": scatter,
                "topPairs": [],
                "thresholdUsed": 0.0,
            },
            "mode": "domain",
            "domainSummary": [
                {"domain": c["domain"], "size": c["size"],
                 "totalExecutions": c["totalExecutions"]}
                for c in clusters_out
            ],
        }

        PLAYGROUND_DIR.mkdir(parents=True, exist_ok=True)
        out_path = PLAYGROUND_DIR / f"{exp_id}.json"
        out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {out_path}")

        # Update index with same structure as cosine experiments so the UI's
        # existing list filter works uniformly.
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
                "model": model, "dim": 0, "threshold": 0.0,
                "minClusterSize": 2, "fields": {},
                "mode": "domain",
            },
        })
        idx_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        print(f"updated {idx_path} ({len(existing)} experiments)")
        return result
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Classify reports into business domains")
    ap.add_argument("--project", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--id", default=None)
    ap.add_argument("--scope", default="post-family",
                    choices=["post-family", "post-ast", "active", "final-kept", "post-collision"])
    ap.add_argument("--source", dest="sourceFilter", default="all")
    ap.add_argument("--model", default=CLASSIFY_MODEL)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    _load_env_local()

    cfg = {
        "project": args.project,
        "name": args.name or f"domains-{args.project}",
        "id": args.id,
        "scope": args.scope,
        "sourceFilter": args.sourceFilter,
        "model": args.model,
        "limit": args.limit,
    }
    run_domain_classification(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
