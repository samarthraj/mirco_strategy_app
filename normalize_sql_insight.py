#!/usr/bin/env python3
"""
Extract and normalize SQL from active reports into individual .sql files.

Creates two folders:
  sql/raw/       — original SQL as returned by MSTR API
  sql/normalized/ — normalized SQL (lowercased, comments stripped, literals replaced,
                    temp table names standardized, whitespace collapsed)

File naming: {report_name}_{report_id_last8}.sql
"""

import hashlib
import json
import re
import sys
from pathlib import Path


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Make a string safe for use as a filename."""
    name = name.strip()
    # Replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Collapse multiple underscores/spaces
    name = re.sub(r'[\s_]+', '_', name)
    # Trim
    name = name.strip('_. ')
    if len(name) > max_len:
        name = name[:max_len].rstrip('_')
    return name or "unnamed"


def normalize_sql(sql: str) -> str:
    """Normalize SQL for semantic comparison."""
    if not sql:
        return ""
    n = sql.lower()

    # Strip single-line comments
    n = re.sub(r"--[^\n]*", "", n)
    # Strip block comments
    n = re.sub(r"/\*.*?\*/", "", n, flags=re.DOTALL)

    # Remove MicroStrategy SET query_group headers (per-report identifier)
    n = re.sub(r"set\s+query_group\s*=\s*'[^']*'\s*;?\s*", "", n)

    # Normalize auto-generated temp table names (e.g., ZZMD00, TIG3JE6NZMD000)
    n = re.sub(r"\b[A-Z0-9]{6,}ZMD\d{3}\b", "ZTMP", n, flags=re.IGNORECASE)
    n = re.sub(r"\bZZT\w+\b", "ZTMP", n, flags=re.IGNORECASE)
    n = re.sub(r"\bpa\d+\b", "pa0", n, flags=re.IGNORECASE)

    # Replace string literals with placeholder
    n = re.sub(r"'[^']*'", "'?'", n)
    # Replace numeric literals (standalone numbers)
    n = re.sub(r"\b\d+(?:\.\d+)?\b", "0", n)

    # Collapse whitespace
    n = re.sub(r"\s+", " ", n).strip()

    return n


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract and normalize SQL into files")
    parser.add_argument("--output-dir", default="INSIGHT/rationalization")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    sql_path = output_dir / "inventory" / "active_reports_sql.json"

    if not sql_path.exists():
        print(f"ERROR: {sql_path} not found.")
        return 1

    print(f"Loading {sql_path}...")
    reports = json.loads(sql_path.read_text(encoding="utf-8"))
    print(f"  {len(reports)} reports loaded")

    raw_dir = output_dir / "sql" / "raw"
    norm_dir = output_dir / "sql" / "normalized"
    raw_dir.mkdir(parents=True, exist_ok=True)
    norm_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    hashes = {}  # normalized hash -> list of (id, name, filename)

    for rec in reports:
        sql = rec.get("sql", "")
        if not sql:
            skipped += 1
            continue

        rid = rec.get("id", "unknown")
        name = rec.get("name", "unnamed")
        short_id = rid[-8:].upper() if len(rid) >= 8 else rid.upper()
        safe_name = sanitize_filename(name)
        filename = f"{safe_name}_{short_id}.sql"

        # Write raw SQL
        (raw_dir / filename).write_text(sql, encoding="utf-8")

        # Normalize and write
        norm = normalize_sql(sql)
        (norm_dir / filename).write_text(norm, encoding="utf-8")

        # Track hash for summary
        norm_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
        hashes.setdefault(norm_hash, []).append({
            "id": rid,
            "name": name,
            "filename": filename,
        })

        written += 1

    # Write index
    index = {
        "totalReports": len(reports),
        "withSql": written,
        "withoutSql": skipped,
        "uniqueHashes": len(hashes),
        "duplicateGroups": sum(1 for v in hashes.values() if len(v) > 1),
        "files": [],
    }
    for norm_hash, members in sorted(hashes.items()):
        for m in members:
            index["files"].append({
                "id": m["id"],
                "name": m["name"],
                "filename": m["filename"],
                "normalizedHash": norm_hash,
                "duplicateCount": len(members),
            })

    index_path = output_dir / "sql" / "sql_index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nDone!")
    print(f"  Raw SQL:        {raw_dir} ({written} files)")
    print(f"  Normalized SQL: {norm_dir} ({written} files)")
    print(f"  Index:          {index_path}")
    print(f"  Skipped (no SQL): {skipped}")
    print(f"  Unique hashes:    {len(hashes)}")
    print(f"  Duplicate groups: {sum(1 for v in hashes.values() if len(v) > 1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
