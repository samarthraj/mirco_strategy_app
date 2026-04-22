"""SQLite connection + init helpers.

The database lives at ``data.db`` at the repo root. It's gitignored.
Run ``python -m db.init`` or ``python db/init.py`` to create the schema.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with sensible defaults for this pipeline."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init(db_path: Path | str = DB_PATH, schema_path: Path | str = SCHEMA_PATH) -> None:
    """Create the schema if it doesn't exist. Idempotent."""
    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    conn = connect(db_path)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


# Catalog of the three projects + their dashboard data dirs.
#
# reports_scope controls what the emitter writes to reports.json:
#   'active' = only status='active' (matches GO's original behaviour)
#   'all'    = every row in the report table (matches GI/INSIGHT)
#
# active_batches is the list of raw_inventory.batch values that feed the
# compute pipeline (collision collapse, fingerprint, etc.). Varies per
# project based on how the inventory was onboarded:
#   GO onboarded in multiple waves -> 'original', 'new', 'added'
#   GI/INSIGHT onboarded as a single blob -> 'inventory'
#
# retired_batches is the list of raw_inventory.batch values whose records
# are retired reports (no telemetry). Again, project-specific.
PROJECTS = [
    {
        "project_id": "global-operational",
        "name": "Global Operational",
        "mstr_project_id": "E77B77894C04BF0E6D244F9363CFAF64",
        "data_dir": REPO_ROOT / "public" / "data" / "Global Operational",
        "reports_scope": "active",
        "active_batches": ["original", "new", "added"],
        "retired_batches": ["new_retire", "added_retire"],
        "dossier_batches": ["dossier"],
    },
    {
        "project_id": "global-insight",
        "name": "Global Insight",
        "mstr_project_id": "",
        "data_dir": REPO_ROOT / "public" / "data" / "Global Insight",
        "reports_scope": "all",
        "active_batches": ["inventory"],
        "retired_batches": [],
        "dossier_batches": [],
    },
    {
        "project_id": "insight",
        "name": "INSIGHT",
        "mstr_project_id": "",
        "data_dir": REPO_ROOT / "public" / "data" / "INSIGHT",
        "reports_scope": "all",
        "active_batches": ["inventory"],
        "retired_batches": ["retire"],
        "dossier_batches": [],
    },
]


def get_project(project_id: str) -> dict:
    """Look up a project config by its internal id."""
    for p in PROJECTS:
        if p["project_id"] == project_id:
            return p
    raise ValueError(f"Unknown project_id '{project_id}'. "
                     f"Known: {', '.join(p['project_id'] for p in PROJECTS)}")
