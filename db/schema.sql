-- ================================================================
-- MicroStrategy Rationalization — unified SQLite schema
--
-- One schema serves all projects (GO, GI, INSIGHT). Every table carries
-- project_id so cross-project queries stay valid.
--
-- Loaded from public/data/<project>/*.json (the already-normalized files
-- the React dashboard reads).
-- ================================================================

PRAGMA foreign_keys = ON;

-- ---------------- Catalog ---------------------------------------

CREATE TABLE IF NOT EXISTS project (
    project_id        TEXT PRIMARY KEY,           -- 'global-operational', 'global-insight', 'insight'
    name              TEXT NOT NULL,              -- 'Global Operational'
    mstr_project_id   TEXT,                       -- MSTR UUID
    snapshot_date     TEXT,                       -- ISO date of the load
    loaded_at         TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS report (
    project_id        TEXT NOT NULL,
    report_id         TEXT NOT NULL,
    name              TEXT,
    owner             TEXT,
    path              TEXT,
    date_created      TEXT,
    date_modified     TEXT,
    object_type       INTEGER,                    -- 3=report, 55=dossier/document
    subtype           INTEGER,
    match_tier        TEXT,                       -- exact_name / full_path / fuzzy / none / ''
    family_base       TEXT,
    status            TEXT,                       -- active / retired / collapsed / dossier
    cluster_id        TEXT,                       -- similarity cluster membership, if any
    metric_count      INTEGER DEFAULT 0,
    table_count       INTEGER DEFAULT 0,
    filter_count      INTEGER DEFAULT 0,
    attribute_count   INTEGER DEFAULT 0,
    PRIMARY KEY (project_id, report_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_report_proj_status ON report(project_id, status);
CREATE INDEX IF NOT EXISTS ix_report_cluster    ON report(project_id, cluster_id);
CREATE INDEX IF NOT EXISTS ix_report_family     ON report(project_id, family_base);

-- Many-to-many: each report has 0..N metrics/tables/filters
CREATE TABLE IF NOT EXISTS report_metric (
    project_id  TEXT NOT NULL,
    report_id   TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    PRIMARY KEY (project_id, report_id, metric_name),
    FOREIGN KEY (project_id, report_id) REFERENCES report(project_id, report_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rep_metric_name ON report_metric(project_id, metric_name);

CREATE TABLE IF NOT EXISTS report_table (
    project_id TEXT NOT NULL,
    report_id  TEXT NOT NULL,
    table_name TEXT NOT NULL,
    PRIMARY KEY (project_id, report_id, table_name),
    FOREIGN KEY (project_id, report_id) REFERENCES report(project_id, report_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rep_table_name ON report_table(project_id, table_name);

CREATE TABLE IF NOT EXISTS report_filter (
    project_id  TEXT NOT NULL,
    report_id   TEXT NOT NULL,
    filter_name TEXT NOT NULL,
    PRIMARY KEY (project_id, report_id, filter_name),
    FOREIGN KEY (project_id, report_id) REFERENCES report(project_id, report_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rep_filter_name ON report_filter(project_id, filter_name);

-- Template attributes (GROUP BY dimensions). Distinct from report_filter which
-- holds WHERE-clause constraint references. Previously attributes were
-- mis-loaded into report_filter; they are now first-class.
CREATE TABLE IF NOT EXISTS report_attribute (
    project_id      TEXT NOT NULL,
    report_id       TEXT NOT NULL,
    attribute_name  TEXT NOT NULL,
    PRIMARY KEY (project_id, report_id, attribute_name),
    FOREIGN KEY (project_id, report_id) REFERENCES report(project_id, report_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rep_attribute_name ON report_attribute(project_id, attribute_name);

CREATE TABLE IF NOT EXISTS report_sql (
    project_id  TEXT NOT NULL,
    report_id   TEXT NOT NULL,
    sql_text    TEXT,
    sql_error   TEXT,
    PRIMARY KEY (project_id, report_id),
    FOREIGN KEY (project_id, report_id) REFERENCES report(project_id, report_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ast_hash (
    project_id TEXT NOT NULL,
    report_id  TEXT NOT NULL,
    hash       TEXT NOT NULL,
    PRIMARY KEY (project_id, report_id, hash),
    FOREIGN KEY (project_id, report_id) REFERENCES report(project_id, report_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_ast_hash_hash ON ast_hash(project_id, hash);

-- ---------------- Telemetry -------------------------------------

CREATE TABLE IF NOT EXISTS telemetry_match (
    project_id         TEXT NOT NULL,
    report_id          TEXT NOT NULL,
    match_tier         TEXT,
    match_score        REAL,
    matched_telemetry_name TEXT,
    telemetry_path     TEXT,
    total_executions   INTEGER DEFAULT 0,
    total_users        INTEGER DEFAULT 0,
    last_exec_ts       TEXT,
    PRIMARY KEY (project_id, report_id),
    FOREIGN KEY (project_id, report_id) REFERENCES report(project_id, report_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_tel_executions ON telemetry_match(project_id, total_executions DESC);

CREATE TABLE IF NOT EXISTS retired_report (
    project_id TEXT NOT NULL,
    report_id  TEXT NOT NULL,
    bucket     TEXT,                              -- original / new / added
    PRIMARY KEY (project_id, report_id),
    FOREIGN KEY (project_id, report_id) REFERENCES report(project_id, report_id) ON DELETE CASCADE
);

-- ---------------- Derived analysis — groups + members -----------

CREATE TABLE IF NOT EXISTS collision_group (
    project_id        TEXT NOT NULL,
    group_id          TEXT NOT NULL,              -- synthesized (e.g. 'COL0001')
    pass              INTEGER,                    -- 1 or 2
    canonical_id      TEXT,
    canonical_name    TEXT,
    canonical_path    TEXT,
    normalized_name   TEXT,
    executions        INTEGER,
    last_exec         TEXT,
    size              INTEGER,
    collapsed         INTEGER,
    PRIMARY KEY (project_id, group_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS collision_member (
    project_id  TEXT NOT NULL,
    group_id    TEXT NOT NULL,
    report_id   TEXT NOT NULL,
    match_tier  TEXT,
    folder_path TEXT,
    owner       TEXT,
    PRIMARY KEY (project_id, group_id, report_id),
    FOREIGN KEY (project_id, group_id) REFERENCES collision_group(project_id, group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fingerprint_group (
    project_id       TEXT NOT NULL,
    group_id         TEXT NOT NULL,              -- 'FP0000'
    size             INTEGER,
    reducible        INTEGER,
    metric_count     INTEGER,
    table_count      INTEGER,
    filter_count     INTEGER,
    total_executions INTEGER,
    -- the shared fingerprint key, encoded as JSON arrays
    metrics_json     TEXT,
    tables_json      TEXT,
    filters_json     TEXT,
    PRIMARY KEY (project_id, group_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fingerprint_member (
    project_id TEXT NOT NULL,
    group_id   TEXT NOT NULL,
    report_id  TEXT NOT NULL,
    PRIMARY KEY (project_id, group_id, report_id),
    FOREIGN KEY (project_id, group_id) REFERENCES fingerprint_group(project_id, group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sql_hash_group (
    project_id       TEXT NOT NULL,
    group_id         TEXT NOT NULL,              -- 'H0000'
    sql_hash         TEXT,
    sql_preview      TEXT,
    size             INTEGER,
    reducible        INTEGER,
    total_executions INTEGER,
    PRIMARY KEY (project_id, group_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sql_hash_member (
    project_id TEXT NOT NULL,
    group_id   TEXT NOT NULL,
    report_id  TEXT NOT NULL,
    PRIMARY KEY (project_id, group_id, report_id),
    FOREIGN KEY (project_id, group_id) REFERENCES sql_hash_group(project_id, group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ast_cluster (
    project_id       TEXT NOT NULL,
    cluster_id       TEXT NOT NULL,              -- 'AST0000' or similar
    size             INTEGER,
    reducible        INTEGER,
    all_exact        INTEGER DEFAULT 0,          -- 0/1 boolean
    avg_score        REAL,
    min_score        REAL,
    total_executions INTEGER,
    in_sql_hash      INTEGER DEFAULT 0,
    ast_exclusive    INTEGER DEFAULT 0,
    PRIMARY KEY (project_id, cluster_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ast_cluster_member (
    project_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    report_id  TEXT NOT NULL,
    PRIMARY KEY (project_id, cluster_id, report_id),
    FOREIGN KEY (project_id, cluster_id) REFERENCES ast_cluster(project_id, cluster_id) ON DELETE CASCADE
);

-- Families run on the POST-AST canonical set. The family stage picks a
-- canonical_id per multi-member group (highest executions). Non-canonical
-- members are removed from the downstream clustering input.
CREATE TABLE IF NOT EXISTS family (
    project_id       TEXT NOT NULL,
    base             TEXT NOT NULL,
    size             INTEGER,
    reducible        INTEGER,
    total_executions INTEGER,
    canonical_id     TEXT,
    PRIMARY KEY (project_id, base),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS family_member (
    project_id     TEXT NOT NULL,
    base           TEXT NOT NULL,
    report_id      TEXT NOT NULL,
    variant_suffix TEXT,
    is_canonical   INTEGER DEFAULT 0,
    PRIMARY KEY (project_id, base, report_id),
    FOREIGN KEY (project_id, base) REFERENCES family(project_id, base) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS similarity_cluster (
    project_id        TEXT NOT NULL,
    cluster_id        TEXT NOT NULL,             -- 'C001'
    size              INTEGER,
    primary_report_id TEXT,
    primary_name      TEXT,
    total_executions  INTEGER,
    total_users       INTEGER,
    metric_count      INTEGER,
    table_count       INTEGER,
    filter_count      INTEGER,
    avg_similarity    REAL,
    -- denormalized LLM action fields for convenience
    llm_label         TEXT,
    llm_action        TEXT,
    llm_removable     INTEGER,
    PRIMARY KEY (project_id, cluster_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS similarity_cluster_member (
    project_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    report_id  TEXT NOT NULL,
    PRIMARY KEY (project_id, cluster_id, report_id),
    FOREIGN KEY (project_id, cluster_id) REFERENCES similarity_cluster(project_id, cluster_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS similarity_cluster_common (
    project_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    kind       TEXT NOT NULL,                    -- 'metric' | 'table' | 'filter'
    name       TEXT NOT NULL,
    PRIMARY KEY (project_id, cluster_id, kind, name),
    FOREIGN KEY (project_id, cluster_id) REFERENCES similarity_cluster(project_id, cluster_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS similarity_pair (
    project_id TEXT NOT NULL,
    id_a       TEXT NOT NULL,
    id_b       TEXT NOT NULL,
    combined   REAL,
    metric     REAL,
    tables     REAL,    -- 'table' is a reserved-ish column name, use 'tables' for clarity
    filters    REAL,
    ast        REAL,
    PRIMARY KEY (project_id, id_a, id_b),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_sim_pair_combined ON similarity_pair(project_id, combined DESC);

CREATE TABLE IF NOT EXISTS llm_review (
    project_id            TEXT NOT NULL,
    cluster_id            TEXT NOT NULL,
    label                 TEXT,
    business_function     TEXT,
    relationship          TEXT,
    action                TEXT,                  -- MERGE_IMMEDIATE / PARAMETERIZE / REVIEW_WITH_OWNER / KEEP_SEPARATE
    confidence            TEXT,                  -- HIGH / MEDIUM / LOW
    consolidation_detail  TEXT,
    keep_report           TEXT,
    removable_count       INTEGER,
    PRIMARY KEY (project_id, cluster_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

-- ---------------- Summary + funnel -----------------------------

-- One wide row per project holding the scalar stats.
CREATE TABLE IF NOT EXISTS run_summary (
    project_id              TEXT PRIMARY KEY,
    total_inventory         INTEGER,
    total_dossiers          INTEGER,
    dossiers_with_telemetry INTEGER,
    total_objects           INTEGER,
    after_telemetry         INTEGER,
    retired                 INTEGER,
    after_collision_collapse INTEGER,
    collision_collapsed     INTEGER,
    after_fingerprint       INTEGER,
    fingerprint_groups      INTEGER,
    fingerprint_multi_groups INTEGER,
    fingerprint_reports_in_groups INTEGER,
    fingerprint_removable   INTEGER,
    ast_parsed              INTEGER,
    ast_clusters            INTEGER,
    ast_multi_clusters      INTEGER,
    ast_reducible           INTEGER,
    ast_exact_groups        INTEGER,
    ast_exact_removable     INTEGER,
    ast_final_to_migrate    INTEGER,
    after_sql_hash          INTEGER,
    sql_hash_sequential_removable INTEGER,
    sql_hash_sequential_groups    INTEGER,
    after_ast               INTEGER,
    ast_sequential_removable INTEGER,
    ast_sequential_groups    INTEGER,
    after_family            INTEGER,
    family_reducible        INTEGER,
    after_post_ast_family   INTEGER,
    post_ast_family_collapsed INTEGER,
    after_similarity        INTEGER,
    clusters_total          INTEGER,
    clusters_multi          INTEGER,
    clusters_singleton      INTEGER,
    llm_reviews_total       INTEGER,
    llm_removable_raw       INTEGER,
    llm_safe_clusters       INTEGER,
    reduction_total         INTEGER,
    reduction_pct           REAL,
    -- "Alternative: Family Name Dedup" baseline shown on the Overview card
    alt_family_label        TEXT,
    alt_family_value        INTEGER,
    alt_family_detail       TEXT,
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS funnel_stage (
    project_id TEXT NOT NULL,
    ordinal    INTEGER NOT NULL,
    label      TEXT,
    value      INTEGER,
    detail     TEXT,
    PRIMARY KEY (project_id, ordinal),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tier_breakdown (
    project_id TEXT NOT NULL,
    tier       TEXT NOT NULL,                    -- 'exact' / 'fuzzy' / 'noMatch'
    count      INTEGER,
    PRIMARY KEY (project_id, tier),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS object_type_count (
    project_id TEXT NOT NULL,
    name       TEXT NOT NULL,                    -- 'reports', 'attributes', 'metrics', etc.
    count      INTEGER,
    PRIMARY KEY (project_id, name),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

-- Top-N lists for metrics / tables / filters / executed reports.
CREATE TABLE IF NOT EXISTS top_list (
    project_id TEXT NOT NULL,
    kind       TEXT NOT NULL,                    -- 'metric' | 'table' | 'filter' | 'executed'
    rank       INTEGER NOT NULL,                 -- 1..N
    name       TEXT,                             -- metric/table/filter name, or report name for 'executed'
    count      INTEGER,                          -- frequency for metric/table/filter
    report_id  TEXT,                             -- set only for kind='executed'
    executions INTEGER,                          -- set only for kind='executed'
    users      INTEGER,                          -- set only for kind='executed'
    last_exec  TEXT,                             -- set only for kind='executed'
    PRIMARY KEY (project_id, kind, rank),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

-- Per-project LLM-action histogram (e.g. PARAMETERIZE: 21, REVIEW_WITH_OWNER: 1)
CREATE TABLE IF NOT EXISTS llm_action_count (
    project_id TEXT NOT NULL,
    action     TEXT NOT NULL,
    count      INTEGER,
    PRIMARY KEY (project_id, action),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

-- ================================================================
-- RAW DATA tables (Phase 3a)
--
-- These mirror the "raw" inputs that the existing build scripts read
-- from each project's inventory/, telemetry/, analysis/, sql/ dirs.
-- The build scripts continue to compute in Python (no compute rewire),
-- but the same raw data is now queryable from the DB.
--
-- These tables are separate from the derived `report`, `report_*`,
-- etc. tables above. They preserve batch distinctions (GO has
-- multiple enrichment batches) and unmatched telemetry rows.
-- ================================================================

CREATE TABLE IF NOT EXISTS raw_inventory (
    project_id        TEXT NOT NULL,
    report_id         TEXT NOT NULL,
    batch             TEXT NOT NULL,    -- 'original' | 'new' | 'added' | 'dossier' | 'inventory'
    name              TEXT,
    owner             TEXT,
    folder_path       TEXT,
    date_created      TEXT,
    date_modified     TEXT,
    object_type       INTEGER,          -- 3=report, 55=dossier
    subtype           INTEGER,
    source_type       TEXT,
    prompted          INTEGER DEFAULT 0,
    has_sql           INTEGER DEFAULT 0,
    sql_hash          TEXT,
    metric_count      INTEGER DEFAULT 0,
    table_count       INTEGER DEFAULT 0,
    filter_count      INTEGER DEFAULT 0,
    PRIMARY KEY (project_id, report_id, batch),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_raw_inv_name ON raw_inventory(project_id, name);
CREATE INDEX IF NOT EXISTS ix_raw_inv_type ON raw_inventory(project_id, object_type);

CREATE TABLE IF NOT EXISTS raw_inventory_metric (
    project_id  TEXT NOT NULL,
    report_id   TEXT NOT NULL,
    batch       TEXT NOT NULL,
    metric_id   TEXT,
    metric_name TEXT NOT NULL,
    PRIMARY KEY (project_id, report_id, batch, metric_name),
    FOREIGN KEY (project_id, report_id, batch)
        REFERENCES raw_inventory(project_id, report_id, batch) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_raw_inv_metric_name ON raw_inventory_metric(project_id, metric_name);

CREATE TABLE IF NOT EXISTS raw_inventory_table (
    project_id TEXT NOT NULL,
    report_id  TEXT NOT NULL,
    batch      TEXT NOT NULL,
    table_name TEXT NOT NULL,
    PRIMARY KEY (project_id, report_id, batch, table_name),
    FOREIGN KEY (project_id, report_id, batch)
        REFERENCES raw_inventory(project_id, report_id, batch) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_raw_inv_table_name ON raw_inventory_table(project_id, table_name);

CREATE TABLE IF NOT EXISTS raw_inventory_filter (
    project_id  TEXT NOT NULL,
    report_id   TEXT NOT NULL,
    batch       TEXT NOT NULL,
    filter_id   TEXT,
    filter_name TEXT NOT NULL,
    PRIMARY KEY (project_id, report_id, batch, filter_name),
    FOREIGN KEY (project_id, report_id, batch)
        REFERENCES raw_inventory(project_id, report_id, batch) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_raw_inv_filter_name ON raw_inventory_filter(project_id, filter_name);

-- Template attributes from MSTR report definition (rec["attributes"] in the
-- inventory JSON). Kept separate from raw_inventory_filter which holds the
-- WHERE-clause constraint references.
CREATE TABLE IF NOT EXISTS raw_inventory_attribute (
    project_id      TEXT NOT NULL,
    report_id       TEXT NOT NULL,
    batch           TEXT NOT NULL,
    attribute_id    TEXT,
    attribute_name  TEXT NOT NULL,
    PRIMARY KEY (project_id, report_id, batch, attribute_name),
    FOREIGN KEY (project_id, report_id, batch)
        REFERENCES raw_inventory(project_id, report_id, batch) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_raw_inv_attribute_name ON raw_inventory_attribute(project_id, attribute_name);

CREATE TABLE IF NOT EXISTS raw_inventory_sql (
    project_id TEXT NOT NULL,
    report_id  TEXT NOT NULL,
    batch      TEXT NOT NULL,
    sql_text   TEXT,
    sql_hash   TEXT,
    sql_error  TEXT,
    PRIMARY KEY (project_id, report_id, batch),
    FOREIGN KEY (project_id, report_id, batch)
        REFERENCES raw_inventory(project_id, report_id, batch) ON DELETE CASCADE
);

-- Raw telemetry rows from telemetry/active_reports.json + retire_reports.json.
-- Rows are keyed by the inventory ID they're attached to.
-- `matched` = 1 for active (telemetry hit), 0 for retire (no telemetry).
CREATE TABLE IF NOT EXISTS raw_telemetry (
    project_id         TEXT NOT NULL,
    report_id          TEXT NOT NULL,
    matched            INTEGER NOT NULL,         -- 1=active, 0=retire
    match_tier         TEXT,                     -- exact_name | full_path | fuzzy | none
    match_score        REAL,
    matched_telemetry_name TEXT,
    telemetry_path     TEXT,
    total_executions   INTEGER DEFAULT 0,
    total_users        INTEGER DEFAULT 0,
    last_exec_ts       TEXT,
    PRIMARY KEY (project_id, report_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_raw_tel_matched ON raw_telemetry(project_id, matched);

-- Raw AST cache. Two shapes in the wild:
--   GO: key = report_id (32-char), value = list of subtree hashes
--   GI: key = ast cluster fingerprint (16-char), value = list of subtree hashes
-- We store both; kind discriminates.
CREATE TABLE IF NOT EXISTS raw_ast_cache (
    project_id      TEXT NOT NULL,
    cache_key       TEXT NOT NULL,        -- report_id or ast cluster hash
    kind            TEXT NOT NULL,        -- 'report' | 'ast_cluster'
    subtree_count   INTEGER,
    subtree_hashes  TEXT,                 -- JSON array
    PRIMARY KEY (project_id, cache_key),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

-- Raw object-type counts straight from analysis/summary.json byCategory.
CREATE TABLE IF NOT EXISTS raw_object_type (
    project_id TEXT NOT NULL,
    category   TEXT NOT NULL,             -- e.g. 'reports', 'attributes', 'metrics'
    count      INTEGER,
    PRIMARY KEY (project_id, category),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

-- Out-of-band path cache populated by fetch_all_missing_paths.py / merge_go_dossiers.py
CREATE TABLE IF NOT EXISTS raw_fetched_path (
    project_id TEXT NOT NULL,
    report_id  TEXT NOT NULL,
    path       TEXT,
    PRIMARY KEY (project_id, report_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

-- Raw user activity log from UserActivity.csv — NOT scoped per project.
-- Captures granular (user, report, action) rows across the whole MSTR server.
-- Used to reconstruct "who ran what and when" beyond the aggregate counts in
-- raw_telemetry.
CREATE TABLE IF NOT EXISTS raw_user_activity (
    row_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_name     TEXT,
    object_id       TEXT,        -- 'Unknown' if MSTR couldn't resolve the object
    folder_path     TEXT,        -- begins with /<Project Name>/... when classifiable
    user            TEXT,
    action          TEXT,        -- 'Execute', 'Edit in Design Mode', 'Export to Excel', etc.
    executions      INTEGER,
    sessions        INTEGER,
    errors          INTEGER,
    last_execution  TEXT         -- "YYYY-MM-DD HH:MM:SS" string as provided
);
CREATE INDEX IF NOT EXISTS ix_rua_object  ON raw_user_activity(object_id);
CREATE INDEX IF NOT EXISTS ix_rua_user    ON raw_user_activity(user);
CREATE INDEX IF NOT EXISTS ix_rua_action  ON raw_user_activity(action);
CREATE INDEX IF NOT EXISTS ix_rua_folder  ON raw_user_activity(folder_path);


-- Record of which raw files were ingested, with row counts, for auditability.
CREATE TABLE IF NOT EXISTS raw_ingest_log (
    project_id  TEXT NOT NULL,
    source_file TEXT NOT NULL,
    kind        TEXT NOT NULL,         -- 'inventory' | 'telemetry' | 'ast_cache' | 'object_type' | 'fetched_path'
    batch       TEXT,                  -- e.g. 'original' for inventory files
    rows_read   INTEGER,
    rows_loaded INTEGER,
    ingested_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, source_file),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

-- ================================================================
-- CROSS-PROJECT RATIONALIZATION tables
--
-- Scope: find reports + data-source patterns that appear in more than
-- one project. Populated by stage_cross_project (db/compute/pipeline.py).
-- Not project-scoped — these tables describe relationships ACROSS projects.
-- ================================================================

-- Reports with the same normalized name in 2+ projects (migration / copy candidates)
CREATE TABLE IF NOT EXISTS cross_project_name_match (
    match_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_name  TEXT NOT NULL,
    n_projects       INTEGER NOT NULL,
    n_reports        INTEGER NOT NULL,
    projects_csv     TEXT                -- sorted comma list of project_ids
);
CREATE INDEX IF NOT EXISTS ix_cpnm_name ON cross_project_name_match(normalized_name);

-- Individual members of each cross-project name match (one row per report)
CREATE TABLE IF NOT EXISTS cross_project_name_member (
    match_id    INTEGER NOT NULL,
    project_id  TEXT NOT NULL,
    report_id   TEXT NOT NULL,
    name        TEXT,
    path        TEXT,
    executions  INTEGER DEFAULT 0,
    PRIMARY KEY (match_id, project_id, report_id),
    FOREIGN KEY (match_id) REFERENCES cross_project_name_match(match_id) ON DELETE CASCADE
);

-- Byte-identical normalized SQL shared across projects
CREATE TABLE IF NOT EXISTS cross_project_sql_match (
    match_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    sql_hash     TEXT NOT NULL,
    n_projects   INTEGER,
    n_reports    INTEGER,
    projects_csv TEXT
);
CREATE TABLE IF NOT EXISTS cross_project_sql_member (
    match_id    INTEGER NOT NULL,
    project_id  TEXT NOT NULL,
    report_id   TEXT NOT NULL,
    PRIMARY KEY (match_id, project_id, report_id),
    FOREIGN KEY (match_id) REFERENCES cross_project_sql_match(match_id) ON DELETE CASCADE
);

-- Tables used across 2+ projects (data-source overlap)
CREATE TABLE IF NOT EXISTS cross_project_shared_table (
    table_name   TEXT PRIMARY KEY,
    n_projects   INTEGER,
    n_references INTEGER,
    projects_csv TEXT
);

-- Metrics used across 2+ projects
CREATE TABLE IF NOT EXISTS cross_project_shared_metric (
    metric_name  TEXT PRIMARY KEY,
    n_projects   INTEGER,
    n_references INTEGER,
    projects_csv TEXT
);

-- Family-name roots shared across projects ("GFE085a - Projections by PD" etc.)
CREATE TABLE IF NOT EXISTS cross_project_family (
    family_base  TEXT PRIMARY KEY,
    n_projects   INTEGER,
    n_reports    INTEGER,
    projects_csv TEXT
);

-- ---------------- Post-AST Family collapse ----------------------
-- Runs on the post-AST canonical set. Groups surviving reports by family_base
-- (same name-pattern logic as stage_family) and picks the top-executions
-- member as the family canonical. Collapsed siblings are excluded from the
-- downstream Similarity + Semantic clustering input.
--
-- Rationale: the standard Families tab catches name-siblings across the
-- WHOLE active universe (many of which fingerprint/SQL-hash/AST then merge).
-- But many name-siblings survive all structural dedup because their SQL
-- differs slightly (parameterized variants). Collapsing them at this stage
-- means Jaccard/Semantic cluster on truly distinct reports only.

CREATE TABLE IF NOT EXISTS post_ast_family (
    project_id        TEXT NOT NULL,
    base              TEXT NOT NULL,
    size              INTEGER NOT NULL,
    reducible         INTEGER NOT NULL,
    canonical_id      TEXT,
    total_executions  INTEGER,
    PRIMARY KEY (project_id, base),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS post_ast_family_member (
    project_id     TEXT NOT NULL,
    base           TEXT NOT NULL,
    report_id      TEXT NOT NULL,
    is_canonical   INTEGER DEFAULT 0,
    PRIMARY KEY (project_id, base, report_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_post_ast_family_member_rid
    ON post_ast_family_member(project_id, report_id);

-- ---------------- Semantic (embedding-based) clustering ---------
-- Runs on the post-AST survivor set — same entry point as stage_similarity.
-- Complements the fuzzy Jaccard clustering by capturing natural-language
-- semantic similarity (names, paths, terminology) that token-overlap misses.

-- Cached per-report vector embeddings. content_hash lets us re-embed only
-- reports whose normalized text has changed.
CREATE TABLE IF NOT EXISTS semantic_embedding (
    project_id    TEXT NOT NULL,
    report_id     TEXT NOT NULL,
    model         TEXT NOT NULL,
    dim           INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,
    vector        BLOB NOT NULL,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, report_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS semantic_cluster (
    project_id        TEXT NOT NULL,
    cluster_id        TEXT NOT NULL,
    size              INTEGER NOT NULL,
    primary_report_id TEXT,
    primary_name      TEXT,
    total_executions  INTEGER,
    total_users       INTEGER,
    avg_cosine        REAL,
    min_cosine        REAL,
    llm_label         TEXT,
    llm_action        TEXT,
    llm_confidence    TEXT,
    llm_removable     INTEGER,
    PRIMARY KEY (project_id, cluster_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS semantic_cluster_member (
    project_id         TEXT NOT NULL,
    cluster_id         TEXT NOT NULL,
    report_id          TEXT NOT NULL,
    cosine_to_primary  REAL,
    PRIMARY KEY (project_id, cluster_id, report_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_semantic_cluster_member_rid
    ON semantic_cluster_member(project_id, report_id);

CREATE TABLE IF NOT EXISTS semantic_pair (
    project_id  TEXT NOT NULL,
    id_a        TEXT NOT NULL,
    id_b        TEXT NOT NULL,
    cosine      REAL NOT NULL,
    PRIMARY KEY (project_id, id_a, id_b),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_semantic_pair_cosine ON semantic_pair(project_id, cosine DESC);

CREATE TABLE IF NOT EXISTS semantic_llm_review (
    project_id            TEXT NOT NULL,
    cluster_id            TEXT NOT NULL,
    label                 TEXT,
    business_function     TEXT,
    relationship          TEXT,
    action                TEXT,
    confidence            TEXT,
    consolidation_detail  TEXT,
    keep_report           TEXT,
    removable_count       INTEGER,
    error                 TEXT,
    PRIMARY KEY (project_id, cluster_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);
