// API client for rationalization analysis data
// Fetches static JSON files from public/data/{projectName}/

export interface RationalizationSummary {
  projectName: string;
  projectId: string;
  snapshotDate?: string;
  totalInventory: number;
  totalObjects: number;
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
  afterFamily: number;
  familyReducible: number;
  afterSimilarity: number;
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
  metricCount: number;
  tableCount: number;
  filterCount: number;
  clusterId: string | null;
  familyBase: string;
  dateCreated: string | null;
  dateModified: string | null;
  sql?: string | null;
  sqlError?: string | null;
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
