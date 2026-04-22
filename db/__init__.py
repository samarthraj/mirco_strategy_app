"""SQLite-backed unified store for all project data."""
from .db import connect, init, DB_PATH, SCHEMA_PATH, PROJECTS, get_project  # noqa: F401
