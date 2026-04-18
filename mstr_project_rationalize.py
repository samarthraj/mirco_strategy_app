#!/usr/bin/env python3
"""
MicroStrategy Project Rationalization Tool.

Performs a full inventory of all object types in a MicroStrategy project,
maps dependencies between objects, detects duplicates, and produces
rationalization analysis (orphans, high-impact objects, staleness).

Phases
------
1. inventory     — enumerate all object types, optionally fetch definitions & SQL
2. dependencies  — build a dependency graph via the dependents API
3. analysis      — local computation: orphans, duplicates, high-impact, staleness

Usage
-----
python mstr_project_rationalize.py \
  --base-url https://demo.microstrategy.com/MicroStrategyLibrary/api \
  --username administrator \
  --password "" \
  --project-name "MicroStrategy Tutorial" \
  --output-dir mstr_rationalization \
  --phases inventory,dependencies,analysis \
  --include-sql \
  --include-definitions \
  --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from mstr_report_inventory import (
    MstrClient,
    Project,
    extract_filter,
    extract_units,
    parse_table_names_from_sql,
    write_json,
)

# ---------------------------------------------------------------------------
# Object type registry
# ---------------------------------------------------------------------------

OBJECT_TYPES: Dict[str, Dict[str, Any]] = {
    "reports":          {"type": 3},
    "documents":        {"type": 55},
    "cubes":            {"type": 21},
    "metrics":          {"type": 4},
    "filters":          {"type": 1},
    "prompts":          {"type": 10},
    "attributes":       {"type": 12},
    "facts":            {"type": 13},
    "tables":           {"type": 15},
    "security_filters": {"type": 47},
    "custom_groups":    {"type": 1, "subtype": 9984},
}

# Categories where we can fetch rich definitions
DEFINITION_CATEGORIES = {"reports", "cubes", "metrics"}

# Categories where we can fetch SQL
SQL_CATEGORIES = {"reports", "cubes"}

# Categories that are "leaf" consumers — not flagged as orphans
LEAF_CATEGORIES = {"reports", "documents", "cubes"}

ALL_PHASES = ["inventory", "paths", "dependencies", "analysis", "sql"]


# ---------------------------------------------------------------------------
# RationalizationClient — extends MstrClient
# ---------------------------------------------------------------------------

class RationalizationClient(MstrClient):
    """Extended MicroStrategy REST API client for project rationalization."""

    def __init__(self, *args: Any, delay: float = 0.1, max_retries: int = 3,
                 sql_batch_size: int = 10, sql_batch_delay: float = 5.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.delay = delay
        self.max_retries = max_retries
        self.sql_batch_size = sql_batch_size
        self.sql_batch_delay = sql_batch_delay
        self._sql_call_count = 0

    # -- helpers -------------------------------------------------------------

    def _throttle(self) -> None:
        if self.delay > 0:
            time.sleep(self.delay)

    def _throttle_sql(self) -> None:
        """Same delay as regular calls for POST requests."""
        self._throttle()

    def _request_with_retry(self, method: str, path: str, **kwargs: Any) -> Any:
        """Execute an HTTP request with retry on transient errors and auto re-login on 401."""
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                func = getattr(self.session, method)
                t0 = time.time()
                resp = func(
                    self._url(path),
                    headers=kwargs.pop("headers", None) or self._headers(),
                    verify=self.verify_ssl,
                    timeout=self.timeout,
                    **kwargs,
                )
                elapsed = time.time() - t0
                status = resp.status_code
                ok = "OK" if resp.ok else "ERR"
                print(f"    {method.upper():6s} {path:60s} -> {status} {ok} ({elapsed:.1f}s)", file=sys.stderr)

                if status == 401 and attempt < self.max_retries:
                    print(f"    [retry] 401 — re-authenticating (attempt {attempt + 1})", file=sys.stderr)
                    self.login()
                    continue
                if status in (429, 500, 502, 503) and attempt < self.max_retries:
                    wait = min(2 ** attempt, 30)
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        wait = int(retry_after)
                    print(f"    [retry] {status} — waiting {wait}s (attempt {attempt + 1})", file=sys.stderr)
                    time.sleep(wait)
                    continue
                return resp
            except Exception as exc:
                elapsed = time.time() - t0
                print(f"    {method.upper():6s} {path:60s} -> EXCEPTION ({elapsed:.1f}s) {exc}", file=sys.stderr)
                last_exc = exc
                if attempt < self.max_retries:
                    print(f"    [retry] exception — waiting {2 ** attempt}s (attempt {attempt + 1})", file=sys.stderr)
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last_exc  # type: ignore[misc]

    def _extract_search_results(self, data: Any) -> List[Dict[str, Any]]:
        """Normalize search API response to a flat list."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("result", "results", "items", "objects"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
        return []

    # -- search --------------------------------------------------------------

    def search_objects(self, object_type: int, *, offset: int = 0, limit: int = 1000) -> List[Dict[str, Any]]:
        """GET /searches/results with pagination."""
        self._throttle()
        resp = self._request_with_retry(
            "get",
            "/searches/results",
            params={"type": object_type, "offset": offset, "limit": limit},
        )
        self._raise_for_status(resp, f"GET /searches/results?type={object_type}&offset={offset}")
        return self._extract_search_results(resp.json())

    def search_objects_all(self, object_type: int, *, page_size: int = 1000) -> List[Dict[str, Any]]:
        """Paginate through all objects of a given type."""
        all_results: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page = self.search_objects(object_type, offset=offset, limit=page_size)
            if not page:
                break
            all_results.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return all_results

    # -- object info & dependencies ------------------------------------------

    def get_object_info(self, object_id: str, object_type: int) -> Dict[str, Any]:
        """GET /objects/{id}?type={type}"""
        self._throttle()
        resp = self._request_with_retry(
            "get",
            f"/objects/{object_id}",
            params={"type": object_type},
        )
        self._raise_for_status(resp, f"GET /objects/{object_id}")
        return resp.json()

    def get_object_dependents(self, object_id: str, object_type: int) -> List[Dict[str, Any]]:
        """GET /objects/{id}/dependents — objects that USE this object."""
        self._throttle()
        resp = self._request_with_retry(
            "get",
            f"/objects/{object_id}/dependents",
            params={"type": object_type},
        )
        if resp.status_code == 404:
            return []  # endpoint not available on this server version
        self._raise_for_status(resp, f"GET /objects/{object_id}/dependents")
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("dependents", data.get("objects", []))
        return []

    # -- cube definitions & SQL ----------------------------------------------

    def create_report_instance_for_sql(self, report_id: str) -> str:
        """Override parent to apply SQL throttling on this POST call."""
        self._throttle_sql()
        resp = self._request_with_retry(
            "post",
            f"/v2/reports/{report_id}/instances",
            params={"executionStage": "resolve_prompts"},
        )
        self._raise_for_status(resp, f"POST /v2/reports/{report_id}/instances")
        data = resp.json()
        instance_id = data.get("instanceId") or data.get("id")
        if not instance_id:
            raise RuntimeError(f"Could not find instance ID for report {report_id}: {data}")
        return instance_id

    def get_metric_definition(self, metric_id: str) -> Dict[str, Any]:
        """GET /model/metrics/{id}?showExpressionAs=tree — requires Web Modeling privilege."""
        self._throttle()
        resp = self._request_with_retry(
            "get",
            f"/model/metrics/{metric_id}",
            params={"showExpressionAs": "tree"},
        )
        self._raise_for_status(resp, f"GET /model/metrics/{metric_id}")
        return resp.json()

    def get_cube_definition(self, cube_id: str) -> Dict[str, Any]:
        """GET /model/cubes/{id}?showExpressionAs=tree"""
        self._throttle()
        resp = self._request_with_retry(
            "get",
            f"/model/cubes/{cube_id}",
            params={"showExpressionAs": "tree"},
        )
        self._raise_for_status(resp, f"GET /model/cubes/{cube_id}")
        return resp.json()

    def create_cube_instance_for_sql(self, cube_id: str) -> str:
        """POST /v2/cubes/{id}/instances — returns instanceId."""
        self._throttle_sql()
        resp = self._request_with_retry(
            "post",
            f"/v2/cubes/{cube_id}/instances",
            params={"executionStage": "resolve_prompts"},
        )
        self._raise_for_status(resp, f"POST /v2/cubes/{cube_id}/instances")
        data = resp.json()
        instance_id = data.get("instanceId") or data.get("id")
        if not instance_id:
            raise RuntimeError(f"Could not find instance ID for cube {cube_id}: {data}")
        return instance_id

    def get_cube_sql(self, cube_id: str, instance_id: str) -> str:
        """GET /v2/cubes/{id}/instances/{instanceId}/sqlView"""
        self._throttle()
        resp = self._request_with_retry(
            "get",
            f"/v2/cubes/{cube_id}/instances/{instance_id}/sqlView",
        )
        self._raise_for_status(resp, f"GET /v2/cubes/{cube_id}/instances/{instance_id}/sqlView")
        data = resp.json()
        return data.get("sqlStatement", "")

    def delete_report_instance(self, report_id: str, instance_id: str) -> None:
        """DELETE /v2/reports/{id}/instances/{instanceId} — free server resources."""
        try:
            self.session.delete(
                self._url(f"/v2/reports/{report_id}/instances/{instance_id}"),
                headers=self._headers(),
                verify=self.verify_ssl,
                timeout=self.timeout,
            )
        except Exception:
            pass  # best-effort cleanup

    def delete_cube_instance(self, cube_id: str, instance_id: str) -> None:
        """DELETE /v2/cubes/{id}/instances/{instanceId} — free server resources."""
        try:
            self.session.delete(
                self._url(f"/v2/cubes/{cube_id}/instances/{instance_id}"),
                headers=self._headers(),
                verify=self.verify_ssl,
                timeout=self.timeout,
            )
        except Exception:
            pass  # best-effort cleanup

    def get_object_folder_path(self, object_id: str, object_type: int) -> Optional[str]:
        """GET /objects/{id}?type={type} and extract folder path from ancestors."""
        self._throttle()
        try:
            resp = self._request_with_retry(
                "get",
                f"/objects/{object_id}",
                params={"type": object_type},
            )
            if not resp.ok:
                return None
            data = resp.json()
            ancestors = data.get("ancestors", [])
            if not ancestors:
                return None
            # Sort by level descending (root first) and build path
            sorted_anc = sorted(ancestors, key=lambda a: a.get("level", 0), reverse=True)
            return "/".join(a.get("name", "") for a in sorted_anc)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Phase 1.5: Folder paths
# ---------------------------------------------------------------------------

def fetch_folder_paths(
    client: RationalizationClient,
    output_dir: Path,
    inventory: Dict[str, List[Dict[str, Any]]],
    *,
    resume: bool,
    verbose: bool,
    save_interval: int = 100,
) -> None:
    """Fetch folder paths for all objects via GET /objects/{id} ancestors.

    Updates inventory records in place and saves incrementally.
    """
    inv_dir = output_dir / "inventory"
    total = sum(len(recs) for recs in inventory.values())
    processed = 0
    fetched = 0

    for category, records in inventory.items():
        cat_file = inv_dir / f"{category}.json"
        changed = False

        for idx, rec in enumerate(records):
            processed += 1

            # Skip if already has a path
            if rec.get("folderPath"):
                continue

            obj_id = rec.get("id")
            obj_type = rec.get("type")
            if not obj_id or not obj_type:
                continue

            if verbose and processed % 200 == 0:
                print(f"  [paths] {processed}/{total} checked, {fetched} fetched...", file=sys.stderr)

            path = client.get_object_folder_path(obj_id, obj_type)
            if path:
                rec["folderPath"] = path
                changed = True
                fetched += 1

            # Incremental save
            if changed and fetched % save_interval == 0:
                write_json(cat_file, records)
                if verbose:
                    print(f"  [paths save] {fetched} paths saved", file=sys.stderr)

        if changed:
            write_json(cat_file, records)

    if verbose:
        print(f"[paths] Complete. {fetched} folder paths fetched for {total} objects.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Phase 1: Inventory
# ---------------------------------------------------------------------------

def normalize_object_stub(item: Dict[str, Any], category: str) -> Dict[str, Any]:
    """Normalize a search result stub into a consistent inventory record."""
    owner = item.get("owner")
    if isinstance(owner, dict):
        owner = owner.get("name")
    return {
        "id": item.get("id") or item.get("objectId"),
        "name": item.get("name"),
        "type": item.get("type"),
        "subType": item.get("subType"),
        "description": item.get("description"),
        "dateCreated": item.get("dateCreated"),
        "dateModified": item.get("dateModified"),
        "owner": owner,
        "path": item.get("path"),
        "category": category,
        "errors": {},
    }


def _get_sql_with_prompts(client: RationalizationClient, report_id: str) -> Optional[str]:
    """Handle prompted reports: create instance, answer prompts, get SQL, cleanup.

    For each prompt:
    - If it has existing answers (from cached user selections), use those
    - If it has a default answer, use that
    - If it's optional with no answers, skip it
    - If it's required with no answers/default, give up
    """
    instance_id = None
    try:
        # Create instance without executionStage (allows prompt resolution)
        client._throttle()
        resp = client._request_with_retry("post", f"/v2/reports/{report_id}/instances")
        if not resp.ok:
            return None
        data = resp.json()
        instance_id = data.get("instanceId") or data.get("id")
        if not instance_id:
            return None

        # Get prompts (v1 path works, v2 returns 404)
        client._throttle()
        resp2 = client._request_with_retry("get", f"/reports/{report_id}/instances/{instance_id}/prompts")
        if not resp2.ok:
            return None
        prompts = resp2.json()
        if not isinstance(prompts, list) or not prompts:
            return None

        # Build answers
        answer_body: Dict[str, Any] = {"prompts": []}
        can_answer = True
        for p in prompts:
            has_answers = bool(p.get("answers") and p["answers"] != {})
            has_default = bool(p.get("defaultAnswer") and p["defaultAnswer"] != {})
            entry: Dict[str, Any] = {"key": p["key"], "id": p["id"], "type": p["type"]}

            if has_answers:
                entry["answers"] = p["answers"]
            elif has_default:
                entry["useDefault"] = True
            elif not p.get("required"):
                continue  # skip optional prompt with no answer
            else:
                can_answer = False
                break  # required prompt with no answer — give up

            answer_body["prompts"].append(entry)

        if not can_answer:
            return None

        # Submit answers
        client._throttle()
        resp3 = client._request_with_retry(
            "put",
            f"/reports/{report_id}/instances/{instance_id}/prompts/answers",
            json=answer_body,
        )
        if not resp3.ok:
            return None

        # Get SQL
        client._throttle()
        resp4 = client._request_with_retry(
            "get",
            f"/v2/reports/{report_id}/instances/{instance_id}/sqlView",
        )
        if resp4.ok:
            return resp4.json().get("sqlStatement", "")
        return None
    finally:
        if instance_id:
            client.delete_report_instance(report_id, instance_id)


def enrich_report(client: RationalizationClient, record: Dict[str, Any],
                   include_definitions: bool, include_sql: bool) -> None:
    """Fetch rich definition for a report, updating record in place.

    SQL extraction is handled separately by the 'sql' phase for efficiency.
    """
    report_id = record["id"]

    if include_definitions:
        try:
            report_def = client.get_report_definition(report_id)
            info = report_def.get("information", {})
            record["definition"] = {
                "id": info.get("objectId"),
                "name": info.get("name"),
                "subType": info.get("subType"),
                "dateCreated": info.get("dateCreated"),
                "dateModified": info.get("dateModified"),
                "versionId": info.get("versionId"),
            }
            record["filter"] = extract_filter(report_def)
            attrs, metrics = extract_units(report_def)
            record["attributes"] = attrs
            record["metrics"] = metrics

            # Extract source type and ICube reference
            record["sourceType"] = report_def.get("sourceType")
            cube_ref = report_def.get("dataSource", {}).get("cube")
            if cube_ref:
                record["sourceCubeId"] = cube_ref.get("objectId")
        except Exception as exc:
            record["errors"]["definition"] = str(exc)


def enrich_metric(client: RationalizationClient, record: Dict[str, Any],
                   include_definitions: bool, include_sql: bool) -> None:
    """Fetch metric definition (formula/expression) via the Modeling API."""
    metric_id = record["id"]

    if include_definitions:
        try:
            metric_def = client.get_metric_definition(metric_id)
            info = metric_def.get("information", {})
            record["definition"] = {
                "id": info.get("objectId"),
                "name": info.get("name"),
                "subType": info.get("subType"),
                "dateCreated": info.get("dateCreated"),
                "dateModified": info.get("dateModified"),
                "versionId": info.get("versionId"),
            }
            # Extract formula expression
            expression = metric_def.get("expression")
            if expression:
                record["expression"] = expression
            # Extract formula text if available
            tokens = metric_def.get("dimty", {})
            if tokens:
                record["dimty"] = tokens
        except Exception as exc:
            record["errors"]["definition"] = str(exc)


def enrich_cube(client: RationalizationClient, record: Dict[str, Any],
                include_definitions: bool, include_sql: bool) -> None:
    """Fetch rich definition and/or SQL for a cube, updating record in place."""
    cube_id = record["id"]

    if include_definitions:
        try:
            cube_def = client.get_cube_definition(cube_id)
            info = cube_def.get("information", {})
            record["definition"] = {
                "id": info.get("objectId"),
                "name": info.get("name"),
                "subType": info.get("subType"),
                "dateCreated": info.get("dateCreated"),
                "dateModified": info.get("dateModified"),
                "versionId": info.get("versionId"),
            }
            record["filter"] = extract_filter(cube_def)
            attrs, metrics = extract_units(cube_def)
            record["attributes"] = attrs
            record["metrics"] = metrics
        except Exception as exc:
            record["errors"]["definition"] = str(exc)

    if include_sql:
        instance_id = None
        try:
            instance_id = client.create_cube_instance_for_sql(cube_id)
            sql_text = client.get_cube_sql(cube_id, instance_id)
            record["sql"] = sql_text
            record["sqlHash"] = hash_sql(sql_text)
            record["sourceTables"] = parse_table_names_from_sql(sql_text)
        except Exception as exc:
            err_msg = str(exc)
            if "open prompts" in err_msg.lower() or "prompt" in err_msg.lower():
                record["errors"]["sql"] = "prompted (requires user input)"
                record["prompted"] = True
            else:
                record["errors"]["sql"] = err_msg
        finally:
            if instance_id:
                client.delete_cube_instance(cube_id, instance_id)


def _is_enriched(record: Dict[str, Any]) -> bool:
    """Check if a record has already been enriched (has definition, sql, expression, or errors)."""
    if record.get("definition"):
        return True
    if record.get("sql"):
        return True
    if record.get("expression"):
        return True
    errors = record.get("errors", {})
    if errors.get("definition") or errors.get("sql"):
        return True
    return False


def build_inventory(
    client: RationalizationClient,
    output_dir: Path,
    *,
    include_definitions: bool,
    include_sql: bool,
    skip_types: Set[str],
    limit: Optional[int],
    resume: bool,
    verbose: bool,
    save_interval: int = 100,
) -> Dict[str, List[Dict[str, Any]]]:
    """Phase 1: enumerate all object types and optionally enrich reports/cubes.

    Saves progress incrementally every `save_interval` objects during enrichment,
    so interrupted runs can be resumed without losing work.
    """
    inv_dir = output_dir / "inventory"
    inv_dir.mkdir(parents=True, exist_ok=True)

    inventory: Dict[str, List[Dict[str, Any]]] = {}

    for category, spec in OBJECT_TYPES.items():
        if category in skip_types:
            if verbose:
                print(f"[inventory] Skipping {category} (--skip-types)", file=sys.stderr)
            continue

        cat_file = inv_dir / f"{category}.json"
        needs_enrichment = category in ("reports", "cubes") and (include_definitions or include_sql)

        # Resume: load existing partial/complete data
        if resume and cat_file.exists():
            existing = json.loads(cat_file.read_text(encoding="utf-8"))
            if existing:
                if needs_enrichment:
                    # Check if all records are enriched (complete) or some still need work
                    unenriched = sum(1 for r in existing if not _is_enriched(r))
                    if unenriched == 0:
                        if verbose:
                            print(f"[inventory] Resuming {category} from {cat_file} (complete, {len(existing)} objects)", file=sys.stderr)
                        inventory[category] = existing
                        continue
                    else:
                        if verbose:
                            print(f"[inventory] Resuming {category} — {len(existing) - unenriched}/{len(existing)} already enriched, {unenriched} remaining", file=sys.stderr)
                        # Use existing records and enrich the remaining ones below
                        records = existing
                else:
                    if verbose:
                        print(f"[inventory] Resuming {category} from {cat_file} ({len(existing)} objects)", file=sys.stderr)
                    inventory[category] = existing
                    continue
        else:
            records = None

        # If we don't have records yet (no resume data), fetch them
        if records is None:
            if verbose:
                print(f"[inventory] Enumerating {category} (type={spec['type']})...", file=sys.stderr)

            raw = client.search_objects_all(spec["type"])

            required_subtype = spec.get("subtype")
            if required_subtype is not None:
                raw = [obj for obj in raw if obj.get("subType") == required_subtype]

            records = [normalize_object_stub(obj, category) for obj in raw]
            records = [r for r in records if r.get("id") and r.get("name")]

            if limit is not None:
                records = records[:limit]

            if verbose:
                print(f"[inventory] Found {len(records)} {category}", file=sys.stderr)

        # Enrich reports, cubes, and metrics with incremental saves
        if needs_enrichment:
            if category == "reports":
                enrich_fn = enrich_report
            elif category == "cubes":
                enrich_fn = enrich_cube
            elif category == "metrics":
                enrich_fn = enrich_metric
            else:
                enrich_fn = enrich_report  # fallback
            label = category.rstrip("s")
            enriched_count = 0

            for idx, rec in enumerate(records, 1):
                if _is_enriched(rec):
                    enriched_count += 1
                    continue  # Already enriched (from resume)

                if verbose:
                    print(f"\n  [{idx}/{len(records)}] {rec['name']}", file=sys.stderr)
                t0 = time.time()
                enrich_fn(client, rec, include_definitions, include_sql)
                elapsed = time.time() - t0
                enriched_count += 1

                # Print result summary
                if verbose:
                    errors = rec.get("errors", {})
                    parts = []
                    if rec.get("definition"):
                        parts.append("def:OK")
                    elif errors.get("definition"):
                        parts.append(f"def:ERR")
                    if rec.get("sql"):
                        sql_len = len(rec["sql"])
                        parts.append(f"sql:OK({sql_len}ch)")
                    elif errors.get("sql"):
                        parts.append(f"sql:ERR")
                    if rec.get("prompted"):
                        parts.append("prompted")
                    if rec.get("sourceTables"):
                        parts.append(f"tables:{len(rec['sourceTables'])}")
                    print(f"    -> {' | '.join(parts)} [{elapsed:.1f}s]", file=sys.stderr)

                # Incremental save
                if enriched_count % save_interval == 0:
                    write_json(cat_file, records)
                    if verbose:
                        print(f"  [save] {enriched_count}/{len(records)} saved to disk", file=sys.stderr)

        inventory[category] = records
        write_json(cat_file, records)

    # Write combined inventory
    combined: List[Dict[str, Any]] = []
    for cat_records in inventory.values():
        combined.extend(cat_records)
    write_json(output_dir / "inventory_combined.json", combined)

    return inventory


# ---------------------------------------------------------------------------
# Phase 2: Dependency graph
# ---------------------------------------------------------------------------

def build_dependency_graph(
    client: RationalizationClient,
    output_dir: Path,
    inventory: Dict[str, List[Dict[str, Any]]],
    *,
    skip_types: Set[str],
    dep_batch_size: int = 50,
    dep_batch_delay: float = 3.0,
    resume: bool,
    verbose: bool,
) -> Dict[str, Any]:
    """Phase 2: build dependency graph.

    Strategy:
    1. Try the /objects/{id}/dependents API on a sample object.
    2. If it returns 404 (not supported), fall back to inferring
       dependencies from report/cube definitions (attributes, metrics,
       filters extracted during Phase 1).
    3. If the API works, use it for all objects with batch throttling.
    """
    dep_file = output_dir / "dependencies.json"

    graph: Dict[str, Any] = {
        "edges": [],
        "dependents_of": defaultdict(list),
        "dependencies_of": defaultdict(list),
        "processed_ids": set(),
    }

    if resume and dep_file.exists():
        existing = json.loads(dep_file.read_text(encoding="utf-8"))
        graph["edges"] = existing.get("edges", [])
        graph["processed_ids"] = set(existing.get("processed_ids", []))
        for edge in graph["edges"]:
            graph["dependents_of"][edge["target"]].append(edge["source"])
            graph["dependencies_of"][edge["source"]].append(edge["target"])
        if verbose:
            print(f"[dependencies] Resumed with {len(graph['processed_ids'])} already processed", file=sys.stderr)

    # Probe the API with a sample object to see if /dependents is available
    api_available = _probe_dependents_api(client, inventory, verbose)

    if api_available:
        _build_deps_from_api(client, graph, inventory, skip_types,
                             dep_batch_size, dep_batch_delay, dep_file, verbose)
    else:
        if verbose:
            print("[dependencies] API not available, inferring from definitions...", file=sys.stderr)
        _build_deps_from_definitions(graph, inventory, verbose)

    _save_dependency_graph(dep_file, graph)

    if verbose:
        print(f"[dependencies] Complete. {len(graph['edges'])} edges.", file=sys.stderr)

    return graph


def _probe_dependents_api(client: RationalizationClient,
                          inventory: Dict[str, List[Dict[str, Any]]],
                          verbose: bool) -> bool:
    """Try the /dependents endpoint on a sample object. Return True if it works."""
    for category in ("reports", "metrics", "attributes"):
        records = inventory.get(category, [])
        if records:
            sample = records[0]
            try:
                resp = client._request_with_retry(
                    "get",
                    f"/objects/{sample['id']}/dependents",
                    params={"type": sample["type"]},
                )
                if resp.status_code == 404:
                    if verbose:
                        print(f"[dependencies] /dependents API returned 404 — not available on this server", file=sys.stderr)
                    return False
                if resp.ok:
                    if verbose:
                        print(f"[dependencies] /dependents API is available", file=sys.stderr)
                    return True
            except Exception:
                pass
    return False


def _build_deps_from_api(
    client: RationalizationClient,
    graph: Dict[str, Any],
    inventory: Dict[str, List[Dict[str, Any]]],
    skip_types: Set[str],
    dep_batch_size: int,
    dep_batch_delay: float,
    dep_file: Path,
    verbose: bool,
) -> None:
    """Build dependency graph using the /dependents REST API."""
    all_objects: List[Dict[str, Any]] = []
    for category, records in inventory.items():
        if category in skip_types:
            continue
        all_objects.extend(records)

    total = len(all_objects)
    errors = 0

    for idx, obj in enumerate(all_objects, 1):
        obj_id = obj["id"]
        obj_type = obj["type"]

        if obj_id in graph["processed_ids"]:
            continue

        if verbose and idx % 50 == 0:
            print(f"[dependencies] {idx}/{total} objects processed...", file=sys.stderr)

        if idx > 1 and idx % dep_batch_size == 0:
            if verbose:
                print(f"  [throttle] Pausing {dep_batch_delay}s after {idx} dependency lookups...", file=sys.stderr)
            time.sleep(dep_batch_delay)

        try:
            dependents = client.get_object_dependents(obj_id, obj_type)
            for dep in dependents:
                dep_id = dep.get("id") or dep.get("objectId")
                if dep_id:
                    _add_edge(graph, source=dep_id, source_type=dep.get("type"),
                              target=obj_id, target_type=obj_type)
        except Exception as exc:
            errors += 1
            if verbose:
                print(f"  [error] Dependents for {obj.get('name', obj_id)}: {exc}", file=sys.stderr)

        graph["processed_ids"].add(obj_id)

        if idx % 100 == 0:
            _save_dependency_graph(dep_file, graph)

    if verbose and errors:
        print(f"[dependencies] API errors: {errors}", file=sys.stderr)


def _build_deps_from_definitions(
    graph: Dict[str, Any],
    inventory: Dict[str, List[Dict[str, Any]]],
    verbose: bool,
) -> None:
    """Infer dependencies from report/cube definitions collected in Phase 1.

    Each report/cube definition contains attributes and metrics it uses.
    We match those IDs against the inventory to build edges:
        report/cube -> uses -> metric
        report/cube -> uses -> attribute
        report/cube -> uses -> filter (from filter.tree)
    """
    # Build a lookup of all known object IDs
    known_ids: Dict[str, Dict[str, Any]] = {}
    for records in inventory.values():
        for obj in records:
            known_ids[obj["id"]] = obj

    edge_count = 0

    for category in ("reports", "cubes"):
        records = inventory.get(category, [])
        for obj in records:
            obj_id = obj["id"]
            obj_type = obj.get("type")

            # Extract attribute IDs this report/cube uses
            for attr in obj.get("attributes", []):
                attr_id = attr.get("id")
                if attr_id and attr_id in known_ids:
                    _add_edge(graph, source=obj_id, source_type=obj_type,
                              target=attr_id, target_type=known_ids[attr_id].get("type"))
                    edge_count += 1

            # Extract metric IDs this report/cube uses
            for metric in obj.get("metrics", []):
                metric_id = metric.get("id")
                if metric_id and metric_id in known_ids:
                    _add_edge(graph, source=obj_id, source_type=obj_type,
                              target=metric_id, target_type=known_ids[metric_id].get("type"))
                    edge_count += 1

            # Extract filter references from filter tree
            filter_ids = _extract_ids_from_filter_tree(obj.get("filter", {}))
            for fid in filter_ids:
                if fid in known_ids:
                    _add_edge(graph, source=obj_id, source_type=obj_type,
                              target=fid, target_type=known_ids[fid].get("type"))
                    edge_count += 1

    if verbose:
        print(f"[dependencies] Inferred {edge_count} edges from definitions", file=sys.stderr)


def _extract_ids_from_filter_tree(filter_data: Dict[str, Any]) -> List[str]:
    """Recursively extract object IDs from a filter expression tree."""
    ids: List[str] = []
    if not filter_data:
        return ids

    tree = filter_data.get("tree")
    if not tree:
        return ids

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            obj_id = node.get("objectId") or node.get("id")
            if obj_id and isinstance(obj_id, str) and len(obj_id) > 10:
                ids.append(obj_id)
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(tree)
    return ids


def _add_edge(graph: Dict[str, Any], *, source: str, source_type: Any,
              target: str, target_type: Any) -> None:
    """Add a directed edge: source depends on target."""
    edge = {
        "source": source,
        "source_type": source_type,
        "target": target,
        "target_type": target_type,
    }
    graph["edges"].append(edge)
    graph["dependents_of"][target].append(source)
    graph["dependencies_of"][source].append(target)


def _save_dependency_graph(path: Path, graph: Dict[str, Any]) -> None:
    """Serialize the dependency graph to JSON (converting sets to lists)."""
    serializable = {
        "edges": graph["edges"],
        "dependents_of": dict(graph["dependents_of"]),
        "dependencies_of": dict(graph["dependencies_of"]),
        "processed_ids": list(graph["processed_ids"]),
    }
    write_json(path, serializable)


def load_dependency_graph(output_dir: Path) -> Dict[str, Any]:
    """Load a previously saved dependency graph."""
    dep_file = output_dir / "dependencies.json"
    if not dep_file.exists():
        return {"edges": [], "dependents_of": {}, "dependencies_of": {}, "processed_ids": []}
    data = json.loads(dep_file.read_text(encoding="utf-8"))
    return data


# ---------------------------------------------------------------------------
# Phase 3: Analysis
# ---------------------------------------------------------------------------

def hash_sql(sql: str) -> str:
    """Normalize SQL and return a SHA-256 hash for duplicate detection."""
    if not sql:
        return ""
    normalized = sql.lower()
    # Strip comments
    normalized = re.sub(r"--[^\n]*", "", normalized)
    normalized = re.sub(r"/\*.*?\*/", "", normalized, flags=re.DOTALL)
    # Collapse whitespace
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # Replace string literals with placeholder
    normalized = re.sub(r"'[^']*'", "'?'", normalized)
    # Replace numeric literals (standalone numbers) with placeholder
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "0", normalized)
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _normalize_sql_preview(sql: str) -> str:
    """Return first 200 chars of normalized SQL for display."""
    if not sql:
        return ""
    normalized = sql.lower()
    normalized = re.sub(r"--[^\n]*", "", normalized)
    normalized = re.sub(r"/\*.*?\*/", "", normalized, flags=re.DOTALL)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:200]


def detect_orphans(
    inventory: Dict[str, List[Dict[str, Any]]],
    graph: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Find objects that nothing depends on (excluding leaf consumers)."""
    dependents_of = graph.get("dependents_of", {})
    orphans: List[Dict[str, Any]] = []

    for category, records in inventory.items():
        if category in LEAF_CATEGORIES:
            continue  # reports/documents/cubes are leaf objects by design
        for obj in records:
            obj_id = obj["id"]
            deps = dependents_of.get(obj_id, [])
            if not deps:
                orphans.append({
                    "id": obj_id,
                    "name": obj.get("name"),
                    "type": obj.get("type"),
                    "subType": obj.get("subType"),
                    "category": category,
                    "owner": obj.get("owner"),
                    "path": obj.get("path"),
                    "dateModified": obj.get("dateModified"),
                })
    return orphans


def detect_duplicate_sql(
    inventory: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Group reports/cubes by normalized SQL hash to find duplicates."""
    hash_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for category in SQL_CATEGORIES:
        for obj in inventory.get(category, []):
            sql_hash = obj.get("sqlHash")
            if sql_hash:
                hash_groups[sql_hash].append({
                    "id": obj["id"],
                    "name": obj.get("name"),
                    "category": category,
                    "path": obj.get("path"),
                    "sql_preview": _normalize_sql_preview(obj.get("sql", "")),
                })

    duplicates: List[Dict[str, Any]] = []
    for sql_hash, members in hash_groups.items():
        if len(members) >= 2:
            duplicates.append({
                "sqlHash": sql_hash,
                "normalizedSqlPreview": members[0].get("sql_preview", ""),
                "count": len(members),
                "objects": members,
            })

    duplicates.sort(key=lambda g: g["count"], reverse=True)
    return duplicates


def find_high_impact(
    inventory: Dict[str, List[Dict[str, Any]]],
    graph: Dict[str, Any],
    top_n: int = 50,
) -> List[Dict[str, Any]]:
    """Find objects with the most dependents (highest downstream impact)."""
    dependents_of = graph.get("dependents_of", {})
    all_objects: Dict[str, Dict[str, Any]] = {}
    for records in inventory.values():
        for obj in records:
            all_objects[obj["id"]] = obj

    impact: List[Dict[str, Any]] = []
    for obj_id, dep_ids in dependents_of.items():
        obj = all_objects.get(obj_id)
        if obj and len(dep_ids) > 0:
            impact.append({
                "id": obj_id,
                "name": obj.get("name"),
                "type": obj.get("type"),
                "category": obj.get("category"),
                "path": obj.get("path"),
                "dependentCount": len(dep_ids),
            })

    impact.sort(key=lambda x: x["dependentCount"], reverse=True)
    return impact[:top_n]


def find_stale_objects(
    inventory: Dict[str, List[Dict[str, Any]]],
    stale_days: int = 365,
) -> List[Dict[str, Any]]:
    """Find objects not modified within the staleness threshold."""
    now = datetime.now(timezone.utc)
    stale: List[Dict[str, Any]] = []

    for records in inventory.values():
        for obj in records:
            date_str = obj.get("dateModified")
            if not date_str:
                continue
            try:
                # MicroStrategy dates: "2024-01-15T10:30:00.000+0000" or ISO
                cleaned = date_str.replace("Z", "+00:00")
                dt = datetime.fromisoformat(cleaned)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta = (now - dt).days
                if delta >= stale_days:
                    stale.append({
                        "id": obj["id"],
                        "name": obj.get("name"),
                        "type": obj.get("type"),
                        "category": obj.get("category"),
                        "owner": obj.get("owner"),
                        "path": obj.get("path"),
                        "dateModified": date_str,
                        "daysSinceModified": delta,
                    })
            except (ValueError, TypeError):
                continue

    stale.sort(key=lambda x: x.get("daysSinceModified", 0), reverse=True)
    return stale


def find_unused_metrics(
    inventory: Dict[str, List[Dict[str, Any]]],
    graph: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Find metrics not referenced by any report or cube."""
    dependents_of = graph.get("dependents_of", {})
    unused: List[Dict[str, Any]] = []

    for obj in inventory.get("metrics", []):
        obj_id = obj["id"]
        deps = dependents_of.get(obj_id, [])
        if not deps:
            unused.append({
                "id": obj_id,
                "name": obj.get("name"),
                "owner": obj.get("owner"),
                "path": obj.get("path"),
                "dateModified": obj.get("dateModified"),
            })
    return unused


def run_analysis(
    output_dir: Path,
    inventory: Dict[str, List[Dict[str, Any]]],
    graph: Dict[str, Any],
    *,
    stale_days: int,
    top_impact: int,
    verbose: bool,
) -> Dict[str, Any]:
    """Phase 3: run all analysis and write results."""
    analysis_dir = output_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    if verbose:
        print("[analysis] Detecting orphans...", file=sys.stderr)
    orphans = detect_orphans(inventory, graph)
    write_json(analysis_dir / "orphans.json", orphans)

    if verbose:
        print("[analysis] Detecting duplicate SQL...", file=sys.stderr)
    duplicates = detect_duplicate_sql(inventory)
    write_json(analysis_dir / "duplicate_sql.json", duplicates)

    if verbose:
        print("[analysis] Finding high-impact objects...", file=sys.stderr)
    high_impact = find_high_impact(inventory, graph, top_n=top_impact)
    write_json(analysis_dir / "high_impact.json", high_impact)

    if verbose:
        print("[analysis] Finding stale objects...", file=sys.stderr)
    stale = find_stale_objects(inventory, stale_days=stale_days)
    write_json(analysis_dir / "stale_objects.json", stale)

    if verbose:
        print("[analysis] Finding unused metrics...", file=sys.stderr)
    unused_metrics = find_unused_metrics(inventory, graph)
    write_json(analysis_dir / "unused_metrics.json", unused_metrics)

    # Summary
    total_objects = sum(len(recs) for recs in inventory.values())
    by_category = {cat: len(recs) for cat, recs in inventory.items()}
    error_count = sum(
        1 for recs in inventory.values() for obj in recs if obj.get("errors")
    )

    summary = {
        "totalObjects": total_objects,
        "byCategory": by_category,
        "totalEdges": len(graph.get("edges", [])),
        "orphanCount": len(orphans),
        "duplicateSqlGroupCount": len(duplicates),
        "duplicateSqlObjectCount": sum(g["count"] for g in duplicates),
        "highImpactCount": len(high_impact),
        "staleObjectCount": len(stale),
        "unusedMetricCount": len(unused_metrics),
        "errorCount": error_count,
    }
    write_json(analysis_dir / "summary.json", summary)

    if verbose:
        print(f"[analysis] Summary: {json.dumps(summary, indent=2)}", file=sys.stderr)

    # Combined rationalization report for frontend (includes inventory + deps for drill-down)
    # Build a slim inventory lookup: id -> {name, category, type, owner}
    objects_lookup: Dict[str, Dict[str, Any]] = {}
    for records in inventory.values():
        for obj in records:
            entry: Dict[str, Any] = {
                "id": obj["id"],
                "name": obj.get("name"),
                "category": obj.get("category"),
                "type": obj.get("type"),
                "owner": obj.get("owner"),
                "dateModified": obj.get("dateModified"),
            }
            if obj.get("folderPath"):
                entry["folderPath"] = obj["folderPath"]
            objects_lookup[obj["id"]] = entry

    # Collect all errors for the errors tab
    error_list: List[Dict[str, Any]] = []
    for records in inventory.values():
        for obj in records:
            errors = obj.get("errors", {})
            if errors:
                for err_type, err_msg in errors.items():
                    error_list.append({
                        "id": obj["id"],
                        "name": obj.get("name"),
                        "category": obj.get("category"),
                        "errorType": err_type,
                        "message": err_msg[:300],  # truncate long error messages
                    })

    report = {
        "summary": summary,
        "orphans": orphans,
        "duplicateSql": duplicates,
        "highImpact": high_impact,
        "staleObjects": stale,
        "unusedMetrics": unused_metrics,
        "errors": error_list,
        "objects": objects_lookup,
        "dependentsOf": graph.get("dependents_of", {}),
        "dependenciesOf": graph.get("dependencies_of", {}),
    }
    write_json(output_dir / "rationalization_report.json", report)

    return summary


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Phase: SQL extraction (targeted, with ICube dedup)
# ---------------------------------------------------------------------------

def _extract_sql_for_report(client: RationalizationClient, record: Dict[str, Any]) -> None:
    """Extract SQL for a single report. Skips prompted reports (no resolution attempt). Updates record in place."""
    report_id = record["id"]
    instance_id = None
    try:
        instance_id = client.create_report_instance_for_sql(report_id)
        sql_text = client.get_report_sql(report_id, instance_id)
        record["sql"] = sql_text
        record["sqlHash"] = hash_sql(sql_text)
        record["sourceTables"] = parse_table_names_from_sql(sql_text)
    except Exception as exc:
        err_msg = str(exc)
        if "open prompts" in err_msg.lower() or "prompt" in err_msg.lower():
            # Fail fast — no attempt to resolve prompts (saves 4 API calls per prompted report)
            record["errors"]["sql"] = "prompted (skipped)"
            record["prompted"] = True
        else:
            record["errors"]["sql"] = err_msg
    finally:
        if instance_id:
            client.delete_report_instance(report_id, instance_id)


def _extract_sql_for_cube(client: RationalizationClient, cube_id: str) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Extract SQL for a cube. Returns (sql_text, sql_hash, source_tables)."""
    instance_id = None
    try:
        instance_id = client.create_cube_instance_for_sql(cube_id)
        sql_text = client.get_cube_sql(cube_id, instance_id)
        return sql_text, hash_sql(sql_text), parse_table_names_from_sql(sql_text)
    except Exception:
        return None, None, []
    finally:
        if instance_id:
            client.delete_cube_instance(cube_id, instance_id)


def build_sql_extraction_plan(
    inventory: Dict[str, List[Dict[str, Any]]],
    orphan_ids: Set[str],
    verbose: bool,
    target_ids: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Analyze reports and build a plan for targeted SQL extraction.

    Returns a plan dict with:
    - by_source_type: counts per sourceType
    - cube_groups: {cube_id: [report_ids]} for ICube dedup
    - individual_reports: report IDs needing individual SQL extraction
    - skip_ids: orphan/already-has-sql report IDs to skip
    - savings: estimated call reduction

    If target_ids is provided, only those report IDs are considered.
    """
    reports = inventory.get("reports", [])

    by_source_type: Dict[str, int] = {}
    cube_groups: Dict[str, List[str]] = {}  # cube_id -> [report_ids]
    individual_reports: List[str] = []
    skip_ids: Set[str] = set()
    already_have_sql = 0

    for rec in reports:
        rid = rec["id"]
        src = rec.get("sourceType", "unknown")

        # Skip if not in target set
        if target_ids is not None and rid not in target_ids:
            skip_ids.add(rid)
            continue

        by_source_type[src] = by_source_type.get(src, 0) + 1

        # Skip if already has SQL
        if rec.get("sql"):
            already_have_sql += 1
            skip_ids.add(rid)
            continue

        # Skip if prior SQL error recorded (resume: don't retry known failures)
        if rec.get("errors", {}).get("sql"):
            skip_ids.add(rid)
            continue

        # Skip orphans
        if rid in orphan_ids:
            skip_ids.add(rid)
            continue

        # Skip if prior definition error (report is broken — SQL extraction will 500)
        if rec.get("errors", {}).get("definition"):
            rec.setdefault("errors", {})["sql"] = "Skipped: prior definition error (likely 500)"
            skip_ids.add(rid)
            continue

        # Skip if sourceType is cube (cube-sourced — can't extract SQL)
        if src == "cube":
            rec.setdefault("errors", {})["sql"] = "Skipped: cube-sourced (sourceType=cube)"
            skip_ids.add(rid)
            continue

        # Group by ICube (sourceCubeId set)
        cube_id = rec.get("sourceCubeId")
        if cube_id:
            cube_groups.setdefault(cube_id, []).append(rid)
        else:
            individual_reports.append(rid)

    cube_report_count = sum(len(rids) for rids in cube_groups.values())
    unique_cubes = len(cube_groups)
    total_reports = len(reports)
    total_post_calls = unique_cubes + len(individual_reports)
    naive_post_calls = total_reports - already_have_sql - len(orphan_ids & {r["id"] for r in reports})

    plan = {
        "totalReports": total_reports,
        "bySourceType": by_source_type,
        "cubeGroups": cube_groups,
        "uniqueCubes": unique_cubes,
        "cubeReportCount": cube_report_count,
        "individualReports": individual_reports,
        "skipIds": skip_ids,
        "alreadyHaveSql": already_have_sql,
        "orphanSkipped": len(orphan_ids & {r["id"] for r in reports}),
        "totalPostCalls": total_post_calls,
        "naivePostCalls": naive_post_calls,
        "estimatedTimeSec": total_post_calls * 5,
    }

    if verbose:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  SQL EXTRACTION PLAN", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(f"  Total reports:           {total_reports}", file=sys.stderr)
        print(f"  Already have SQL:        {already_have_sql}", file=sys.stderr)
        print(f"  Orphans (skip):          {plan['orphanSkipped']}", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"  Reports by source type:", file=sys.stderr)
        for src, count in sorted(by_source_type.items(), key=lambda x: -x[1]):
            print(f"    {src:25s} {count}", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"  ICube-sourced reports:    {cube_report_count}", file=sys.stderr)
        print(f"  Unique ICubes:           {unique_cubes} (1 SQL call each)", file=sys.stderr)
        if unique_cubes > 0:
            avg = cube_report_count / unique_cubes
            print(f"  Avg reports per ICube:   {avg:.1f}", file=sys.stderr)
        print(f"  Individual SQL needed:   {len(individual_reports)}", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"  TOTAL POST calls:        {total_post_calls}", file=sys.stderr)
        print(f"  Without optimization:    {naive_post_calls}", file=sys.stderr)
        if naive_post_calls > 0:
            saved = naive_post_calls - total_post_calls
            print(f"  Calls saved:             {saved} ({saved/naive_post_calls*100:.0f}%)", file=sys.stderr)
        est_min = total_post_calls * 5 / 60
        print(f"  Estimated time:          ~{est_min:.0f} minutes", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

    return plan


def execute_sql_phase(
    client: RationalizationClient,
    output_dir: Path,
    inventory: Dict[str, List[Dict[str, Any]]],
    plan: Dict[str, Any],
    *,
    skip_types: Set[str],
    verbose: bool,
    save_interval: int = 50,
) -> None:
    """Execute targeted SQL extraction based on the plan."""
    inv_dir = output_dir / "inventory"
    reports = inventory.get("reports", [])
    report_lookup = {r["id"]: r for r in reports}

    cube_groups = plan["cubeGroups"]
    individual_reports = plan["individualReports"]
    skip_ids = plan["skipIds"]

    # If cubes are skipped, skip cube-sourced reports entirely (they can't generate SQL individually)
    if "cubes" in skip_types and cube_groups:
        cube_report_count = sum(len(v) for v in cube_groups.values())
        print(f"[sql] Skipping {cube_report_count} ICube-sourced reports ({len(cube_groups)} cubes) — cube instances return 500 on this server", file=sys.stderr)
        for report_ids in cube_groups.values():
            for rid in report_ids:
                rec = report_lookup.get(rid)
                if rec:
                    rec["errors"]["sql"] = "Skipped: cube-sourced report (cube instance not available)"
        cube_groups = {}

    extracted = 0
    errors = 0

    # 1. Extract SQL per unique ICube and assign to all reports using it
    if cube_groups:
        print(f"\n[sql] Extracting SQL for {len(cube_groups)} unique ICubes...", file=sys.stderr)
        for idx, (cube_id, report_ids) in enumerate(cube_groups.items(), 1):
            if verbose:
                cube_name = cube_id[:16]  # short ID for display
                # Try to find cube name from inventory
                for c in inventory.get("cubes", []):
                    if c["id"] == cube_id:
                        cube_name = c["name"]
                        break
                print(f"\n  [cube {idx}/{len(cube_groups)}] {cube_name} -> {len(report_ids)} reports", file=sys.stderr)

            sql_text, sql_hash, source_tables = _extract_sql_for_cube(client, cube_id)

            if sql_text:
                # Assign SQL to all reports using this cube
                for rid in report_ids:
                    rec = report_lookup.get(rid)
                    if rec:
                        rec["sql"] = sql_text
                        rec["sqlHash"] = sql_hash
                        rec["sourceTables"] = source_tables
                extracted += 1
                if verbose:
                    print(f"    -> sql:OK({len(sql_text)}ch) | assigned to {len(report_ids)} reports", file=sys.stderr)
            else:
                errors += 1
                for rid in report_ids:
                    rec = report_lookup.get(rid)
                    if rec:
                        rec["errors"]["sql"] = f"ICube {cube_id} SQL extraction failed"
                if verbose:
                    print(f"    -> sql:ERR", file=sys.stderr)

            if extracted % save_interval == 0 and extracted > 0:
                write_json(inv_dir / "reports.json", reports)

    # 2. Extract SQL individually for non-cube reports
    if individual_reports:
        print(f"\n[sql] Extracting SQL for {len(individual_reports)} individual reports...", file=sys.stderr)
        for idx, rid in enumerate(individual_reports, 1):
            rec = report_lookup.get(rid)
            if not rec or rec.get("sql"):
                continue

            if verbose:
                print(f"\n  [{idx}/{len(individual_reports)}] {rec['name']}", file=sys.stderr)

            t0 = time.time()
            _extract_sql_for_report(client, rec)
            elapsed = time.time() - t0
            extracted += 1

            if verbose:
                if rec.get("sql"):
                    sql_len = len(rec["sql"])
                    parts = [f"sql:OK({sql_len}ch)"]
                    if rec.get("prompted"):
                        parts.append("prompted")
                    if rec.get("sourceTables"):
                        parts.append(f"tables:{len(rec['sourceTables'])}")
                    print(f"    -> {' | '.join(parts)} [{elapsed:.1f}s]", file=sys.stderr)
                else:
                    print(f"    -> sql:ERR [{elapsed:.1f}s]", file=sys.stderr)
                    errors += 1

            if extracted % save_interval == 0:
                write_json(inv_dir / "reports.json", reports)

    # Final save
    write_json(inv_dir / "reports.json", reports)

    print(f"\n[sql] Complete. {extracted} extractions, {errors} errors.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Run manifest (tracks completed phases for resume)
# ---------------------------------------------------------------------------

def load_manifest(output_dir: Path) -> Dict[str, Any]:
    manifest_file = output_dir / "run_manifest.json"
    if manifest_file.exists():
        return json.loads(manifest_file.read_text(encoding="utf-8"))
    return {"completedPhases": [], "startedAt": None, "project": None}


def save_manifest(output_dir: Path, manifest: Dict[str, Any]) -> None:
    write_json(output_dir / "run_manifest.json", manifest)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MicroStrategy project rationalization: inventory, dependencies, and analysis."
    )
    # Connection
    parser.add_argument("--base-url", required=True,
                        help="MicroStrategy Library REST API base URL")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default=os.getenv("MSTR_PASSWORD", ""))
    parser.add_argument("--login-mode", type=int, default=1)
    parser.add_argument("--project-id", help="Project GUID")
    parser.add_argument("--project-name", help="Project name")
    parser.add_argument("--verify-ssl", action="store_true", default=False)
    parser.add_argument("--timeout", type=int, default=60)

    # Phases & scope
    parser.add_argument("--phases", default="inventory,paths,dependencies,analysis",
                        help="Comma-separated phases to run (default: all)")
    parser.add_argument("--skip-types", default="",
                        help="Comma-separated categories to skip (e.g. documents,prompts)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap objects per category (useful for testing)")

    # Enrichment
    parser.add_argument("--include-sql", action="store_true",
                        help="Fetch SQL for reports and cubes")
    parser.add_argument("--include-definitions", action="store_true",
                        help="Fetch rich model definitions for reports and cubes")

    # Analysis tuning
    parser.add_argument("--stale-days", type=int, default=365,
                        help="Days since last modification to flag as stale (default: 365)")
    parser.add_argument("--top-impact", type=int, default=50,
                        help="Number of high-impact objects to report (default: 50)")

    # Execution & throttling
    parser.add_argument("--delay", type=float, default=0.1,
                        help="Seconds between GET API calls (default: 0.1)")
    parser.add_argument("--sql-batch-size", type=int, default=10,
                        help="Number of SQL POST calls per batch before pausing (default: 10)")
    parser.add_argument("--sql-batch-delay", type=float, default=5.0,
                        help="Seconds to pause between SQL batches (default: 5)")
    parser.add_argument("--dep-batch-size", type=int, default=50,
                        help="Number of dependency lookups per batch before pausing (default: 50)")
    parser.add_argument("--dep-batch-delay", type=float, default=3.0,
                        help="Seconds to pause between dependency batches (default: 3)")
    parser.add_argument("--output-dir", default="mstr_rationalization")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from previously saved progress")
    parser.add_argument("--verbose", action="store_true")

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    for p in phases:
        if p not in ALL_PHASES:
            print(f"Unknown phase: {p}. Valid phases: {', '.join(ALL_PHASES)}", file=sys.stderr)
            return 1

    skip_types = {s.strip() for s in args.skip_types.split(",") if s.strip()}
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(output_dir) if args.resume else {"completedPhases": [], "startedAt": None, "project": None}

    client = RationalizationClient(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        login_mode=args.login_mode,
        verify_ssl=args.verify_ssl,
        timeout=args.timeout,
        delay=args.delay,
        sql_batch_size=args.sql_batch_size,
        sql_batch_delay=args.sql_batch_delay,
    )

    def _fmt_elapsed(seconds: float) -> str:
        h = int(seconds) // 3600
        m = (int(seconds) % 3600) // 60
        s = int(seconds) % 60
        if h > 0:
            return f"{h}h {m}m {s}s"
        if m > 0:
            return f"{m}m {s}s"
        return f"{s}s"

    try:
        run_start = time.time()
        client.login()
        project = client.resolve_project(project_id=args.project_id, project_name=args.project_name)

        manifest["startedAt"] = manifest.get("startedAt") or datetime.now(timezone.utc).isoformat()
        manifest["project"] = {"id": project.id, "name": project.name}
        manifest["baseUrl"] = args.base_url
        save_manifest(output_dir, manifest)

        print(f"Project: {project.name} ({project.id})")

        # ---- Phase 1: Inventory ----
        inventory: Dict[str, List[Dict[str, Any]]] = {}
        if "inventory" in phases:
            phase_start = time.time()
            if args.resume and "inventory" in manifest.get("completedPhases", []):
                print("[inventory] Already completed, loading from disk...")
                inv_dir = output_dir / "inventory"
                for category in OBJECT_TYPES:
                    cat_file = inv_dir / f"{category}.json"
                    if cat_file.exists():
                        inventory[category] = json.loads(cat_file.read_text(encoding="utf-8"))
            else:
                inventory = build_inventory(
                    client, output_dir,
                    include_definitions=args.include_definitions,
                    include_sql=args.include_sql,
                    skip_types=skip_types,
                    limit=args.limit,
                    resume=args.resume,
                    verbose=args.verbose,
                )
                manifest["completedPhases"] = list(set(manifest.get("completedPhases", []) + ["inventory"]))
                save_manifest(output_dir, manifest)

            total = sum(len(recs) for recs in inventory.values())
            print(f"Inventory: {total} objects across {len(inventory)} categories [{_fmt_elapsed(time.time() - phase_start)}]")
        else:
            # Load inventory from disk for downstream phases
            inv_dir = output_dir / "inventory"
            if inv_dir.exists():
                for category in OBJECT_TYPES:
                    cat_file = inv_dir / f"{category}.json"
                    if cat_file.exists():
                        inventory[category] = json.loads(cat_file.read_text(encoding="utf-8"))

        # ---- Phase 1.5: Folder Paths ----
        if "paths" in phases:
            phase_start = time.time()
            if args.resume and "paths" in manifest.get("completedPhases", []):
                print("[paths] Already completed, loading from disk...")
                # Reload inventory with paths from disk
                inv_dir = output_dir / "inventory"
                for category in OBJECT_TYPES:
                    cat_file = inv_dir / f"{category}.json"
                    if cat_file.exists():
                        inventory[category] = json.loads(cat_file.read_text(encoding="utf-8"))
            else:
                fetch_folder_paths(
                    client, output_dir, inventory,
                    resume=args.resume,
                    verbose=args.verbose,
                )
                manifest["completedPhases"] = list(set(manifest.get("completedPhases", []) + ["paths"]))
                save_manifest(output_dir, manifest)

            paths_count = sum(1 for recs in inventory.values() for r in recs if r.get("folderPath"))
            print(f"Folder paths: {paths_count} resolved [{_fmt_elapsed(time.time() - phase_start)}]")

        # ---- Phase 2: Dependencies ----
        graph: Dict[str, Any] = {}
        if "dependencies" in phases:
            phase_start = time.time()
            if args.resume and "dependencies" in manifest.get("completedPhases", []):
                print("[dependencies] Already completed, loading from disk...")
                graph = load_dependency_graph(output_dir)
            else:
                graph = build_dependency_graph(
                    client, output_dir, inventory,
                    skip_types=skip_types,
                    dep_batch_size=args.dep_batch_size,
                    dep_batch_delay=args.dep_batch_delay,
                    resume=args.resume,
                    verbose=args.verbose,
                )
                manifest["completedPhases"] = list(set(manifest.get("completedPhases", []) + ["dependencies"]))
                save_manifest(output_dir, manifest)

            print(f"Dependencies: {len(graph.get('edges', []))} edges [{_fmt_elapsed(time.time() - phase_start)}]")
        else:
            graph = load_dependency_graph(output_dir)

        # ---- Phase 3: Analysis ----
        if "analysis" in phases:
            phase_start = time.time()
            summary = run_analysis(
                output_dir, inventory, graph,
                stale_days=args.stale_days,
                top_impact=args.top_impact,
                verbose=args.verbose,
            )
            manifest["completedPhases"] = list(set(manifest.get("completedPhases", []) + ["analysis"]))
            manifest["finishedAt"] = datetime.now(timezone.utc).isoformat()
            save_manifest(output_dir, manifest)

            print(f"\nRationalization Summary:")
            print(f"  Total objects:       {summary['totalObjects']}")
            print(f"  Dependency edges:    {summary['totalEdges']}")
            print(f"  Orphans:             {summary['orphanCount']}")
            print(f"  Duplicate SQL groups:{summary['duplicateSqlGroupCount']}")
            print(f"  Stale objects:       {summary['staleObjectCount']}")
            print(f"  Unused metrics:      {summary['unusedMetricCount']}")
            print(f"  Errors:              {summary['errorCount']}")
            print(f"  Analysis time:       {_fmt_elapsed(time.time() - phase_start)}")

        # ---- Phase 4: SQL Extraction (targeted) ----
        if "sql" in phases:
            phase_start = time.time()

            # Need orphan IDs from analysis to skip them
            orphan_ids: Set[str] = set()
            analysis_dir = output_dir / "analysis"
            orphans_file = analysis_dir / "orphans.json"
            if orphans_file.exists():
                orphan_data = json.loads(orphans_file.read_text(encoding="utf-8"))
                orphan_ids = {o["id"] for o in orphan_data}

            # Load active de-duped IDs if telemetry results exist
            target_ids: Optional[Set[str]] = None
            telemetry_dir = output_dir / "telemetry"
            active_file = telemetry_dir / "active_reports.json"
            if active_file.exists():
                active_reports = json.loads(active_file.read_text(encoding="utf-8"))
                # De-dup by family: keep 1 per family (highest executions)
                from collections import defaultdict as _dd
                _groups: Dict[str, List[Dict[str, Any]]] = _dd(list)
                for r in active_reports:
                    name = r.get("name", "").strip()
                    parts = name.rsplit(" - ", 1)
                    base = parts[0].strip() if len(parts) == 2 and len(parts[0]) >= 10 else name
                    _groups[base].append(r)
                target_ids = set()
                for base, recs in _groups.items():
                    if len(recs) == 1:
                        target_ids.add(recs[0]["id"])
                    else:
                        parent = max(recs, key=lambda x: x.get("totalExecutions", 0))
                        target_ids.add(parent["id"])
                print(f"[sql] Targeting {len(target_ids):,} de-duped active reports (from {len(active_reports):,} active)", file=sys.stderr)
            else:
                print(f"[sql] No telemetry data found — targeting all reports", file=sys.stderr)

            # Build plan and show it
            plan = build_sql_extraction_plan(inventory, orphan_ids, verbose=True, target_ids=target_ids)

            # Execute
            execute_sql_phase(
                client, output_dir, inventory, plan,
                skip_types=skip_types,
                verbose=args.verbose,
            )

            manifest["completedPhases"] = list(set(manifest.get("completedPhases", []) + ["sql"]))
            save_manifest(output_dir, manifest)

            # Re-run analysis with SQL data
            print(f"\n[sql] Re-running analysis with SQL data...", file=sys.stderr)
            summary = run_analysis(
                output_dir, inventory, graph,
                stale_days=args.stale_days,
                top_impact=args.top_impact,
                verbose=args.verbose,
            )

            print(f"\nPost-SQL Rationalization Summary:")
            print(f"  Total objects:       {summary['totalObjects']}")
            print(f"  Dependency edges:    {summary['totalEdges']}")
            print(f"  Orphans:             {summary['orphanCount']}")
            print(f"  Duplicate SQL groups:{summary['duplicateSqlGroupCount']}")
            print(f"  Stale objects:       {summary['staleObjectCount']}")
            print(f"  Unused metrics:      {summary['unusedMetricCount']}")
            print(f"  Errors:              {summary['errorCount']}")
            print(f"  SQL phase time:      {_fmt_elapsed(time.time() - phase_start)}")

        total_elapsed = time.time() - run_start
        print(f"\nTotal run time: {_fmt_elapsed(total_elapsed)}")
        print(f"Output: {output_dir.resolve()}")
        return 0

    finally:
        client.logout()


if __name__ == "__main__":
    raise SystemExit(main())
