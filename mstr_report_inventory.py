#!/usr/bin/env python3
"""
MicroStrategy / Strategy REST API report inventory extractor.

What it does
------------
- Logs in to a Strategy / MicroStrategy Library REST API server
- Lists projects and resolves a project by ID or name
- Finds report objects in a project
- Retrieves each report's modeling definition
- Extracts filter text and a simplified list of attributes / metrics
- Optionally creates a v2 report instance and retrieves sqlView
- Heuristically parses table names from the SQL statement
- Writes JSON and CSV outputs for downstream rationalization work

Typical demo usage
------------------
python mstr_report_inventory.py \
  --base-url https://demo.microstrategy.com/MicroStrategyLibrary/api \
  --username administrator \
  --password "" \
  --project-name "MicroStrategy Tutorial" \
  --limit 25 \
  --include-sql

Notes
-----
1) Login commonly returns HTTP 204 and the auth token in the X-MSTR-AuthToken
   response header. This script reads it from the header.
2) The report definition endpoint used here is:
      GET /api/model/reports/{reportId}?showExpressionAs=tree
3) SQL retrieval uses:
      POST /api/v2/reports/{id}/instances?executionStage=resolve_prompts
      GET  /api/v2/reports/{id}/instances/{instanceId}/sqlView
   The user needs the privilege to view report SQL.
4) Physical "sources" are not directly returned as a neat list by the report
   definition endpoint, so this script infers source tables from sqlView.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import requests
except ImportError as exc:
    raise SystemExit(
        "This script requires the 'requests' package. Install it with: pip install requests"
    ) from exc


REPORT_OBJECT_TYPE = 3


@dataclass
class Project:
    id: str
    name: str


class MstrClient:
    def __init__(self, base_url: str, username: str, password: str, login_mode: int = 1, verify_ssl: bool = True, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.login_mode = login_mode
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()
        # Increase connection pool to prevent "HTTPSConnectionPool full" errors
        from requests.adapters import HTTPAdapter
        adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, pool_block=False)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.auth_token: Optional[str] = None
        self.project_id: Optional[str] = None

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _headers(self, project_id: Optional[str] = None, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {"Accept": "application/json"}
        if self.auth_token:
            headers["X-MSTR-AuthToken"] = self.auth_token
        effective_project = project_id or self.project_id
        if effective_project:
            headers["X-MSTR-ProjectID"] = effective_project
        if extra:
            headers.update(extra)
        return headers

    def login(self) -> None:
        payload = {
            "username": self.username,
            "password": self.password,
            "loginMode": self.login_mode,
        }
        resp = self.session.post(
            self._url("/auth/login"),
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        # Strategy/MicroStrategy often returns 204 No Content on success.
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Login failed: HTTP {resp.status_code} - {resp.text}")
        token = resp.headers.get("X-MSTR-AuthToken") or resp.headers.get("x-mstr-authtoken")
        if not token:
            raise RuntimeError(
                "Login succeeded but X-MSTR-AuthToken was not found in response headers."
            )
        self.auth_token = token

    def logout(self) -> None:
        if not self.auth_token:
            return
        try:
            self.session.post(
                self._url("/auth/logout"),
                headers=self._headers(),
                verify=self.verify_ssl,
                timeout=self.timeout,
            )
        finally:
            self.auth_token = None

    def get_projects(self) -> List[Project]:
        resp = self.session.get(
            self._url("/projects"),
            headers=self._headers(),
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        self._raise_for_status(resp, "GET /projects")
        data = resp.json()
        projects: List[Project] = []
        for item in data:
            pid = item.get("id")
            name = item.get("name")
            if pid and name:
                projects.append(Project(id=pid, name=name))
        return projects

    def resolve_project(self, project_id: Optional[str], project_name: Optional[str]) -> Project:
        projects = self.get_projects()
        if project_id:
            for p in projects:
                if p.id.lower() == project_id.lower():
                    self.project_id = p.id
                    return p
            raise RuntimeError(f"Project ID not found: {project_id}")

        if project_name:
            exact = [p for p in projects if p.name == project_name]
            if len(exact) == 1:
                self.project_id = exact[0].id
                return exact[0]

            casefold = [p for p in projects if p.name.lower() == project_name.lower()]
            if len(casefold) == 1:
                self.project_id = casefold[0].id
                return casefold[0]

            contains = [p for p in projects if project_name.lower() in p.name.lower()]
            if len(contains) == 1:
                self.project_id = contains[0].id
                return contains[0]

            available = ", ".join(f"{p.name} ({p.id})" for p in projects)
            raise RuntimeError(
                f"Could not uniquely resolve project name '{project_name}'. Available projects: {available}"
            )

        if len(projects) == 1:
            self.project_id = projects[0].id
            return projects[0]

        available = ", ".join(f"{p.name} ({p.id})" for p in projects)
        raise RuntimeError(
            "Multiple projects are available. Pass --project-id or --project-name. "
            f"Available projects: {available}"
        )

    def search_reports(self, *, name_begins: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Tries a few search strategies because environments differ slightly.
        """
        candidates = [
            ("/searches/results", {"type": REPORT_OBJECT_TYPE}),
            ("/api/searches/results", {"type": REPORT_OBJECT_TYPE}),  # fallback if someone passes a trimmed base URL
        ]

        if name_begins:
            # Common server-side search parameters vary by environment. We keep it conservative.
            candidates[0][1]["nameBegins"] = name_begins

        errors: List[str] = []
        for path, params in candidates:
            actual_path = path if self.base_url.endswith("/api") else path.replace("/api", "", 1)
            resp = self.session.get(
                self._url(actual_path),
                headers=self._headers(),
                params=params,
                verify=self.verify_ssl,
                timeout=self.timeout,
            )
            if resp.ok:
                data = resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    # Some environments wrap the result set.
                    for key in ("result", "results", "items", "objects"):
                        if isinstance(data.get(key), list):
                            return data[key]
                    return [data]
            errors.append(f"{actual_path}: HTTP {resp.status_code}")
        raise RuntimeError("Unable to search reports. Tried: " + "; ".join(errors))

    def get_report_definition(self, report_id: str) -> Dict[str, Any]:
        resp = self.session.get(
            self._url(f"/model/reports/{report_id}"),
            headers=self._headers(),
            params={"showExpressionAs": "tree"},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        self._raise_for_status(resp, f"GET /model/reports/{report_id}")
        return resp.json()

    def create_report_instance_for_sql(self, report_id: str) -> str:
        resp = self.session.post(
            self._url(f"/v2/reports/{report_id}/instances"),
            headers=self._headers(),
            params={"executionStage": "resolve_prompts"},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        self._raise_for_status(resp, f"POST /v2/reports/{report_id}/instances")
        data = resp.json()
        instance_id = data.get("instanceId") or data.get("id")
        if not instance_id:
            raise RuntimeError(f"Could not find instance ID for report {report_id}: {data}")
        return instance_id

    def get_report_sql(self, report_id: str, instance_id: str) -> str:
        resp = self.session.get(
            self._url(f"/v2/reports/{report_id}/instances/{instance_id}/sqlView"),
            headers=self._headers(),
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        self._raise_for_status(resp, f"GET /v2/reports/{report_id}/instances/{instance_id}/sqlView")
        data = resp.json()
        return data.get("sqlStatement", "")

    def _raise_for_status(self, response: requests.Response, action: str) -> None:
        if response.ok:
            return
        body = response.text
        try:
            body = json.dumps(response.json(), indent=2)
        except Exception:
            pass
        raise RuntimeError(f"{action} failed: HTTP {response.status_code}\n{body}")


def normalize_report_stub(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id") or item.get("objectId"),
        "name": item.get("name"),
        "type": item.get("type"),
        "subType": item.get("subType"),
        "description": item.get("description"),
        "dateCreated": item.get("dateCreated"),
        "dateModified": item.get("dateModified"),
        "owner": (
            item.get("owner", {}).get("name")
            if isinstance(item.get("owner"), dict)
            else item.get("owner")
        ),
        "path": item.get("path"),
    }


def extract_units(report_def: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """
    Returns (attributes, metrics) from dataSource.dataTemplate.units.

    Handles two MSTR response shapes:
    1. Flat: each unit IS the attribute/metric (has id, name, type at top level)
    2. Grouped: units contain nested `elements` (e.g. metric groups with child metrics)
    """
    attributes: List[Dict[str, str]] = []
    metrics: List[Dict[str, str]] = []

    units = (
        report_def.get("dataSource", {})
        .get("dataTemplate", {})
        .get("units", [])
    )

    for unit in units:
        unit_type = unit.get("type")
        elements = unit.get("elements", [])

        # Flat shape: the unit itself is the object
        if not elements and unit.get("id") and unit.get("name"):
            entry = {
                "id": unit.get("id"),
                "name": unit.get("name"),
                "subType": unit.get("subType") or unit_type,
            }
            if (entry["subType"] or "").lower() == "metric" or unit_type == "metrics":
                metrics.append(entry)
            else:
                attributes.append(entry)
            continue

        # Grouped shape: iterate elements
        for element in elements:
            entry = {
                "id": element.get("id") or element.get("objectId"),
                "name": element.get("name"),
                "subType": element.get("subType") or unit_type,
            }
            sub = (entry["subType"] or "").lower()
            if "metric" in sub or unit_type == "metrics":
                metrics.append(entry)
            else:
                attributes.append(entry)

    return dedupe_object_list(attributes), dedupe_object_list(metrics)


def dedupe_object_list(items: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: set[Tuple[Optional[str], Optional[str], Optional[str]]] = set()
    out: List[Dict[str, str]] = []
    for item in items:
        key = (item.get("id"), item.get("name"), item.get("subType"))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def extract_filter(report_def: Dict[str, Any]) -> Dict[str, Any]:
    filt = report_def.get("dataSource", {}).get("filter") or {}
    return {
        "text": filt.get("text"),
        "tree": filt.get("tree"),
    }


def extract_report_info(report_def: Dict[str, Any]) -> Dict[str, Any]:
    info = report_def.get("information", {})
    return {
        "id": info.get("objectId"),
        "name": info.get("name"),
        "subType": info.get("subType"),
        "dateCreated": info.get("dateCreated"),
        "dateModified": info.get("dateModified"),
        "versionId": info.get("versionId"),
    }


_SQL_TABLE_PATTERNS = [
    re.compile(r"\bfrom\s+([`\"\[\]\w\.]+)", re.IGNORECASE),
    re.compile(r"\bjoin\s+([`\"\[\]\w\.]+)", re.IGNORECASE),
]


def parse_table_names_from_sql(sql: str) -> List[str]:
    if not sql:
        return []
    results: List[str] = []
    for pattern in _SQL_TABLE_PATTERNS:
        for match in pattern.findall(sql):
            cleaned = match.strip().strip(",")
            cleaned = cleaned.strip("`").strip('"')
            if cleaned.startswith("[") and cleaned.endswith("]"):
                cleaned = cleaned[1:-1]
            if cleaned and cleaned not in results:
                results.append(cleaned)
    return results


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_inventory(
    client: MstrClient,
    *,
    limit: Optional[int],
    include_sql: bool,
    name_begins: Optional[str],
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    raw_reports = client.search_reports(name_begins=name_begins)
    normalized = [normalize_report_stub(item) for item in raw_reports]
    normalized = [r for r in normalized if r.get("id") and r.get("name")]

    if limit is not None:
        normalized = normalized[:limit]

    inventory: List[Dict[str, Any]] = []

    for idx, report in enumerate(normalized, start=1):
        report_id = str(report["id"])
        report_name = report.get("name", "")
        if verbose:
            print(f"[{idx}/{len(normalized)}] Processing report: {report_name} ({report_id})", file=sys.stderr)

        row: Dict[str, Any] = {
            "reportId": report_id,
            "reportName": report_name,
            "reportPath": report.get("path"),
        }

        try:
            report_def = client.get_report_definition(report_id)
            row["definition"] = extract_report_info(report_def)
            row["filter"] = extract_filter(report_def)
            attrs, metrics = extract_units(report_def)
            row["attributes"] = attrs
            row["metrics"] = metrics
        except Exception as exc:
            row["definitionError"] = str(exc)

        if include_sql:
            try:
                instance_id = client.create_report_instance_for_sql(report_id)
                sql_text = client.get_report_sql(report_id, instance_id)
                row["sql"] = sql_text
                row["sourceTables"] = parse_table_names_from_sql(sql_text)
            except Exception as exc:
                row["sqlError"] = str(exc)

        inventory.append(row)

    return inventory


def flatten_for_csv(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for rec in records:
        definition = rec.get("definition", {})
        filt = rec.get("filter", {})
        attrs = rec.get("attributes", [])
        metrics = rec.get("metrics", [])
        rows.append(
            {
                "reportId": rec.get("reportId"),
                "reportName": rec.get("reportName"),
                "reportPath": rec.get("reportPath"),
                "subType": definition.get("subType"),
                "dateCreated": definition.get("dateCreated"),
                "dateModified": definition.get("dateModified"),
                "filterText": filt.get("text"),
                "attributeCount": len(attrs),
                "attributes": "; ".join(a.get("name", "") for a in attrs if a.get("name")),
                "metricCount": len(metrics),
                "metrics": "; ".join(m.get("name", "") for m in metrics if m.get("name")),
                "sourceTables": "; ".join(rec.get("sourceTables", [])),
                "definitionError": rec.get("definitionError"),
                "sqlError": rec.get("sqlError"),
            }
        )
    return rows


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract MicroStrategy / Strategy reports with filters and inferred source tables."
    )
    parser.add_argument("--base-url", required=True, help="Example: https://demo.microstrategy.com/MicroStrategyLibrary/api")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default=os.getenv("MSTR_PASSWORD", ""))
    parser.add_argument("--login-mode", type=int, default=1)
    parser.add_argument("--project-id", help="Project GUID")
    parser.add_argument("--project-name", help='Example: "MicroStrategy Tutorial"')
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on number of reports")
    parser.add_argument("--name-begins", help="Optional server-side name prefix filter")
    parser.add_argument("--include-sql", action="store_true", help="Retrieve sqlView and infer source tables")
    parser.add_argument("--verify-ssl", action="store_true", default=False, help="Verify SSL certificates")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output-dir", default="mstr_output")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = MstrClient(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        login_mode=args.login_mode,
        verify_ssl=args.verify_ssl,
        timeout=args.timeout,
    )

    try:
        client.login()
        project = client.resolve_project(project_id=args.project_id, project_name=args.project_name)

        inventory = build_inventory(
            client,
            limit=args.limit,
            include_sql=args.include_sql,
            name_begins=args.name_begins,
            verbose=args.verbose,
        )

        summary = {
            "baseUrl": args.base_url,
            "projectId": project.id,
            "projectName": project.name,
            "reportCount": len(inventory),
            "includeSql": args.include_sql,
        }

        json_path = out_dir / "report_inventory.json"
        csv_path = out_dir / "report_inventory.csv"
        summary_path = out_dir / "run_summary.json"

        write_json(json_path, inventory)
        write_json(summary_path, summary)
        write_csv(
            csv_path,
            flatten_for_csv(inventory),
            fieldnames=[
                "reportId",
                "reportName",
                "reportPath",
                "subType",
                "dateCreated",
                "dateModified",
                "filterText",
                "attributeCount",
                "attributes",
                "metricCount",
                "metrics",
                "sourceTables",
                "definitionError",
                "sqlError",
            ],
        )

        print(f"Project: {project.name} ({project.id})")
        print(f"Reports processed: {len(inventory)}")
        print(f"JSON: {json_path.resolve()}")
        print(f"CSV:  {csv_path.resolve()}")
        print(f"Summary: {summary_path.resolve()}")

        return 0
    finally:
        client.logout()


if __name__ == "__main__":
    raise SystemExit(main())
