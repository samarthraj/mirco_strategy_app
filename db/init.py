"""Create the SQLite database and schema. Idempotent."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.db import DB_PATH, SCHEMA_PATH, init


def main() -> int:
    init()
    print(f"  schema: {SCHEMA_PATH}")
    print(f"  db:     {DB_PATH}")
    # Quick sanity-check: list tables
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print(f"  tables: {len(rows)}")
    for (name,) in rows:
        print(f"    - {name}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
