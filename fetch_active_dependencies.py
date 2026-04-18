#!/usr/bin/env python3
"""Build dependency graph for active reports only, using enriched definitions."""

from __future__ import annotations
import json, sys, time
from collections import defaultdict
from pathlib import Path
from mstr_project_rationalize import (
    RationalizationClient, _build_deps_from_definitions, _save_dependency_graph,
    _probe_dependents_api, _add_edge, write_json, OBJECT_TYPES
)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build dependency graph for active reports")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default="")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--login-mode", type=int, default=1)
    parser.add_argument("--verify-ssl", action="store_true", default=False)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--dep-batch-size", type=int, default=50)
    parser.add_argument("--dep-batch-delay", type=float, default=3.0)
    parser.add_argument("--save-interval", type=int, default=100)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    enriched_path = output_dir / "inventory" / "active_reports_enriched.json"
    dep_file = output_dir / "active_dependencies.json"

    if not enriched_path.exists():
        print(f"ERROR: {enriched_path} not found. Run definitions fetch first.")
        return 1

    # Load enriched active reports
    print("Loading enriched active reports...")
    enriched = json.loads(enriched_path.read_text(encoding="utf-8"))
    print(f"  {len(enriched)} active reports loaded")

    # Also load supporting inventory (metrics, attributes, filters) for ID matching
    inv_dir = output_dir / "inventory"
    inventory = {"reports": enriched}
    for cat in ("metrics", "attributes", "filters", "cubes", "facts", "tables", "prompts"):
        cat_file = inv_dir / f"{cat}.json"
        if cat_file.exists():
            inventory[cat] = json.loads(cat_file.read_text(encoding="utf-8"))
            print(f"  {cat}: {len(inventory[cat])}")

    # First try to infer from definitions (no API calls needed)
    has_definitions = sum(1 for r in enriched if r.get("definition") or r.get("attributes") or r.get("metrics"))
    print(f"\n  Reports with definitions: {has_definitions}")

    if has_definitions > 0:
        print("\nInferring dependencies from definitions (no API calls needed)...")
        graph = {
            "edges": [],
            "dependents_of": defaultdict(list),
            "dependencies_of": defaultdict(list),
            "processed_ids": set(),
        }
        _build_deps_from_definitions(graph, inventory, verbose=True)
        _save_dependency_graph(dep_file, graph)
        print(f"\nDone! {len(graph['edges'])} dependency edges found.")
        print(f"Output: {dep_file}")
    else:
        # Fall back to API if no definitions available
        print("\nNo definitions found, using API to fetch dependencies...")
        client = RationalizationClient(
            base_url=args.base_url,
            username=args.username,
            password=args.password,
            login_mode=args.login_mode,
            verify_ssl=args.verify_ssl,
            timeout=args.timeout,
            delay=args.delay,
        )
        client.login()
        project = client.resolve_project(project_id=None, project_name=args.project_name)
        print(f"Project: {project.name} ({project.id})")

        graph = {
            "edges": [],
            "dependents_of": defaultdict(list),
            "dependencies_of": defaultdict(list),
            "processed_ids": set(),
        }

        # Resume support
        if dep_file.exists():
            existing = json.loads(dep_file.read_text(encoding="utf-8"))
            graph["edges"] = existing.get("edges", [])
            graph["processed_ids"] = set(existing.get("processed_ids", []))
            for edge in graph["edges"]:
                graph["dependents_of"][edge["target"]].append(edge["source"])
                graph["dependencies_of"][edge["source"]].append(edge["target"])
            print(f"  Resumed with {len(graph['processed_ids'])} already processed")

        t0 = time.time()
        errors = 0
        fetched = 0

        try:
            for idx, rec in enumerate(enriched, 1):
                obj_id = rec["id"]
                obj_type = rec.get("type", 3)

                if obj_id in graph["processed_ids"]:
                    continue

                if idx % args.dep_batch_size == 0:
                    time.sleep(args.dep_batch_delay)

                try:
                    dependents = client.get_object_dependents(obj_id, obj_type)
                    for dep in dependents:
                        dep_id = dep.get("id") or dep.get("objectId")
                        if dep_id:
                            _add_edge(graph, source=dep_id, source_type=dep.get("type"),
                                      target=obj_id, target_type=obj_type)
                    fetched += 1
                except Exception as e:
                    errors += 1

                graph["processed_ids"].add(obj_id)

                elapsed = time.time() - t0
                rate = (fetched + errors) / elapsed if elapsed > 0 else 0
                eta = (len(enriched) - fetched - errors) / rate if rate > 0 else 0

                if (fetched + errors) % 100 == 0:
                    print(f"  [{fetched + errors}/{len(enriched)}] "
                          f"({fetched} ok, {errors} err, "
                          f"ETA: {int(eta//60)}m {int(eta%60)}s)", flush=True)
                    _save_dependency_graph(dep_file, graph)

        except KeyboardInterrupt:
            print("\nInterrupted! Saving progress...")
        finally:
            _save_dependency_graph(dep_file, graph)
            client.logout()

        print(f"\nDone! {len(graph['edges'])} edges, {errors} errors [{time.time()-t0:.1f}s]")
        print(f"Output: {dep_file}")

    # Update manifest
    manifest_path = output_dir / "run_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if "dependencies" not in manifest.get("completedPhases", []):
            manifest["completedPhases"].append("dependencies")
        write_json(manifest_path, manifest)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
