// API client for rationalization analysis data
// Fetches static JSON files from public/data/{projectName}/

export interface RationalizationSummary {
  projectName: string;
  projectId: string;
  snapshotDate?: string;
  totalInventory: number;
  totalReportsInventory?: number;
  totalObjects?: number;
  afterTelemetry: number;
  retired: number;
  afterCollisionCollapse?: number;
  collisionCollapsed?: number;
  afterFingerprint?: number;
  fingerprintGroups?: number;
  fingerprintMultiGroups?: number;
  fingerprintReportsInGroups?: number;
  fingerprintRemovable?: number;
  astParsed?: number;
  astClusters?: number;
  astMultiClusters?: number;
  astReducible?: number;
  astExactGroups?: number;
  astExactRemovable?: number;
  astFinalToMigrate?: number;
  afterAst?: number;
  astSequentialRemovable?: number;
  astSequentialGroups?: number;
  afterSqlHash?: number;
  sqlHashSequentialRemovable?: number;
  sqlHashSequentialGroups?: number;
  afterFamily?: number;
  familyReducible?: number;
  afterPostAstFamily?: number;
  postAstFamilyCollapsed?: number;
  afterSimilarity: number;
  afterSemantic?: number;
  semanticClustersTotal?: number;
  semanticSingletons?: number;
  semanticLlmRemovable?: number;
  clustersTotal: number;
  clustersMulti: number;
  clustersSingleton: number;
  llmReviewsTotal?: number;
  llmReviewsByAction?: Record<string, number>;
  llmSafeClusters?: number;
  llmRemovableRaw?: number;
  reductionTotal: number;
  reductionPct: number;
  funnel: FunnelStage[];
  alternativeFamilyMethod?: { label: string; value: number; detail: string };
  tierBreakdown: { exact: number; fuzzy: number; noMatch: number };
  objectTypes: { name: string; count: number }[];
  topMetrics: { name: string; count: number }[];
  topTables: { name: string; count: number }[];
  topFilters: { name: string; count: number }[];
  topExecuted: TopReport[];
  scenarios: Scenario[];
}

export interface FunnelStage {
  label: string;
  value: number;
  detail: string;
}

export interface TopReport {
  id: string;
  name: string;
  executions: number;
  users: number;
  lastExec: string;
}

export interface Scenario {
  name: string;
  count: number;
  approach: string;
  confidence: string;
}

export interface ClusterMeta {
  id: string;
  size: number;
  primaryReportId: string;
  primaryName: string;
  totalExecutions: number;
  totalUsers: number;
  commonMetrics: string[];
  commonTables: string[];
  commonFilters: string[];
  metricCount: number;
  tableCount: number;
  filterCount: number;
  memberIds: string[];
  isSingletons?: boolean;
  topExampleName?: string;
  dataQuality?: 'high' | 'mixed' | 'low' | 'unknown';
  dataQualityReason?: 'ok' | 'empty_features' | 'sparse_features' | 'mixed_features' | 'no_members';
  emptyMemberCount?: number;
}

export interface SemanticClusterLlm {
  label: string;
  businessFunction: string;
  relationship: string;
  action: string;
  confidence: string;
  detail: string;
  keepReport: string;
  removableCount: number;
  /** IDs GPT explicitly marked removable, from the visible-members prompt window (up to 25). */
  removableIds?: string[];
  error?: string | null;
}

export interface SemanticClusterMember {
  id: string;
  cosineToPrimary: number | null;
}

export interface SemanticCluster {
  id: string;
  size: number;
  primaryReportId: string;
  primaryName: string;
  totalExecutions: number;
  totalUsers: number;
  avgCosine: number | null;
  minCosine: number | null;
  members: SemanticClusterMember[];
  memberIds: string[];
  isSingletons?: boolean;
  topExampleName?: string;
  llm?: SemanticClusterLlm;
  dataQuality?: 'high' | 'mixed' | 'low' | 'unknown';
  dataQualityReason?: 'ok' | 'empty_features' | 'sparse_features' | 'mixed_features' | 'no_members';
  emptyMemberCount?: number;
}

export interface ReportDetail {
  id: string;
  name: string;
  owner: string;
  path: string;
  executions: number;
  users: number;
  lastExec: string;
  matchTier: string;
  metrics: string[];
  tables: string[];
  filters: string[];
  attributes?: string[];
  metricCount: number;
  tableCount: number;
  filterCount: number;
  attributeCount?: number;
  clusterId: string | null;
  familyBase: string;
  dateCreated: string | null;
  dateModified: string | null;
  sql?: string | null;
  sqlError?: string | null;
  sourceType?: string | null;
  status?: string | null;
  isFinalCanonical?: boolean;
  /** If this report is a family canonical, the collapsed siblings it stands
   *  for. Empty otherwise. */
  familySiblings?: FamilyCollapsedSibling[];
  /** For reports copied into the Combined Reports Project, the source
   *  project id (global-operational / global-insight / insight). Null for
   *  regular project rows. Drives the [GO]/[GI]/[IN] origin pill in the UI. */
  sourceProjectId?: string | null;
}

export interface FamilyCollapsedSibling {
  id: string;
  name: string;
  suffix: string;
  status: string;
  executions: number;
}

export interface PairSimilarity {
  combined: number;
  metric: number;
  table: number;
  filter: number;
  ast?: number;
}

export interface FamilyMember {
  id: string;
  name: string;
  variantSuffix: string;
  executions: number;
  users: number;
  lastExec: string;
}

export interface Family {
  base: string;
  size: number;
  reducible: number;
  totalExecutions: number;
  members: FamilyMember[];
}

export interface FingerprintMember {
  id: string;
  name: string;
  path: string;
  executions: number;
  users: number;
  lastExec: string;
}

export interface FingerprintGroup {
  id: string;
  size: number;
  reducible: number;
  metricCount: number;
  tableCount: number;
  filterCount: number;
  metrics: string[];
  tables: string[];
  filters: string[];
  totalExecutions: number;
  members: FingerprintMember[];
}

export interface SqlHashGroupMember {
  id: string;
  name: string;
  path: string;
  executions: number;
  users: number;
  lastExec: string;
}

export interface SqlHashGroup {
  id: string;
  size: number;
  reducible: number;
  totalExecutions: number;
  sqlHash: string;
  sqlPreview: string;
  members: SqlHashGroupMember[];
}

export interface AstClusterMember {
  id: string;
  name: string;
  path: string;
  executions: number;
  users: number;
  lastExec: string;
}

export interface AstCluster {
  id: string;
  size: number;
  reducible: number;
  allExact: boolean;
  avgScore: number;
  minScore: number;
  totalExecutions: number;
  members: AstClusterMember[];
  inSqlHash?: boolean;
  astExclusive?: boolean;
}

function dataUrl(projectName: string, file: string): string {
  return `/data/${encodeURIComponent(projectName)}/${file}`;
}

export async function fetchSummary(projectName: string): Promise<RationalizationSummary> {
  const res = await fetch(dataUrl(projectName, "summary.json"));
  if (!res.ok) throw new Error(`Failed to load summary: ${res.status}`);
  return res.json();
}

export async function fetchClusters(projectName: string): Promise<ClusterMeta[]> {
  const res = await fetch(dataUrl(projectName, "clusters.json"));
  if (!res.ok) throw new Error(`Failed to load clusters: ${res.status}`);
  return res.json();
}

export interface LlmReviewSummary {
  reviewed: number;
  removable: number;
  safeToAuto: number;
  byAction: Record<string, number>;
}

export interface PlaygroundExperimentIndexEntry {
  id: string;
  name: string;
  project: string;
  createdAt: string;
  stats: {
    inputReports: number;
    reportsEmbedded: number;
    edges: number;
    multiClusters: number;
    singletons: number;
    finalUnique: number;
  };
  config: {
    scope: string;
    sourceFilter: string;
    model: string;
    dim: number;
    threshold: number;
    minClusterSize: number;
    fields: Record<string, boolean | number>;
  };
  llmReviewedAt?: string;
  llmReviewSummary?: LlmReviewSummary;
}

export interface PlaygroundVizPoint {
  id: string;
  x: number;
  y: number;
  clusterId: string | null;
  name: string;
  executions: number;
}

export interface PlaygroundViz {
  scatter: PlaygroundVizPoint[];
  topPairs: Array<[string, string, number]>; // [rid_a, rid_b, cosine]
  thresholdUsed: number;
}

export interface PlaygroundExperiment {
  id: string;
  name: string;
  config: Record<string, unknown>;
  createdAt: string;
  stats: PlaygroundExperimentIndexEntry['stats'];
  clusters: Array<{
    id: string;
    size: number;
    primaryReportId: string;
    primaryName: string;
    totalExecutions: number;
    avgCosine: number | null;
    minCosine: number | null;
    memberIds: string[];
  }>;
  singletonIds: string[];
  viz?: PlaygroundViz;
}

// Playground data lives OUTSIDE public/ — Vite's file watcher would
// full-page-reload the UI whenever an experiment wrote a file there and
// kick the user back to the login screen. The sidecar is the only source.
export async function fetchPlaygroundIndex(): Promise<PlaygroundExperimentIndexEntry[]> {
  try {
    const res = await fetch("/playground_api/index");
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

export async function fetchPlaygroundExperiment(id: string): Promise<PlaygroundExperiment | null> {
  try {
    const res = await fetch(`/playground_api/exp/${encodeURIComponent(id)}`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export interface PlaygroundTask {
  id: string;
  status: "running" | "completed" | "failed" | "interrupted";
  name: string;
  expId: string | null;
  config: Record<string, unknown>;
  startedAt: string;
  endedAt: string | null;
  error: string | null;
}

async function _unwrapError(res: Response): Promise<never> {
  let detail = `HTTP ${res.status}`;
  try {
    const err = await res.json();
    detail = err.detail || err.error || detail;
  } catch { /* non-JSON */ }
  throw new Error(detail);
}

/** Kick off an experiment asynchronously. Returns the task descriptor. Poll
 *  fetchPlaygroundTask(task.id) until status !== 'running'. */
export async function runPlaygroundExperiment(config: Record<string, unknown>): Promise<PlaygroundTask> {
  const res = await fetch("/playground_api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) await _unwrapError(res);
  return res.json();
}

/** Kick off a business-domain classification. Same task contract as run. */
export async function runDomainClassification(config: Record<string, unknown>): Promise<PlaygroundTask> {
  const res = await fetch("/playground_api/domains/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) await _unwrapError(res);
  return res.json();
}

export interface DomainClassifiedReport {
  id: string;
  name: string;
  confidence: number | null;
  classifiedAt: string | null;
  executions: number;
}

export interface DomainResults {
  projectId: string;
  totalClassified: number;
  byDomain: Record<string, DomainClassifiedReport[]>;
  lastRunAt: string | null;
}

export async function fetchDomainResults(project: string): Promise<DomainResults> {
  const res = await fetch(`/playground_api/domains/results/${encodeURIComponent(project)}`);
  if (!res.ok) {
    if (res.status === 404) {
      return { projectId: project, totalClassified: 0, byDomain: {}, lastRunAt: null };
    }
    await _unwrapError(res);
  }
  return res.json();
}

// ============================================================
// User-retained reports — manual retention overrides that flow
// into the "Final Reports to Keep" set.
// ============================================================

export interface RetainedReport {
  id: string;
  retainedAt: string;
  note: string | null;
}

export async function fetchRetainedReports(project: string): Promise<Set<string>> {
  try {
    const res = await fetch(`/playground_api/retained/${encodeURIComponent(project)}`);
    if (!res.ok) return new Set();
    const data: { retained: RetainedReport[] } = await res.json();
    return new Set(data.retained.map((r) => r.id));
  } catch {
    return new Set();
  }
}

export async function toggleReportRetained(
  project: string, reportId: string, retained: boolean, note?: string,
): Promise<boolean> {
  try {
    const res = await fetch(`/playground_api/retained/${encodeURIComponent(project)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reportId, retained, note }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// Mirror of retained, for explicit "retire" overrides (user says "drop this
// even though the LLM/cluster kept it"). The two sets are mutually exclusive
// server-side: toggling one clears the other.
export async function fetchRetiredOverrides(project: string): Promise<Set<string>> {
  try {
    const res = await fetch(`/playground_api/retired_overrides/${encodeURIComponent(project)}`);
    if (!res.ok) return new Set();
    const data: { retired: { id: string }[] } = await res.json();
    return new Set((data.retired || []).map((r) => r.id));
  } catch {
    return new Set();
  }
}

export async function toggleReportRetired(
  project: string, reportId: string, retired: boolean, note?: string,
): Promise<boolean> {
  try {
    const res = await fetch(`/playground_api/retired_overrides/${encodeURIComponent(project)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reportId, retired, note }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export interface DensityDefaults {
  umap_n_neighbors: number;
  umap_min_dist: number;
  umap_n_components: number;
  hdbscan_min_cluster_size: number;
  hdbscan_min_samples: number;
  hdbscan_cluster_selection_method: string;
}

/** Kick off UMAP + HDBSCAN density clustering. Same task contract as run. */
export async function runDensityClustering(config: Record<string, unknown>): Promise<PlaygroundTask> {
  const res = await fetch("/playground_api/density/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) await _unwrapError(res);
  return res.json();
}

// ============================================================
// Named embedding sets — the "asset layer" that clustering builds on.
// Create once, reuse across Semantic / Density / any future clusterer.
// ============================================================

export interface EmbeddingSet {
  name: string;
  projectId: string;
  scope: string;
  sourceFilter: string;
  model: string;
  dim: number;
  fields: Record<string, boolean | number>;
  reportCount: number;
  tokenEstimate: number | null;
  createdAt: string;
  refreshedAt: string | null;
}

export interface EmbeddingSetPreview {
  totalReports: number;
  cached: number;
  new: number;
  tokenEstimate: number;
  estimatedCostUsd: number;
  model: string;
  projectId: string;
}

export interface EmbeddingSetConfig {
  name: string;
  project: string;
  scope: string;
  sourceFilter: string;
  domainFilter?: string | null;  // "all"/null = no filter, else an exact domain name
  model: string;
  dimensions: number;
  fields: Record<string, boolean | number>;
  limit?: number | null;
}

export async function fetchEmbeddingSets(project?: string): Promise<EmbeddingSet[]> {
  try {
    const q = project ? `?project=${encodeURIComponent(project)}` : "";
    const res = await fetch(`/playground_api/embeddings${q}`);
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

export async function previewEmbeddingSet(config: EmbeddingSetConfig): Promise<EmbeddingSetPreview | null> {
  try {
    const res = await fetch("/playground_api/embeddings/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function runEmbeddingSet(config: EmbeddingSetConfig): Promise<PlaygroundTask> {
  // Hard timeout so a silent hang surfaces as a clear error in the run log
  // instead of sitting forever behind the button.
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 15000);
  try {
    const res = await fetch("/playground_api/embeddings/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
      signal: ctrl.signal,
    });
    if (!res.ok) await _unwrapError(res);
    return res.json();
  } catch (e) {
    if (e instanceof Error && e.name === "AbortError") {
      throw new Error(
        "fetch timed out after 15s — Vite proxy to /playground_api/ isn't reaching the sidecar. " +
        "Hard-refresh the tab (Ctrl+Shift+R); if it still hangs, check the browser Network tab."
      );
    }
    throw e;
  } finally {
    clearTimeout(t);
  }
}

export async function deleteEmbeddingSet(name: string): Promise<void> {
  await fetch(`/playground_api/embeddings/${encodeURIComponent(name)}`, { method: "DELETE" });
}

// ============================================================
// Combined Reports Project — virtual project uniting final-kept reports
// from GO, GI, INSIGHT. Rebuilt on demand.
// ============================================================

export interface CombinedSourceStatus {
  sourceProjectId: string;
  sourceName: string;
  builtAt: string | null;
  reportCount: number;
  stale: boolean;
  reason: string | null;
  currentCount: number;
}

export interface CombinedStatus {
  projectId: string;
  totalReports: number;
  lastBuiltAt: string | null;
  stale: boolean;
  sources: CombinedSourceStatus[];
}

export async function fetchCombinedStatus(): Promise<CombinedStatus | null> {
  try {
    const res = await fetch("/playground_api/combined/status");
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function rebuildCombinedReports(): Promise<PlaygroundTask> {
  const res = await fetch("/playground_api/combined/rebuild", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_pipeline: true, emit: true }),
  });
  if (!res.ok) await _unwrapError(res);
  return res.json();
}

export async function fetchDensityDefaults(): Promise<DensityDefaults | null> {
  try {
    const res = await fetch("/playground_api/density/defaults");
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

/** Kick off an async LLM review of every multi-member cluster in an
 *  experiment. Same task contract as the other runners — poll
 *  fetchPlaygroundTask(id) until status != 'running'. */
export async function runExperimentLlmReview(
  expId: string,
  project: string,
): Promise<PlaygroundTask> {
  const res = await fetch(`/playground_api/exp/${encodeURIComponent(expId)}/llm_review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project }),
  });
  if (!res.ok) await _unwrapError(res);
  return res.json();
}

/** Default fixed taxonomy exposed by the sidecar. */
export async function fetchDomainTaxonomy(): Promise<string[]> {
  try {
    const res = await fetch("/playground_api/domains/taxonomy");
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data.domains) ? data.domains : [];
  } catch {
    return [];
  }
}

export async function fetchPlaygroundTask(id: string): Promise<PlaygroundTask | null> {
  try {
    const res = await fetch(`/playground_api/task/${encodeURIComponent(id)}`);
    if (res.status === 404) return null;
    if (!res.ok) await _unwrapError(res);
    return res.json();
  } catch {
    return null;
  }
}

export async function fetchPlaygroundTasks(): Promise<PlaygroundTask[]> {
  try {
    const res = await fetch("/playground_api/tasks");
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

export async function checkPlaygroundHealth(): Promise<{ ok: boolean; openai_key_set: boolean } | null> {
  try {
    const res = await fetch("/playground_api/health");
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function deletePlaygroundExperiment(id: string): Promise<void> {
  await fetch(`/playground_api/exp/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export interface ActiveExperimentInfo {
  project: string;
  expId: string | null;
  name?: string;
  createdAt?: string;
  stats?: {
    inputReports?: number;
    reportsEmbedded?: number;
    edges?: number;
    multiClusters?: number;
    singletons?: number;
    finalUnique?: number;
  };
}

/** Which playground experiment (if any) is promoted as "primary" for a project.
 *  When set, the Semantic Clusters tab loads this experiment instead of the
 *  static pipeline-emitted file. */
export async function fetchActiveExperiment(project: string): Promise<ActiveExperimentInfo> {
  try {
    const res = await fetch(`/playground_api/active/${encodeURIComponent(project)}`);
    if (!res.ok) return { project, expId: null };
    return res.json();
  } catch {
    return { project, expId: null };
  }
}

export async function setActiveExperiment(project: string, expId: string): Promise<ActiveExperimentInfo> {
  const res = await fetch(`/playground_api/active/${encodeURIComponent(project)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expId }),
  });
  if (!res.ok) await _unwrapError(res);
  return res.json();
}

export async function clearActiveExperiment(project: string): Promise<void> {
  await fetch(`/playground_api/active/${encodeURIComponent(project)}`, { method: "DELETE" });
}

/** Fetch the active experiment's clusters transformed into SemanticCluster
 *  shape. Returns null if there's no active experiment set. */
export async function fetchActiveExperimentSemanticClusters(project: string): Promise<SemanticCluster[] | null> {
  try {
    const res = await fetch(`/playground_api/active/${encodeURIComponent(project)}/semantic_clusters`);
    if (res.status === 404) return null;
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export interface AiChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export async function askAssistant(project: string, messages: AiChatMessage[]): Promise<{ answer: string; model: string; usage: unknown }> {
  const res = await fetch("/playground_api/assistant/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, messages }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const err = await res.json();
      detail = err.detail || err.error || detail;
    } catch { /* non-JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

export async function fetchSemanticClusters(projectName: string): Promise<SemanticCluster[]> {
  const res = await fetch(dataUrl(projectName, "semantic_clusters.json"));
  if (!res.ok) {
    if (res.status === 404) return [];
    throw new Error(`Failed to load semantic clusters: ${res.status}`);
  }
  return res.json();
}

export type ClusterComparisonCategory = 'both' | 'jaccardOnly' | 'semanticOnly' | 'neither';

export interface ClusterComparisonReport {
  id: string;
  name: string;
  path: string;
  owner: string;
  executions: number;
  hasSql: boolean;
  category: ClusterComparisonCategory;
  jaccardClusterId: string | null;
  jaccardClusterSize: number | null;
  semanticClusterId: string | null;
  semanticClusterSize: number | null;
  semanticLabel: string | null;
}

export interface ClusterComparison {
  postAstTotal: number;
  stats: {
    both: number;
    jaccardOnly: number;
    semanticOnly: number;
    neither: number;
  };
  reports: ClusterComparisonReport[];
}

export async function fetchClusterComparison(projectName: string): Promise<ClusterComparison | null> {
  const res = await fetch(dataUrl(projectName, "cluster_comparison.json"));
  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(`Failed to load cluster comparison: ${res.status}`);
  }
  return res.json();
}

export interface HeavyUserReport {
  objectId: string;
  reportName: string;
  executions: number;
  sessions: number;
  errors: number;
  lastExec: string;
  folderPath: string;
  inInventory: boolean;
  status: string | null;
  clusterId: string | null;
  isFinalCanonical: boolean;
  inventoryName: string | null;
}

export interface HeavyUser {
  user: string;
  isService: boolean;
  totalExecutions: number;
  uniqueReports: number;
  sessions: number;
  errors: number;
  lastExec: string;
  topReports: HeavyUserReport[];
}

export async function fetchHeavyUsers(projectName: string): Promise<HeavyUser[]> {
  const res = await fetch(dataUrl(projectName, "heavy_users.json"));
  if (!res.ok) {
    if (res.status === 404) return [];
    throw new Error(`Failed to load heavy users: ${res.status}`);
  }
  return res.json();
}

export async function fetchReports(projectName: string): Promise<ReportDetail[]> {
  const res = await fetch(dataUrl(projectName, "reports.json"));
  if (!res.ok) throw new Error(`Failed to load reports: ${res.status}`);
  return res.json();
}

export async function fetchSimilarities(
  projectName: string
): Promise<Record<string, PairSimilarity>> {
  const res = await fetch(dataUrl(projectName, "similarities.json"));
  if (!res.ok) throw new Error(`Failed to load similarities: ${res.status}`);
  return res.json();
}

export async function fetchFamilies(projectName: string): Promise<Family[]> {
  const res = await fetch(dataUrl(projectName, "families.json"));
  if (!res.ok) throw new Error(`Failed to load families: ${res.status}`);
  return res.json();
}

export interface CollisionMember {
  id: string;
  name: string;
  matchTier: string;
  folderPath: string;
  owner: string;
  // Optional fingerprint data (may be absent for non-enriched members)
  metrics?: string[];
  tables?: string[];
  filters?: string[];
}

export interface CollisionGroup {
  pass: number;
  executions: number | null;
  lastExec?: string;
  normalizedName?: string;
  canonicalId: string;
  canonicalName: string;
  canonicalPath?: string;
  size: number;
  collapsed: number;
  members: CollisionMember[];
}

export async function fetchCollisions(projectName: string): Promise<CollisionGroup[]> {
  const res = await fetch(dataUrl(projectName, "collisions.json"));
  if (!res.ok) throw new Error(`Failed to load collisions: ${res.status}`);
  return res.json();
}

export interface SlimReport {
  id: string;
  name: string;
  owner: string;
  path: string;
  dateCreated: string | null;
  dateModified: string | null;
  sourceType?: string | null;
}

export interface InventoryRecord extends SlimReport {
  status: 'active' | 'retired' | 'collapsed';
}

export interface RetiredRecord extends SlimReport {
  bucket: 'original' | 'new' | 'added';
}

export async function fetchInventoryAll(projectName: string): Promise<InventoryRecord[]> {
  const res = await fetch(dataUrl(projectName, "inventory_all.json"));
  if (!res.ok) throw new Error(`Failed to load inventory_all: ${res.status}`);
  return res.json();
}

export async function fetchRetired(projectName: string): Promise<RetiredRecord[]> {
  const res = await fetch(dataUrl(projectName, "retired.json"));
  if (!res.ok) throw new Error(`Failed to load retired: ${res.status}`);
  return res.json();
}

export async function fetchFingerprints(projectName: string): Promise<FingerprintGroup[]> {
  const res = await fetch(dataUrl(projectName, "fingerprints.json"));
  if (!res.ok) throw new Error(`Failed to load fingerprints: ${res.status}`);
  return res.json();
}

export async function fetchSqlHashGroups(projectName: string): Promise<SqlHashGroup[]> {
  const res = await fetch(dataUrl(projectName, "sql_hash_groups.json"));
  if (!res.ok) throw new Error(`Failed to load SQL hash groups: ${res.status}`);
  return res.json();
}

export async function fetchAstClusters(projectName: string): Promise<AstCluster[]> {
  const res = await fetch(dataUrl(projectName, "ast_clusters.json"));
  if (!res.ok) throw new Error(`Failed to load AST clusters: ${res.status}`);
  return res.json();
}

export async function fetchAstHashes(
  projectName: string
): Promise<Record<string, string[]>> {
  const res = await fetch(dataUrl(projectName, "ast_hashes.json"));
  if (!res.ok) throw new Error(`Failed to load AST hashes: ${res.status}`);
  return res.json();
}

export interface LlmReview {
  clusterId: string;
  clusterSize: number;
  primaryName: string;
  label: string;
  business_function: string;
  relationship: string;
  consolidation_action: string;
  confidence: string;
  consolidation_detail: string;
  keep_report: string;
  removable_count: number;
  error?: string;
}

export async function fetchLlmReviews(projectName: string): Promise<LlmReview[]> {
  const res = await fetch(dataUrl(projectName, "llm_reviews.json"));
  if (!res.ok) {
    if (res.status === 404) return [];
    throw new Error(`Failed to load LLM reviews: ${res.status}`);
  }
  return res.json();
}

export function astJaccard(a: string[] | undefined, b: string[] | undefined): number | null {
  if (!a || !b) return null;
  if (a.length === 0 && b.length === 0) return 1;
  if (a.length === 0 || b.length === 0) return 0;
  const setA = new Set(a);
  let inter = 0;
  for (const x of b) if (setA.has(x)) inter++;
  const union = a.length + b.length - inter;
  return union === 0 ? 1 : inter / union;
}

// Helper: look up a pair similarity regardless of order
export function getPairSimilarity(
  similarities: Record<string, PairSimilarity>,
  idA: string,
  idB: string
): PairSimilarity | null {
  return similarities[`${idA}|${idB}`] || similarities[`${idB}|${idA}`] || null;
}

// Helper: compute diff between two sets
export interface SetDiff {
  shared: string[];
  onlyA: string[];
  onlyB: string[];
  similarity: number;
}

export function computeSetDiff(a: string[], b: string[]): SetDiff {
  const setA = new Set(a);
  const setB = new Set(b);
  const shared = [...setA].filter((x) => setB.has(x)).sort();
  const onlyA = [...setA].filter((x) => !setB.has(x)).sort();
  const onlyB = [...setB].filter((x) => !setA.has(x)).sort();
  const union = new Set([...setA, ...setB]);
  const similarity = union.size === 0 ? 1.0 : shared.length / union.size;
  return { shared, onlyA, onlyB, similarity };
}
