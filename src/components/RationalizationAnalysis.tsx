import { useEffect, useMemo, useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Legend,
} from 'recharts';
import { VscSearch, VscChevronRight, VscDiff } from 'react-icons/vsc';
import {
  fetchSummary, fetchClusters, fetchReports, fetchSimilarities, fetchFamilies,
  fetchFingerprints, fetchSqlHashGroups, fetchAstClusters, fetchAstHashes,
  fetchCollisions, fetchInventoryAll, fetchRetired, fetchLlmReviews, getPairSimilarity,
  astJaccard,
} from '../api/rationalizationClient';
import type {
  RationalizationSummary, ClusterMeta, ReportDetail, PairSimilarity, Family,
  FingerprintGroup, SqlHashGroup, AstCluster, CollisionGroup, LlmReview,
} from '../api/rationalizationClient';
import ReportComparison from './ReportComparison';
import { useApp } from '../context/AppContext';

type TabKey = 'overview' | 'methodology' | 'phases' | 'collisions' | 'fingerprints' | 'sqlhash' | 'ast' | 'families' | 'clusters' | 'llm-review' | 'reports' | 'summary';

interface SlimListRow {
  id: string;
  name: string;
  owner: string;
  path: string;
  dateCreated: string | null;
  dateModified: string | null;
  executions: number | null;
  users: number | null;
  lastExec: string | null;
  metricCount: number | null;
  tableCount: number | null;
  filterCount: number | null;
  status: 'active' | 'retired' | 'collapsed';
  bucket?: 'original' | 'new' | 'added';
}

const TAB_LABELS: Record<TabKey, string> = {
  overview: 'Overview',
  methodology: 'Methodology',
  phases: 'Phases',
  collisions: 'Collisions',
  fingerprints: 'Fingerprints',
  sqlhash: 'SQL Hash',
  ast: 'AST',
  families: 'Families',
  clusters: 'Clusters',
  'llm-review': 'LLM Review',
  reports: 'Reports',
  summary: 'Summary',
};

export default function RationalizationAnalysis() {
  const { currentProject } = useApp();
  const projectName = currentProject?.name || '';
  const [activeTab, setActiveTab] = useState<TabKey>('overview');
  const [summary, setSummary] = useState<RationalizationSummary | null>(null);
  const [clusters, setClusters] = useState<ClusterMeta[]>([]);
  const [reports, setReports] = useState<ReportDetail[]>([]);
  const [similarities, setSimilarities] = useState<Record<string, PairSimilarity>>({});
  const [families, setFamilies] = useState<Family[]>([]);
  const [fingerprints, setFingerprints] = useState<FingerprintGroup[]>([]);
  const [selectedFingerprint, setSelectedFingerprint] = useState<FingerprintGroup | null>(null);
  const [fingerprintSearch, setFingerprintSearch] = useState('');
  const [sqlHashGroups, setSqlHashGroups] = useState<SqlHashGroup[]>([]);
  const [sqlHashSearch, setSqlHashSearch] = useState('');
  const [selectedSqlHash, setSelectedSqlHash] = useState<SqlHashGroup | null>(null);
  const [astClusters, setAstClusters] = useState<AstCluster[]>([]);
  const [astHashes, setAstHashes] = useState<Record<string, string[]>>({});
  const [selectedAstCluster, setSelectedAstCluster] = useState<AstCluster | null>(null);
  const [astSearch, setAstSearch] = useState('');
  const [astShowExclusiveOnly, setAstShowExclusiveOnly] = useState(false);
  const [collisions, setCollisions] = useState<CollisionGroup[]>([]);
  const [collisionSearch, setCollisionSearch] = useState('');
  const [selectedCollision, setSelectedCollision] = useState<CollisionGroup | null>(null);
  const [collisionPassFilter, setCollisionPassFilter] = useState<'all' | '1' | '2'>('all');
  const [llmReviews, setLlmReviews] = useState<LlmReview[]>([]);
  const [llmSearch, setLlmSearch] = useState('');
  const [llmActionFilter, setLlmActionFilter] = useState<'all' | string>('all');
  const [selectedLlmReview, setSelectedLlmReview] = useState<LlmReview | null>(null);
  const [reportListModal, setReportListModal] = useState<
    null | { title: string; kind: 'inventory' | 'retired' | 'active' }
  >(null);
  const [reportListData, setReportListData] = useState<SlimListRow[] | null>(null);
  const [reportListLoading, setReportListLoading] = useState(false);
  const [reportListError, setReportListError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedCluster, setSelectedCluster] = useState<ClusterMeta | null>(null);
  const [selectedFamily, setSelectedFamily] = useState<Family | null>(null);
  const [compareA, setCompareA] = useState<ReportDetail | null>(null);
  const [compareB, setCompareB] = useState<ReportDetail | null>(null);
  const [clusterSearch, setClusterSearch] = useState('');
  const [familySearch, setFamilySearch] = useState('');
  const [reportSearch, setReportSearch] = useState('');
  const [reportSort, setReportSort] = useState<'executions' | 'name' | 'users'>('executions');
  const [reportPathFilter, setReportPathFilter] = useState<'all' | 'public' | 'personal'>('all');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    // Reset selections on project change
    setSelectedCluster(null);
    setSelectedFamily(null);
    setSelectedFingerprint(null);
    setSelectedAstCluster(null);
    setSelectedSqlHash(null);
    setSelectedLlmReview(null);
    setCompareA(null);
    setCompareB(null);

    if (!projectName) {
      setLoading(false);
      setError('No project selected. Choose a project from the sidebar.');
      return;
    }

    Promise.all([
      fetchSummary(projectName),
      fetchClusters(projectName),
      fetchReports(projectName),
      fetchSimilarities(projectName).catch(() => ({} as Record<string, PairSimilarity>)),
      fetchFamilies(projectName),
      fetchFingerprints(projectName).catch(() => [] as FingerprintGroup[]),
      fetchSqlHashGroups(projectName).catch(() => [] as SqlHashGroup[]),
      fetchAstClusters(projectName).catch(() => [] as AstCluster[]),
      fetchAstHashes(projectName).catch(() => ({} as Record<string, string[]>)),
      fetchCollisions(projectName).catch(() => [] as CollisionGroup[]),
      fetchLlmReviews(projectName).catch(() => [] as LlmReview[]),
    ])
      .then(([s, c, r, sim, fams, fps, sqlh, asts, astH, cols, llm]) => {
        if (cancelled) return;
        setSummary(s);
        setClusters(c);
        setReports(r);
        setSimilarities(sim);
        setFamilies(fams);
        setFingerprints(fps);
        setSqlHashGroups(sqlh);
        setAstClusters(asts);
        setAstHashes(astH);
        setCollisions(cols);
        setLlmReviews(llm);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.message || 'Failed to load data');
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [projectName]);

  const reportById = useMemo(() => {
    const map = new Map<string, ReportDetail>();
    reports.forEach((r) => map.set(r.id, r));
    return map;
  }, [reports]);

  const filteredClusters = useMemo(() => {
    const q = clusterSearch.toLowerCase().trim();
    if (!q) return clusters;
    return clusters.filter(
      (c) =>
        c.primaryName.toLowerCase().includes(q) ||
        c.commonMetrics.some((m) => m.includes(q)) ||
        c.commonTables.some((t) => t.includes(q))
    );
  }, [clusters, clusterSearch]);

  const filteredFamilies = useMemo(() => {
    const q = familySearch.toLowerCase().trim();
    if (!q) return families;
    return families.filter((f) => f.base.toLowerCase().includes(q));
  }, [families, familySearch]);

  const filteredFingerprints = useMemo(() => {
    const q = fingerprintSearch.toLowerCase().trim();
    if (!q) return fingerprints;
    return fingerprints.filter(
      (g) =>
        g.id.toLowerCase().includes(q) ||
        g.members.some((m) => m.name.toLowerCase().includes(q)) ||
        g.metrics.some((m) => m.toLowerCase().includes(q)) ||
        g.tables.some((t) => t.toLowerCase().includes(q))
    );
  }, [fingerprints, fingerprintSearch]);

  const filteredAstClusters = useMemo(() => {
    const q = astSearch.toLowerCase().trim();
    let list = astClusters;
    if (astShowExclusiveOnly) list = list.filter((g) => g.astExclusive);
    if (q) {
      list = list.filter(
        (g) =>
          g.id.toLowerCase().includes(q) ||
          g.members.some((m) => m.name.toLowerCase().includes(q))
      );
    }
    return list;
  }, [astClusters, astSearch, astShowExclusiveOnly]);

  const filteredCollisions = useMemo(() => {
    const q = collisionSearch.toLowerCase().trim();
    let list = collisions;
    if (collisionPassFilter !== 'all') {
      const p = parseInt(collisionPassFilter);
      list = list.filter((c) => c.pass === p);
    }
    if (q) {
      list = list.filter(
        (c) =>
          c.canonicalName.toLowerCase().includes(q) ||
          (c.canonicalPath && c.canonicalPath.toLowerCase().includes(q)) ||
          c.members.some((m) => m.name.toLowerCase().includes(q) || (m.folderPath && m.folderPath.toLowerCase().includes(q)))
      );
    }
    return list;
  }, [collisions, collisionSearch, collisionPassFilter]);

  const filteredLlmReviews = useMemo(() => {
    const q = llmSearch.toLowerCase().trim();
    let list = llmReviews.filter((r) => !r.error);
    if (llmActionFilter !== 'all') {
      list = list.filter((r) => r.consolidation_action === llmActionFilter);
    }
    if (q) {
      list = list.filter(
        (r) =>
          (r.label || '').toLowerCase().includes(q) ||
          (r.primaryName || '').toLowerCase().includes(q) ||
          (r.keep_report || '').toLowerCase().includes(q) ||
          (r.business_function || '').toLowerCase().includes(q) ||
          (r.clusterId || '').toLowerCase().includes(q)
      );
    }
    return list;
  }, [llmReviews, llmSearch, llmActionFilter]);

  const filteredSqlHash = useMemo(() => {
    const q = sqlHashSearch.toLowerCase().trim();
    if (!q) return sqlHashGroups;
    return sqlHashGroups.filter(
      (g) =>
        g.id.toLowerCase().includes(q) ||
        g.members.some((m) => m.name.toLowerCase().includes(q))
    );
  }, [sqlHashGroups, sqlHashSearch]);

  const filteredReports = useMemo(() => {
    const q = reportSearch.toLowerCase().trim();
    let list = reports;
    if (q) {
      list = list.filter(
        (r) =>
          r.name.toLowerCase().includes(q) ||
          (r.owner && r.owner.toLowerCase().includes(q)) ||
          (r.path && r.path.toLowerCase().includes(q))
      );
    }
    if (reportPathFilter !== 'all') {
      list = list.filter((r) => {
        const p = (r.path || '').toLowerCase();
        const isPersonal = p.includes('/profiles/') || p.includes('/my reports');
        return reportPathFilter === 'personal' ? isPersonal : !isPersonal;
      });
    }
    list = [...list].sort((a, b) => {
      if (reportSort === 'name') return a.name.localeCompare(b.name);
      if (reportSort === 'users') return b.users - a.users;
      return b.executions - a.executions;
    });
    return list.slice(0, 500);
  }, [reports, reportSearch, reportSort, reportPathFilter]);

  function handleOpenCluster(cluster: ClusterMeta) {
    setSelectedCluster(cluster);
  }

  function handleCompareReports(a: ReportDetail, b: ReportDetail) {
    setCompareA(a);
    setCompareB(b);
  }

  function handleCloseComparison() {
    setCompareA(null);
    setCompareB(null);
  }

  async function handleOpenKpiList(kind: 'inventory' | 'retired' | 'active') {
    const titles = {
      inventory: `Original Inventory — all ${summary?.totalInventory.toLocaleString() ?? ''} reports`,
      retired: `Retired Reports — ${summary?.retired.toLocaleString() ?? ''} with no telemetry`,
      active: `Active Reports — ${summary?.afterTelemetry.toLocaleString() ?? ''} with telemetry (before collision collapse)`,
    };
    setReportListModal({ title: titles[kind], kind });
    setReportListData(null);
    setReportListError(null);
    setReportListLoading(true);
    try {
      if (kind === 'active') {
        // For "Active Reports" we use the 994 canonical reports
        setReportListData(
          reports.map((r) => ({
            id: r.id,
            name: r.name,
            owner: r.owner,
            path: r.path,
            dateCreated: r.dateCreated ?? null,
            dateModified: r.dateModified ?? null,
            executions: r.executions,
            users: r.users,
            lastExec: r.lastExec,
            metricCount: r.metricCount,
            tableCount: r.tableCount,
            filterCount: r.filterCount,
            status: 'active',
          }))
        );
      } else if (kind === 'retired') {
        const data = await fetchRetired(projectName);
        setReportListData(
          data.map((r) => ({
            id: r.id,
            name: r.name,
            owner: r.owner,
            path: r.path,
            dateCreated: r.dateCreated,
            dateModified: r.dateModified,
            executions: null,
            users: null,
            lastExec: null,
            metricCount: null,
            tableCount: null,
            filterCount: null,
            status: 'retired',
            bucket: r.bucket,
          }))
        );
      } else {
        const data = await fetchInventoryAll(projectName);
        setReportListData(
          data.map((r) => ({
            id: r.id,
            name: r.name,
            owner: r.owner,
            path: r.path,
            dateCreated: r.dateCreated,
            dateModified: r.dateModified,
            executions: null,
            users: null,
            lastExec: null,
            metricCount: null,
            tableCount: null,
            filterCount: null,
            status: r.status,
          }))
        );
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // 404 means the project's dashboard data hasn't been rebuilt with these files yet
      if (/404/.test(msg)) {
        setReportListError(
          `This drill-down isn't available for the "${projectName}" project yet. ` +
          `Rebuild the dashboard data for this project to enable it.`
        );
      } else {
        setReportListError(msg);
      }
    } finally {
      setReportListLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center bg-[#0f0f1a] text-gray-400">
        <div className="text-center">
          <div className="animate-spin h-8 w-8 border-4 border-blue-500 border-t-transparent rounded-full mx-auto mb-3"></div>
          <div>Loading rationalization data...</div>
        </div>
      </div>
    );
  }

  if (error || !summary) {
    return (
      <div className="flex-1 flex items-center justify-center bg-[#0f0f1a] text-gray-400">
        <div className="text-center">
          <div className="text-red-400 text-xl mb-2">Failed to load</div>
          <div className="text-sm">{error || 'Unknown error'}</div>
          <div className="text-xs mt-4 text-gray-600">
            Run <code className="bg-[#16213e] px-2 py-1 rounded">python build_web_dashboard_data.py</code> to generate data files
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto bg-[#0f0f1a]">
      {/* Header */}
      <div className="bg-gradient-to-r from-[#1a3a6c] to-[#2F5496] px-6 py-5 border-b border-[#1e2d50]">
        <div>
          <h1 className="text-2xl font-bold text-white">Rationalization Analysis</h1>
          <div className="text-sm text-blue-200 mt-1">
            Project: {summary.projectName} · {summary.totalInventory.toLocaleString()} reports analyzed · Methodology: Telemetry + Fingerprint
          </div>
          <div className="text-xs text-blue-300/80 mt-1">
            Snapshot from inventory run{summary.snapshotDate ? ` on ${new Date(summary.snapshotDate).toLocaleString()}` : ''}
            {' · '}Counts reflect all report subtypes (grid, dossier, document). Dashboard shows live grid-report counts only.
          </div>
        </div>
      </div>

      {/* Tab Nav */}
      <div className="bg-[#16213e] border-b border-[#1e2d50] px-6">
        <div className="flex gap-1">
          {(Object.keys(TAB_LABELS) as TabKey[]).map((key) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`px-4 py-3 text-sm font-medium transition-colors ${
                activeTab === key
                  ? 'text-white border-b-2 border-red-500'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              {TAB_LABELS[key]}
            </button>
          ))}
        </div>
      </div>

      {/* Tab Content */}
      <div className="p-6">
        {activeTab === 'overview' && <OverviewTab summary={summary} onKpiClick={handleOpenKpiList} />}
        {activeTab === 'methodology' && <MethodologyTab />}
        {activeTab === 'phases' && <PhasesTab summary={summary} />}
        {activeTab === 'collisions' && (
          <CollisionsTab
            collisions={filteredCollisions}
            totalCount={collisions.length}
            search={collisionSearch}
            setSearch={setCollisionSearch}
            passFilter={collisionPassFilter}
            setPassFilter={setCollisionPassFilter}
            summary={summary}
            onOpen={setSelectedCollision}
          />
        )}
        {activeTab === 'fingerprints' && (
          <FingerprintsTab
            groups={filteredFingerprints}
            totalCount={fingerprints.length}
            search={fingerprintSearch}
            setSearch={setFingerprintSearch}
            summary={summary}
            onOpen={setSelectedFingerprint}
          />
        )}
        {activeTab === 'sqlhash' && (
          <SqlHashTab
            groups={filteredSqlHash}
            totalCount={sqlHashGroups.length}
            search={sqlHashSearch}
            setSearch={setSqlHashSearch}
            summary={summary}
            onOpen={setSelectedSqlHash}
          />
        )}
        {activeTab === 'ast' && (
          <AstClustersTab
            clusters={filteredAstClusters}
            totalCount={astClusters.length}
            exclusiveCount={astClusters.filter((g) => g.astExclusive).length}
            search={astSearch}
            setSearch={setAstSearch}
            showExclusiveOnly={astShowExclusiveOnly}
            setShowExclusiveOnly={setAstShowExclusiveOnly}
            summary={summary}
            onOpen={setSelectedAstCluster}
          />
        )}
        {activeTab === 'families' && (
          <FamiliesTab
            families={filteredFamilies}
            totalCount={families.length}
            search={familySearch}
            setSearch={setFamilySearch}
            onOpen={setSelectedFamily}
          />
        )}
        {activeTab === 'clusters' && (
          <ClustersTab
            clusters={filteredClusters}
            search={clusterSearch}
            setSearch={setClusterSearch}
            onOpen={handleOpenCluster}
          />
        )}
        {activeTab === 'llm-review' && (
          <LlmReviewTab
            reviews={filteredLlmReviews}
            totalCount={llmReviews.filter((r) => !r.error).length}
            search={llmSearch}
            setSearch={setLlmSearch}
            actionFilter={llmActionFilter}
            setActionFilter={setLlmActionFilter}
            summary={summary}
            onOpen={setSelectedLlmReview}
          />
        )}
        {activeTab === 'reports' && (
          <ReportsTab
            reports={filteredReports}
            totalCount={reports.length}
            search={reportSearch}
            setSearch={setReportSearch}
            sort={reportSort}
            setSort={setReportSort}
            pathFilter={reportPathFilter}
            setPathFilter={setReportPathFilter}
            clusters={clusters}
            onOpenCluster={handleOpenCluster}
          />
        )}
        {activeTab === 'summary' && <SummaryTab summary={summary} />}
      </div>

      {/* Cluster drill-down modal */}
      {selectedCluster && (
        <ClusterModal
          cluster={selectedCluster}
          reportById={reportById}
          onClose={() => setSelectedCluster(null)}
          onCompare={handleCompareReports}
        />
      )}

      {/* LLM review drill-down modal */}
      {selectedLlmReview && (
        <LlmReviewModal
          review={selectedLlmReview}
          cluster={clusters.find((c) => c.id === selectedLlmReview.clusterId) || null}
          reportById={reportById}
          onClose={() => setSelectedLlmReview(null)}
          onOpenCluster={(c) => {
            setSelectedLlmReview(null);
            setSelectedCluster(c);
          }}
        />
      )}

      {/* Family drill-down modal */}
      {selectedFamily && (
        <FamilyModal
          family={selectedFamily}
          reportById={reportById}
          onClose={() => setSelectedFamily(null)}
          onCompare={handleCompareReports}
        />
      )}

      {/* Fingerprint drill-down modal */}
      {selectedFingerprint && (
        <FingerprintModal
          group={selectedFingerprint}
          reportById={reportById}
          onClose={() => setSelectedFingerprint(null)}
          onCompare={handleCompareReports}
        />
      )}

      {/* Collision drill-down modal */}
      {selectedCollision && (
        <CollisionModal
          group={selectedCollision}
          reportById={reportById}
          onClose={() => setSelectedCollision(null)}
          onCompare={handleCompareReports}
        />
      )}

      {/* KPI list modal (Original Inventory / Retired / Active) */}
      {reportListModal && (
        <ReportListModal
          title={reportListModal.title}
          kind={reportListModal.kind}
          data={reportListData}
          loading={reportListLoading}
          error={reportListError}
          onClose={() => {
            setReportListModal(null);
            setReportListData(null);
            setReportListError(null);
          }}
        />
      )}

      {/* AST cluster drill-down modal */}
      {selectedAstCluster && (
        <AstClusterModal
          cluster={selectedAstCluster}
          reportById={reportById}
          onClose={() => setSelectedAstCluster(null)}
          onCompare={handleCompareReports}
        />
      )}

      {/* SQL Hash group drill-down modal */}
      {selectedSqlHash && (
        <SqlHashGroupModal
          group={selectedSqlHash}
          reportById={reportById}
          onClose={() => setSelectedSqlHash(null)}
          onCompare={handleCompareReports}
        />
      )}

      {/* Comparison modal */}
      {compareA && compareB && (() => {
        const baseSim = getPairSimilarity(similarities, compareA.id, compareB.id);
        // If the pair isn't above the clustering threshold, the build pipeline
        // doesn't persist its scores — compute them on-the-fly so the modal
        // can still show something useful.
        const ast = astJaccard(astHashes[compareA.id], astHashes[compareB.id]);
        const similarity: PairSimilarity = baseSim
          ? { ...baseSim, ast: baseSim.ast ?? (ast ?? undefined) }
          : {
              combined:
                0.4 * jaccardArr(compareA.metrics, compareB.metrics) +
                0.4 * jaccardArr(compareA.tables, compareB.tables) +
                0.2 * jaccardArr(compareA.filters, compareB.filters),
              metric: jaccardArr(compareA.metrics, compareB.metrics),
              table: jaccardArr(compareA.tables, compareB.tables),
              filter: jaccardArr(compareA.filters, compareB.filters),
              ast: ast ?? undefined,
            };
        return (
        <ReportComparison
          reportA={compareA}
          reportB={compareB}
          similarity={similarity}
          onClose={handleCloseComparison}
        />
        );
      })()}
    </div>
  );
}

function jaccardArr(a: string[], b: string[]): number {
  if (a.length === 0 && b.length === 0) return 1;
  if (a.length === 0 || b.length === 0) return 0;
  const setA = new Set(a);
  let inter = 0;
  for (const x of b) if (setA.has(x)) inter++;
  const union = a.length + b.length - inter;
  return union === 0 ? 1 : inter / union;
}

// ─────────────────────────────────────────────────────────────────────
// OVERVIEW TAB
// ─────────────────────────────────────────────────────────────────────

function OverviewTab({
  summary,
  onKpiClick,
}: {
  summary: RationalizationSummary;
  onKpiClick: (kind: 'inventory' | 'retired' | 'active') => void;
}) {
  const kpis: {
    label: string;
    value: number | string;
    color: string;
    border: string;
    clickKind?: 'inventory' | 'retired' | 'active';
  }[] = [
    { label: 'Original Inventory', value: summary.totalInventory, color: 'text-white', border: 'border-blue-500', clickKind: 'inventory' },
    { label: 'Retired (No Usage)', value: summary.retired, color: 'text-orange-400', border: 'border-orange-500', clickKind: 'retired' },
    { label: 'Active Reports', value: summary.afterTelemetry, color: 'text-white', border: 'border-blue-500', clickKind: 'active' },
    { label: 'Similarity Clusters', value: summary.clustersTotal, color: 'text-white', border: 'border-blue-500' },
    { label: 'Final Unique', value: summary.afterSimilarity, color: 'text-green-400', border: 'border-green-500' },
    { label: 'Total Reduction', value: `${summary.reductionPct}%`, color: 'text-green-400', border: 'border-green-500' },
  ];

  return (
    <div className="space-y-6">
      {/* KPI Grid */}
      <div className="grid grid-cols-6 gap-4">
        {kpis.map((kpi) => {
          const isClickable = !!kpi.clickKind;
          return (
          <div
            key={kpi.label}
            onClick={isClickable ? () => onKpiClick(kpi.clickKind!) : undefined}
            className={`bg-[#16213e] p-5 rounded-lg border-l-4 ${kpi.border} ${
              isClickable ? 'cursor-pointer hover:bg-[#1a2a4a] transition-colors' : ''
            }`}
            title={isClickable ? 'Click to view detail list' : undefined}
          >
            <div className="text-xs text-gray-400 uppercase tracking-wide font-semibold mb-2 flex items-center justify-between">
              <span>{kpi.label}</span>
              {isClickable && (
                <span className="text-[10px] text-blue-400 normal-case tracking-normal">
                  view {'\u2192'}
                </span>
              )}
            </div>
            <div className={`text-3xl font-bold ${kpi.color}`}>
              {typeof kpi.value === 'number' ? kpi.value.toLocaleString() : kpi.value}
            </div>
          </div>
          );
        })}
      </div>

      {/* Funnel */}
      <div className="bg-[#16213e] p-6 rounded-lg">
        <h2 className="text-lg font-bold text-white mb-4">End-to-End Reduction Funnel</h2>
        <BranchedFunnelChart
          stages={summary.funnel}
          branch={
            summary.alternativeFamilyMethod
              ? {
                  label: summary.alternativeFamilyMethod.label,
                  value: summary.alternativeFamilyMethod.value,
                  detail: summary.alternativeFamilyMethod.detail,
                  forkAfter: 'After Telemetry Retirement',
                }
              : null
          }
        />
      </div>

      {/* Reduction Breakdown + Tier Breakdown */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-[#16213e] p-6 rounded-lg">
          <h2 className="text-lg font-bold text-white mb-4">Where Reductions Come From</h2>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart
              data={[
                { name: 'Telemetry Retired', value: summary.retired, fill: '#e67e22' },
                { name: 'Fingerprint Dedup', value: summary.afterTelemetry - (summary.afterFingerprint ?? summary.afterTelemetry), fill: '#9b59b6' },
                { name: 'Similarity Cluster', value: (summary.afterFingerprint ?? summary.afterTelemetry) - summary.afterSimilarity, fill: '#16a085' },
                { name: 'Final Unique', value: summary.afterSimilarity, fill: '#27ae60' },
              ]}
            >
              <XAxis dataKey="name" tick={{ fill: '#8895a7', fontSize: 11 }} />
              <YAxis tick={{ fill: '#8895a7', fontSize: 11 }} />
              <Tooltip contentStyle={{ background: '#0f0f1a', border: '1px solid #2F5496', borderRadius: 6 }} />
              <Bar dataKey="value" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-[#16213e] p-6 rounded-lg">
          <h2 className="text-lg font-bold text-white mb-4">Telemetry Match Tiers</h2>
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={[
                  { name: 'Tier 1: Exact', value: summary.tierBreakdown.exact, fill: '#27ae60' },
                  { name: 'Tier 3: Fuzzy', value: summary.tierBreakdown.fuzzy, fill: '#3498db' },
                  { name: 'No Match (Retired)', value: summary.tierBreakdown.noMatch, fill: '#e74c3c' },
                ]}
                dataKey="value"
                nameKey="name"
                outerRadius={90}
                innerRadius={50}
                label={(entry) => `${entry.name}: ${entry.value}`}
                labelLine={false}
              />
              <Tooltip contentStyle={{ background: '#0f0f1a', border: '1px solid #2F5496' }} />
              <Legend wrapperStyle={{ fontSize: 11, color: '#8895a7' }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

// Branched funnel — main linear path + one side branch forking off after a named stage
function BranchedFunnelChart({
  stages,
  branch,
}: {
  stages: { label: string; value: number; detail: string }[];
  branch: { label: string; value: number; detail: string; forkAfter: string } | null;
}) {
  const max = stages[0]?.value || 1;
  const colors = ['#3498db', '#e67e22', '#9b59b6', '#27ae60'];
  const forkIdx = branch ? stages.findIndex((s) => s.label === branch.forkAfter) : -1;

  return (
    <div className="space-y-3">
      {stages.map((stage, i) => {
        const width = (stage.value / max) * 100;
        const pct = ((stage.value / max) * 100).toFixed(1);
        return (
          <div key={stage.label}>
            <div className="group">
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-semibold text-white">{stage.label}</span>
                <span className="text-sm text-gray-400">
                  {stage.value.toLocaleString()} <span className="text-gray-500">({pct}%)</span>
                </span>
              </div>
              <div className="relative h-12 bg-[#0f0f1a] rounded overflow-hidden">
                <div
                  className="absolute inset-y-0 left-0 flex items-center px-4 transition-all duration-300"
                  style={{ width: `${width}%`, background: colors[i % colors.length] }}
                >
                  <span className="text-white font-bold text-sm drop-shadow">
                    {stage.value.toLocaleString()}
                  </span>
                </div>
              </div>
              <div className="text-xs text-gray-500 mt-1">{stage.detail}</div>
            </div>

            {branch && i === forkIdx && (
              <div className="mt-3 pl-8 relative">
                {/* Fork connector */}
                <div className="absolute left-3 top-0 bottom-1/2 border-l-2 border-dashed border-purple-500/60" />
                <div className="absolute left-3 top-1/2 w-5 border-t-2 border-dashed border-purple-500/60" />

                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-purple-300 bg-purple-900/40 px-2 py-0.5 rounded">
                    Alternative path
                  </span>
                  <span className="text-sm font-semibold text-white">{branch.label}</span>
                  <span className="text-sm text-gray-400 ml-auto">
                    {branch.value.toLocaleString()}{' '}
                    <span className="text-gray-500">
                      ({((branch.value / max) * 100).toFixed(1)}%)
                    </span>
                  </span>
                </div>
                <div className="relative h-10 bg-[#0f0f1a] rounded overflow-hidden border border-purple-900/40">
                  <div
                    className="absolute inset-y-0 left-0 flex items-center px-4 transition-all duration-300"
                    style={{
                      width: `${(branch.value / max) * 100}%`,
                      background:
                        'repeating-linear-gradient(135deg,#7c3aed 0 12px,#6d28d9 12px 24px)',
                    }}
                  >
                    <span className="text-white font-bold text-sm drop-shadow">
                      {branch.value.toLocaleString()}
                    </span>
                  </div>
                </div>
                <div className="text-xs text-purple-300/70 mt-1 italic">{branch.detail}</div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}


// ─────────────────────────────────────────────────────────────────────
// METHODOLOGY TAB
// ─────────────────────────────────────────────────────────────────────

function MethodologyTab() {
  const steps = [
    {
      title: 'Inventory Collection',
      body: 'Enumerate every object in the project via the MicroStrategy REST API (/searches/results?type=3 for reports, with pagination + permission waves to surface objects locked behind ACLs). Object types include reports (3), documents (55), cubes (21), metrics (4), filters (1), prompts (10), attributes (12), facts (13), and tables (15). The full population becomes the denominator for every downstream reduction step.',
      color: 'blue',
    },
    {
      title: 'Telemetry Matching (3-Tier)',
      body: 'Match each inventory report to execution telemetry over the last 7 months. Tier 1 — Exact Name: normalized (lowercase, strip asterisks, collapse whitespace). Tier 2 — Full Path: folder + name, disambiguates same-named copies. Tier 3 — Fuzzy: substring containment + Jaccard token similarity ≥ 0.60. Reports with no telemetry match in any tier are flagged for retirement.',
      color: 'blue',
    },
    {
      title: 'Telemetry-Row Collision Collapse',
      body: 'The fuzzy matcher can attribute a single telemetry row to multiple MSTR objects (same signature — executions, lastExec — across renamed copies). Two passes: (1) exact-signature collapse on (execs, lastExec, normalizedName), (2) name + executions collapse. Each group picks a canonical (highest executions, oldest creation date) and collapses the rest. This is where the biggest early reduction happens for large projects.',
      color: 'amber',
    },
    {
      title: 'Definition Enrichment',
      body: 'For every canonical report, fetch the modeling definition via GET /model/reports/{id}: metrics, source tables, filter expression tree, source type, prompts. No SQL execution required. This is the input to both fingerprinting and AST-based comparison.',
      color: 'blue',
    },
    {
      title: 'Fingerprint Dedup (Exact Structural Match)',
      body: 'Build a fingerprint per report = frozenset(metrics) + frozenset(normalized tables) + frozenset(filter attributes). Reports with identical fingerprints are provable structural duplicates — same metrics, same tables, same filter dimensions. Each group collapses to one canonical; the rest become "Removable".',
      color: 'green',
    },
    {
      title: 'SQL Extraction',
      body: 'Trigger each canonical report via POST /v2/reports/{id}/instances with the prompts short-circuited to defaults, then retrieve the generated SQL. SQL is extracted to .sql files and normalized (strip comments, collapse whitespace, lowercase identifiers, strip literal values). Cube-sourced reports and a subset of prompt-heavy reports fail extraction (500 errors) — these are retained and carried forward without SQL.',
      color: 'blue',
    },
    {
      title: 'SQL Hash Dedup (on fingerprint canonicals)',
      body: 'Hash the normalized SQL text with SHA-256 and group. Two reports producing byte-identical normalized SQL are the same query regardless of name/path. Runs sequentially on fingerprint canonicals so the removable count never exceeds the prior stage.',
      color: 'green',
    },
    {
      title: 'AST Dedup (Structural SQL Match)',
      body: 'Parse each report\'s SQL with the Redshift dialect into an abstract syntax tree. Extract a sorted list of subtree hashes (SELECT projections, join graph, WHERE predicates, GROUP BY keys). Cluster reports whose hash sets are identical (exact) or nearly identical (Jaccard ≥ configured threshold). Catches duplicates that differ only in alias order, column order, or whitespace — things SQL hashing misses.',
      color: 'green',
    },
    {
      title: 'Similarity Clustering (Weighted Jaccard)',
      body: 'For every pair of surviving canonicals, compute a weighted Jaccard similarity. Pairs with SQL get: 0.60 AST + 0.25 metrics + 0.15 tables. Pairs without SQL get: 0.40 metrics + 0.40 tables + 0.20 filter attributes. Pairs ≥ 0.80 are linked; connected components form clusters. Each multi-member cluster represents reports that do essentially the same query with parameter-level differences.',
      color: 'purple',
    },
    {
      title: 'Family Consolidation (Parallel Cross-Check)',
      body: 'Group reports by base name pattern (split on the last " - " separator) independently of fingerprints. Reports sharing a base like "GFE085a - Projections by PD" are flagged as name-pattern variants. Runs as a sanity check alongside similarity clustering — surfaces consolidation candidates that naming reveals but fingerprints might have missed.',
      color: 'purple',
    },
    {
      title: 'LLM Review (Cluster Classification)',
      body: 'Send each multi-report similarity cluster to GPT-5.4 with cluster composition (primary, members, common metrics / tables / filters, total executions). The model returns a label, business function, relationship type, and a consolidation action: MERGE_IMMEDIATE (provable duplicates), PARAMETERIZE (same query, different filter values), REVIEW_WITH_OWNER (similar but need confirmation), or KEEP_SEPARATE (legitimately different) — plus a confidence grade and a keep_report recommendation. This is a classification layer, not a reduction layer — similarity clustering has already collapsed each cluster to one canonical.',
      color: 'purple',
    },
    {
      title: 'Final Decision',
      body: 'Final unique count = number of similarity clusters (each becomes one parameterized report) + singleton canonicals kept as-is. HIGH-confidence MERGE_IMMEDIATE / PARAMETERIZE clusters can be auto-consolidated; REVIEW_WITH_OWNER and KEEP_SEPARATE clusters are surfaced for business owner sign-off before migration.',
      color: 'blue',
    },
  ];

  const borderColor: Record<string, string> = {
    blue: 'border-blue-500',
    amber: 'border-amber-500',
    green: 'border-green-500',
    purple: 'border-purple-500',
  };
  const bgColor: Record<string, string> = {
    blue: 'bg-blue-600',
    amber: 'bg-amber-600',
    green: 'bg-green-600',
    purple: 'bg-purple-600',
  };

  return (
    <div className="bg-[#16213e] p-6 rounded-lg">
      <h2 className="text-lg font-bold text-white mb-2">End-to-End Methodology</h2>
      <p className="text-xs text-gray-400 mb-5">
        Each step runs on the survivors of the previous step so the funnel stays sequential. Stages in <span className="text-green-400">green</span> are provable dedup (fingerprint / SQL hash / AST), <span className="text-purple-400">purple</span> are semantic (similarity / family / LLM), <span className="text-amber-400">amber</span> is telemetry collapse, and <span className="text-blue-400">blue</span> is ingestion / classification.
      </p>
      <div className="space-y-3">
        {steps.map((step, i) => (
          <div
            key={i}
            className={`flex items-start gap-4 p-4 bg-[#0f0f1a] rounded-lg border-l-4 ${borderColor[step.color] || 'border-blue-500'}`}
          >
            <div
              className={`w-9 h-9 ${bgColor[step.color] || 'bg-blue-600'} text-white rounded-full flex items-center justify-center font-bold flex-shrink-0`}
            >
              {i + 1}
            </div>
            <div>
              <h3 className="text-white font-semibold mb-1">{step.title}</h3>
              <p className="text-sm text-gray-400">{step.body}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// PHASES TAB
// ─────────────────────────────────────────────────────────────────────

function PhasesTab({ summary }: { summary: RationalizationSummary }) {
  return (
    <div className="space-y-6">
      <div className="bg-[#16213e] p-6 rounded-lg">
        <h2 className="text-lg font-bold text-white mb-4">Object Types in Project</h2>
        <ResponsiveContainer width="100%" height={400}>
          <BarChart data={summary.objectTypes} layout="vertical">
            <XAxis type="number" tick={{ fill: '#8895a7', fontSize: 11 }} />
            <YAxis type="category" dataKey="name" tick={{ fill: '#8895a7', fontSize: 11 }} width={150} />
            <Tooltip contentStyle={{ background: '#0f0f1a', border: '1px solid #2F5496' }} />
            <Bar dataKey="count" fill="#2F5496" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <TopListCard title="Top Metrics" items={summary.topMetrics} color="#3498db" />
        <TopListCard title="Top Source Tables" items={summary.topTables} color="#16a085" />
        <TopListCard title="Top Filter Attributes" items={summary.topFilters} color="#9b59b6" />
      </div>
    </div>
  );
}

function TopListCard({
  title,
  items,
  color,
}: {
  title: string;
  items: { name: string; count: number }[];
  color: string;
}) {
  return (
    <div className="bg-[#16213e] p-6 rounded-lg">
      <h2 className="text-lg font-bold text-white mb-4">{title}</h2>
      <div className="space-y-2">
        {items.slice(0, 15).map((item, i) => {
          const max = Math.max(...items.map((x) => x.count));
          const width = (item.count / max) * 100;
          return (
            <div key={i} className="group">
              <div className="flex justify-between text-xs mb-1">
                <span className="text-gray-300 truncate max-w-[70%]" title={item.name}>
                  {item.name}
                </span>
                <span className="text-gray-400 font-mono">{item.count}</span>
              </div>
              <div className="h-2 bg-[#0f0f1a] rounded overflow-hidden">
                <div
                  className="h-full transition-all"
                  style={{ width: `${width}%`, background: color }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// FAMILIES TAB
// ─────────────────────────────────────────────────────────────────────

function FingerprintsTab({
  groups,
  totalCount,
  search,
  setSearch,
  summary,
  onOpen,
}: {
  groups: FingerprintGroup[];
  totalCount: number;
  search: string;
  setSearch: (v: string) => void;
  summary: RationalizationSummary | null;
  onOpen: (g: FingerprintGroup) => void;
}) {
  const totalReducible = groups.reduce((s, g) => s + g.reducible, 0);
  const totalReports = groups.reduce((s, g) => s + g.size, 0);

  return (
    <div className="space-y-4">
      {/* Lineage banner */}
      {summary && (
        <div className="bg-gradient-to-r from-purple-950/60 to-[#16213e] p-4 rounded-lg border border-purple-800/40">
          <div className="text-xs uppercase tracking-wider text-purple-300 font-bold mb-2">Pipeline Lineage</div>
          <div className="flex items-center gap-2 text-sm flex-wrap">
            <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
              <span className="text-gray-400">Inventory:</span>{' '}
              <span className="font-bold text-white">{summary.totalInventory.toLocaleString()}</span>
            </span>
            <span className="text-gray-500">{'\u2192'}</span>
            <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
              <span className="text-gray-400">After telemetry:</span>{' '}
              <span className="font-bold text-white">{summary.afterTelemetry.toLocaleString()}</span>
              <span className="text-red-400 text-xs ml-1">({'\u2212'}{summary.retired.toLocaleString()})</span>
            </span>
            {summary.afterCollisionCollapse !== undefined && summary.collisionCollapsed !== undefined && summary.collisionCollapsed > 0 && (
              <>
                <span className="text-gray-500">{'\u2192'}</span>
                <span
                  className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]"
                  title="Same telemetry row attributed to multiple MSTR objects — collapsed to one canonical per row"
                >
                  <span className="text-gray-400">After Collision Collapse:</span>{' '}
                  <span className="font-bold text-white">{summary.afterCollisionCollapse.toLocaleString()}</span>
                  <span className="text-amber-400 text-xs ml-1">({'\u2212'}{summary.collisionCollapsed.toLocaleString()})</span>
                </span>
              </>
            )}
            <span className="text-gray-500">{'\u2192'}</span>
            <span className="px-3 py-1.5 bg-purple-900/40 rounded border border-purple-700 ring-2 ring-purple-500/40">
              <span className="text-purple-300">After Fingerprint:</span>{' '}
              <span className="font-bold text-white">{summary.afterFingerprint?.toLocaleString() ?? '-'}</span>
              {summary.fingerprintRemovable !== undefined && (
                <span className="text-green-400 text-xs ml-1">({'\u2212'}{summary.fingerprintRemovable.toLocaleString()})</span>
              )}
            </span>
          </div>
        </div>
      )}

      <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-purple-500">
        <h3 className="text-white font-bold mb-2">Fingerprint Dedup</h3>
        <p className="text-xs text-gray-400 leading-relaxed">
          Reports are grouped when their <strong className="text-purple-300">exact</strong> fingerprint
          <code className="mx-1 px-1 bg-[#0f0f1a] rounded text-purple-300">(metrics, tables, filters)</code>
          matches byte-for-byte. These are provable duplicates — zero false positives.
          Unlike Similarity Clustering (fuzzy Jaccard ≥ 0.80), every group here is safe to collapse.
        </p>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-purple-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Fingerprint Groups</div>
          <div className="text-2xl font-bold text-white">{totalCount.toLocaleString()}</div>
          <div className="text-xs text-gray-500">multi-member (size ≥ 2)</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-blue-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Reports Covered</div>
          <div className="text-2xl font-bold text-white">{totalReports.toLocaleString()}</div>
          <div className="text-xs text-gray-500">in a dedup group</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-green-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Provable Duplicates</div>
          <div className="text-2xl font-bold text-green-400">{totalReducible.toLocaleString()}</div>
          <div className="text-xs text-gray-500">removable (size − 1 per group)</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-orange-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Largest Group</div>
          <div className="text-2xl font-bold text-white">{groups[0]?.size || 0}</div>
          <div className="text-xs text-gray-500 truncate" title={groups[0]?.members[0]?.name}>
            {groups[0]?.members[0]?.name || '-'}
          </div>
        </div>
      </div>

      <div className="bg-[#16213e] p-4 rounded-lg">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-white font-bold">
            Fingerprint Groups <span className="text-sm text-gray-400 font-normal">({groups.length} shown)</span>
          </h3>
          <div className="relative w-80">
            <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by id, report name, metric, or table..."
              className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded pl-9 pr-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>
        <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-1">
          {groups.map((g) => (
            <div
              key={g.id}
              onClick={() => onOpen(g)}
              className="bg-[#0f0f1a] hover:bg-[#1a2a4a] border border-[#1e2d50] rounded p-3 cursor-pointer group"
            >
              <div className="flex items-center justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-mono text-purple-300 bg-purple-900/30 px-2 py-0.5 rounded">
                      {g.id}
                    </span>
                    <span className="text-xs font-bold text-white">{g.size} members</span>
                    <span className="text-xs text-green-400">−{g.reducible} reducible</span>
                    <span className="text-xs text-gray-500">
                      {g.totalExecutions.toLocaleString()} execs
                    </span>
                  </div>
                  <div className="text-sm text-gray-300 truncate">{g.members[0]?.name}</div>
                  <div className="text-xs text-gray-500 mt-1">
                    {g.metricCount} metrics · {g.tableCount} tables · {g.filterCount} filter attrs
                  </div>
                </div>
                <VscChevronRight className="text-gray-500 group-hover:text-white ml-2 flex-shrink-0" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function FingerprintModal({
  group,
  reportById,
  onClose,
  onCompare,
}: {
  group: FingerprintGroup;
  reportById: Map<string, ReportDetail>;
  onClose: () => void;
  onCompare: (a: ReportDetail, b: ReportDetail) => void;
}) {
  const [selectedForCompare, setSelectedForCompare] = useState<string[]>([]);

  function toggleSelect(id: string) {
    if (selectedForCompare.includes(id)) {
      setSelectedForCompare((s) => s.filter((x) => x !== id));
    } else if (selectedForCompare.length < 2) {
      setSelectedForCompare((s) => [...s, id]);
    } else {
      setSelectedForCompare([selectedForCompare[1], id]);
    }
  }

  function handleCompareClick() {
    if (selectedForCompare.length === 2) {
      const a = reportById.get(selectedForCompare[0]);
      const b = reportById.get(selectedForCompare[1]);
      if (a && b) {
        onCompare(a, b);
        onClose();
      }
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-[#16213e] rounded-lg max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6 border-b border-[#1e2d50] flex justify-between items-start">
          <div className="flex-1 min-w-0 mr-4">
            <div className="text-xs text-purple-400 mb-1 uppercase font-bold">Fingerprint Group</div>
            <div className="flex items-center gap-3">
              <h2 className="text-xl font-bold text-white font-mono">{group.id}</h2>
              <span className="text-xs bg-purple-900/40 text-purple-200 px-2 py-0.5 rounded">
                Exact match
              </span>
            </div>
            <div className="text-sm text-gray-400 mt-2 flex gap-4 flex-wrap">
              <span><strong className="text-white">{group.size}</strong> members</span>
              <span><strong className="text-green-400">−{group.reducible}</strong> reducible</span>
              <span><strong className="text-white">{group.totalExecutions.toLocaleString()}</strong> total executions</span>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-2xl leading-none">×</button>
        </div>

        <div className="p-6 overflow-y-auto flex-1 space-y-4">
          <div className="bg-[#0f0f1a] p-3 rounded text-xs text-gray-400">
            <strong className="text-white">Why these are duplicates:</strong> every member has the
            identical frozen fingerprint below. Collapsing is safe — pick one canonical and drop
            the rest.
          </div>

          <div className="grid grid-cols-3 gap-3">
            <FingerprintSetCard title="Metrics" items={group.metrics} />
            <FingerprintSetCard title="Source Tables" items={group.tables} />
            <FingerprintSetCard title="Filter Attrs" items={group.filters} />
          </div>

          {selectedForCompare.length === 2 && (
            <button
              onClick={handleCompareClick}
              className="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 rounded-lg transition-colors"
            >
              Compare Selected Reports →
            </button>
          )}

          <table className="w-full text-sm">
            <thead className="bg-[#0f0f1a] sticky top-0">
              <tr>
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-8"></th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Name</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Path</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Last Exec</th>
              </tr>
            </thead>
            <tbody>
              {group.members.map((m, i) => {
                const isSelected = selectedForCompare.includes(m.id);
                return (
                  <tr
                    key={m.id}
                    onClick={() => toggleSelect(m.id)}
                    className={`border-t border-[#1e2d50] cursor-pointer ${
                      isSelected ? 'bg-blue-900/30' : 'hover:bg-[#1a2a4a]'
                    }`}
                  >
                    <td className="p-2">
                      <input type="checkbox" checked={isSelected} onChange={() => {}} className="accent-blue-500" />
                    </td>
                    <td className="p-2 text-gray-200 text-xs">
                      {m.name}
                      {i === 0 && <span className="text-xs text-green-400 ml-2">(primary)</span>}
                    </td>
                    <td className="p-2 text-gray-500 text-xs truncate max-w-xs" title={m.path}>{m.path}</td>
                    <td className="p-2 text-right font-mono text-gray-300">{m.executions.toLocaleString()}</td>
                    <td className="p-2 text-right font-mono text-gray-300">{m.users}</td>
                    <td className="p-2 text-xs text-gray-400">{m.lastExec || '-'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function FingerprintSetCard({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="bg-[#0f0f1a] border border-[#1e2d50] rounded p-3">
      <div className="text-xs font-bold text-gray-400 uppercase mb-2">
        {title} <span className="text-gray-600">({items.length})</span>
      </div>
      <div className="space-y-1 max-h-40 overflow-y-auto">
        {items.length === 0 && <div className="text-xs text-gray-600 italic">None</div>}
        {items.map((x) => (
          <div key={x} className="text-xs text-gray-300 font-mono truncate" title={x}>
            {x}
          </div>
        ))}
      </div>
    </div>
  );
}

function AstClustersTab({
  clusters,
  totalCount,
  exclusiveCount,
  search,
  setSearch,
  showExclusiveOnly,
  setShowExclusiveOnly,
  summary,
  onOpen,
}: {
  clusters: AstCluster[];
  totalCount: number;
  exclusiveCount: number;
  search: string;
  setSearch: (v: string) => void;
  showExclusiveOnly: boolean;
  setShowExclusiveOnly: (v: boolean) => void;
  summary: RationalizationSummary | null;
  onOpen: (g: AstCluster) => void;
}) {
  const totalReducible = clusters.reduce((s, g) => s + g.reducible, 0);
  const totalReports = clusters.reduce((s, g) => s + g.size, 0);

  return (
    <div className="space-y-4">
      {/* Lineage banner */}
      {summary && (
        <div className="bg-gradient-to-r from-pink-950/60 to-[#16213e] p-4 rounded-lg border border-pink-800/40">
          <div className="text-xs uppercase tracking-wider text-pink-300 font-bold mb-2">Pipeline Lineage</div>
          <div className="flex items-center gap-2 text-sm flex-wrap">
            <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
              <span className="text-gray-400">Inventory:</span>{' '}
              <span className="font-bold text-white">{summary.totalInventory.toLocaleString()}</span>
            </span>
            <span className="text-gray-500">→</span>
            <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
              <span className="text-gray-400">After telemetry:</span>{' '}
              <span className="font-bold text-white">{summary.afterTelemetry.toLocaleString()}</span>
              <span className="text-red-400 text-xs ml-1">(−{summary.retired.toLocaleString()})</span>
            </span>
            {summary.afterCollisionCollapse !== undefined && summary.collisionCollapsed !== undefined && summary.collisionCollapsed > 0 && (
              <>
                <span className="text-gray-500">→</span>
                <span
                  className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]"
                  title="Same telemetry row attributed to multiple MSTR objects by the fuzzy matcher — collapsed to one canonical object per row"
                >
                  <span className="text-gray-400">After collision collapse:</span>{' '}
                  <span className="font-bold text-white">{summary.afterCollisionCollapse.toLocaleString()}</span>
                  <span className="text-amber-400 text-xs ml-1">(−{summary.collisionCollapsed.toLocaleString()})</span>
                </span>
              </>
            )}
            <span className="text-gray-500">{'\u2192'}</span>
            <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
              <span className="text-gray-400">After Fingerprint:</span>{' '}
              <span className="font-bold text-white">{summary.afterFingerprint?.toLocaleString() ?? '-'}</span>
              {summary.fingerprintRemovable !== undefined && (
                <span className="text-green-400 text-xs ml-1">({'\u2212'}{summary.fingerprintRemovable.toLocaleString()})</span>
              )}
            </span>
            {summary.afterSqlHash !== undefined && (
              <>
                <span className="text-gray-500">{'\u2192'}</span>
                <span
                  className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]"
                  title="Merge fingerprint canonicals whose normalized SQL hash is byte-identical"
                >
                  <span className="text-blue-300">After SQL Hash:</span>{' '}
                  <span className="font-bold text-white">{summary.afterSqlHash.toLocaleString()}</span>
                  {summary.sqlHashSequentialRemovable !== undefined && summary.sqlHashSequentialRemovable > 0 ? (
                    <span className="text-green-400 text-xs ml-1">({'\u2212'}{summary.sqlHashSequentialRemovable.toLocaleString()})</span>
                  ) : (
                    <span className="text-gray-500 text-xs ml-1">(0 new)</span>
                  )}
                </span>
              </>
            )}
            <span className="text-gray-500">{'\u2192'}</span>
            <span className="px-3 py-1.5 bg-pink-900/40 rounded border border-pink-700 ring-2 ring-pink-500/40">
              <span className="text-pink-300">After AST dedup:</span>{' '}
              <span className="font-bold text-white">
                {summary.afterAst?.toLocaleString() ?? '-'}
              </span>
              {summary.astSequentialRemovable !== undefined && summary.astSequentialRemovable > 0 && (
                <span className="text-green-400 text-xs ml-1">({'\u2212'}{summary.astSequentialRemovable.toLocaleString()})</span>
              )}
              {summary.astSequentialRemovable === 0 && (
                <span className="text-gray-500 text-xs ml-1">(0 new)</span>
              )}
            </span>
          </div>
        </div>
      )}

      <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-pink-500">
        <h3 className="text-white font-bold mb-2">AST-Based Clustering</h3>
        <p className="text-xs text-gray-400 leading-relaxed">
          Reports are grouped by the <strong className="text-pink-300">structural shape of their SQL</strong> —
          parsed into an AST, canonicalized (aliases stripped, literals masked), then compared with
          weighted Jaccard ≥ 0.80 on subtree hashes. This catches variants that share identical query
          structure but differ only in literal values (brand, season, date), which the metric/table/filter
          fingerprint dedup misses. Only reports with successfully extracted SQL are AST-eligible.
        </p>
      </div>

      <div className="grid grid-cols-5 gap-3">
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-pink-500">
          <div className="text-xs text-gray-400 uppercase mb-1">AST Clusters</div>
          <div className="text-2xl font-bold text-white">{totalCount.toLocaleString()}</div>
          <div className="text-xs text-gray-500">multi-member</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-blue-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Reports Covered</div>
          <div className="text-2xl font-bold text-white">{totalReports.toLocaleString()}</div>
          <div className="text-xs text-gray-500">in a cluster</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-green-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Reducible</div>
          <div className="text-2xl font-bold text-green-400">{totalReducible.toLocaleString()}</div>
          <div className="text-xs text-gray-500">AST-confirmed duplicates</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-purple-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Exact Groups</div>
          <div className="text-2xl font-bold text-white">{summary?.astExactGroups ?? 0}</div>
          <div className="text-xs text-gray-500">
            {summary?.astExactRemovable ?? 0} provable
          </div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-orange-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Final To Migrate</div>
          <div className="text-2xl font-bold text-orange-400">
            {summary?.astFinalToMigrate?.toLocaleString() ?? '-'}
          </div>
          <div className="text-xs text-gray-500">AST path, incl. non-SQL</div>
        </div>
      </div>

      <div className="bg-[#16213e] p-4 rounded-lg">
        <div className="flex items-center justify-between mb-4 gap-3">
          <h3 className="text-white font-bold">
            AST Clusters <span className="text-sm text-gray-400 font-normal">({clusters.length} shown)</span>
          </h3>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={showExclusiveOnly}
                onChange={(e) => setShowExclusiveOnly(e.target.checked)}
                className="accent-pink-500"
              />
              <span>AST-only <span className="text-xs text-gray-500">(not in SQL Hash — {exclusiveCount.toLocaleString()})</span></span>
            </label>
            <div className="relative w-80">
              <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by cluster id or report name..."
                className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded pl-9 pr-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>
        </div>
        <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-1">
          {clusters.map((g) => {
            const scoreColor =
              g.avgScore >= 0.95
                ? 'text-green-400'
                : g.avgScore >= 0.85
                ? 'text-yellow-400'
                : 'text-orange-400';
            return (
              <div
                key={g.id}
                onClick={() => onOpen(g)}
                className="bg-[#0f0f1a] hover:bg-[#1a2a4a] border border-[#1e2d50] rounded p-3 cursor-pointer group"
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <span className="text-xs font-mono text-pink-300 bg-pink-900/30 px-2 py-0.5 rounded">
                        {g.id}
                      </span>
                      {g.allExact && (
                        <span className="text-xs font-bold text-green-300 bg-green-900/40 px-2 py-0.5 rounded">
                          exact
                        </span>
                      )}
                      {g.astExclusive && (
                        <span className="text-xs font-bold text-pink-300 bg-pink-900/40 px-2 py-0.5 rounded" title="Found by AST but not by SQL Hash">
                          AST-only
                        </span>
                      )}
                      <span className="text-xs font-bold text-white">{g.size} members</span>
                      <span className="text-xs text-green-400">−{g.reducible} reducible</span>
                      <span className={`text-xs font-mono ${scoreColor}`}>
                        avg {(g.avgScore * 100).toFixed(0)}%
                      </span>
                      <span className="text-xs text-gray-500 font-mono">
                        min {(g.minScore * 100).toFixed(0)}%
                      </span>
                      <span className="text-xs text-gray-500">
                        {g.totalExecutions.toLocaleString()} execs
                      </span>
                    </div>
                    <div className="text-sm text-gray-300 truncate">{g.members[0]?.name}</div>
                    <div className="text-xs text-gray-500 mt-1 truncate">
                      + {g.members.slice(1, 4).map((m) => m.name).join(' · ')}
                      {g.members.length > 4 ? ` · +${g.members.length - 4} more` : ''}
                    </div>
                  </div>
                  <VscChevronRight className="text-gray-500 group-hover:text-white ml-2 flex-shrink-0" />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function AstClusterModal({
  cluster,
  reportById,
  onClose,
  onCompare,
}: {
  cluster: AstCluster;
  reportById: Map<string, ReportDetail>;
  onClose: () => void;
  onCompare: (a: ReportDetail, b: ReportDetail) => void;
}) {
  const [selectedForCompare, setSelectedForCompare] = useState<string[]>([]);

  function toggleSelect(id: string) {
    if (selectedForCompare.includes(id)) {
      setSelectedForCompare((s) => s.filter((x) => x !== id));
    } else if (selectedForCompare.length < 2) {
      setSelectedForCompare((s) => [...s, id]);
    } else {
      setSelectedForCompare([selectedForCompare[1], id]);
    }
  }

  function handleCompareClick() {
    if (selectedForCompare.length === 2) {
      const a = reportById.get(selectedForCompare[0]);
      const b = reportById.get(selectedForCompare[1]);
      if (a && b) {
        onCompare(a, b);
        onClose();
      }
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-[#16213e] rounded-lg max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6 border-b border-[#1e2d50] flex justify-between items-start">
          <div className="flex-1 min-w-0 mr-4">
            <div className="text-xs text-pink-400 mb-1 uppercase font-bold">AST Cluster</div>
            <div className="flex items-center gap-3 flex-wrap">
              <h2 className="text-xl font-bold text-white font-mono">{cluster.id}</h2>
              {cluster.allExact && (
                <span className="text-xs bg-green-900/40 text-green-200 px-2 py-0.5 rounded">
                  all members exact-AST match
                </span>
              )}
            </div>
            <div className="text-sm text-gray-400 mt-2 flex gap-4 flex-wrap">
              <span><strong className="text-white">{cluster.size}</strong> members</span>
              <span><strong className="text-green-400">−{cluster.reducible}</strong> reducible</span>
              <span>avg <strong className="text-white">{(cluster.avgScore * 100).toFixed(0)}%</strong></span>
              <span>min <strong className="text-white">{(cluster.minScore * 100).toFixed(0)}%</strong></span>
              <span><strong className="text-white">{cluster.totalExecutions.toLocaleString()}</strong> execs</span>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-2xl leading-none">×</button>
        </div>

        <div className="p-6 overflow-y-auto flex-1 space-y-4">
          <div className="bg-[#0f0f1a] p-3 rounded text-xs text-gray-400">
            <strong className="text-white">Why these cluster:</strong> every pair's parsed SQL shares
            ≥ {(cluster.minScore * 100).toFixed(0)}% of subtree structure (canonicalized, literals
            masked). These are variants of the same underlying query shape — likely differing only in
            literal filters like brand, season, or date. Pick one canonical member and collapse the
            rest into a parameterized version.
          </div>

          {selectedForCompare.length === 2 && (
            <button
              onClick={handleCompareClick}
              className="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 rounded-lg transition-colors"
            >
              Compare Selected Reports →
            </button>
          )}

          <table className="w-full text-sm">
            <thead className="bg-[#0f0f1a] sticky top-0">
              <tr>
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-8"></th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Name</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Path</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Last Exec</th>
              </tr>
            </thead>
            <tbody>
              {cluster.members.map((m, i) => {
                const isSelected = selectedForCompare.includes(m.id);
                return (
                  <tr
                    key={m.id}
                    onClick={() => toggleSelect(m.id)}
                    className={`border-t border-[#1e2d50] cursor-pointer ${
                      isSelected ? 'bg-blue-900/30' : 'hover:bg-[#1a2a4a]'
                    }`}
                  >
                    <td className="p-2">
                      <input type="checkbox" checked={isSelected} onChange={() => {}} className="accent-blue-500" />
                    </td>
                    <td className="p-2 text-gray-200 text-xs">
                      {m.name}
                      {i === 0 && <span className="text-xs text-green-400 ml-2">(primary)</span>}
                    </td>
                    <td className="p-2 text-gray-500 text-xs truncate max-w-xs" title={m.path}>{m.path}</td>
                    <td className="p-2 text-right font-mono text-gray-300">{m.executions.toLocaleString()}</td>
                    <td className="p-2 text-right font-mono text-gray-300">{m.users}</td>
                    <td className="p-2 text-xs text-gray-400">{m.lastExec || '-'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function FamiliesTab({
  families,
  totalCount,
  search,
  setSearch,
  onOpen,
}: {
  families: Family[];
  totalCount: number;
  search: string;
  setSearch: (v: string) => void;
  onOpen: (f: Family) => void;
}) {
  const totalReducible = families.reduce((s, f) => s + f.reducible, 0);
  const totalReports = families.reduce((s, f) => s + f.size, 0);

  return (
    <div className="space-y-4">
      {/* Explainer panel */}
      <div className="bg-[#16213e] p-5 rounded-lg border-l-4 border-purple-500">
        <h3 className="text-white font-bold mb-2">How Family Dedup Works</h3>
        <p className="text-sm text-gray-400 mb-3">
          Each report name is split on the <strong className="text-white">last " - " (space-dash-space)</strong> separator. The left side becomes the "family base" and the right side is the variant suffix. Reports sharing the same base are grouped as one family — assumed to be variants of the same template with different brands, seasons, or regions.
        </p>
        <div className="bg-[#0f0f1a] p-3 rounded text-xs font-mono text-gray-300 space-y-1">
          <div><span className="text-green-400">"GFE085a - Projections by PD - GOLF"</span> → base: <span className="text-blue-400">"GFE085a - Projections by PD"</span>, variant: <span className="text-yellow-400">"GOLF"</span></div>
          <div><span className="text-green-400">"GFE085a - Projections by PD - MENS"</span> → base: <span className="text-blue-400">"GFE085a - Projections by PD"</span>, variant: <span className="text-yellow-400">"MENS"</span></div>
          <div><span className="text-green-400">"GFE085a - Projections by PD - W POLO"</span> → base: <span className="text-blue-400">"GFE085a - Projections by PD"</span>, variant: <span className="text-yellow-400">"W POLO"</span></div>
        </div>
        <p className="text-sm text-gray-400 mt-3">
          Rule: A family must have a base name ≥ 10 characters and at least 2 members. Each family contributes <code className="bg-[#0f0f1a] px-1.5 py-0.5 rounded">(members - 1)</code> to the reducible count (keep 1 parent, retire/consolidate the rest).
        </p>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-[#16213e] p-4 rounded-lg">
          <div className="text-xs text-gray-400 uppercase mb-1">Families Found</div>
          <div className="text-2xl font-bold text-white">{totalCount}</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg">
          <div className="text-xs text-gray-400 uppercase mb-1">Reports in Families</div>
          <div className="text-2xl font-bold text-white">{totalReports.toLocaleString()}</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-green-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Reducible</div>
          <div className="text-2xl font-bold text-green-400">{totalReducible.toLocaleString()}</div>
          <div className="text-xs text-gray-500">keep 1 per family</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg">
          <div className="text-xs text-gray-400 uppercase mb-1">Largest Family</div>
          <div className="text-2xl font-bold text-white">{families[0]?.size || 0}</div>
          <div className="text-xs text-gray-500 truncate" title={families[0]?.base}>{families[0]?.base || '-'}</div>
        </div>
      </div>

      {/* Search + list */}
      <div className="bg-[#16213e] p-6 rounded-lg">
        <h2 className="text-lg font-bold text-white mb-4">
          All Families <span className="text-sm text-gray-400 font-normal">({families.length} shown)</span>
        </h2>
        <div className="mb-4 relative">
          <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search families by base name..."
            className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded-lg pl-10 pr-4 py-3 text-white text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
        <div className="space-y-2 max-h-[60vh] overflow-y-auto pr-2">
          {families.map((f) => (
            <div
              key={f.base}
              onClick={() => onOpen(f)}
              className="bg-[#0f0f1a] p-4 rounded-lg cursor-pointer hover:bg-[#1a2a4a] transition-colors border-l-4 border-purple-500"
            >
              <div className="flex justify-between items-center mb-2">
                <div className="flex-1 min-w-0 mr-3">
                  <div className="text-white font-semibold text-sm truncate">{f.base}</div>
                </div>
                <div className="flex items-center gap-3 flex-shrink-0">
                  <div className="bg-purple-600 text-white text-xs font-bold px-3 py-1 rounded-full">
                    {f.size} variants
                  </div>
                  <div className="text-xs text-green-400 font-bold">
                    -{f.reducible} reducible
                  </div>
                  <VscChevronRight className="text-gray-500" />
                </div>
              </div>
              <div className="text-xs text-gray-500">
                {f.totalExecutions.toLocaleString()} total executions
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// CLUSTERS TAB
// ─────────────────────────────────────────────────────────────────────

function ClustersTab({
  clusters,
  search,
  setSearch,
  onOpen,
}: {
  clusters: ClusterMeta[];
  search: string;
  setSearch: (v: string) => void;
  onOpen: (c: ClusterMeta) => void;
}) {
  return (
    <div className="bg-[#16213e] p-6 rounded-lg">
      <h2 className="text-lg font-bold text-white mb-4">
        Similarity Clusters <span className="text-sm text-gray-400 font-normal">({clusters.length} shown)</span>
      </h2>
      <div className="mb-4 relative">
        <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search clusters by name, metric, or table..."
          className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded-lg pl-10 pr-4 py-3 text-white text-sm focus:outline-none focus:border-blue-500"
        />
      </div>
      <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-2">
        {clusters.map((c) => (
          <div
            key={c.id}
            onClick={() => onOpen(c)}
            className="bg-[#0f0f1a] p-4 rounded-lg cursor-pointer hover:bg-[#1a2a4a] transition-colors border-l-4 border-blue-500"
          >
            <div className="flex justify-between items-center mb-2">
              <div>
                <span className="text-xs text-gray-500 font-mono mr-2">{c.id}</span>
                <span className="text-white font-semibold text-sm">{c.primaryName}</span>
              </div>
              <div className="flex items-center gap-3">
                <div className="bg-blue-600 text-white text-xs font-bold px-3 py-1 rounded-full">
                  {c.size} reports
                </div>
                <VscChevronRight className="text-gray-500" />
              </div>
            </div>
            <div className="text-xs text-gray-400">
              {c.totalExecutions.toLocaleString()} total executions · {c.commonMetrics.length} metrics · {c.commonTables.length} tables · {c.commonFilters.length} filter attrs
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// REPORTS TAB
// ─────────────────────────────────────────────────────────────────────

function ReportsTab({
  reports,
  totalCount,
  search,
  setSearch,
  sort,
  setSort,
  pathFilter,
  setPathFilter,
  clusters,
  onOpenCluster,
}: {
  reports: ReportDetail[];
  totalCount: number;
  search: string;
  setSearch: (v: string) => void;
  sort: 'executions' | 'name' | 'users';
  setSort: (v: 'executions' | 'name' | 'users') => void;
  pathFilter: 'all' | 'public' | 'personal';
  setPathFilter: (v: 'all' | 'public' | 'personal') => void;
  clusters: ClusterMeta[];
  onOpenCluster: (c: ClusterMeta) => void;
}) {
  const clusterById = useMemo(() => {
    const map = new Map<string, ClusterMeta>();
    clusters.forEach((c) => map.set(c.id, c));
    return map;
  }, [clusters]);

  return (
    <div className="bg-[#16213e] p-6 rounded-lg">
      <h2 className="text-lg font-bold text-white mb-4">
        All Active Reports{' '}
        <span className="text-sm text-gray-400 font-normal">
          ({reports.length.toLocaleString()} of {totalCount.toLocaleString()} shown)
        </span>
      </h2>

      <div className="flex gap-3 mb-4">
        <div className="flex-1 relative">
          <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name, owner, or path..."
            className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded-lg pl-10 pr-4 py-2.5 text-white text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as 'executions' | 'name' | 'users')}
          className="bg-[#0f0f1a] border border-[#1e2d50] rounded-lg px-3 py-2.5 text-white text-sm focus:outline-none"
        >
          <option value="executions">Sort: Executions</option>
          <option value="users">Sort: Users</option>
          <option value="name">Sort: Name</option>
        </select>
        <select
          value={pathFilter}
          onChange={(e) => setPathFilter(e.target.value as 'all' | 'public' | 'personal')}
          className="bg-[#0f0f1a] border border-[#1e2d50] rounded-lg px-3 py-2.5 text-white text-sm focus:outline-none"
        >
          <option value="all">All Locations</option>
          <option value="public">Public Only</option>
          <option value="personal">Personal Only</option>
        </select>
      </div>

      <div className="max-h-[70vh] overflow-y-auto border border-[#1e2d50] rounded-lg">
        <table className="w-full text-sm">
          <thead className="bg-[#0f0f1a] sticky top-0">
            <tr>
              <th className="text-left p-3 text-xs text-gray-400 uppercase">Report Name</th>
              <th className="text-left p-3 text-xs text-gray-400 uppercase">Owner</th>
              <th className="text-right p-3 text-xs text-gray-400 uppercase">Execs</th>
              <th className="text-right p-3 text-xs text-gray-400 uppercase">Users</th>
              <th className="text-right p-3 text-xs text-gray-400 uppercase">M / T / F</th>
              <th className="text-left p-3 text-xs text-gray-400 uppercase">Cluster</th>
            </tr>
          </thead>
          <tbody>
            {reports.map((r) => {
              const cluster = r.clusterId ? clusterById.get(r.clusterId) : null;
              return (
                <tr
                  key={r.id}
                  className="border-t border-[#1e2d50] hover:bg-[#1a2a4a] text-gray-300"
                >
                  <td className="p-3 text-white">{r.name}</td>
                  <td className="p-3 text-xs">{r.owner || '-'}</td>
                  <td className="p-3 text-right font-mono">{r.executions.toLocaleString()}</td>
                  <td className="p-3 text-right font-mono">{r.users}</td>
                  <td className="p-3 text-right font-mono text-xs">
                    {r.metricCount}/{r.tableCount}/{r.filterCount}
                  </td>
                  <td className="p-3">
                    {cluster ? (
                      <button
                        onClick={() => onOpenCluster(cluster)}
                        className="text-xs text-blue-400 hover:underline"
                      >
                        {r.clusterId} ({cluster.size})
                      </button>
                    ) : (
                      <span className="text-xs text-gray-600">Singleton</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// SUMMARY TAB
// ─────────────────────────────────────────────────────────────────────

function SummaryTab({ summary }: { summary: RationalizationSummary }) {
  return (
    <div className="space-y-6">
      <div className="bg-[#16213e] p-6 rounded-lg">
        <h2 className="text-lg font-bold text-white mb-2">Final Recommendation</h2>
        <p className="text-sm text-gray-400 mb-5">
          Starting from <strong className="text-white">{summary.totalInventory.toLocaleString()}</strong> reports,
          the pipeline produces <strong className="text-green-400">{summary.afterSimilarity.toLocaleString()}</strong> unique
          reports for migration — a <strong className="text-green-400">{summary.reductionPct}%</strong> reduction.
        </p>
        <h3 className="text-white font-semibold mb-3">Three Migration Scenarios</h3>
        <div className="space-y-3">
          {summary.scenarios.map((s, i) => {
            const colors = ['border-orange-500', 'border-green-500', 'border-blue-500'];
            return (
              <div
                key={i}
                className={`bg-[#0f0f1a] p-4 rounded-lg border-l-4 ${colors[i]} flex items-start justify-between gap-4`}
              >
                <div>
                  <div className="text-white font-semibold mb-1">{s.name}</div>
                  <div className="text-xs text-gray-400">{s.approach}</div>
                </div>
                <div className="text-right flex-shrink-0">
                  <div className="text-2xl font-bold text-white">{s.count.toLocaleString()}</div>
                  <div className="text-xs text-gray-500">{s.confidence}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-[#16213e] p-6 rounded-lg">
        <h2 className="text-lg font-bold text-white mb-4">Top Executed Reports</h2>
        <div className="max-h-[50vh] overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-[#0f0f1a] sticky top-0">
              <tr>
                <th className="text-left p-3 text-xs text-gray-400 uppercase">#</th>
                <th className="text-left p-3 text-xs text-gray-400 uppercase">Report Name</th>
                <th className="text-right p-3 text-xs text-gray-400 uppercase">Executions</th>
                <th className="text-right p-3 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-left p-3 text-xs text-gray-400 uppercase">Last Execution</th>
              </tr>
            </thead>
            <tbody>
              {summary.topExecuted.map((r, i) => (
                <tr key={r.id} className="border-t border-[#1e2d50] text-gray-300">
                  <td className="p-3 text-gray-500">{i + 1}</td>
                  <td className="p-3 text-white">{r.name}</td>
                  <td className="p-3 text-right font-mono">{r.executions.toLocaleString()}</td>
                  <td className="p-3 text-right font-mono">{r.users}</td>
                  <td className="p-3 text-xs">{r.lastExec || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// CLUSTER MODAL
// ─────────────────────────────────────────────────────────────────────

function ClusterModal({
  cluster,
  reportById,
  onClose,
  onCompare,
}: {
  cluster: ClusterMeta;
  reportById: Map<string, ReportDetail>;
  onClose: () => void;
  onCompare: (a: ReportDetail, b: ReportDetail) => void;
}) {
  const [selectedForCompare, setSelectedForCompare] = useState<string[]>([]);

  const members = useMemo(
    () =>
      cluster.memberIds
        .map((id) => reportById.get(id))
        .filter((r): r is ReportDetail => !!r)
        .sort((a, b) => b.executions - a.executions),
    [cluster, reportById]
  );

  function toggleSelect(id: string) {
    if (selectedForCompare.includes(id)) {
      setSelectedForCompare((s) => s.filter((x) => x !== id));
    } else if (selectedForCompare.length < 2) {
      setSelectedForCompare((s) => [...s, id]);
    } else {
      setSelectedForCompare([selectedForCompare[1], id]);
    }
  }

  function handleCompareClick() {
    if (selectedForCompare.length === 2) {
      const a = reportById.get(selectedForCompare[0]);
      const b = reportById.get(selectedForCompare[1]);
      if (a && b) {
        onCompare(a, b);
        onClose();
      }
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-[#16213e] rounded-lg max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6 border-b border-[#1e2d50] flex justify-between items-start">
          <div>
            <div className="text-xs text-gray-500 mb-1 font-mono">{cluster.id}</div>
            <h2 className="text-xl font-bold text-white">{cluster.primaryName}</h2>
            <div className="text-sm text-gray-400 mt-1">
              {cluster.size} reports · {cluster.totalExecutions.toLocaleString()} total executions
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white text-2xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-6 overflow-y-auto flex-1">
          <div className="grid grid-cols-3 gap-4 mb-5">
            <TagSection title="Metrics" items={cluster.commonMetrics} color="bg-blue-900 text-blue-200" />
            <TagSection title="Tables" items={cluster.commonTables} color="bg-emerald-900 text-emerald-200" />
            <TagSection title="Filter Attrs" items={cluster.commonFilters} color="bg-purple-900 text-purple-200" />
          </div>

          <div className="bg-[#0f0f1a] p-3 rounded mb-3 text-xs text-gray-400 flex items-center gap-2">
            <VscDiff className="text-blue-400" />
            Select up to 2 reports to compare side-by-side and see exactly how they're similar
          </div>

          {selectedForCompare.length === 2 && (
            <button
              onClick={handleCompareClick}
              className="mb-3 w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 rounded-lg transition-colors"
            >
              Compare Selected Reports →
            </button>
          )}

          <table className="w-full text-sm">
            <thead className="bg-[#0f0f1a]">
              <tr>
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-8"></th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Report Name</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Last Exec</th>
              </tr>
            </thead>
            <tbody>
              {members.map((r) => {
                const isSelected = selectedForCompare.includes(r.id);
                return (
                  <tr
                    key={r.id}
                    onClick={() => toggleSelect(r.id)}
                    className={`border-t border-[#1e2d50] cursor-pointer ${
                      isSelected ? 'bg-blue-900/30' : 'hover:bg-[#1a2a4a]'
                    }`}
                  >
                    <td className="p-2">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => {}}
                        className="accent-blue-500"
                      />
                    </td>
                    <td className="p-2 text-white">{r.name}</td>
                    <td className="p-2 text-right font-mono text-gray-300">
                      {r.executions.toLocaleString()}
                    </td>
                    <td className="p-2 text-right font-mono text-gray-300">{r.users}</td>
                    <td className="p-2 text-xs text-gray-400">{r.lastExec || '-'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function TagSection({ title, items, color }: { title: string; items: string[]; color: string }) {
  return (
    <div>
      <div className="text-xs font-bold text-gray-400 uppercase mb-2">{title}</div>
      <div className="flex flex-wrap gap-1">
        {items.slice(0, 10).map((item, i) => (
          <span key={i} className={`text-xs px-2 py-0.5 rounded ${color}`}>
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// FAMILY MODAL
// ─────────────────────────────────────────────────────────────────────

function FamilyModal({
  family,
  reportById,
  onClose,
  onCompare,
}: {
  family: Family;
  reportById: Map<string, ReportDetail>;
  onClose: () => void;
  onCompare: (a: ReportDetail, b: ReportDetail) => void;
}) {
  const [selectedForCompare, setSelectedForCompare] = useState<string[]>([]);
  const [sqlFilter, setSqlFilter] = useState<'all' | 'extracted' | 'missing'>('all');

  const visibleMembers = useMemo(() => {
    if (sqlFilter === 'all') return family.members;
    return family.members.filter((m) => {
      const hasSql = !!reportById.get(m.id)?.sql;
      return sqlFilter === 'extracted' ? hasSql : !hasSql;
    });
  }, [family.members, reportById, sqlFilter]);

  const extractedCount = useMemo(
    () => family.members.filter((m) => !!reportById.get(m.id)?.sql).length,
    [family.members, reportById]
  );
  const missingCount = family.members.length - extractedCount;

  function toggleSelect(id: string) {
    if (selectedForCompare.includes(id)) {
      setSelectedForCompare((s) => s.filter((x) => x !== id));
    } else if (selectedForCompare.length < 2) {
      setSelectedForCompare((s) => [...s, id]);
    } else {
      setSelectedForCompare([selectedForCompare[1], id]);
    }
  }

  function handleCompareClick() {
    if (selectedForCompare.length === 2) {
      const a = reportById.get(selectedForCompare[0]);
      const b = reportById.get(selectedForCompare[1]);
      if (a && b) {
        onCompare(a, b);
        onClose();
      }
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-[#16213e] rounded-lg max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6 border-b border-[#1e2d50] flex justify-between items-start">
          <div className="flex-1 min-w-0 mr-4">
            <div className="text-xs text-purple-400 mb-1 uppercase font-bold">Family Base Name</div>
            <h2 className="text-xl font-bold text-white">{family.base}</h2>
            <div className="text-sm text-gray-400 mt-2 flex gap-4">
              <span><strong className="text-white">{family.size}</strong> variants</span>
              <span><strong className="text-green-400">-{family.reducible}</strong> reducible</span>
              <span><strong className="text-white">{family.totalExecutions.toLocaleString()}</strong> total executions</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white text-2xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-6 overflow-y-auto flex-1">
          <div className="bg-[#0f0f1a] p-3 rounded mb-4 text-xs text-gray-400">
            <strong className="text-white">Validation:</strong> These reports all share the base name "<span className="text-blue-400">{family.base}</span>". Each has a different variant suffix. The assumption is they're variants of the same parent template. Click two to compare their metrics/tables/filters and verify.
          </div>

          {selectedForCompare.length === 2 && (
            <button
              onClick={handleCompareClick}
              className="mb-3 w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 rounded-lg transition-colors"
            >
              Compare Selected Reports →
            </button>
          )}

          <div className="mb-3 flex items-center gap-2 text-xs flex-wrap">
            <span className="text-gray-400 uppercase font-bold mr-1">SQL:</span>
            {(['all', 'extracted', 'missing'] as const).map((k) => {
              const label =
                k === 'all'
                  ? `All (${family.members.length})`
                  : k === 'extracted'
                  ? `Extracted (${extractedCount})`
                  : `Missing (${missingCount})`;
              const active = sqlFilter === k;
              return (
                <button
                  key={k}
                  onClick={() => setSqlFilter(k)}
                  className={`px-3 py-1 rounded-full border transition-colors ${
                    active
                      ? 'bg-blue-600 border-blue-500 text-white'
                      : 'bg-[#0f0f1a] border-[#1e2d50] text-gray-400 hover:text-white'
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>

          <table className="w-full text-sm">
            <thead className="bg-[#0f0f1a] sticky top-0">
              <tr>
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-8"></th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Variant Suffix</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Full Name</th>
                <th className="text-center p-2 text-xs text-gray-400 uppercase">SQL</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Last Exec</th>
              </tr>
            </thead>
            <tbody>
              {visibleMembers.length === 0 && (
                <tr>
                  <td colSpan={7} className="p-4 text-center text-xs text-gray-500 italic">
                    No reports match the current SQL filter.
                  </td>
                </tr>
              )}
              {visibleMembers.map((m, i) => {
                const isSelected = selectedForCompare.includes(m.id);
                const hasSql = !!reportById.get(m.id)?.sql;
                return (
                  <tr
                    key={m.id}
                    onClick={() => toggleSelect(m.id)}
                    className={`border-t border-[#1e2d50] cursor-pointer ${
                      isSelected ? 'bg-blue-900/30' : 'hover:bg-[#1a2a4a]'
                    }`}
                  >
                    <td className="p-2">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => {}}
                        className="accent-blue-500"
                      />
                    </td>
                    <td className="p-2">
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded ${
                        i === 0 ? 'bg-green-900 text-green-200' : 'bg-purple-900 text-purple-200'
                      }`}>
                        {m.variantSuffix}
                      </span>
                      {i === 0 && <span className="text-xs text-green-400 ml-2">(primary)</span>}
                    </td>
                    <td className="p-2 text-gray-300 text-xs">{m.name}</td>
                    <td className="p-2 text-center">
                      <span
                        className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                          hasSql
                            ? 'bg-green-900/50 text-green-300'
                            : 'bg-red-900/40 text-red-300'
                        }`}
                        title={hasSql ? 'SQL extracted' : 'SQL not available'}
                      >
                        {hasSql ? 'YES' : 'NO'}
                      </span>
                    </td>
                    <td className="p-2 text-right font-mono text-gray-300">
                      {m.executions.toLocaleString()}
                    </td>
                    <td className="p-2 text-right font-mono text-gray-300">{m.users}</td>
                    <td className="p-2 text-xs text-gray-400">{m.lastExec || '-'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function SqlHashTab({
  groups,
  totalCount,
  search,
  setSearch,
  summary,
  onOpen,
}: {
  groups: SqlHashGroup[];
  totalCount: number;
  search: string;
  setSearch: (v: string) => void;
  summary: RationalizationSummary | null;
  onOpen: (g: SqlHashGroup) => void;
}) {
  const totalReducible = groups.reduce((s, g) => s + g.reducible, 0);
  const totalReports = groups.reduce((s, g) => s + g.size, 0);

  return (
    <div className="space-y-4">
      {summary && (
        <div className="bg-gradient-to-r from-blue-950/60 to-[#16213e] p-4 rounded-lg border border-blue-800/40">
          <div className="text-xs uppercase tracking-wider text-blue-300 font-bold mb-2">Pipeline Lineage</div>
          <div className="flex items-center gap-2 text-sm flex-wrap">
            <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
              <span className="text-gray-400">Inventory:</span>{' '}
              <span className="font-bold text-white">{summary.totalInventory.toLocaleString()}</span>
            </span>
            <span className="text-gray-500">{'\u2192'}</span>
            <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
              <span className="text-gray-400">After telemetry:</span>{' '}
              <span className="font-bold text-white">{summary.afterTelemetry.toLocaleString()}</span>
              <span className="text-red-400 text-xs ml-1">({'\u2212'}{summary.retired.toLocaleString()})</span>
            </span>
            {summary.afterCollisionCollapse !== undefined && summary.collisionCollapsed !== undefined && summary.collisionCollapsed > 0 && (
              <>
                <span className="text-gray-500">{'\u2192'}</span>
                <span
                  className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]"
                  title="Same telemetry row attributed to multiple MSTR objects by the fuzzy matcher — collapsed to one canonical object per row"
                >
                  <span className="text-gray-400">After Collision Collapse:</span>{' '}
                  <span className="font-bold text-white">{summary.afterCollisionCollapse.toLocaleString()}</span>
                  <span className="text-amber-400 text-xs ml-1">({'\u2212'}{summary.collisionCollapsed.toLocaleString()})</span>
                </span>
              </>
            )}
            <span className="text-gray-500">{'\u2192'}</span>
            <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
              <span className="text-gray-400">After Fingerprint:</span>{' '}
              <span className="font-bold text-white">{summary.afterFingerprint?.toLocaleString() ?? '-'}</span>
              {summary.fingerprintRemovable !== undefined && (
                <span className="text-green-400 text-xs ml-1">({'\u2212'}{summary.fingerprintRemovable.toLocaleString()})</span>
              )}
            </span>
            <span className="text-gray-500">{'\u2192'}</span>
            <span className="px-3 py-1.5 bg-blue-900/40 rounded border border-blue-700 ring-2 ring-blue-500/40">
              <span className="text-blue-300">After SQL Hash:</span>{' '}
              <span className="font-bold text-white">{summary.afterSqlHash?.toLocaleString() ?? summary.afterFingerprint?.toLocaleString() ?? '-'}</span>
              {summary.sqlHashSequentialRemovable !== undefined && summary.sqlHashSequentialRemovable > 0 ? (
                <span className="text-green-400 text-xs ml-1">({'\u2212'}{summary.sqlHashSequentialRemovable.toLocaleString()})</span>
              ) : (
                <span className="text-gray-500 text-xs ml-1">(0 new)</span>
              )}
            </span>
          </div>
        </div>
      )}

      <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-blue-500">
        <h3 className="text-white font-bold mb-2">Exact SQL Hash Dedup</h3>
        <p className="text-xs text-gray-400 leading-relaxed">
          Extracted SQL is normalized (lowercased, comments stripped, literals replaced, temp tables
          standardized) then hashed with SHA-256. Reports sharing the same hash produce{' '}
          <strong className="text-blue-300">byte-for-byte identical SQL</strong>. Catches duplicates
          the fingerprint method misses (different metric IDs resolving to the same SQL) and feeds AST
          structural analysis.
        </p>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-blue-500">
          <div className="text-xs text-gray-400 uppercase mb-1">SQL Hash Groups</div>
          <div className="text-2xl font-bold text-white">{totalCount.toLocaleString()}</div>
          <div className="text-xs text-gray-500">multi-member</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-purple-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Reports Covered</div>
          <div className="text-2xl font-bold text-white">{totalReports.toLocaleString()}</div>
          <div className="text-xs text-gray-500">in a SQL hash group</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-green-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Provable Duplicates</div>
          <div className="text-2xl font-bold text-green-400">{totalReducible.toLocaleString()}</div>
          <div className="text-xs text-gray-500">removable (size - 1 per group)</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-orange-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Largest Group</div>
          <div className="text-2xl font-bold text-white">{groups[0]?.size || 0}</div>
          <div className="text-xs text-gray-500 truncate" title={groups[0]?.members[0]?.name}>
            {groups[0]?.members[0]?.name || '-'}
          </div>
        </div>
      </div>

      <div className="bg-[#16213e] p-4 rounded-lg">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-white font-bold">
            SQL Hash Groups <span className="text-sm text-gray-400 font-normal">({groups.length} shown)</span>
          </h3>
          <div className="relative w-80">
            <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by id or report name..."
              className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded pl-9 pr-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>
        <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-1">
          {groups.map((g) => (
            <div
              key={g.id}
              onClick={() => onOpen(g)}
              className="bg-[#0f0f1a] hover:bg-[#1a2a4a] border border-[#1e2d50] rounded p-3 cursor-pointer group"
            >
              <div className="flex items-center justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-2 flex-wrap">
                    <span className="text-xs font-mono text-blue-300 bg-blue-900/30 px-2 py-0.5 rounded">{g.id}</span>
                    <span className="text-xs font-bold text-white">{g.size} members</span>
                    <span className="text-xs text-green-400">-{g.reducible} reducible</span>
                    <span className="text-xs text-gray-500">{g.totalExecutions.toLocaleString()} execs</span>
                  </div>
                  <div className="text-sm text-gray-300 truncate">{g.members[0]?.name}</div>
                  <div className="text-xs text-gray-500 mt-1 truncate">
                    + {g.members.slice(1, 4).map((m) => m.name).join(' . ')}
                    {g.members.length > 4 ? ` . +${g.members.length - 4} more` : ''}
                  </div>
                  {g.sqlPreview && (
                    <pre className="text-[10px] text-gray-500 mt-2 font-mono whitespace-pre-wrap overflow-hidden max-h-16">
                      {g.sqlPreview}
                    </pre>
                  )}
                </div>
                <VscChevronRight className="text-gray-500 group-hover:text-white ml-2 flex-shrink-0" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function SqlHashGroupModal({
  group,
  reportById,
  onClose,
  onCompare,
}: {
  group: SqlHashGroup;
  reportById: Map<string, ReportDetail>;
  onClose: () => void;
  onCompare: (a: ReportDetail, b: ReportDetail) => void;
}) {
  const [selectedForCompare, setSelectedForCompare] = useState<string[]>([]);

  function toggleSelect(id: string) {
    if (selectedForCompare.includes(id)) {
      setSelectedForCompare((s) => s.filter((x) => x !== id));
    } else if (selectedForCompare.length < 2) {
      setSelectedForCompare((s) => [...s, id]);
    } else {
      setSelectedForCompare([selectedForCompare[1], id]);
    }
  }

  function handleCompareClick() {
    if (selectedForCompare.length === 2) {
      const a = reportById.get(selectedForCompare[0]);
      const b = reportById.get(selectedForCompare[1]);
      if (a && b) {
        onCompare(a, b);
        onClose();
      }
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-[#16213e] rounded-lg max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6 border-b border-[#1e2d50] flex justify-between items-start">
          <div className="flex-1 min-w-0 mr-4">
            <div className="text-xs text-blue-400 mb-1 uppercase font-bold">SQL Hash Group</div>
            <div className="flex items-center gap-3 flex-wrap">
              <h2 className="text-xl font-bold text-white font-mono">{group.id}</h2>
              <span className="text-xs bg-green-900/40 text-green-200 px-2 py-0.5 rounded">
                byte-for-byte identical SQL
              </span>
            </div>
            <div className="text-sm text-gray-400 mt-2 flex gap-4 flex-wrap">
              <span><strong className="text-white">{group.size}</strong> members</span>
              <span><strong className="text-green-400">{'−'}{group.reducible}</strong> reducible</span>
              <span><strong className="text-white">{group.totalExecutions.toLocaleString()}</strong> execs</span>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-2xl leading-none">×</button>
        </div>

        <div className="p-6 overflow-y-auto flex-1 space-y-4">
          <div className="bg-[#0f0f1a] p-3 rounded text-xs text-gray-400">
            <strong className="text-white">Why these group:</strong> every member produces the same
            normalized SQL (lowercased, comments stripped, string and numeric literals replaced with
            placeholders). SHA-256 hash matches byte-for-byte. Safe to collapse to a single canonical
            member.
          </div>

          {group.sqlPreview && (
            <div>
              <div className="text-xs font-bold text-blue-300 uppercase mb-2">SQL Preview (normalized)</div>
              <pre className="text-[10px] text-gray-300 font-mono bg-[#0f0f1a] p-3 rounded whitespace-pre-wrap overflow-auto max-h-40">
                {group.sqlPreview}
              </pre>
            </div>
          )}

          {selectedForCompare.length === 2 && (
            <button
              onClick={handleCompareClick}
              className="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 rounded-lg transition-colors"
            >
              Compare Selected Reports {'→'}
            </button>
          )}

          <table className="w-full text-sm">
            <thead className="bg-[#0f0f1a] sticky top-0">
              <tr>
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-8"></th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Name</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Path</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Last Exec</th>
              </tr>
            </thead>
            <tbody>
              {group.members.map((m, i) => {
                const isSelected = selectedForCompare.includes(m.id);
                return (
                  <tr
                    key={m.id}
                    onClick={() => toggleSelect(m.id)}
                    className={`border-t border-[#1e2d50] cursor-pointer ${
                      isSelected ? 'bg-blue-900/30' : 'hover:bg-[#1a2a4a]'
                    }`}
                  >
                    <td className="p-2">
                      <input type="checkbox" checked={isSelected} onChange={() => {}} className="accent-blue-500" />
                    </td>
                    <td className="p-2 text-gray-200 text-xs">
                      {m.name}
                      {i === 0 && <span className="text-xs text-green-400 ml-2">(primary)</span>}
                    </td>
                    <td className="p-2 text-gray-500 text-xs truncate max-w-xs" title={m.path}>{m.path}</td>
                    <td className="p-2 text-right font-mono text-gray-300">{m.executions.toLocaleString()}</td>
                    <td className="p-2 text-right font-mono text-gray-300">{m.users}</td>
                    <td className="p-2 text-xs text-gray-400">{m.lastExec || '-'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}


// -----------------------------------------------------------------
// COLLISIONS TAB
// -----------------------------------------------------------------

function CollisionsTab({
  collisions,
  totalCount,
  search,
  setSearch,
  passFilter,
  setPassFilter,
  summary,
  onOpen,
}: {
  collisions: CollisionGroup[];
  totalCount: number;
  search: string;
  setSearch: (v: string) => void;
  passFilter: 'all' | '1' | '2';
  setPassFilter: (v: 'all' | '1' | '2') => void;
  summary: RationalizationSummary;
  onOpen: (c: CollisionGroup) => void;
}) {
  const totalCollapsed = collisions.reduce((s, c) => s + c.collapsed, 0);
  const totalReportsInGroups = collisions.reduce((s, c) => s + c.size, 0);
  const pass1 = collisions.filter((c) => c.pass === 1).length;
  const pass2 = collisions.filter((c) => c.pass === 2).length;

  // Graceful empty state when the project hasn't been rebuilt with collision data
  const dataNotAvailable =
    collisions.length === 0 && summary.afterCollisionCollapse === undefined;
  if (dataNotAvailable) {
    return (
      <div className="space-y-4">
        <div className="bg-[#16213e] p-8 rounded-lg border-l-4 border-amber-500 text-center">
          <h3 className="text-white font-bold mb-2 text-lg">Collision data not available</h3>
          <p className="text-sm text-gray-400 max-w-xl mx-auto">
            The telemetry-row collision collapse step hasn't been emitted for this project yet.
            Rebuild the project's dashboard data (run the project-specific build script) to enable
            this tab. The main funnel, fingerprint dedup, and other tabs continue to work without it.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Lineage banner */}
      <div className="bg-gradient-to-r from-amber-950/60 to-[#16213e] p-4 rounded-lg border border-amber-800/40">
        <div className="text-xs uppercase tracking-wider text-amber-300 font-bold mb-2">Pipeline Lineage</div>
        <div className="flex items-center gap-2 text-sm flex-wrap">
          <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
            <span className="text-gray-400">Inventory:</span>{' '}
            <span className="font-bold text-white">{summary.totalInventory.toLocaleString()}</span>
          </span>
          <span className="text-gray-500">{'\u2192'}</span>
          <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
            <span className="text-gray-400">After telemetry:</span>{' '}
            <span className="font-bold text-white">{summary.afterTelemetry.toLocaleString()}</span>
            <span className="text-red-400 text-xs ml-1">({'\u2212'}{summary.retired.toLocaleString()})</span>
          </span>
          <span className="text-gray-500">{'\u2192'}</span>
          <span className="px-3 py-1.5 bg-amber-900/40 rounded border border-amber-700 ring-2 ring-amber-500/40">
            <span className="text-amber-300">After Collision Collapse:</span>{' '}
            <span className="font-bold text-white">
              {summary.afterCollisionCollapse?.toLocaleString() ?? '-'}
            </span>
            {summary.collisionCollapsed !== undefined && summary.collisionCollapsed > 0 && (
              <span className="text-green-400 text-xs ml-1">({'\u2212'}{summary.collisionCollapsed.toLocaleString()})</span>
            )}
          </span>
        </div>
      </div>

      <div className="bg-[#16213e] p-5 rounded-lg border-l-4 border-amber-500">
        <h3 className="text-white font-bold mb-2">What Collision Collapse Does</h3>
        <p className="text-sm text-gray-400 mb-3">
          The telemetry matcher sometimes attributes the <strong className="text-white">same underlying telemetry row</strong> to
          many different MSTR objects — this happens when multiple reports share an exact name across folders,
          or when the fuzzy matcher snaps multiple objects to the same canonical entry. Each matched object
          inherits the <em>full</em> execution count, <strong className="text-amber-300">inflating every downstream
          metric</strong>. This step identifies those collisions and keeps only one canonical MSTR object
          per telemetry row.
        </p>
        <div className="grid grid-cols-2 gap-3 mt-3">
          <div className="bg-[#0f0f1a] p-3 rounded text-xs text-gray-300">
            <div className="font-bold text-amber-300 mb-1">Pass 1 — Signature match</div>
            Group by <code className="bg-black/30 px-1 rounded">(executions, lastExecTs)</code> — that pair uniquely
            identifies a telemetry row. Collision triggered when 3+ objects share the signature (or 2 objects
            with identical normalized names). Keep the one with the best match tier + shortest name.
          </div>
          <div className="bg-[#0f0f1a] p-3 rounded text-xs text-gray-300">
            <div className="font-bold text-amber-300 mb-1">Pass 2 — Name + exec count</div>
            Catches pairs the signature pass misses (when <code className="bg-black/30 px-1 rounded">lastExec</code> timestamps
            drift by a day but name + execution count are identical). Keep the exact-tier match or lowest id.
          </div>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <div className="bg-[#16213e] p-4 rounded-lg">
          <div className="text-xs text-gray-400 uppercase mb-1">Collision Groups</div>
          <div className="text-2xl font-bold text-white">{totalCount}</div>
          <div className="text-xs text-gray-500">{pass1} pass-1, {pass2} pass-2</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg">
          <div className="text-xs text-gray-400 uppercase mb-1">Reports in Groups</div>
          <div className="text-2xl font-bold text-white">{totalReportsInGroups.toLocaleString()}</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-amber-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Collapsed</div>
          <div className="text-2xl font-bold text-amber-400">{totalCollapsed.toLocaleString()}</div>
          <div className="text-xs text-gray-500">inflation removed</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-green-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Canonical Active</div>
          <div className="text-2xl font-bold text-green-400">
            {summary.afterCollisionCollapse?.toLocaleString() ?? '-'}
          </div>
          <div className="text-xs text-gray-500">unique reports</div>
        </div>
      </div>

      <div className="bg-[#16213e] p-6 rounded-lg">
        <div className="flex justify-between items-center mb-4 gap-3 flex-wrap">
          <h2 className="text-lg font-bold text-white">
            Collision Groups <span className="text-sm text-gray-400 font-normal">({collisions.length.toLocaleString()} shown)</span>
          </h2>
          <div className="flex gap-2">
            {(['all', '1', '2'] as const).map((p) => (
              <button
                key={p}
                onClick={() => setPassFilter(p)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                  passFilter === p
                    ? 'bg-amber-700 text-white'
                    : 'bg-[#0f0f1a] text-gray-400 hover:text-white border border-[#1e2d50]'
                }`}
              >
                {p === 'all' ? 'All Passes' : `Pass ${p}`}
              </button>
            ))}
          </div>
        </div>
        <div className="mb-4 relative">
          <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by canonical name, member name, or folder path..."
            className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded-lg pl-10 pr-4 py-3 text-white text-sm focus:outline-none focus:border-amber-500"
          />
        </div>
        <div className="space-y-2 max-h-[60vh] overflow-y-auto pr-2">
          {collisions.map((c, i) => (
            <div
              key={`${c.canonicalId}-${i}`}
              onClick={() => onOpen(c)}
              className="bg-[#0f0f1a] p-4 rounded-lg cursor-pointer hover:bg-[#1a2a4a] transition-colors border-l-4 border-amber-500"
            >
              <div className="flex justify-between items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                      c.pass === 1 ? 'bg-amber-900 text-amber-200' : 'bg-orange-900 text-orange-200'
                    }`}>
                      PASS {c.pass}
                    </span>
                    <span className="text-xs text-gray-500">
                      {c.executions?.toLocaleString() ?? '-'} execs
                      {c.lastExec ? ` · last: ${c.lastExec}` : ''}
                    </span>
                  </div>
                  <div className="text-white font-semibold text-sm truncate" title={c.canonicalName}>
                    {c.canonicalName}
                  </div>
                  {c.canonicalPath ? (
                    <div className="text-[11px] text-gray-500 truncate mt-0.5" title={c.canonicalPath}>
                      {'\ud83d\udcc1 '}{c.canonicalPath}
                    </div>
                  ) : (
                    <div className="text-[11px] text-gray-700 italic mt-0.5">no folder path</div>
                  )}
                </div>
                <div className="flex items-center gap-3 flex-shrink-0">
                  <div className="bg-amber-600 text-white text-xs font-bold px-3 py-1 rounded-full">
                    {c.size} copies
                  </div>
                  <div className="text-xs text-amber-400 font-bold">
                    {'\u2212'}{c.collapsed}
                  </div>
                  <VscChevronRight className="text-gray-500" />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function CollisionModal({
  group,
  reportById,
  onClose,
  onCompare,
}: {
  group: CollisionGroup;
  reportById: Map<string, ReportDetail>;
  onClose: () => void;
  onCompare: (a: ReportDetail, b: ReportDetail) => void;
}) {
  const [selectedForCompare, setSelectedForCompare] = useState<string[]>([]);

  function toggleSelect(id: string) {
    if (selectedForCompare.includes(id)) {
      setSelectedForCompare((s) => s.filter((x) => x !== id));
    } else if (selectedForCompare.length < 2) {
      setSelectedForCompare((s) => [...s, id]);
    } else {
      setSelectedForCompare([selectedForCompare[1], id]);
    }
  }

  // Build a ReportDetail from a collision member — either from the canonical
  // reportById lookup, or synthesized from the sparse collision metadata.
  function buildDetail(memberId: string): ReportDetail {
    const full = reportById.get(memberId);
    const m = group.members.find((x) => x.id === memberId);
    // Prefer the full canonical record but fall back to the member's own
    // fingerprint fields (metrics/tables/filters) when the full record is
    // missing (collapsed reports aren't in the canonical reports.json).
    const memberMetrics = m?.metrics || [];
    const memberTables = m?.tables || [];
    const memberFilters = m?.filters || [];
    if (full) {
      // If the canonical record has no fingerprint data but the member does,
      // layer the member data on top for this comparison.
      if ((!full.metrics || full.metrics.length === 0) && memberMetrics.length) {
        return { ...full, metrics: memberMetrics, metricCount: memberMetrics.length,
                 tables: memberTables.length ? memberTables : full.tables,
                 tableCount: (memberTables.length ? memberTables : full.tables).length,
                 filters: memberFilters.length ? memberFilters : full.filters,
                 filterCount: (memberFilters.length ? memberFilters : full.filters).length };
      }
      return full;
    }
    return {
      id: memberId,
      name: m?.name || '',
      owner: m?.owner || '',
      path: m?.folderPath || '',
      executions: group.executions || 0,
      users: 0,
      lastExec: group.lastExec || '',
      matchTier: m?.matchTier || '',
      metrics: memberMetrics,
      tables: memberTables,
      filters: memberFilters,
      metricCount: memberMetrics.length,
      tableCount: memberTables.length,
      filterCount: memberFilters.length,
      clusterId: null,
      familyBase: '',
      dateCreated: null,
      dateModified: null,
    };
  }

  function handleCompareClick() {
    if (selectedForCompare.length === 2) {
      onCompare(buildDetail(selectedForCompare[0]), buildDetail(selectedForCompare[1]));
      onClose();
    }
  }

  const anyCollapsedSelected = selectedForCompare.some((id) => !reportById.has(id));

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-[#16213e] rounded-lg max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6 border-b border-[#1e2d50] flex justify-between items-start">
          <div className="flex-1 min-w-0 mr-4">
            <div className="flex items-center gap-2 mb-1">
              <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                group.pass === 1 ? 'bg-amber-900 text-amber-200' : 'bg-orange-900 text-orange-200'
              }`}>
                PASS {group.pass}
              </span>
              <span className="text-xs text-amber-400 uppercase font-bold">Collision Group</span>
            </div>
            <h2 className="text-xl font-bold text-white">{group.canonicalName}</h2>
            <div className="text-sm text-gray-400 mt-2 flex flex-wrap gap-4">
              <span><strong className="text-white">{group.size}</strong> copies</span>
              <span><strong className="text-amber-400">{'\u2212'}{group.collapsed}</strong> collapsed</span>
              {group.executions !== null && group.executions !== undefined && (
                <span>
                  <strong className="text-white">{group.executions.toLocaleString()}</strong> shared executions
                </span>
              )}
              {group.lastExec && (
                <span>Last exec: <strong className="text-white">{group.lastExec}</strong></span>
              )}
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-2xl leading-none">
            ×
          </button>
        </div>

        <div className="p-6 overflow-y-auto flex-1">
          <div className="bg-[#0f0f1a] p-3 rounded mb-3 text-xs text-gray-400 flex items-center gap-2">
            <VscDiff className="text-amber-400" />
            Select 2 reports to compare side-by-side. Collapsed members have only telemetry + name/path metadata — full fingerprint comparison is only available when both reports exist in the canonical set.
          </div>

          {selectedForCompare.length === 2 && (
            <button
              onClick={handleCompareClick}
              className="mb-3 w-full bg-amber-600 hover:bg-amber-700 text-white font-semibold py-2.5 rounded-lg transition-colors"
            >
              Compare Selected Reports {'\u2192'}
              {anyCollapsedSelected && (
                <span className="ml-2 text-xs font-normal opacity-80">(name/path only — collapsed member has no fingerprint)</span>
              )}
            </button>
          )}

          <table className="w-full text-sm">
            <thead className="bg-[#0f0f1a] sticky top-0">
              <tr>
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-8"></th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Status</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Report Name</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Tier</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Folder Path</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Owner</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">ID</th>
              </tr>
            </thead>
            <tbody>
              {group.members.map((m) => {
                const isCanonical = m.id === group.canonicalId;
                const isSelected = selectedForCompare.includes(m.id);
                const hasFullDetail =
                  reportById.has(m.id) ||
                  !!(m.metrics?.length || m.tables?.length || m.filters?.length);
                return (
                  <tr
                    key={m.id}
                    onClick={() => toggleSelect(m.id)}
                    className={`border-t border-[#1e2d50] cursor-pointer ${
                      isSelected ? 'bg-blue-900/30' : isCanonical ? 'bg-green-900/20' : 'hover:bg-[#1a2a4a]'
                    }`}
                  >
                    <td className="p-2">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => {}}
                        className="accent-blue-500"
                      />
                    </td>
                    <td className="p-2">
                      {isCanonical ? (
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-green-900 text-green-200">
                          KEEP
                        </span>
                      ) : (
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-red-900/50 text-red-300">
                          COLLAPSE
                        </span>
                      )}
                    </td>
                    <td className="p-2 text-white">
                      {m.name}
                      {!hasFullDetail && (
                        <span className="ml-2 text-[10px] text-gray-500" title="No full fingerprint data — member was removed by collapse">
                          (no fp)
                        </span>
                      )}
                    </td>
                    <td className="p-2 text-xs">
                      <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                        m.matchTier === 'exact' || m.matchTier === 'exact_name'
                          ? 'bg-blue-900 text-blue-200'
                          : 'bg-gray-800 text-gray-300'
                      }`}>
                        {m.matchTier || '-'}
                      </span>
                    </td>
                    <td className="p-2 text-xs text-gray-400" title={m.folderPath}>
                      {m.folderPath ? (m.folderPath.length > 50 ? '\u2026' + m.folderPath.slice(-50) : m.folderPath) : '-'}
                    </td>
                    <td className="p-2 text-xs text-gray-400">{m.owner || '-'}</td>
                    <td className="p-2 text-[10px] text-gray-500 font-mono">{m.id.slice(0, 8)}{'\u2026'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}


// -----------------------------------------------------------------
// REPORT LIST MODAL (used by Overview KPIs)
// -----------------------------------------------------------------

function ReportListModal({
  title,
  kind,
  data,
  loading,
  error,
  onClose,
}: {
  title: string;
  kind: 'inventory' | 'retired' | 'active';
  data: SlimListRow[] | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'retired' | 'collapsed'>('all');
  const [bucketFilter, setBucketFilter] = useState<'all' | 'original' | 'new' | 'added'>('all');

  const filtered = (data || []).filter((r) => {
    if (kind === 'inventory' && statusFilter !== 'all' && r.status !== statusFilter) return false;
    if (kind === 'retired' && bucketFilter !== 'all' && r.bucket !== bucketFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return (
        (r.name || '').toLowerCase().includes(q) ||
        (r.owner || '').toLowerCase().includes(q) ||
        (r.path || '').toLowerCase().includes(q) ||
        (r.id || '').toLowerCase().includes(q)
      );
    }
    return true;
  });

  // Show counts by status for inventory
  const statusCounts = kind === 'inventory' && data ? {
    active: data.filter((r) => r.status === 'active').length,
    retired: data.filter((r) => r.status === 'retired').length,
    collapsed: data.filter((r) => r.status === 'collapsed').length,
  } : null;

  const bucketCounts = kind === 'retired' && data ? {
    original: data.filter((r) => r.bucket === 'original').length,
    new: data.filter((r) => r.bucket === 'new').length,
    added: data.filter((r) => r.bucket === 'added').length,
  } : null;

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-[#16213e] rounded-lg max-w-[95vw] w-full max-h-[92vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-5 border-b border-[#1e2d50] flex justify-between items-start">
          <div className="flex-1 min-w-0 mr-4">
            <h2 className="text-lg font-bold text-white">{title}</h2>
            {data && (
              <div className="text-sm text-gray-400 mt-1">
                {filtered.length.toLocaleString()} of {data.length.toLocaleString()} shown
              </div>
            )}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-2xl leading-none">
            {'×'}
          </button>
        </div>

        <div className="p-5 overflow-hidden flex-1 flex flex-col">
          {/* Filters */}
          <div className="flex gap-2 mb-3 flex-wrap items-center">
            <div className="relative flex-1 min-w-[200px]">
              <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search name, owner, path, id..."
                className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded-lg pl-10 pr-4 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
              />
            </div>
            {kind === 'inventory' && statusCounts && (
              <div className="flex gap-1">
                {(['all', 'active', 'retired', 'collapsed'] as const).map((s) => (
                  <button
                    key={s}
                    onClick={() => setStatusFilter(s)}
                    className={`px-3 py-1.5 rounded text-xs font-semibold ${
                      statusFilter === s
                        ? 'bg-blue-700 text-white'
                        : 'bg-[#0f0f1a] text-gray-400 hover:text-white border border-[#1e2d50]'
                    }`}
                  >
                    {s === 'all'
                      ? 'All'
                      : s.charAt(0).toUpperCase() + s.slice(1)}
                    {s !== 'all' && (
                      <span className="ml-1 opacity-70">
                        ({statusCounts[s].toLocaleString()})
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
            {kind === 'retired' && bucketCounts && (
              <div className="flex gap-1">
                {(['all', 'original', 'new', 'added'] as const).map((b) => (
                  <button
                    key={b}
                    onClick={() => setBucketFilter(b)}
                    className={`px-3 py-1.5 rounded text-xs font-semibold ${
                      bucketFilter === b
                        ? 'bg-orange-700 text-white'
                        : 'bg-[#0f0f1a] text-gray-400 hover:text-white border border-[#1e2d50]'
                    }`}
                    title={
                      b === 'original'
                        ? 'Part of the original 1,347 inventory'
                        : b === 'new'
                        ? 'From the 1,177 new-access batch'
                        : b === 'added'
                        ? 'From the 5,091 recently-added batch'
                        : 'All retire buckets'
                    }
                  >
                    {b === 'all' ? 'All' : b.charAt(0).toUpperCase() + b.slice(1)}
                    {b !== 'all' && (
                      <span className="ml-1 opacity-70">
                        ({bucketCounts[b].toLocaleString()})
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          {loading && (
            <div className="flex-1 flex items-center justify-center text-gray-400">
              <div className="text-center">
                <div className="animate-spin h-6 w-6 border-4 border-blue-500 border-t-transparent rounded-full mx-auto mb-2"></div>
                <div className="text-sm">Loading report list...</div>
              </div>
            </div>
          )}

          {error && (
            <div className="flex-1 flex items-center justify-center text-red-400">
              <div className="text-center">
                <div className="text-sm mb-1">Failed to load</div>
                <div className="text-xs text-gray-500">{error}</div>
              </div>
            </div>
          )}

          {!loading && !error && data && (
            <div className="flex-1 overflow-y-auto border border-[#1e2d50] rounded-lg">
              <table className="w-full text-xs">
                <thead className="bg-[#0f0f1a] sticky top-0">
                  <tr>
                    <th className="text-left p-2 text-[10px] text-gray-400 uppercase">Name</th>
                    <th className="text-left p-2 text-[10px] text-gray-400 uppercase">Owner</th>
                    <th className="text-left p-2 text-[10px] text-gray-400 uppercase">Path</th>
                    {kind === 'inventory' && (
                      <th className="text-left p-2 text-[10px] text-gray-400 uppercase">Status</th>
                    )}
                    {kind === 'retired' && (
                      <th className="text-left p-2 text-[10px] text-gray-400 uppercase">Bucket</th>
                    )}
                    {kind === 'active' && (
                      <>
                        <th className="text-right p-2 text-[10px] text-gray-400 uppercase">Execs</th>
                        <th className="text-right p-2 text-[10px] text-gray-400 uppercase">Users</th>
                        <th className="text-left p-2 text-[10px] text-gray-400 uppercase">Last Exec</th>
                        <th className="text-right p-2 text-[10px] text-gray-400 uppercase">M/T/F</th>
                      </>
                    )}
                    <th className="text-left p-2 text-[10px] text-gray-400 uppercase">Created</th>
                    <th className="text-left p-2 text-[10px] text-gray-400 uppercase">Modified</th>
                    <th className="text-left p-2 text-[10px] text-gray-400 uppercase">ID</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.slice(0, 5000).map((r) => (
                    <tr key={r.id} className="border-t border-[#1e2d50] hover:bg-[#1a2a4a]">
                      <td className="p-2 text-white">{r.name}</td>
                      <td className="p-2 text-gray-400">{r.owner || '-'}</td>
                      <td className="p-2 text-gray-500 truncate max-w-[280px]" title={r.path}>
                        {r.path || '-'}
                      </td>
                      {kind === 'inventory' && (
                        <td className="p-2">
                          <span
                            className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                              r.status === 'active'
                                ? 'bg-green-900 text-green-200'
                                : r.status === 'retired'
                                ? 'bg-red-900/50 text-red-300'
                                : 'bg-amber-900 text-amber-200'
                            }`}
                          >
                            {r.status}
                          </span>
                        </td>
                      )}
                      {kind === 'retired' && (
                        <td className="p-2">
                          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-gray-800 text-gray-300">
                            {r.bucket || '-'}
                          </span>
                        </td>
                      )}
                      {kind === 'active' && (
                        <>
                          <td className="p-2 text-right font-mono text-gray-300">
                            {r.executions != null ? r.executions.toLocaleString() : '-'}
                          </td>
                          <td className="p-2 text-right font-mono text-gray-300">
                            {r.users != null ? r.users : '-'}
                          </td>
                          <td className="p-2 text-gray-400">{r.lastExec || '-'}</td>
                          <td className="p-2 text-right font-mono text-[10px] text-gray-400">
                            {r.metricCount != null
                              ? `${r.metricCount}/${r.tableCount}/${r.filterCount}`
                              : '-'}
                          </td>
                        </>
                      )}
                      <td className="p-2 text-[10px] text-gray-500">
                        {r.dateCreated ? r.dateCreated.slice(0, 10) : '-'}
                      </td>
                      <td className="p-2 text-[10px] text-gray-500">
                        {r.dateModified ? r.dateModified.slice(0, 10) : '-'}
                      </td>
                      <td className="p-2 text-[10px] text-gray-500 font-mono">
                        {r.id.slice(0, 8)}{'…'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filtered.length > 5000 && (
                <div className="p-3 text-xs text-amber-400 bg-amber-900/20 text-center">
                  Showing first 5,000 of {filtered.length.toLocaleString()} {'—'} refine search to see more
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


// -------------------------------------------------------------------
// LLM REVIEW TAB
// -------------------------------------------------------------------

const LLM_ACTION_STYLES: Record<string, { bg: string; border: string; label: string }> = {
  MERGE_IMMEDIATE: { bg: 'bg-red-600', border: 'border-red-500', label: 'Merge Immediately' },
  PARAMETERIZE: { bg: 'bg-amber-600', border: 'border-amber-500', label: 'Parameterize' },
  REVIEW_WITH_OWNER: { bg: 'bg-blue-600', border: 'border-blue-500', label: 'Review with Owner' },
  KEEP_SEPARATE: { bg: 'bg-gray-600', border: 'border-gray-500', label: 'Keep Separate' },
};

function actionStyle(action: string) {
  return LLM_ACTION_STYLES[action] || { bg: 'bg-gray-600', border: 'border-gray-500', label: action };
}

function confidenceStyle(conf: string): string {
  if (conf === 'HIGH') return 'bg-green-900/50 text-green-300';
  if (conf === 'MEDIUM') return 'bg-yellow-900/50 text-yellow-300';
  return 'bg-gray-800 text-gray-400';
}

function LlmReviewTab({
  reviews,
  totalCount,
  search,
  setSearch,
  actionFilter,
  setActionFilter,
  summary,
  onOpen,
}: {
  reviews: LlmReview[];
  totalCount: number;
  search: string;
  setSearch: (v: string) => void;
  actionFilter: string;
  setActionFilter: (v: string) => void;
  summary: RationalizationSummary | null;
  onOpen: (r: LlmReview) => void;
}) {
  const byAction = summary?.llmReviewsByAction || {};
  const afterSim = summary?.afterSimilarity ?? 0;
  const safeClusters = summary?.llmSafeClusters ?? 0;
  const totalClusters = summary?.llmReviewsTotal ?? 0;

  return (
    <div className="bg-[#16213e] p-6 rounded-lg">
      <div className="flex justify-between items-start mb-4">
        <div>
          <h2 className="text-lg font-bold text-white">
            LLM Review <span className="text-sm text-gray-400 font-normal">({reviews.length} shown of {totalCount})</span>
          </h2>
          <p className="text-xs text-gray-400 mt-1">
            GPT classification of the {totalClusters} multi-report similarity clusters {'\u2014'} recommends what action to take per cluster (merge now, parameterize, review with owner, or keep separate).
          </p>
        </div>
        <div className="bg-[#0f0f1a] rounded-lg px-4 py-3 text-xs">
          <div className="text-gray-400 mb-1">Context</div>
          <div className="text-white font-mono">
            After Similarity: <span className="text-green-400 font-bold">{afterSim.toLocaleString()}</span> canonical units
          </div>
          <div className="text-gray-500 mt-1">
            {safeClusters} cluster{safeClusters === 1 ? '' : 's'} flagged safe to auto-consolidate (HIGH confidence)
          </div>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3 mb-4">
        {Object.entries(LLM_ACTION_STYLES).map(([key, s]) => {
          const count = byAction[key] || 0;
          const selected = actionFilter === key;
          return (
            <button
              key={key}
              onClick={() => setActionFilter(selected ? 'all' : key)}
              className={`text-left p-3 rounded-lg border-l-4 ${s.border} ${selected ? 'bg-[#1a2a4a] ring-1 ring-blue-500' : 'bg-[#0f0f1a] hover:bg-[#1a2a4a]'} transition-colors`}
            >
              <div className="text-[10px] text-gray-400 uppercase tracking-wider">{s.label}</div>
              <div className="text-2xl font-bold text-white mt-1">{count}</div>
              <div className="text-[10px] text-gray-500 mt-0.5">clusters</div>
            </button>
          );
        })}
      </div>

      <div className="flex gap-2 mb-4 items-center">
        <div className="flex-1 relative">
          <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by label, primary name, keep_report, business function..."
            className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded-lg pl-10 pr-4 py-3 text-white text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
        {actionFilter !== 'all' && (
          <button
            onClick={() => setActionFilter('all')}
            className="text-xs text-gray-400 hover:text-white px-3 py-2 bg-[#0f0f1a] rounded border border-[#1e2d50]"
          >
            Clear filter
          </button>
        )}
      </div>

      <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-2">
        {reviews.map((r) => {
          const s = actionStyle(r.consolidation_action);
          return (
            <div
              key={r.clusterId}
              onClick={() => onOpen(r)}
              className={`bg-[#0f0f1a] p-4 rounded-lg cursor-pointer hover:bg-[#1a2a4a] transition-colors border-l-4 ${s.border}`}
            >
              <div className="flex justify-between items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs text-gray-500 font-mono">{r.clusterId}</span>
                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${s.bg} text-white`}>{s.label}</span>
                    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded ${confidenceStyle(r.confidence)}`}>{r.confidence}</span>
                    <span className="text-[10px] text-gray-500 px-2 py-0.5 rounded bg-gray-800">{r.relationship}</span>
                  </div>
                  <div className="text-white font-semibold text-sm truncate">{r.label}</div>
                  <div className="text-xs text-gray-400 mt-1 line-clamp-2">{r.business_function}</div>
                </div>
                <div className="flex flex-col items-end gap-1 flex-shrink-0">
                  <div className="bg-blue-600 text-white text-xs font-bold px-3 py-1 rounded-full whitespace-nowrap">
                    {r.clusterSize} reports
                  </div>
                  <div className="text-xs text-red-400 font-bold whitespace-nowrap">
                    {'\u2212'}{r.removable_count} removable
                  </div>
                  <VscChevronRight className="text-gray-500" />
                </div>
              </div>
              {r.keep_report && (
                <div className="mt-2 text-[11px] text-gray-500">
                  Keep: <span className="text-green-400 font-mono">{r.keep_report}</span>
                </div>
              )}
            </div>
          );
        })}
        {reviews.length === 0 && (
          <div className="text-center text-gray-500 py-12 text-sm">
            No LLM reviews match your filter.
          </div>
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------
// LLM REVIEW DRILL-DOWN MODAL
// -------------------------------------------------------------------

function LlmReviewModal({
  review,
  cluster,
  reportById,
  onClose,
  onOpenCluster,
}: {
  review: LlmReview;
  cluster: ClusterMeta | null;
  reportById: Map<string, ReportDetail>;
  onClose: () => void;
  onOpenCluster: (c: ClusterMeta) => void;
}) {
  const s = actionStyle(review.consolidation_action);
  const members = cluster ? cluster.memberIds.map((id) => reportById.get(id)).filter(Boolean) as ReportDetail[] : [];

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-[#16213e] rounded-lg w-full max-w-5xl max-h-[90vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="p-5 border-b border-[#1e2d50] flex justify-between items-start gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs text-gray-500 font-mono">{review.clusterId}</span>
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${s.bg} text-white`}>{s.label}</span>
              <span className={`text-[10px] font-semibold px-2 py-0.5 rounded ${confidenceStyle(review.confidence)}`}>{review.confidence}</span>
              <span className="text-[10px] text-gray-400 px-2 py-0.5 rounded bg-gray-800">{review.relationship}</span>
            </div>
            <h3 className="text-lg font-bold text-white">{review.label}</h3>
            <div className="text-xs text-gray-400 mt-1">
              {review.clusterSize} reports in cluster {'\u00b7'} {review.removable_count} removable {'\u00b7'} keep: <span className="text-green-400 font-mono">{review.keep_report}</span>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-2xl leading-none">{'\u00d7'}</button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          <div className="bg-[#0f0f1a] p-4 rounded-lg">
            <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-1">Business Function</div>
            <div className="text-sm text-gray-200">{review.business_function}</div>
          </div>

          <div className="bg-[#0f0f1a] p-4 rounded-lg">
            <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-1">Consolidation Recommendation</div>
            <div className="text-sm text-gray-200 whitespace-pre-wrap">{review.consolidation_detail}</div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="bg-[#0f0f1a] p-3 rounded-lg">
              <div className="text-[10px] text-gray-400 uppercase tracking-wider">Action</div>
              <div className={`inline-block text-xs font-bold px-2 py-0.5 rounded mt-1 ${s.bg} text-white`}>{s.label}</div>
            </div>
            <div className="bg-[#0f0f1a] p-3 rounded-lg">
              <div className="text-[10px] text-gray-400 uppercase tracking-wider">Relationship</div>
              <div className="text-sm text-gray-200 mt-1">{review.relationship}</div>
            </div>
            <div className="bg-[#0f0f1a] p-3 rounded-lg">
              <div className="text-[10px] text-gray-400 uppercase tracking-wider">Confidence</div>
              <div className={`inline-block text-xs font-semibold px-2 py-0.5 rounded mt-1 ${confidenceStyle(review.confidence)}`}>{review.confidence}</div>
            </div>
          </div>

          {cluster && (
            <div className="bg-[#0f0f1a] p-4 rounded-lg">
              <div className="flex justify-between items-center mb-3">
                <div className="text-[10px] text-gray-400 uppercase tracking-wider">Cluster Members ({members.length})</div>
                <button
                  onClick={() => onOpenCluster(cluster)}
                  className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1"
                >
                  Open cluster <VscChevronRight />
                </button>
              </div>
              <div className="max-h-80 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-[#0f0f1a]">
                    <tr className="text-gray-400 text-left border-b border-[#1e2d50]">
                      <th className="p-2 font-normal">Report</th>
                      <th className="p-2 font-normal text-right">Executions</th>
                      <th className="p-2 font-normal text-right">Users</th>
                      <th className="p-2 font-normal text-gray-500">Last Exec</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members
                      .slice()
                      .sort((a, b) => (b.executions || 0) - (a.executions || 0))
                      .map((m) => {
                        const isKeep = m.name === review.keep_report || m.id === cluster.primaryReportId;
                        return (
                          <tr key={m.id} className="border-b border-[#1e2d50]/50">
                            <td className="p-2">
                              <div className="flex items-center gap-2">
                                {isKeep && (
                                  <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-green-900 text-green-200">KEEP</span>
                                )}
                                <span className="text-gray-200">{m.name}</span>
                              </div>
                              {m.path && <div className="text-[10px] text-gray-500 font-mono mt-0.5">{m.path}</div>}
                            </td>
                            <td className="p-2 text-right font-mono text-gray-300">{(m.executions || 0).toLocaleString()}</td>
                            <td className="p-2 text-right font-mono text-gray-300">{m.users || 0}</td>
                            <td className="p-2 text-gray-400">{m.lastExec || '-'}</td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
