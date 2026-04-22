"""Phase 3b — DB-driven compute stages.

Each stage reads raw_* tables (and upstream derived tables) from the SQLite
DB, runs the existing Python compute logic over DB-fetched rows, and writes
results back into the derived tables. db/emit.py then writes JSON from DB.

Stages (execute in order):
  1. collisions  — collapse telemetry-row collisions
  2. fingerprint — exact (metrics, tables, filters) match
  3. sql_hash    — byte-identical normalized SQL
  4. ast         — AST subtree-hash Jaccard
  5. family      — name-pattern grouping
  6. similarity  — weighted Jaccard clustering
  7. summary     — scalars + funnel + top-lists
"""
