import { useEffect, useMemo, useRef, useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Legend,
} from 'recharts';
import { VscSearch, VscChevronRight, VscDiff } from 'react-icons/vsc';
import {
  fetchSummary, fetchClusters, fetchReports, fetchSimilarities, fetchFamilies,
  fetchFingerprints, fetchSqlHashGroups, fetchAstClusters, fetchAstHashes,
  fetchCollisions, fetchInventoryAll, fetchRetired, fetchLlmReviews, getPairSimilarity,
  astJaccard, fetchSemanticClusters, fetchHeavyUsers, fetchClusterComparison,
  fetchPlaygroundIndex, fetchPlaygroundExperiment, runPlaygroundExperiment,
  checkPlaygroundHealth, deletePlaygroundExperiment,
  fetchPlaygroundTask, askAssistant,
  fetchActiveExperiment, setActiveExperiment, clearActiveExperiment,
  fetchActiveExperimentSemanticClusters,
  runDomainClassification, fetchDomainResults,
  fetchRetainedReports, toggleReportRetained,
  fetchRetiredOverrides, toggleReportRetired,
  runDensityClustering, fetchDensityDefaults,
  downloadRationalizationExport,
  runExperimentLlmReview,
  fetchEmbeddingSets, previewEmbeddingSet, runEmbeddingSet, deleteEmbeddingSet,
  fetchCombinedStatus, rebuildCombinedReports,
} from '../api/rationalizationClient';
import type {
  RationalizationSummary, ClusterMeta, ReportDetail, PairSimilarity, Family,
  FingerprintGroup, SqlHashGroup, AstCluster, CollisionGroup, LlmReview,
  SemanticCluster, HeavyUser, ClusterComparison, ClusterComparisonCategory,
  PlaygroundExperimentIndexEntry, PlaygroundExperiment, PlaygroundTask,
  PlaygroundVizPoint, ActiveExperimentInfo,
  FamilyCollapsedSibling,
  EmbeddingSet, EmbeddingSetPreview,
  CombinedStatus,
} from '../api/rationalizationClient';

// localStorage-backed state — survives tab switches AND page reloads.
function usePersistentState<T>(key: string, initial: T): [T, (v: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw != null) return JSON.parse(raw) as T;
    } catch { /* ignore */ }
    return initial;
  });
  useEffect(() => {
    try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* quota */ }
  }, [key, value]);
  return [value, setValue];
}
import ReportComparison from './ReportComparison';
import { useApp } from '../context/AppContext';

type TabKey = 'overview' | 'methodology' | 'phases' | 'collisions' | 'fingerprints' | 'sqlhash' | 'ast' | 'families' | 'clusters' | 'semantic' | 'comparison' | 'embeddings' | 'ai-playground' | 'density' | 'classify-domain' | 'llm-review' | 'reports' | 'heavy-users' | 'summary';

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
  sourceType?: string | null;
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
  clusters: 'Jaccard Clusters',
  'classify-domain': 'Classify Domain',
  comparison: 'Jaccard vs Semantic',
  embeddings: 'Embeddings',
  'ai-playground': 'AI Playground',
  semantic: 'Semantic Clusters',
  density: 'Density Clustering',
  'llm-review': 'LLM Review',
  reports: 'Final Reports to Keep',
  'heavy-users': 'Heavy Users',
  summary: 'Summary',
};

// Tabs that still render (and can be reached programmatically) but are
// omitted from the main nav bar. Keeps the UI uncluttered without ripping
// out the underlying component or summary data.
const HIDDEN_TABS = new Set<TabKey>(['phases', 'comparison', 'density', 'llm-review']);

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
  const [semanticClusters, setSemanticClusters] = useState<SemanticCluster[]>([]);
  // Raw pipeline-emitted semantic clusters (with real LLM reviews). Kept
  // separate from `semanticClusters` so that when a Playground experiment
  // is promoted as Primary, the LLM Review tab still sees the pipeline's
  // LLM-reviewed clusters instead of the experiment's un-reviewed ones.
  const [rawPipelineSemanticClusters, setRawPipelineSemanticClusters] = useState<SemanticCluster[]>([]);
  // If a Playground experiment is promoted as "primary" for this project, the
  // Semantic Clusters tab loads its data instead of the static pipeline file.
  const [activeExpInfo, setActiveExpInfo] = useState<ActiveExperimentInfo | null>(null);
  const [semanticSearch, setSemanticSearch] = useState('');
  const [semanticActionFilter, setSemanticActionFilter] = useState<'all' | string>('all');
  const [selectedSemantic, setSelectedSemantic] = useState<SemanticCluster | null>(null);
  const [selectedReport, setSelectedReport] = useState<ReportDetail | null>(null);
  const [clusterComparison, setClusterComparison] = useState<ClusterComparison | null>(null);
  const [comparisonCategory, setComparisonCategory] = useState<'all' | ClusterComparisonCategory>('all');
  const [comparisonSearch, setComparisonSearch] = useState('');
  const [heavyUsers, setHeavyUsers] = useState<HeavyUser[]>([]);
  const [heavyUserSearch, setHeavyUserSearch] = useState('');
  const [heavyUserFilter, setHeavyUserFilter] = useState<'all' | 'human' | 'service'>('all');
  // Project-scoped user overrides — "retain this even if LLM said drop it"
  // and "retire this even if LLM/primary kept it". Loaded when the project
  // changes and when the cluster modal closes (so toggles in the modal
  // propagate back to the Final Reports count).
  const [projectRetained, setProjectRetained] = useState<Set<string>>(new Set());
  const [projectRetired, setProjectRetired] = useState<Set<string>>(new Set());
  const [selectedHeavyUser, setSelectedHeavyUser] = useState<HeavyUser | null>(null);
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
  // Min-threshold filters — empty string = no filter, any non-negative integer filters `>`.
  const [reportMinUsers, setReportMinUsers] = useState<string>('');
  const [reportMinExecs, setReportMinExecs] = useState<string>('');
  const [reportSort, setReportSort] = useState<'executions' | 'name' | 'users'>('executions');
  const [reportPathFilter, setReportPathFilter] = useState<'all' | 'public' | 'personal'>('all');
  const [reportSourceFilter, setReportSourceFilter] = useState<'all' | 'normal' | 'cube' | 'custom_sql_free_form' | 'none'>('all');
  const [reportScope, setReportScope] = useState<'final' | 'all'>('final');

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
      fetchSemanticClusters(projectName).catch(() => [] as SemanticCluster[]),
      fetchHeavyUsers(projectName).catch(() => [] as HeavyUser[]),
      fetchClusterComparison(projectName).catch(() => null as ClusterComparison | null),
      // Active experiment (Playground promotion) — if one is set for this
      // project, we'll swap its clusters in below, replacing `sem`.
      fetchActiveExperiment(projectName).catch(() => ({ project: '', expId: null } as ActiveExperimentInfo)),
    ])
      .then(async ([s, c, r, sim, fams, fps, sqlh, asts, astH, cols, llm, sem, hu, cmp, active]) => {
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
        setHeavyUsers(hu);
        setClusterComparison(cmp);

        // Always keep the pipeline's LLM-reviewed semantic clusters around
        // (they carry the `llm` payloads the LLM Review tab needs).
        setRawPipelineSemanticClusters(sem);

        if (active && active.expId) {
          // Replace pipeline's semantic clusters with the active experiment's
          const expSem = await fetchActiveExperimentSemanticClusters(projectName).catch(() => null);
          if (!cancelled) {
            setActiveExpInfo(active);
            setSemanticClusters(expSem ?? sem);
          }
        } else {
          setActiveExpInfo(null);
          setSemanticClusters(sem);
        }
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

  // Called by the Playground tab when the user promotes/clears a "primary"
  // experiment. Re-fetches the active info + semantic clusters (either the
  // experiment's or the pipeline's) so the Semantic Clusters tab reflects
  // the change without a full project reload.
  async function reloadSemanticFromActive() {
    if (!projectName) return;
    try {
      const active = await fetchActiveExperiment(projectName);
      // Refresh the raw pipeline clusters every time so LLM Review always
      // reflects what the pipeline currently has reviewed.
      const pipeSem = await fetchSemanticClusters(projectName).catch(() => [] as SemanticCluster[]);
      setRawPipelineSemanticClusters(pipeSem);
      if (active && active.expId) {
        const expSem = await fetchActiveExperimentSemanticClusters(projectName);
        setActiveExpInfo(active);
        setSemanticClusters(expSem ?? pipeSem);
      } else {
        setActiveExpInfo(null);
        setSemanticClusters(pipeSem);
      }
    } catch { /* ignore */ }
  }

  // Effective summary — when an experiment is promoted as primary, overlay its
  // semantic counts onto the pipeline's summary so the Overview/Phases and any
  // other consumers reflect the experiment's reduction instead of the pipeline's.
  const effectiveSummary = useMemo<RationalizationSummary | null>(() => {
    if (!summary) return null;
    if (!activeExpInfo?.expId) return summary;
    const multi = semanticClusters.filter((c) => !c.isSingletons);
    const sing = semanticClusters.find((c) => c.isSingletons);
    const finalUnique = multi.length + (sing?.size ?? 0);
    return {
      ...summary,
      semanticClustersTotal: multi.length,
      afterSemantic: finalUnique,
    };
  }, [summary, activeExpInfo, semanticClusters]);

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

  // Refresh user overrides whenever the project changes or the cluster
  // modal closes (closing the modal implies user may have toggled flags).
  useEffect(() => {
    const short = PROJECT_NAME_TO_SHORT[projectName];
    if (!short) return;
    let cancelled = false;
    fetchRetainedReports(short).then((s) => { if (!cancelled) setProjectRetained(s); });
    fetchRetiredOverrides(short).then((s) => { if (!cancelled) setProjectRetired(s); });
    return () => { cancelled = true; };
  }, [projectName, selectedCluster]);

  // Shared toggle handlers — used by both the Semantic cluster modal and
  // the Final Reports tab. Optimistic local update + persisted POST.
  // Toggling one set clears the other (mutually exclusive server-side).
  const projectShortForMutation = PROJECT_NAME_TO_SHORT[projectName] || '';
  const handleToggleRetain = async (rid: string) => {
    if (!projectShortForMutation) return;
    const wasRetained = projectRetained.has(rid);
    setProjectRetained((prev) => {
      const next = new Set(prev);
      if (wasRetained) next.delete(rid); else next.add(rid);
      return next;
    });
    if (!wasRetained && projectRetired.has(rid)) {
      setProjectRetired((prev) => { const n = new Set(prev); n.delete(rid); return n; });
    }
    const ok = await toggleReportRetained(projectShortForMutation, rid, !wasRetained);
    if (!ok) {
      setProjectRetained((prev) => {
        const next = new Set(prev);
        if (wasRetained) next.add(rid); else next.delete(rid);
        return next;
      });
    }
  };
  const handleToggleRetire = async (rid: string) => {
    if (!projectShortForMutation) return;
    const wasRetired = projectRetired.has(rid);
    setProjectRetired((prev) => {
      const next = new Set(prev);
      if (wasRetired) next.delete(rid); else next.add(rid);
      return next;
    });
    if (!wasRetired && projectRetained.has(rid)) {
      setProjectRetained((prev) => { const n = new Set(prev); n.delete(rid); return n; });
    }
    const ok = await toggleReportRetired(projectShortForMutation, rid, !wasRetired);
    if (!ok) {
      setProjectRetired((prev) => {
        const next = new Set(prev);
        if (wasRetired) next.add(rid); else next.delete(rid);
        return next;
      });
    }
  };

  // Derive the "finalKept" ID set from whatever's currently in scope. When
  // an experiment is promoted as primary, we want the Final Reports tab to
  // immediately reflect THAT experiment's primaries + singletons — not the
  // stale `isFinalCanonical` baked into reports.json at emit time. This
  // avoids needing to re-emit every time the user flips the active.
  const experimentFinalKeptIds = useMemo<Set<string> | null>(() => {
    if (!activeExpInfo?.expId || semanticClusters.length === 0) return null;
    const ids = new Set<string>();
    for (const c of semanticClusters) {
      if (c.isSingletons) {
        // Singleton bucket — every member is a final-kept singleton.
        for (const m of c.memberIds) ids.add(m);
      } else {
        // Multi-cluster — only the primary is kept.
        if (c.primaryReportId) ids.add(c.primaryReportId);
      }
    }
    return ids;
  }, [activeExpInfo, semanticClusters]);

  // Effective Final Reports to Keep:
  //   (experimentFinalKept ?? isFinalCanonical) ∪ user_retained − user_retired
  // When a primary experiment is promoted, its primaries+singletons become
  // the truth; retain/retire overrides still apply on top.
  const finalKeptReports = useMemo(() => {
    return reports.filter((r) => {
      if (projectRetired.has(r.id)) return false;
      const inExperiment = experimentFinalKeptIds
        ? experimentFinalKeptIds.has(r.id)
        : r.isFinalCanonical === true;
      return inExperiment || projectRetained.has(r.id);
    });
  }, [reports, experimentFinalKeptIds, projectRetained, projectRetired]);

  const filteredReports = useMemo(() => {
    const q = reportSearch.toLowerCase().trim();
    // Scope: final = only final-kept; all = everything in reports.json
    let list = reportScope === 'final' ? finalKeptReports : reports;
    // Source-type filter
    if (reportSourceFilter !== 'all') {
      list = list.filter((r) => {
        const src = (r.sourceType || '').trim();
        if (reportSourceFilter === 'none') return src === '';
        return src === reportSourceFilter;
      });
    }
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
    // Min-threshold filters (strict `>`). Empty string / NaN = no filter.
    const minU = reportMinUsers === '' ? null : parseInt(reportMinUsers, 10);
    if (minU != null && !Number.isNaN(minU)) {
      list = list.filter((r) => (r.users ?? 0) > minU);
    }
    const minE = reportMinExecs === '' ? null : parseInt(reportMinExecs, 10);
    if (minE != null && !Number.isNaN(minE)) {
      list = list.filter((r) => (r.executions ?? 0) > minE);
    }
    list = [...list].sort((a, b) => {
      if (reportSort === 'name') return a.name.localeCompare(b.name);
      if (reportSort === 'users') return b.users - a.users;
      return b.executions - a.executions;
    });
    // Scope-aware render cap. Final-kept sets are usually <10k (was a hard
    // 2000 previously, which silently truncated GI's 2,610 — noticed by the
    // "shows 2000 but count says 2,610" bug report). All-inventory can be
    // 100k+, so keep a tighter cap there and let filters narrow it down.
    const MAX_ROWS = reportScope === 'final' ? 20000 : 5000;
    return list.slice(0, MAX_ROWS);
  }, [finalKeptReports, reports, reportScope, reportSourceFilter,
      reportSearch, reportSort, reportPathFilter,
      reportMinUsers, reportMinExecs]);

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
        // For "Active Reports" we use the active canonical reports from reports.json
        setReportListData(
          reports
            .filter((r) => (r.status || 'active') === 'active')
            .map((r) => ({
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
              sourceType: r.sourceType ?? null,
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
            sourceType: r.sourceType ?? null,
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
            sourceType: r.sourceType ?? null,
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
            Run <code className="bg-[#16213e] px-2 py-1 rounded">python -m db.compute.build --project all</code> to generate data files
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
          {(Object.keys(TAB_LABELS) as TabKey[])
            .filter((key) => !HIDDEN_TABS.has(key))
            .map((key) => (
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
        {activeTab === 'overview' && <OverviewTab summary={effectiveSummary!} onKpiClick={handleOpenKpiList} />}
        {activeTab === 'methodology' && <MethodologyTab />}
        {activeTab === 'phases' && <PhasesTab summary={effectiveSummary!} />}
        {activeTab === 'collisions' && (
          <CollisionsTab
            collisions={filteredCollisions}
            totalCount={collisions.length}
            search={collisionSearch}
            setSearch={setCollisionSearch}
            passFilter={collisionPassFilter}
            setPassFilter={setCollisionPassFilter}
            summary={effectiveSummary!}
            onOpen={setSelectedCollision}
          />
        )}
        {activeTab === 'fingerprints' && (
          <FingerprintsTab
            groups={filteredFingerprints}
            totalCount={fingerprints.length}
            search={fingerprintSearch}
            setSearch={setFingerprintSearch}
            summary={effectiveSummary!}
            onOpen={setSelectedFingerprint}
          />
        )}
        {activeTab === 'sqlhash' && (
          <SqlHashTab
            groups={filteredSqlHash}
            totalCount={sqlHashGroups.length}
            search={sqlHashSearch}
            setSearch={setSqlHashSearch}
            summary={effectiveSummary!}
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
            summary={effectiveSummary!}
            onOpen={setSelectedAstCluster}
          />
        )}
        {activeTab === 'families' && (
          <FamiliesTab
            families={filteredFamilies}
            totalCount={families.length}
            search={familySearch}
            setSearch={setFamilySearch}
            summary={effectiveSummary!}
            onOpen={setSelectedFamily}
          />
        )}
        {activeTab === 'clusters' && (
          <ClustersTab
            clusters={filteredClusters}
            search={clusterSearch}
            setSearch={setClusterSearch}
            summary={effectiveSummary!}
            reportById={reportById}
            onOpen={handleOpenCluster}
          />
        )}
        {activeTab === 'semantic' && (
          <SemanticTab
            clusters={semanticClusters}
            search={semanticSearch}
            setSearch={setSemanticSearch}
            actionFilter={semanticActionFilter}
            setActionFilter={setSemanticActionFilter}
            summary={effectiveSummary!}
            reportById={reportById}
            onOpen={setSelectedSemantic}
            activeExpInfo={activeExpInfo}
            onClearActive={async () => {
              if (!projectName) return;
              await clearActiveExperiment(projectName);
              await reloadSemanticFromActive();
            }}
            onGoToPlayground={() => setActiveTab('ai-playground')}
            finalKeptCount={finalKeptReports.length}
            retainedCount={projectRetained.size}
            retiredCount={projectRetired.size}
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
            summary={effectiveSummary!}
            semanticClusters={(() => {
              // If the active overlay (promoted experiment) has LLM reviews,
              // prefer THOSE — they reflect the clusters the user actually
              // promoted. Otherwise fall back to the pipeline's reviewed
              // semantic clusters. This keeps the LLM Review tab consistent
              // with whatever the user is currently looking at in Semantic.
              const overlaidReviewed = semanticClusters.filter(
                (c) => !c.isSingletons && c.llm && !c.llm.error && c.llm.label,
              ).length;
              if (overlaidReviewed > 0) return semanticClusters;
              return rawPipelineSemanticClusters.length > 0
                ? rawPipelineSemanticClusters
                : semanticClusters;
            })()}
            reportById={reportById}
            onOpen={setSelectedLlmReview}
          />
        )}
        {activeTab === 'reports' && (
          <ReportsTab
            reports={filteredReports}
            totalCount={reportScope === 'final' ? finalKeptReports.length : reports.length}
            allReports={reports}
            finalKeptCount={finalKeptReports.length}
            search={reportSearch}
            setSearch={setReportSearch}
            sort={reportSort}
            setSort={setReportSort}
            pathFilter={reportPathFilter}
            setPathFilter={setReportPathFilter}
            sourceFilter={reportSourceFilter}
            setSourceFilter={setReportSourceFilter}
            scope={reportScope}
            setScope={setReportScope}
            clusters={clusters}
            onOpenCluster={handleOpenCluster}
            onOpenReport={setSelectedReport}
            projectId={currentProject?.id || ''}
            projectName={projectName}
            retained={projectRetained}
            retiredOverrides={projectRetired}
            onToggleRetain={handleToggleRetain}
            onToggleRetire={handleToggleRetire}
            minUsers={reportMinUsers}
            setMinUsers={setReportMinUsers}
            minExecs={reportMinExecs}
            setMinExecs={setReportMinExecs}
          />
        )}
        {activeTab === 'comparison' && (
          <ClusterComparisonTab
            data={clusterComparison}
            category={comparisonCategory}
            setCategory={setComparisonCategory}
            search={comparisonSearch}
            setSearch={setComparisonSearch}
          />
        )}
        {activeTab === 'embeddings' && (
          <EmbeddingsTab
            projectId={currentProject?.id || ''}
            projectName={projectName}
          />
        )}
        {activeTab === 'ai-playground' && (
          <AiPlaygroundTab
            projectId={currentProject?.id || ''}
            projectName={projectName}
            reportById={reportById}
            onActiveChanged={reloadSemanticFromActive}
          />
        )}
        {activeTab === 'density' && (
          <DensityClusteringTab
            projectId={currentProject?.id || ''}
            projectName={projectName}
            reportById={reportById}
            onActiveChanged={reloadSemanticFromActive}
          />
        )}
        {activeTab === 'classify-domain' && (
          <ClassifyDomainTab
            projectId={currentProject?.id || ''}
          />
        )}
        {activeTab === 'heavy-users' && (
          <HeavyUsersTab
            users={heavyUsers}
            search={heavyUserSearch}
            setSearch={setHeavyUserSearch}
            filter={heavyUserFilter}
            setFilter={setHeavyUserFilter}
            onOpen={setSelectedHeavyUser}
          />
        )}
        {activeTab === 'summary' && <SummaryTab summary={effectiveSummary!} />}
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

      {/* Semantic cluster drill-down modal */}
      {selectedSemantic && (
        <SemanticClusterModal
          cluster={selectedSemantic}
          reportById={reportById}
          projectId={currentProject?.id || ''}
          onClose={() => setSelectedSemantic(null)}
          onCompare={handleCompareReports}
          retained={projectRetained}
          retiredOverrides={projectRetired}
          onToggleRetain={handleToggleRetain}
          onToggleRetire={handleToggleRetire}
        />
      )}

      {/* Heavy user drill-down modal */}
      {selectedHeavyUser && (
        <HeavyUserModal
          user={selectedHeavyUser}
          onClose={() => setSelectedHeavyUser(null)}
        />
      )}

      {/* Report detail modal (Final Reports to Keep drill-down) */}
      {selectedReport && (
        <ReportDetailModal
          report={selectedReport}
          clusters={clusters}
          onClose={() => setSelectedReport(null)}
          onOpenCluster={(c) => {
            setSelectedReport(null);
            handleOpenCluster(c);
          }}
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

// Renders on the Combined Reports Project's Overview tab only. Pulls
// /playground_api/combined/status on mount to tell the user when each
// source project was last snapshotted + whether anything has drifted.
// "Rebuild" kicks off an async task that re-copies + re-runs the pipeline.
function CombinedRebuildBanner() {
  const [status, setStatus] = useState<CombinedStatus | null>(null);
  const [task, setTask] = useState<PlaygroundTask | null>(null);
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const load = async () => {
    setStatus(await fetchCombinedStatus());
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  // Poll the active task
  useEffect(() => {
    if (!task || task.status !== 'running') {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    const tick = async () => {
      const t = await fetchPlaygroundTask(task.id);
      if (!t) return;
      setTask(t);
      if (t.status === 'completed') {
        setRunStartedAt(null);
        setTask(null);
        await load();
        // Nudge the page — the summary/cluster data just changed, so the
        // simplest correct thing is a full reload of the tab.
        window.location.reload();
      } else if (t.status === 'failed' || t.status === 'interrupted') {
        setError(t.error || `Task ${t.status}`);
        setRunStartedAt(null);
        setTask(null);
      }
    };
    pollRef.current = window.setInterval(tick, 2000);
    tick();
    return () => {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [task?.id, task?.status]);

  // Force re-render every second so elapsed timer updates during a run
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!runStartedAt) return;
    const id = window.setInterval(() => setTick((x) => x + 1), 1000);
    return () => window.clearInterval(id);
  }, [runStartedAt]);

  async function handleRebuild() {
    setError(null);
    setRunStartedAt(Date.now());
    try {
      const t = await rebuildCombinedReports();
      setTask(t);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setRunStartedAt(null);
    }
  }

  const running = task?.status === 'running';
  const elapsed = runStartedAt ? Math.floor((Date.now() - runStartedAt) / 1000) : 0;

  if (!status) {
    return (
      <div className="bg-[#16213e] border border-violet-800/40 p-3 rounded text-xs text-gray-400">
        Loading combined status…
      </div>
    );
  }

  return (
    <div className={`p-4 rounded-lg border ${
      status.stale
        ? 'bg-gradient-to-r from-amber-950/40 to-[#16213e] border-amber-700/60'
        : 'bg-gradient-to-r from-violet-950/40 to-[#16213e] border-violet-700/40'
    }`}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className={`text-xs uppercase tracking-wider font-bold mb-2 ${
            status.stale ? 'text-amber-300' : 'text-violet-300'
          }`}>
            Combined Reports Project
            {status.stale && (
              <span className="ml-2 font-normal normal-case text-amber-400">
                ⚠ {status.lastBuiltAt ? 'source projects changed since last rebuild' : 'not built yet'}
              </span>
            )}
          </div>
          <div className="text-sm text-gray-300 leading-relaxed">
            {status.totalReports > 0 ? (
              <>
                <strong className="text-white">{status.totalReports.toLocaleString()}</strong> reports combined
                {status.lastBuiltAt && (
                  <>
                    {' '}· last rebuilt <strong>{new Date(status.lastBuiltAt).toLocaleString()}</strong>
                  </>
                )}
              </>
            ) : (
              <span className="italic text-gray-400">Never built. Click Rebuild to snapshot final-kept reports from GO, GI, and INSIGHT.</span>
            )}
          </div>
          <div className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-2 text-xs">
            {status.sources.map((s) => (
              <div key={s.sourceProjectId} className={`p-2 rounded border ${
                s.stale ? 'border-amber-700/50 bg-amber-950/20' : 'border-[#1e2d50] bg-[#0f0f1a]'
              }`}>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-white">{s.sourceName}</span>
                  {s.stale && (
                    <span className="text-[9px] font-bold text-amber-300 bg-amber-950/60 border border-amber-700/60 rounded px-1 py-0.5">
                      DRIFT
                    </span>
                  )}
                </div>
                <div className="text-gray-400 mt-0.5">
                  {s.builtAt ? (
                    <>
                      <span>{s.reportCount.toLocaleString()} copied</span>
                      {s.currentCount !== s.reportCount && (
                        <span className="text-amber-400"> · now {s.currentCount.toLocaleString()}</span>
                      )}
                    </>
                  ) : (
                    <span className="italic">{s.currentCount.toLocaleString()} final-kept · not copied yet</span>
                  )}
                </div>
              </div>
            ))}
          </div>
          {error && (
            <div className="mt-2 text-xs text-red-300 bg-red-950/40 border border-red-700/50 rounded p-2">
              Rebuild failed: {error}
            </div>
          )}
          {running && (
            <div className="mt-2 text-xs text-violet-200">
              Rebuilding… ({elapsed}s) — copying reports, running the pipeline, emitting JSON. Usually 2–5 minutes.
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2 flex-shrink-0">
          <button onClick={handleRebuild} disabled={running}
            className={`text-sm font-semibold px-4 py-2 rounded ${
              running
                ? 'bg-violet-900 text-gray-500 cursor-not-allowed'
                : status.stale
                ? 'bg-amber-600 hover:bg-amber-500 text-white'
                : 'bg-violet-600 hover:bg-violet-500 text-white'
            }`}>
            {running ? `Rebuilding… (${elapsed}s)` : status.lastBuiltAt ? '↻ Rebuild now' : '▶ Build now'}
          </button>
        </div>
      </div>
    </div>
  );
}

function OverviewTab({
  summary,
  onKpiClick,
}: {
  summary: RationalizationSummary;
  onKpiClick: (kind: 'inventory' | 'retired' | 'active') => void;
}) {
  const isCombined = summary.projectName === 'Combined Reports Project';
  const kpis: {
    label: string;
    value: number | string;
    color: string;
    border: string;
    clickKind?: 'inventory' | 'retired' | 'active';
  }[] = [
    { label: 'Original Reports Inventory', value: summary.totalReportsInventory ?? summary.totalInventory, color: 'text-white', border: 'border-blue-500', clickKind: 'inventory' },
    { label: 'Retired (No Usage)', value: summary.retired, color: 'text-orange-400', border: 'border-orange-500', clickKind: 'retired' },
    { label: 'Active Reports', value: summary.afterTelemetry, color: 'text-white', border: 'border-blue-500', clickKind: 'active' },
    { label: 'Semantic Clusters', value: summary.semanticClustersTotal ?? summary.clustersTotal, color: 'text-white', border: 'border-teal-500' },
    { label: 'Final Unique (Semantic)', value: summary.afterSemantic ?? summary.afterSimilarity, color: 'text-green-400', border: 'border-green-500' },
    { label: 'Total Reduction', value: `${summary.reductionPct}%`, color: 'text-green-400', border: 'border-green-500' },
  ];

  return (
    <div className="space-y-6">
      {isCombined && <CombinedRebuildBanner />}
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
                { name: 'Family Collapse', value: (summary.afterAst ?? summary.afterFingerprint ?? summary.afterTelemetry) - (summary.afterFamily ?? summary.afterPostAstFamily ?? summary.afterAst ?? summary.afterFingerprint ?? summary.afterTelemetry), fill: '#f39c12' },
                { name: 'Semantic Cluster', value: (summary.afterFamily ?? summary.afterPostAstFamily ?? summary.afterAst ?? summary.afterTelemetry) - (summary.afterSemantic ?? summary.afterSimilarity), fill: '#16a085' },
                { name: 'Final Unique', value: summary.afterSemantic ?? summary.afterSimilarity, fill: '#27ae60' },
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
      title: 'Jaccard Clustering (Weighted)',
      body: 'Lexical set-overlap clustering on post-AST canonicals. For every pair compute a weighted Jaccard: pairs with SQL get 0.60 AST + 0.25 metrics + 0.15 tables; pairs without SQL get 0.40 metrics + 0.40 tables + 0.20 filter attributes. Pairs ≥ 0.80 are linked; connected components form clusters. Catches reports doing essentially the same query with parameter-level differences. Complementary to Semantic Clustering (which runs on the same input via vector embeddings and catches synonym/terminology drift this one misses).',
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
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-16">Source</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Path</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Last Exec</th>
              </tr>
            </thead>
            <tbody>
              {group.members.map((m, i) => {
                const isSelected = selectedForCompare.includes(m.id);
                const rd = reportById.get(m.id);
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
                    <td className="p-2"><SourceTypePill sourceType={rd?.sourceType} /></td>
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
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-16">Source</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Path</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Last Exec</th>
              </tr>
            </thead>
            <tbody>
              {cluster.members.map((m, i) => {
                const isSelected = selectedForCompare.includes(m.id);
                const rd = reportById.get(m.id);
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
                    <td className="p-2"><SourceTypePill sourceType={rd?.sourceType} /></td>
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
  summary,
  onOpen,
}: {
  families: Family[];
  totalCount: number;
  search: string;
  setSearch: (v: string) => void;
  summary: RationalizationSummary | null;
  onOpen: (f: Family) => void;
}) {
  const totalReducible = families.reduce((s, f) => s + f.reducible, 0);
  const totalReports = families.reduce((s, f) => s + f.size, 0);

  return (
    <div className="space-y-4">
      {/* Pipeline lineage banner — Families runs on post-AST survivors and
          IS the reduction stage that feeds clustering. */}
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
            {summary.afterCollisionCollapse !== undefined && (
              <>
                <span className="text-gray-500">{'\u2192'}</span>
                <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
                  <span className="text-gray-400">Collision:</span>{' '}
                  <span className="font-bold text-white">{summary.afterCollisionCollapse.toLocaleString()}</span>
                </span>
              </>
            )}
            <span className="text-gray-500">{'\u2192'}</span>
            <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
              <span className="text-gray-400">Fingerprint:</span>{' '}
              <span className="font-bold text-white">{summary.afterFingerprint?.toLocaleString() ?? '-'}</span>
            </span>
            {summary.afterSqlHash !== undefined && (
              <>
                <span className="text-gray-500">{'\u2192'}</span>
                <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
                  <span className="text-blue-300">SQL Hash:</span>{' '}
                  <span className="font-bold text-white">{summary.afterSqlHash.toLocaleString()}</span>
                </span>
              </>
            )}
            {summary.afterAst !== undefined && (
              <>
                <span className="text-gray-500">{'\u2192'}</span>
                <span
                  className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]"
                  title="Post-AST canonical set — the input to the Family stage."
                >
                  <span className="text-pink-300">After AST (Family input):</span>{' '}
                  <span className="font-bold text-white">{summary.afterAst.toLocaleString()}</span>
                </span>
              </>
            )}
            <span className="text-gray-500">{'\u2192'}</span>
            <span
              className="px-3 py-1.5 bg-purple-900/40 rounded border border-purple-700 ring-2 ring-purple-500/40"
              title="Post-AST survivors grouped by name pattern; each multi-member family elects a canonical (highest-executions). Non-canonicals drop out. Output feeds Similarity + Semantic clustering."
            >
              <span className="text-purple-300">After Family:</span>{' '}
              <span className="font-bold text-white">{(summary.afterFamily ?? summary.afterPostAstFamily ?? summary.afterAst)?.toLocaleString() ?? '-'}</span>
              {(summary.familyReducible ?? summary.postAstFamilyCollapsed ?? 0) > 0 && (
                <span className="text-green-400 text-xs ml-1">
                  ({'\u2212'}{(summary.familyReducible ?? summary.postAstFamilyCollapsed)?.toLocaleString()})
                </span>
              )}
            </span>
            <span className="text-gray-500">{'\u2192'}</span>
            <span className="text-gray-500">feeds Similarity + Semantic</span>
          </div>

          <p className="text-xs text-gray-500 mt-3 leading-relaxed">
            Families runs on the <strong className="text-pink-300">{summary.afterAst?.toLocaleString() ?? '?'}</strong> post-AST
            canonicals. Each multi-member family (same name root, different suffixes like{' '}
            <code className="text-gray-400 bg-[#0f0f1a] px-1 rounded">_ACC</code> /{' '}
            <code className="text-gray-400 bg-[#0f0f1a] px-1 rounded">_MENS</code>) elects the highest-exec member as the
            canonical; the {(summary.familyReducible ?? summary.postAstFamilyCollapsed ?? 0).toLocaleString()} non-canonical
            siblings are dropped from the clustering input. The {totalCount.toLocaleString()} family groups below show the
            elected canonical (✓) and the variant suffixes that got collapsed.
          </p>
        </div>
      )}

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
  summary,
  reportById,
  onOpen,
}: {
  clusters: ClusterMeta[];
  search: string;
  setSearch: (v: string) => void;
  summary: RationalizationSummary | null;
  reportById: Map<string, ReportDetail>;
  onOpen: (c: ClusterMeta) => void;
}) {
  // Reducible = (size - 1) per MULTI-member cluster only. The singletons
  // bucket (isSingletons=true) holds N unrelated reports where each already
  // survives — no reduction there. Including it here caused the lineage
  // banner to display "(−4,722)" against "After Similarity: 3,404" which is
  // numerically impossible.
  const clusterReducible = clusters
    .filter((c) => !c.isSingletons)
    .reduce((s, c) => s + Math.max(0, c.size - 1), 0);
  const sqlCountByCluster = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of clusters) {
      let n = 0;
      for (const id of c.memberIds) {
        if (reportById.get(id)?.sql) n++;
      }
      m.set(c.id, n);
    }
    return m;
  }, [clusters, reportById]);
  return (
    <div className="space-y-4">
      {summary && (
        <div className="bg-gradient-to-r from-teal-950/60 to-[#16213e] p-4 rounded-lg border border-teal-800/40">
          <div className="text-xs uppercase tracking-wider text-teal-300 font-bold mb-2">Pipeline Lineage</div>
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
                  <span className="text-gray-400">After collision collapse:</span>{' '}
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
            {summary.afterAst !== undefined && (
              <>
                <span className="text-gray-500">{'\u2192'}</span>
                <span
                  className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]"
                  title="AST-based structural dedup — canonical subtree hashes merged ≥ 0.80"
                >
                  <span className="text-pink-300">After AST:</span>{' '}
                  <span className="font-bold text-white">{summary.afterAst.toLocaleString()}</span>
                  {summary.astSequentialRemovable !== undefined && summary.astSequentialRemovable > 0 ? (
                    <span className="text-green-400 text-xs ml-1">({'\u2212'}{summary.astSequentialRemovable.toLocaleString()})</span>
                  ) : (
                    <span className="text-gray-500 text-xs ml-1">(0 new)</span>
                  )}
                </span>
              </>
            )}
            {summary.afterPostAstFamily !== undefined && (
              <>
                <span className="text-gray-500">{'\u2192'}</span>
                <span
                  className="px-3 py-1.5 bg-orange-900/20 rounded border border-orange-700/50"
                  title="Post-AST Family collapse — name-sibling canonicals collapsed to 1 per family. Input to Similarity + Semantic."
                >
                  <span className="text-orange-300">After Family:</span>{' '}
                  <span className="font-bold text-white">{summary.afterPostAstFamily.toLocaleString()}</span>
                  {summary.postAstFamilyCollapsed !== undefined && summary.postAstFamilyCollapsed > 0 && (
                    <span className="text-green-400 text-xs ml-1">({'\u2212'}{summary.postAstFamilyCollapsed.toLocaleString()})</span>
                  )}
                </span>
              </>
            )}
            <span className="text-gray-500">{'\u2192'}</span>
            <span
              className="px-3 py-1.5 bg-teal-900/40 rounded border border-teal-700 ring-2 ring-teal-500/40"
              title="Weighted Jaccard on metrics/tables/filters (and AST when available), threshold ≥ 0.80 — final fuzzy clustering layer"
            >
              <span className="text-teal-300">After Similarity:</span>{' '}
              <span className="font-bold text-white">{summary.afterSimilarity.toLocaleString()}</span>
              {clusterReducible > 0 && (
                <span className="text-green-400 text-xs ml-1">({'\u2212'}{clusterReducible.toLocaleString()})</span>
              )}
            </span>
          </div>
        </div>
      )}

      <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-teal-500">
        <h3 className="text-white font-bold mb-2">Jaccard Clustering (Weighted)</h3>
        <p className="text-xs text-gray-400 leading-relaxed">
          Lexical fuzzy clustering on post-AST canonicals using{' '}
          <strong className="text-teal-300">weighted Jaccard</strong> over metric / table / filter /
          AST-hash sets — with SQL:{' '}
          <code className="mx-1 px-1 bg-[#0f0f1a] rounded text-teal-300">0.60 AST + 0.25 metrics + 0.15 tables</code>,
          without SQL:{' '}
          <code className="mx-1 px-1 bg-[#0f0f1a] rounded text-teal-300">0.40 metrics + 0.40 tables + 0.20 filters</code>.
          Pairs ≥ 0.80 are linked; connected components form clusters. Unlike fingerprint/SQL-hash/AST
          (provable dedup), these are <strong>fuzzy candidates</strong> that need human or LLM review
          before collapse. The <strong className="text-teal-300">Semantic Clusters</strong> tab is an
          alternative clustering on the same post-AST input that uses vector embeddings instead of
          set-overlap — it catches synonyms and terminology drift this one misses.
        </p>
      </div>

      {summary && (() => {
        const multiClusters = clusters.filter((c) => !c.isSingletons);
        const singletonEntry = clusters.find((c) => c.isSingletons);
        const reportsInMulti = multiClusters.reduce((s, c) => s + c.size, 0);
        const multiCount = multiClusters.length;
        const singletonCount = singletonEntry?.size ?? 0;
        const inputCount = reportsInMulti + singletonCount;
        const collapsed = reportsInMulti - multiCount;
        const finalUnique = multiCount + singletonCount;
        return (
          <div className="bg-[#0f1a26] p-4 rounded-lg border border-teal-700/40">
            <div className="text-xs uppercase tracking-wider text-teal-300 font-bold mb-3">
              How the Final Unique Count Is Derived
            </div>
            <div className="flex items-center flex-wrap gap-2 text-sm">
              <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
                <span className="text-gray-400">Post-AST survivors (input):</span>{' '}
                <span className="font-bold text-white">{inputCount.toLocaleString()}</span>
              </span>
              <span className="text-gray-500">=</span>
              <span className="px-3 py-1.5 bg-blue-900/20 rounded border border-blue-700/50">
                <span className="text-blue-300">reports in multi-clusters:</span>{' '}
                <span className="font-bold text-white">{reportsInMulti.toLocaleString()}</span>
              </span>
              <span className="text-gray-500">+</span>
              <span className="px-3 py-1.5 bg-slate-800/40 rounded border border-slate-600/50">
                <span className="text-slate-300">singletons:</span>{' '}
                <span className="font-bold text-white">{singletonCount.toLocaleString()}</span>
              </span>
            </div>
            <div className="flex items-center flex-wrap gap-2 text-sm mt-2">
              <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
                <span className="text-gray-400">After Similarity (final unique):</span>{' '}
                <span className="font-bold text-teal-300">{finalUnique.toLocaleString()}</span>
                {summary.afterSimilarity !== finalUnique && (
                  <span className="text-red-400 text-xs ml-1" title="Summary value mismatch">
                    (summary: {summary.afterSimilarity.toLocaleString()})
                  </span>
                )}
              </span>
              <span className="text-gray-500">=</span>
              <span className="px-3 py-1.5 bg-blue-900/20 rounded border border-blue-700/50">
                <span className="text-blue-300">cluster canonicals:</span>{' '}
                <span className="font-bold text-white">{multiCount.toLocaleString()}</span>{' '}
                <span className="text-gray-500 text-xs">(1 per multi-cluster)</span>
              </span>
              <span className="text-gray-500">+</span>
              <span className="px-3 py-1.5 bg-slate-800/40 rounded border border-slate-600/50">
                <span className="text-slate-300">singletons kept as-is:</span>{' '}
                <span className="font-bold text-white">{singletonCount.toLocaleString()}</span>
              </span>
              <span className="text-gray-500">·</span>
              <span className="px-3 py-1.5 bg-green-900/20 rounded border border-green-700/50">
                <span className="text-green-300">reducible:</span>{' '}
                <span className="font-bold text-white">{'\u2212'}{collapsed.toLocaleString()}</span>{' '}
                <span className="text-gray-500 text-xs">(collapsed into canonicals)</span>
              </span>
            </div>
          </div>
        );
      })()}

      <div className="bg-[#16213e] p-6 rounded-lg">
      <h2 className="text-lg font-bold text-white mb-4">
        Jaccard Clusters <span className="text-sm text-gray-400 font-normal">({clusters.length} shown)</span>
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
        {clusters.map((c) => {
          const sqlN = sqlCountByCluster.get(c.id) ?? 0;
          const sqlBadgeClass =
            sqlN === c.size
              ? 'bg-emerald-900/50 text-emerald-300 border-emerald-700/50'
              : sqlN === 0
              ? 'bg-gray-800/60 text-gray-400 border-gray-700/50'
              : 'bg-amber-900/40 text-amber-300 border-amber-700/50';
          const isSingletons = c.isSingletons === true;
          return (
            <div
              key={c.id}
              onClick={() => onOpen(c)}
              className={`p-4 rounded-lg cursor-pointer transition-colors border-l-4 ${
                isSingletons
                  ? 'bg-slate-900/60 hover:bg-slate-800/60 border-slate-400'
                  : 'bg-[#0f0f1a] hover:bg-[#1a2a4a] border-blue-500'
              }`}
            >
              <div className="flex justify-between items-center mb-2">
                <div className="min-w-0 flex-1">
                  <span className={`text-xs font-mono mr-2 ${isSingletons ? 'text-slate-300' : 'text-gray-500'}`}>
                    {c.id}
                  </span>
                  {c.dataQuality === 'low' && c.dataQualityReason === 'empty_features' ? (
                    <span className="text-amber-200 font-semibold text-sm italic" title="This isn't a real similarity cluster — it's a bucket of reports whose inventory extraction failed (no metrics/tables captured). The 'primary' name is arbitrary.">
                      Unclassifiable — inventory extraction failed for most members
                    </span>
                  ) : (
                    <>
                      {!isSingletons && (
                        <span
                          className="text-[9px] font-bold text-amber-300 bg-amber-950/40 border border-amber-700/50 rounded px-1.5 py-0.5 mr-2 align-middle"
                          title="Keep-candidate — the primary report of this cluster"
                        >
                          ⭐ PRIMARY
                        </span>
                      )}
                      {!isSingletons && (
                        <span className="mr-2 align-middle">
                          <FamilySiblingsIndicator siblings={reportById.get(c.primaryReportId)?.familySiblings} />
                        </span>
                      )}
                      <span className="text-white font-semibold text-sm" title={c.primaryReportId}>{c.primaryName}</span>
                    </>
                  )}
                  {isSingletons && (
                    <div className="text-xs text-slate-400 mt-0.5">
                      Post-AST reports with no similarity pair {'\u2265'} 0.80 — browse or pick any two to compare.
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-3 flex-shrink-0">
                  <DataQualityBadge cluster={c} />
                  <span
                    className={`text-xs font-semibold px-2 py-0.5 rounded border ${sqlBadgeClass}`}
                    title="Count of members with extracted SQL"
                  >
                    SQL: {sqlN}/{c.size}
                  </span>
                  <div
                    className={`text-white text-xs font-bold px-3 py-1 rounded-full ${
                      isSingletons ? 'bg-slate-600' : 'bg-blue-600'
                    }`}
                  >
                    {c.size} reports
                  </div>
                  <VscChevronRight className="text-gray-500" />
                </div>
              </div>
              {!isSingletons && (
                <div className="text-xs text-gray-400">
                  {c.totalExecutions.toLocaleString()} total executions · {c.commonMetrics.length} metrics · {c.commonTables.length} tables · {c.commonFilters.length} filter attrs
                </div>
              )}
              {isSingletons && (
                <div className="text-xs text-slate-400">
                  {c.totalExecutions.toLocaleString()} total executions across all singletons
                </div>
              )}
            </div>
          );
        })}
      </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// SEMANTIC CLUSTERS TAB
// ─────────────────────────────────────────────────────────────────────

const SEMANTIC_ACTION_COLORS: Record<string, string> = {
  MERGE_IMMEDIATE: 'bg-red-900/50 text-red-200 border-red-700/60',
  PARAMETERIZE: 'bg-orange-900/50 text-orange-200 border-orange-700/60',
  REVIEW_WITH_OWNER: 'bg-yellow-900/40 text-yellow-200 border-yellow-700/60',
  KEEP_SEPARATE: 'bg-slate-800/60 text-slate-300 border-slate-600/60',
};

function SemanticTab({
  clusters,
  search,
  setSearch,
  actionFilter,
  setActionFilter,
  summary,
  reportById,
  onOpen,
  activeExpInfo,
  onClearActive,
  onGoToPlayground,
  finalKeptCount,
  retainedCount,
  retiredCount,
}: {
  clusters: SemanticCluster[];
  search: string;
  setSearch: (v: string) => void;
  actionFilter: string;
  setActionFilter: (v: string) => void;
  summary: RationalizationSummary | null;
  reportById: Map<string, ReportDetail>;
  onOpen: (c: SemanticCluster) => void;
  activeExpInfo?: ActiveExperimentInfo | null;
  onClearActive?: () => void | Promise<void>;
  onGoToPlayground?: () => void;
  finalKeptCount: number;
  retainedCount: number;
  retiredCount: number;
}) {
  const sqlCountByCluster = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of clusters) {
      let n = 0;
      for (const id of c.memberIds) {
        if (reportById.get(id)?.sql) n++;
      }
      m.set(c.id, n);
    }
    return m;
  }, [clusters, reportById]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    let list = clusters;
    if (actionFilter !== 'all') {
      list = list.filter((c) => c.llm?.action === actionFilter);
    }
    if (!q) return list;
    return list.filter(
      (c) =>
        c.primaryName.toLowerCase().includes(q) ||
        (c.llm?.label || '').toLowerCase().includes(q) ||
        (c.llm?.businessFunction || '').toLowerCase().includes(q) ||
        c.id.toLowerCase().includes(q)
    );
  }, [clusters, search, actionFilter]);

  // Empty state — semantic stage hasn't been run yet
  if (clusters.length === 0) {
    return (
      <div className="bg-[#16213e] p-8 rounded-lg text-center space-y-4">
        <div className="text-4xl">🧭</div>
        <h2 className="text-xl font-bold text-white">No semantic clusters yet</h2>
        <p className="text-sm text-gray-400 max-w-2xl mx-auto">
          This tab shows clusters formed by <strong className="text-teal-300">vector embedding</strong> of
          each report's name, path, metrics, tables, filters, and normalized SQL — then grouped by
          cosine similarity ≥ 0.85 in semantic space, and labeled by an LLM.
        </p>
        <p className="text-xs text-gray-500 max-w-2xl mx-auto">
          Populate by running on the backend:
        </p>
        <pre className="inline-block text-left bg-[#0f0f1a] border border-[#1e2d50] rounded px-4 py-3 text-xs text-emerald-300 font-mono">
          python -m db.compute.semantic --project <span className="text-gray-500">&lt;project-id&gt;</span>
        </pre>
        <p className="text-xs text-gray-500">
          Then re-emit JSON with <code className="text-gray-400">python -m db.emit</code> and refresh.
        </p>
      </div>
    );
  }

  const actionCounts: Record<string, number> = {};
  clusters.forEach((c) => {
    const a = c.llm?.action || 'UNREVIEWED';
    actionCounts[a] = (actionCounts[a] || 0) + 1;
  });
  const totalReducible = clusters.reduce((s, c) => s + (c.llm?.removableCount || 0), 0);

  const multiArr = clusters.filter((c) => !c.isSingletons);
  const singEntry = clusters.find((c) => c.isSingletons);
  const semFinalUnique = multiArr.length + (singEntry?.size ?? 0);

  return (
    <div className="space-y-4">
      {activeExpInfo?.expId && (
        <div className="bg-gradient-to-r from-amber-950/60 to-[#16213e] p-3 rounded-lg border border-amber-700/60 flex items-center gap-3">
          <span className="text-2xl">⭐</span>
          <div className="flex-1 min-w-0">
            <div className="text-xs uppercase tracking-wider text-amber-300 font-bold">Showing Playground Experiment</div>
            <div className="text-sm text-white mt-0.5">
              <strong>{activeExpInfo.name || activeExpInfo.expId}</strong>
              <span className="text-gray-400 ml-2 text-xs font-mono">{activeExpInfo.expId}</span>
            </div>
            <div className="text-[11px] text-gray-400 mt-0.5">
              This view and its counts reflect the promoted experiment — not the pipeline's semantic_clusters.json.
            </div>
          </div>
          {onGoToPlayground && (
            <button onClick={onGoToPlayground}
              className="text-xs text-amber-200 hover:text-white border border-amber-700/60 rounded px-2 py-1 flex-shrink-0">
              Open Playground
            </button>
          )}
          {onClearActive && (
            <button onClick={() => onClearActive()}
              className="text-xs text-amber-200 hover:text-white underline flex-shrink-0">
              Revert to pipeline
            </button>
          )}
        </div>
      )}
      {/* Final-kept running total — reflects user overrides from the
          Disposition pill in each cluster modal. Matches the count in the
          "Final Reports to Keep" tab. */}
      <div className="bg-gradient-to-r from-emerald-950/40 to-[#16213e] p-4 rounded-lg border border-emerald-700/40">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <div className="text-xs uppercase tracking-wider text-emerald-300 font-bold mb-1">
              Final Reports to Keep — after overrides
            </div>
            <div className="text-xs text-gray-400">
              Matches the count in the Final Reports to Keep tab. Updates live
              as you toggle Retain / Retire on each cluster member.
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="px-4 py-2 bg-emerald-900/30 rounded-lg border border-emerald-600/60">
              <div className="text-[10px] uppercase tracking-wider text-emerald-300">Final Kept</div>
              <div className="text-2xl font-bold text-white text-right">
                {finalKeptCount.toLocaleString()}
              </div>
            </div>
            <div className="px-3 py-2 bg-[#0f0f1a] rounded-lg border border-[#1e2d50] min-w-[90px]">
              <div className="text-[10px] uppercase tracking-wider text-gray-400">Retained overrides</div>
              <div className="text-lg font-bold text-emerald-300 text-right">
                +{retainedCount.toLocaleString()}
              </div>
            </div>
            <div className="px-3 py-2 bg-[#0f0f1a] rounded-lg border border-[#1e2d50] min-w-[90px]">
              <div className="text-[10px] uppercase tracking-wider text-gray-400">Retired overrides</div>
              <div className="text-lg font-bold text-red-300 text-right">
                {'−'}{retiredCount.toLocaleString()}
              </div>
            </div>
          </div>
        </div>
      </div>
      {/* Pipeline lineage banner — Semantic is a parallel branch off post-AST */}
      {summary && (
        <div className="bg-gradient-to-r from-teal-950/60 to-[#16213e] p-4 rounded-lg border border-teal-800/40">
          <div className="text-xs uppercase tracking-wider text-teal-300 font-bold mb-2">Pipeline Lineage</div>
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
                  title="Same telemetry row attributed to multiple MSTR objects — collapsed to one canonical"
                >
                  <span className="text-gray-400">After collision collapse:</span>{' '}
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
            {summary.afterSqlHash !== undefined && (
              <>
                <span className="text-gray-500">{'\u2192'}</span>
                <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
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
            {summary.afterAst !== undefined && (
              <>
                <span className="text-gray-500">{'\u2192'}</span>
                <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
                  <span className="text-pink-300">After AST:</span>{' '}
                  <span className="font-bold text-white">{summary.afterAst.toLocaleString()}</span>
                  {summary.astSequentialRemovable !== undefined && summary.astSequentialRemovable > 0 && (
                    <span className="text-green-400 text-xs ml-1">
                      ({'\u2212'}{summary.astSequentialRemovable.toLocaleString()})
                    </span>
                  )}
                </span>
              </>
            )}
            {summary.afterPostAstFamily !== undefined && (
              <>
                <span className="text-gray-500">{'\u2192'}</span>
                <span
                  className="px-3 py-1.5 bg-orange-900/20 rounded border border-orange-700/50"
                  title="Post-AST Family collapse — name-sibling canonicals (e.g., GFE085a-ACC, GFE085a-MENS) collapsed to 1 per family. Input to both Similarity and Semantic."
                >
                  <span className="text-orange-300">After Family (clustering input):</span>{' '}
                  <span className="font-bold text-white">{summary.afterPostAstFamily.toLocaleString()}</span>
                  {summary.postAstFamilyCollapsed !== undefined && summary.postAstFamilyCollapsed > 0 && (
                    <span className="text-green-400 text-xs ml-1">
                      ({'\u2212'}{summary.postAstFamilyCollapsed.toLocaleString()})
                    </span>
                  )}
                </span>
              </>
            )}
            {/* Branch indicator — Semantic is parallel to Similarity */}
            <span className="text-gray-500 font-mono">{'\u21B3'}</span>
            <span
              className="px-3 py-1.5 bg-teal-900/40 rounded border border-teal-700 ring-2 ring-teal-500/40"
              title="Embedding + cosine >= 0.85, LLM-labeled — a parallel alternative to the lexical Similarity branch"
            >
              <span className="text-teal-300">After Semantic:</span>{' '}
              <span className="font-bold text-white">{semFinalUnique.toLocaleString()}</span>
              {(() => {
                const clusteringInput = summary.afterPostAstFamily ?? summary.afterAst ?? 0;
                const graphDelta = clusteringInput - semFinalUnique;
                return graphDelta > 0 ? (
                  <span className="text-amber-400 text-xs ml-1" title="Graph-reducible: 1 canonical per multi-member cluster kept, rest collapsed">
                    (graph {'\u2212'}{graphDelta.toLocaleString()})
                  </span>
                ) : null;
              })()}
              {totalReducible > 0 && (
                <span className="text-green-400 text-xs ml-1" title="LLM-judged removable per cluster">
                  (LLM {'\u2212'}{totalReducible.toLocaleString()})
                </span>
              )}
            </span>
          </div>
          {/* Parallel-branch note */}
          <div className="text-xs text-gray-500 mt-3 flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-gray-400">Parallel branch:</span>
            <span className="text-gray-500">post-AST-family</span>
            <span className="font-mono">{'\u2192'}</span>
            <span className="px-2 py-0.5 bg-[#0f0f1a] rounded border border-[#2a2a4a] text-gray-400">
              After Similarity:{' '}
              <span className="text-white font-semibold">{summary.afterSimilarity.toLocaleString()}</span>
              {(() => {
                const input = summary.afterPostAstFamily ?? summary.afterAst ?? 0;
                const delta = input - summary.afterSimilarity;
                return delta > 0 ? (
                  <span className="text-amber-400 ml-1">
                    (graph {'\u2212'}{delta.toLocaleString()})
                  </span>
                ) : null;
              })()}
            </span>
            <span className="text-gray-600">
              &nbsp;— lexical weighted Jaccard on the same input. Semantic and Similarity both read the
              same {(summary.afterPostAstFamily ?? summary.afterAst)?.toLocaleString() ?? '?'} post-family reports; they do not chain.
            </span>
          </div>
        </div>
      )}

      {/* Method description */}
      <div className="bg-gradient-to-r from-teal-950/40 to-[#16213e] p-4 rounded-lg border border-teal-800/40">
        <div className="text-xs uppercase tracking-wider text-teal-300 font-bold mb-2">
          Pipeline — Embedding Layer
        </div>
        <p className="text-xs text-gray-400 leading-relaxed">
          Every post-AST survivor is embedded via <strong className="text-teal-300">OpenAI
          text-embedding-3-large</strong> (3072-dim) on the concatenation of its name, path,
          metrics, tables, filters and normalized SQL. Pairs with cosine similarity ≥ 0.85 form
          edges; connected components are the clusters below. Each multi-member cluster is then
          labeled by an LLM for business function, relationship, and consolidation action.
          Unlike the lexical Similarity tab, this layer tolerates synonym variation and catches
          reports doing the same job with different terminology.
        </p>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-teal-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Multi-member</div>
          <div className="text-2xl font-bold text-white">
            {clusters.filter((c) => !c.isSingletons).length.toLocaleString()}
          </div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-slate-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Singletons</div>
          <div className="text-2xl font-bold text-white">
            {(clusters.find((c) => c.isSingletons)?.size ?? 0).toLocaleString()}
          </div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-green-500">
          <div className="text-xs text-gray-400 uppercase mb-1">LLM Removable</div>
          <div className="text-2xl font-bold text-green-400">{totalReducible.toLocaleString()}</div>
          <div className="text-xs text-gray-500">summed across clusters</div>
        </div>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-purple-500">
          <div className="text-xs text-gray-400 uppercase mb-1">LLM Reviewed</div>
          <div className="text-2xl font-bold text-white">
            {clusters.filter((c) => c.llm && !c.llm.error).length.toLocaleString()}
          </div>
        </div>
      </div>

      {/* Accounting card — same shape as the Clusters tab */}
      {(() => {
        const multi = clusters.filter((c) => !c.isSingletons);
        const singEntry = clusters.find((c) => c.isSingletons);
        const reportsInMulti = multi.reduce((s, c) => s + c.size, 0);
        const multiCount = multi.length;
        const singletonCount = singEntry?.size ?? 0;
        const inputCount = reportsInMulti + singletonCount;
        const collapsed = reportsInMulti - multiCount;  // pure graph reducibility (size − 1 per cluster)
        const finalUnique = multiCount + singletonCount;
        return (
          <div className="bg-[#0f1a26] p-4 rounded-lg border border-teal-700/40">
            <div className="text-xs uppercase tracking-wider text-teal-300 font-bold mb-3">
              How the Semantic Counts Add Up
            </div>
            <div className="flex items-center flex-wrap gap-2 text-sm">
              <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
                <span className="text-gray-400">Post-AST survivors (input):</span>{' '}
                <span className="font-bold text-white">{inputCount.toLocaleString()}</span>
              </span>
              <span className="text-gray-500">=</span>
              <span className="px-3 py-1.5 bg-teal-900/20 rounded border border-teal-700/50">
                <span className="text-teal-300">reports in multi-clusters:</span>{' '}
                <span className="font-bold text-white">{reportsInMulti.toLocaleString()}</span>
              </span>
              <span className="text-gray-500">+</span>
              <span className="px-3 py-1.5 bg-slate-800/40 rounded border border-slate-600/50">
                <span className="text-slate-300">singletons:</span>{' '}
                <span className="font-bold text-white">{singletonCount.toLocaleString()}</span>
              </span>
            </div>
            <div className="flex items-center flex-wrap gap-2 text-sm mt-2">
              <span className="px-3 py-1.5 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
                <span className="text-gray-400">After semantic (graph unique):</span>{' '}
                <span className="font-bold text-teal-300">{finalUnique.toLocaleString()}</span>
              </span>
              <span className="text-gray-500">=</span>
              <span className="px-3 py-1.5 bg-teal-900/20 rounded border border-teal-700/50">
                <span className="text-teal-300">cluster canonicals:</span>{' '}
                <span className="font-bold text-white">{multiCount.toLocaleString()}</span>{' '}
                <span className="text-gray-500 text-xs">(1 per multi-cluster)</span>
              </span>
              <span className="text-gray-500">+</span>
              <span className="px-3 py-1.5 bg-slate-800/40 rounded border border-slate-600/50">
                <span className="text-slate-300">singletons kept:</span>{' '}
                <span className="font-bold text-white">{singletonCount.toLocaleString()}</span>
              </span>
              <span className="text-gray-500">·</span>
              <span className="px-3 py-1.5 bg-green-900/20 rounded border border-green-700/50">
                <span className="text-green-300">graph-reducible:</span>{' '}
                <span className="font-bold text-white">−{collapsed.toLocaleString()}</span>{' '}
                <span className="text-gray-500 text-xs">(size−1 per cluster)</span>
              </span>
              <span className="px-3 py-1.5 bg-green-900/30 rounded border border-green-700/60">
                <span className="text-green-300">LLM-removable:</span>{' '}
                <span className="font-bold text-white">−{totalReducible.toLocaleString()}</span>{' '}
                <span className="text-gray-500 text-xs">(LLM judgment)</span>
              </span>
            </div>
            <p className="text-xs text-gray-500 mt-2 leading-relaxed">
              <strong className="text-gray-400">Graph-reducible</strong> treats every multi-member cluster as 1 canonical
              (size − 1 removable). <strong className="text-gray-400">LLM-removable</strong> is the per-cluster judgment
              — the LLM may mark fewer (KEEP_SEPARATE, REVIEW_WITH_OWNER) or accept the full size − 1 (MERGE_IMMEDIATE,
              PARAMETERIZE). Use LLM-removable as the defensible collapse target.
            </p>
          </div>
        );
      })()}

      {/* Action filter + search */}
      <div className="bg-[#16213e] p-4 rounded-lg">
        <div className="flex gap-3 mb-3 flex-wrap">
          <button
            onClick={() => setActionFilter('all')}
            className={`text-xs px-3 py-1 rounded border ${
              actionFilter === 'all'
                ? 'bg-blue-600 text-white border-blue-500'
                : 'bg-[#0f0f1a] text-gray-400 border-[#1e2d50]'
            }`}
          >
            All ({clusters.length})
          </button>
          {Object.entries(actionCounts)
            .sort((a, b) => b[1] - a[1])
            .map(([a, n]) => (
              <button
                key={a}
                onClick={() => setActionFilter(a)}
                className={`text-xs px-3 py-1 rounded border ${
                  actionFilter === a
                    ? SEMANTIC_ACTION_COLORS[a] || 'bg-blue-600 text-white border-blue-500'
                    : 'bg-[#0f0f1a] text-gray-400 border-[#1e2d50]'
                }`}
              >
                {a} ({n})
              </button>
            ))}
        </div>
        <div className="relative">
          <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name, label, or business function..."
            className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded-lg pl-10 pr-4 py-2.5 text-white text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      {/* Cluster list */}
      <div className="space-y-2 max-h-[65vh] overflow-y-auto pr-2">
        {filtered.map((c) => {
          const isSingletons = c.isSingletons === true;
          const action = c.llm?.action || 'UNREVIEWED';
          const actionCls = SEMANTIC_ACTION_COLORS[action] || 'bg-slate-800/60 text-slate-300 border-slate-700/60';
          const sqlN = sqlCountByCluster.get(c.id) ?? 0;
          const sqlBadge =
            sqlN === c.size
              ? 'bg-emerald-900/50 text-emerald-300 border-emerald-700/50'
              : sqlN === 0
              ? 'bg-gray-800/60 text-gray-400 border-gray-700/50'
              : 'bg-amber-900/40 text-amber-300 border-amber-700/50';
          return (
            <div
              key={c.id}
              onClick={() => onOpen(c)}
              className={`p-4 rounded-lg cursor-pointer transition-colors border-l-4 ${
                isSingletons
                  ? 'bg-slate-900/60 hover:bg-slate-800/60 border-slate-400'
                  : 'bg-[#0f0f1a] hover:bg-[#1a2a4a] border-teal-500'
              }`}
            >
              <div className="flex justify-between items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center flex-wrap gap-2 mb-1">
                    <span className={`text-xs font-mono ${isSingletons ? 'text-slate-300' : 'text-teal-300'}`}>
                      {c.id}
                    </span>
                    {!isSingletons && c.dataQuality === 'low' && c.dataQualityReason === 'empty_features' ? (
                      <span className="text-xs text-amber-200 font-semibold italic">
                        Unclassifiable — inventory extraction failed for most members
                      </span>
                    ) : (
                      !isSingletons && c.llm?.label && (
                        <span className="text-xs text-white font-semibold">{c.llm.label}</span>
                      )
                    )}
                    {!isSingletons && c.dataQuality !== 'low' && c.llm?.action && (
                      <span className={`text-xs font-bold px-2 py-0.5 rounded border ${actionCls}`}>
                        {c.llm.action}
                      </span>
                    )}
                    {!isSingletons && c.dataQuality !== 'low' && c.llm?.confidence && (
                      <span className="text-xs text-gray-500">conf: {c.llm.confidence}</span>
                    )}
                    {!isSingletons && <DataQualityBadge cluster={c} />}
                  </div>
                  <div className="text-sm text-gray-200 truncate flex items-center gap-2">
                    {!isSingletons && (
                      <span
                        className="text-[9px] font-bold text-amber-300 bg-amber-950/40 border border-amber-700/50 rounded px-1.5 py-0.5 flex-shrink-0"
                        title="Keep-candidate — the primary report of this cluster"
                      >
                        ⭐ PRIMARY
                      </span>
                    )}
                    {!isSingletons && (
                      <FamilySiblingsIndicator siblings={reportById.get(c.primaryReportId)?.familySiblings} />
                    )}
                    <span className="truncate" title={c.primaryReportId}>
                      {c.dataQuality === 'low' && c.dataQualityReason === 'empty_features'
                        ? <span className="italic text-gray-500">(primary name "{c.primaryName}" not meaningful — picked arbitrarily from reports with no metadata)</span>
                        : c.primaryName}
                    </span>
                  </div>
                  {!isSingletons && (
                    <div className="text-xs text-gray-500 mt-1">
                      {c.totalExecutions.toLocaleString()} execs ·{' '}
                      avg cosine {c.avgCosine != null ? c.avgCosine.toFixed(3) : '—'} ·{' '}
                      min {c.minCosine != null ? c.minCosine.toFixed(3) : '—'}
                      {c.llm?.removableCount ? (
                        <span className="text-green-400 ml-2">· −{c.llm.removableCount} removable</span>
                      ) : null}
                    </div>
                  )}
                  {isSingletons && (
                    <div className="text-xs text-slate-400 mt-1">
                      Post-AST reports with no semantic pair ≥ threshold — click to browse & compare
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <span
                    className={`text-xs font-semibold px-2 py-0.5 rounded border ${sqlBadge}`}
                    title="Members with extracted SQL"
                  >
                    SQL: {sqlN}/{c.size}
                  </span>
                  <div
                    className={`text-white text-xs font-bold px-3 py-1 rounded-full ${
                      isSingletons ? 'bg-slate-600' : 'bg-teal-600'
                    }`}
                  >
                    {c.size} reports
                  </div>
                  <VscChevronRight className="text-gray-500" />
                </div>
              </div>
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div className="text-center text-sm text-gray-500 py-10">
            No clusters match the current filter.
          </div>
        )}
      </div>
    </div>
  );
}

function SemanticClusterModal({
  cluster,
  reportById,
  projectId,
  onClose,
  onCompare,
  retained,
  retiredOverrides,
  onToggleRetain,
  onToggleRetire,
}: {
  cluster: SemanticCluster;
  reportById: Map<string, ReportDetail>;
  projectId: string;
  onClose: () => void;
  onCompare: (a: ReportDetail, b: ReportDetail) => void;
  // Shared state + shared toggle handlers owned by the parent. Using the
  // parent's handlers means the Final Reports count updates the moment a
  // user flips disposition here, without any re-fetch dance.
  retained: Set<string>;
  retiredOverrides: Set<string>;
  onToggleRetain: (rid: string) => void | Promise<void>;
  onToggleRetire: (rid: string) => void | Promise<void>;
}) {
  // projectId isn't used directly now (toggles go through parent handlers),
  // kept in the signature so existing call sites don't break.
  void projectId;
  const [selectedForCompare, setSelectedForCompare] = useState<string[]>([]);

  // Derived: which members GPT marked removable. Falls back to action-based
  // defaults when the LLM didn't supply per-report IDs (older experiments or
  // big clusters where the prompt only saw the first 25 members).
  const removableSet = useMemo(() => {
    const llm = cluster.llm;
    if (!llm || llm.error) return null;
    const explicit = llm.removableIds || [];
    if (explicit.length > 0) return new Set(explicit);
    // No explicit IDs — infer from action. Primary is never removable.
    const action = (llm.action || '').toUpperCase();
    const inferAll = action === 'MERGE_IMMEDIATE' || action === 'PARAMETERIZE';
    if (!inferAll) return new Set<string>();
    const out = new Set<string>();
    for (const m of cluster.memberIds || []) {
      if (m !== cluster.primaryReportId) out.add(m);
    }
    return out;
  }, [cluster]);

  // Thin wrappers — delegate to the parent's shared handlers.
  const handleToggleRetain = (rid: string) => onToggleRetain(rid);
  const handleToggleRetire = (rid: string) => onToggleRetire(rid);

  // Compute the effective disposition for a given report.
  // Precedence: primary ⇒ retain (locked); user-retained ⇒ retain;
  // user-retired ⇒ retire; else LLM verdict (removable ⇒ retire).
  function effectiveDisposition(rid: string, isPrimary: boolean): 'retain' | 'retire' {
    if (isPrimary) return 'retain';
    if (retained.has(rid)) return 'retain';
    if (retiredOverrides.has(rid)) return 'retire';
    const llmRemovable = removableSet?.has(rid) ?? false;
    return llmRemovable ? 'retire' : 'retain';
  }
  const members = useMemo(() => {
    const enriched = cluster.members
      .map((m) => ({ ...m, report: reportById.get(m.id) }))
      .filter((m) => m.report);
    // Pin the primary first; sort the rest by cosineToPrimary desc (falls
    // back to executions if cosine isn't available, e.g. for promoted
    // Playground experiments).
    const primary = enriched.find((m) => m.id === cluster.primaryReportId);
    const rest = enriched
      .filter((m) => m.id !== cluster.primaryReportId)
      .sort((a, b) => {
        const ca = a.cosineToPrimary, cb = b.cosineToPrimary;
        if (ca != null && cb != null) return cb - ca;
        return (b.report?.executions ?? 0) - (a.report?.executions ?? 0);
      });
    return primary ? [primary, ...rest] : rest;
  }, [cluster, reportById]);
  const sqlCount = useMemo(
    () => members.filter((m) => !!m.report?.sql).length,
    [members]
  );

  function toggle(id: string) {
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

  const llm = cluster.llm;
  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-[#16213e] rounded-lg max-w-5xl w-full max-h-[92vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-5 border-b border-[#1e2d50] flex justify-between items-start">
          <div className="min-w-0 flex-1">
            <div className="text-xs text-gray-500 font-mono mb-1">{cluster.id}</div>
            <h2 className="text-xl font-bold text-white truncate">
              {llm?.label || cluster.primaryName}
            </h2>
            {llm?.label && (
              <div className="text-sm text-gray-400 truncate">Primary: {cluster.primaryName}</div>
            )}
            <div className="text-xs text-gray-400 mt-2 flex items-center gap-3 flex-wrap">
              <span>
                {cluster.size} reports · {cluster.totalExecutions.toLocaleString()} execs
              </span>
              <span>
                avg cosine {cluster.avgCosine != null ? cluster.avgCosine.toFixed(3) : '—'} ·{' '}
                min {cluster.minCosine != null ? cluster.minCosine.toFixed(3) : '—'}
              </span>
              <span
                className={`text-xs px-2 py-0.5 rounded font-semibold ${
                  sqlCount === members.length
                    ? 'bg-emerald-900/50 text-emerald-300 border border-emerald-700/50'
                    : sqlCount === 0
                    ? 'bg-gray-800/60 text-gray-400 border border-gray-700/50'
                    : 'bg-amber-900/40 text-amber-300 border border-amber-700/50'
                }`}
              >
                SQL: {sqlCount}/{members.length}
              </span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white text-2xl leading-none flex-shrink-0 ml-3"
          >
            ×
          </button>
        </div>

        {/* LLM panel */}
        {llm && !llm.error && (
          <div className="p-5 bg-gradient-to-r from-teal-950/40 to-[#16213e] border-b border-[#1e2d50] space-y-2 text-sm">
            <div className="flex gap-2 flex-wrap items-center">
              {llm.action && (
                <span
                  className={`text-xs font-bold px-2 py-1 rounded border ${
                    SEMANTIC_ACTION_COLORS[llm.action] ||
                    'bg-slate-800 text-slate-300 border-slate-700'
                  }`}
                >
                  {llm.action}
                </span>
              )}
              {llm.relationship && (
                <span className="text-xs bg-[#0f0f1a] text-teal-200 px-2 py-1 rounded border border-teal-800/60">
                  {llm.relationship}
                </span>
              )}
              {llm.confidence && (
                <span className="text-xs text-gray-500">Confidence: {llm.confidence}</span>
              )}
              {llm.removableCount > 0 && (
                <span className="text-xs bg-green-900/40 text-green-200 px-2 py-1 rounded border border-green-800/60">
                  −{llm.removableCount} removable
                </span>
              )}
            </div>
            {llm.businessFunction && (
              <div className="text-sm text-gray-300">
                <span className="text-gray-400 font-semibold">Business function:</span>{' '}
                {llm.businessFunction}
              </div>
            )}
            {llm.detail && (
              <div className="text-sm text-gray-300">
                <span className="text-gray-400 font-semibold">Recommendation:</span> {llm.detail}
              </div>
            )}
            {llm.keepReport && (
              <div className="text-sm text-gray-300">
                <span className="text-gray-400 font-semibold">Keep:</span> {llm.keepReport}
              </div>
            )}
          </div>
        )}
        {llm?.error && (
          <div className="p-4 bg-red-950/30 border-b border-red-900/40 text-xs text-red-300">
            LLM review failed: {llm.error}
          </div>
        )}

        <div className="p-5 overflow-y-auto flex-1">
          {selectedForCompare.length === 2 && (
            <button
              onClick={handleCompareClick}
              className="mb-3 w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 rounded-lg"
            >
              Compare Selected Reports →
            </button>
          )}
          <div className="bg-[#0f0f1a] p-3 rounded mb-3 text-xs text-gray-400 flex items-center gap-2">
            <VscDiff className="text-teal-400" />
            Select up to 2 reports to compare side-by-side
          </div>
          <table className="w-full text-sm">
            <thead className="bg-[#0f0f1a]">
              <tr>
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-8"></th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-24">Role</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Report Name</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-16">Source</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase w-24">Cosine</th>
                <th className="text-center p-2 text-xs text-gray-400 uppercase w-16">SQL</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-center p-2 text-xs text-gray-400 uppercase w-28" title="Click to toggle. Retain = included in Final Reports to Keep. Retire = marked as removable (consolidated into the primary). Primary is always retained.">
                  Disposition
                </th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => {
                const r = m.report!;
                const selected = selectedForCompare.includes(r.id);
                const hasSql = !!r.sql;
                const isPrimary = m.id === cluster.primaryReportId;
                return (
                  <tr
                    key={r.id}
                    onClick={() => toggle(r.id)}
                    className={`border-t border-[#1e2d50] cursor-pointer ${
                      selected ? 'bg-teal-900/30'
                        : isPrimary ? 'bg-amber-950/30 hover:bg-amber-900/30'
                        : 'hover:bg-[#1a2a4a]'
                    }`}
                  >
                    <td className="p-2">
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => {}}
                        className="accent-teal-500"
                      />
                    </td>
                    <td className="p-2">
                      <div className="flex items-center gap-1 flex-wrap">
                        {isPrimary ? (
                          <span className="text-[10px] font-bold text-amber-300 bg-amber-950/50 border border-amber-700/60 rounded px-1.5 py-0.5 whitespace-nowrap"
                            title="Keep-candidate — the primary report of this cluster">
                            ⭐ PRIMARY
                          </span>
                        ) : (
                          <span className="text-[10px] text-gray-500">member</span>
                        )}
                        <FamilySiblingsIndicator siblings={r.familySiblings} />
                      </div>
                    </td>
                    <td className={`p-2 ${isPrimary ? 'text-amber-100 font-semibold' : 'text-white'}`}>{r.name}</td>
                    <td className="p-2">
                      <div className="flex items-center gap-1 flex-wrap">
                        <SourceProjectPill sourceProjectId={r.sourceProjectId} />
                        <SourceTypePill sourceType={r.sourceType} />
                      </div>
                    </td>
                    <td className="p-2 text-right font-mono text-teal-300">
                      {isPrimary ? '—' : (m.cosineToPrimary != null ? m.cosineToPrimary.toFixed(3) : '—')}
                    </td>
                    <td className="p-2 text-center">
                      {hasSql ? (
                        <span className="text-xs font-semibold text-emerald-300 bg-emerald-900/40 border border-emerald-700/50 px-2 py-0.5 rounded">
                          ✓
                        </span>
                      ) : (
                        <span className="text-xs font-semibold text-gray-500 bg-gray-800/60 border border-gray-700/50 px-2 py-0.5 rounded">
                          —
                        </span>
                      )}
                    </td>
                    <td className={`p-2 text-right font-mono ${isPrimary ? 'text-amber-200' : 'text-gray-300'}`}>
                      {r.executions.toLocaleString()}
                    </td>
                    <td className="p-2 text-right font-mono text-gray-300">{r.users}</td>
                    <td className="p-2 text-center" onClick={(e) => e.stopPropagation()}>
                      {(() => {
                        const disp = effectiveDisposition(r.id, isPrimary);
                        const kept = disp === 'retain';
                        const userRetained = retained.has(r.id);
                        const userRetired = retiredOverrides.has(r.id);
                        const handleClick = () => {
                          if (isPrimary) return;
                          // Flip the effective disposition. If currently
                          // retained → mark retire; if retired → mark retain.
                          if (kept) handleToggleRetire(r.id);
                          else handleToggleRetain(r.id);
                        };
                        const badge = userRetained ? ' (user)' : userRetired ? ' (user)' : '';
                        return (
                          <button
                            type="button"
                            onClick={handleClick}
                            disabled={isPrimary}
                            className={`text-xs px-2 py-0.5 rounded border font-semibold transition-colors w-28 ${
                              kept
                                ? 'bg-emerald-900/50 border-emerald-600 text-emerald-200 hover:bg-emerald-800/60'
                                : 'bg-red-950/40 border-red-700/50 text-red-300 hover:bg-red-900/50'
                            } ${isPrimary ? 'cursor-default opacity-80' : ''}`}
                            title={
                              isPrimary ? 'Primary — always retained'
                                : kept
                                  ? (userRetained
                                      ? 'You explicitly retained this. Click to flip to Retire.'
                                      : 'LLM says keep. Click to flip to Retire.')
                                  : (userRetired
                                      ? 'You explicitly retired this. Click to flip to Retain.'
                                      : 'LLM says remove. Click to flip to Retain.')
                            }
                          >
                            {kept ? '✓ Retain' : '✗ Retire'}{badge}
                          </button>
                        );
                      })()}
                    </td>
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

// ─────────────────────────────────────────────────────────────────────
// REPORTS TAB
// ─────────────────────────────────────────────────────────────────────

function ReportsTab({
  reports,
  totalCount,
  allReports,
  finalKeptCount,
  search,
  setSearch,
  sort,
  setSort,
  pathFilter,
  setPathFilter,
  sourceFilter,
  setSourceFilter,
  scope,
  setScope,
  clusters,
  onOpenCluster,
  onOpenReport,
  projectId,
  projectName,
  retained,
  retiredOverrides,
  onToggleRetain,
  onToggleRetire,
  minUsers,
  setMinUsers,
  minExecs,
  setMinExecs,
}: {
  reports: ReportDetail[];
  totalCount: number;
  allReports: ReportDetail[];
  finalKeptCount: number;
  search: string;
  setSearch: (v: string) => void;
  sort: 'executions' | 'name' | 'users';
  setSort: (v: 'executions' | 'name' | 'users') => void;
  pathFilter: 'all' | 'public' | 'personal';
  setPathFilter: (v: 'all' | 'public' | 'personal') => void;
  sourceFilter: 'all' | 'normal' | 'cube' | 'custom_sql_free_form' | 'none';
  setSourceFilter: (v: 'all' | 'normal' | 'cube' | 'custom_sql_free_form' | 'none') => void;
  scope: 'final' | 'all';
  setScope: (v: 'final' | 'all') => void;
  clusters: ClusterMeta[];
  onOpenCluster: (c: ClusterMeta) => void;
  onOpenReport: (r: ReportDetail) => void;
  projectId: string;
  projectName: string;
  retained: Set<string>;
  retiredOverrides: Set<string>;
  onToggleRetain: (rid: string) => void | Promise<void>;
  onToggleRetire: (rid: string) => void | Promise<void>;
  minUsers: string;
  setMinUsers: (v: string) => void;
  minExecs: string;
  setMinExecs: (v: string) => void;
}) {
  const [chatOpen, setChatOpen] = useState(false);
  // Excel-export download state — the build takes ~10-30s on large projects;
  // we disable the button + show progress so the user knows it's working.
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportInfo, setExportInfo] = useState<{ rows: number; cols: number; filename: string } | null>(null);
  async function handleExport() {
    if (exporting) return;
    if (!projectName) { setExportError('No project selected'); return; }
    const short = PROJECT_NAME_TO_SHORT[projectName];
    if (!short) { setExportError(`Unknown project: ${projectName}`); return; }
    setExporting(true);
    setExportError(null);
    setExportInfo(null);
    try {
      const info = await downloadRationalizationExport(short);
      setExportInfo(info);
    } catch (e: unknown) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  }
  // Domain classification map — rid -> domain name. Fetched from the
  // Classify Domain run for this project; empty when nothing's been
  // classified, in which case grouping falls back to a single "All" bucket.
  const [domainByReportId, setDomainByReportId] = useState<Map<string, string>>(new Map());
  const [groupByDomain, setGroupByDomain] = useState<boolean>(true);
  const [expandedDomains, setExpandedDomains] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    fetchDomainResults(projectId).then((r) => {
      if (cancelled) return;
      const m = new Map<string, string>();
      for (const [domain, reps] of Object.entries(r.byDomain || {})) {
        for (const rep of reps) m.set(rep.id, domain);
      }
      setDomainByReportId(m);
      // Default: expand every non-empty domain the first time we load.
      setExpandedDomains((prev) => {
        if (Object.keys(prev).length > 0) return prev;
        const init: Record<string, boolean> = {};
        for (const d of Object.keys(r.byDomain || {})) init[d] = true;
        return init;
      });
    }).catch(() => { /* classification not run yet — empty map is fine */ });
    return () => { cancelled = true; };
  }, [projectId]);
  const clusterById = useMemo(() => {
    const map = new Map<string, ClusterMeta>();
    clusters.forEach((c) => map.set(c.id, c));
    return map;
  }, [clusters]);

  // Per-source counts (scope-aware) to drive the filter dropdown labels
  const scopeBase = scope === 'final'
    ? allReports.filter((r) => r.isFinalCanonical === true)
    : allReports;
  const sourceCounts = {
    all: scopeBase.length,
    normal: scopeBase.filter((r) => (r.sourceType || '') === 'normal').length,
    cube: scopeBase.filter((r) => (r.sourceType || '') === 'cube').length,
    custom_sql_free_form: scopeBase.filter((r) => (r.sourceType || '') === 'custom_sql_free_form').length,
    none: scopeBase.filter((r) => (r.sourceType || '').trim() === '').length,
  };

  const title = scope === 'final' ? 'Final Reports to Keep' : 'All Reports (full inventory)';
  // Any active filter (search, source-type, path, min-users, min-execs) — when
  // on, we want the count to reflect the narrowed list.
  const filtersActive =
    search.trim() !== ''
    || sourceFilter !== 'all'
    || pathFilter !== 'all'
    || minUsers !== ''
    || minExecs !== '';
  return (
    <div className="bg-[#16213e] p-6 rounded-lg relative">
      <div className="flex items-start justify-between">
        <h2 className="text-lg font-bold text-white mb-1">
          {title}{' '}
          <span className="text-sm text-gray-400 font-normal">
            (<span className="font-bold text-emerald-400">{reports.length.toLocaleString()}</span>
            {filtersActive ? (
              <>
                {' filtered · '}
                <span className="text-gray-500">
                  {totalCount.toLocaleString()} total in scope
                </span>
              </>
            ) : (
              ' total'
            )})
          </span>
        </h2>
        <div className="flex items-center gap-2">
          <button
            onClick={handleExport}
            disabled={exporting}
            className={`flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg border transition-colors ${
              exporting
                ? 'bg-emerald-900/40 text-emerald-300 border-emerald-700 cursor-wait'
                : 'bg-emerald-600 hover:bg-emerald-500 text-white border-emerald-500'
            }`}
            title="Download an Excel passport showing every report's per-stage outcome, who survived it, LLM verdict, and final disposition. Builds on the sidecar (~10-30s)."
          >
            {exporting ? (
              <>
                <span className="inline-block w-3 h-3 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin" />
                Building xlsx…
              </>
            ) : (
              <>📄 Download Excel</>
            )}
          </button>
          <button
            onClick={() => setChatOpen((v) => !v)}
            className={`flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg border transition-colors ${
              chatOpen
                ? 'bg-fuchsia-900/50 text-fuchsia-200 border-fuchsia-600'
                : 'bg-fuchsia-600 hover:bg-fuchsia-500 text-white border-fuchsia-500'
            }`}
            title="Ask questions about this project's rationalization data"
          >
            🤖 {chatOpen ? 'Hide' : 'Ask'} AI Assistant
          </button>
        </div>
      </div>
      {(exportError || exportInfo) && (
        <div className="mb-2 text-xs">
          {exportError && (
            <div className="text-red-300 bg-red-950/40 border border-red-800/50 rounded px-3 py-1.5">
              Export failed: {exportError}
            </div>
          )}
          {exportInfo && !exportError && (
            <div className="text-emerald-300 bg-emerald-950/40 border border-emerald-800/50 rounded px-3 py-1.5">
              ✓ Downloaded {exportInfo.filename} · {exportInfo.rows.toLocaleString()} rows × {exportInfo.cols} columns
            </div>
          )}
        </div>
      )}
      <p className="text-xs text-gray-500 mb-4">
        {scope === 'final'
          ? `Canonical set after every pipeline stage (${finalKeptCount.toLocaleString()} reports): similarity-cluster primaries + post-AST singletons. Click any row for details.`
          : `Full inventory across all statuses — active, retired, collapsed, dossier. Use this scope to audit reports that didn't make it to the final-kept set (e.g. all cube-sourced, retired, collapsed).`}
      </p>

      {/* Scope toggle — final-kept vs full inventory */}
      <div className="flex gap-2 mb-3 flex-wrap items-center">
        <button
          onClick={() => setScope('final')}
          className={`text-xs px-3 py-1.5 rounded border ${
            scope === 'final'
              ? 'bg-emerald-900/50 text-emerald-300 border-emerald-700 ring-1 ring-emerald-500/40'
              : 'bg-[#0f0f1a] text-gray-400 border-[#1e2d50] hover:bg-[#1a2a4a]'
          }`}
        >
          Final-kept only ({finalKeptCount.toLocaleString()})
        </button>
        <button
          onClick={() => setScope('all')}
          className={`text-xs px-3 py-1.5 rounded border ${
            scope === 'all'
              ? 'bg-blue-900/50 text-blue-300 border-blue-700 ring-1 ring-blue-500/40'
              : 'bg-[#0f0f1a] text-gray-400 border-[#1e2d50] hover:bg-[#1a2a4a]'
          }`}
        >
          All reports ({allReports.length.toLocaleString()})
        </button>
        {scope === 'final' && (
          <label
            className={`flex items-center gap-1.5 text-xs ml-2 cursor-pointer ${
              domainByReportId.size === 0 ? 'opacity-50 cursor-not-allowed' : 'text-gray-300 hover:text-white'
            }`}
            title={
              domainByReportId.size === 0
                ? 'Run Classify Domain on this project first to enable domain grouping.'
                : 'Group the list below by business domain.'
            }
          >
            <input
              type="checkbox"
              checked={groupByDomain}
              disabled={domainByReportId.size === 0}
              onChange={(e) => setGroupByDomain(e.target.checked)}
            />
            Group by domain
          </label>
        )}
      </div>

      <div className="flex gap-3 mb-4 flex-wrap">
        <div className="flex-1 relative min-w-[240px]">
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
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value as typeof sourceFilter)}
          className="bg-[#0f0f1a] border border-[#1e2d50] rounded-lg px-3 py-2.5 text-white text-sm focus:outline-none"
          title="Filter by report source type"
        >
          <option value="all">Source: All ({sourceCounts.all.toLocaleString()})</option>
          <option value="normal">Source: SQL ({sourceCounts.normal.toLocaleString()})</option>
          <option value="cube">Source: Cube ({sourceCounts.cube.toLocaleString()})</option>
          <option value="custom_sql_free_form">Source: Free SQL ({sourceCounts.custom_sql_free_form.toLocaleString()})</option>
          <option value="none">Source: Unknown ({sourceCounts.none.toLocaleString()})</option>
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
        <label className="flex items-center gap-1.5 bg-[#0f0f1a] border border-[#1e2d50] rounded-lg px-3 py-2.5 text-sm text-gray-300">
          <span className="text-xs text-gray-400 whitespace-nowrap">Users greater than:</span>
          <input
            type="number"
            min={0}
            value={minUsers}
            onChange={(e) => setMinUsers(e.target.value)}
            placeholder="—"
            className="bg-transparent w-16 text-white text-sm focus:outline-none"
          />
          {minUsers !== '' && (
            <button
              type="button"
              onClick={() => setMinUsers('')}
              className="text-gray-500 hover:text-red-300 text-xs"
              title="Clear"
            >
              ✕
            </button>
          )}
        </label>
        <label className="flex items-center gap-1.5 bg-[#0f0f1a] border border-[#1e2d50] rounded-lg px-3 py-2.5 text-sm text-gray-300">
          <span className="text-xs text-gray-400 whitespace-nowrap">Executions greater than:</span>
          <input
            type="number"
            min={0}
            value={minExecs}
            onChange={(e) => setMinExecs(e.target.value)}
            placeholder="—"
            className="bg-transparent w-20 text-white text-sm focus:outline-none"
          />
          {minExecs !== '' && (
            <button
              type="button"
              onClick={() => setMinExecs('')}
              className="text-gray-500 hover:text-red-300 text-xs"
              title="Clear"
            >
              ✕
            </button>
          )}
        </label>
      </div>

      <div className="max-h-[70vh] overflow-y-auto border border-[#1e2d50] rounded-lg">
        <table className="w-full text-sm">
          <thead className="bg-[#0f0f1a] sticky top-0">
            <tr>
              <th className="text-left p-3 text-xs text-gray-400 uppercase">Report Name</th>
              <th className="text-left p-3 text-xs text-gray-400 uppercase w-16">Source</th>
              {scope === 'all' && (
                <th className="text-left p-3 text-xs text-gray-400 uppercase w-20">Status</th>
              )}
              <th className="text-left p-3 text-xs text-gray-400 uppercase">Owner</th>
              <th className="text-right p-3 text-xs text-gray-400 uppercase">Execs</th>
              <th className="text-right p-3 text-xs text-gray-400 uppercase">Users</th>
              <th className="text-right p-3 text-xs text-gray-400 uppercase" title="Attributes / Metrics / Tables / Filters">A / M / T / F</th>
              <th className="text-center p-3 text-xs text-gray-400 uppercase w-28" title="Click to flip between Retain and Retire. Primary = locked Retain. Retiring a report in Final-kept scope makes it drop out of this list.">
                Disposition
              </th>
              <th className="text-left p-3 text-xs text-gray-400 uppercase">Role</th>
            </tr>
          </thead>
          {(() => {
            // Render one row — shared between flat + grouped modes.
            const renderRow = (r: ReportDetail) => {
              const cluster = r.clusterId ? clusterById.get(r.clusterId) : null;
              const isPrimary = cluster?.primaryReportId === r.id;
              const status = r.status || 'active';
              const statusPill = {
                active: 'bg-emerald-900/40 text-emerald-300 border-emerald-700/50',
                retired: 'bg-red-900/40 text-red-200 border-red-700/50',
                collapsed: 'bg-amber-900/40 text-amber-200 border-amber-700/50',
                dossier: 'bg-purple-900/40 text-purple-200 border-purple-700/50',
              }[status] || 'bg-gray-800/60 text-gray-300 border-gray-700/50';
              return (
                <tr
                  key={r.id}
                  onClick={() => onOpenReport(r)}
                  className="border-t border-[#1e2d50] hover:bg-[#1a2a4a] text-gray-300 cursor-pointer"
                >
                  <td className="p-3 text-white">{r.name}</td>
                  <td className="p-3">
                    <div className="flex items-center gap-1 flex-wrap">
                      <SourceProjectPill sourceProjectId={r.sourceProjectId} />
                      <SourceTypePill sourceType={r.sourceType} />
                    </div>
                  </td>
                  {scope === 'all' && (
                    <td className="p-3">
                      <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${statusPill}`}>
                        {status}
                      </span>
                    </td>
                  )}
                  <td className="p-3 text-xs">{r.owner || '-'}</td>
                  <td className="p-3 text-right font-mono">{r.executions.toLocaleString()}</td>
                  <td className="p-3 text-right font-mono">{r.users}</td>
                  <td className="p-3 text-right font-mono text-xs">
                    {r.attributeCount ?? 0}/{r.metricCount}/{r.tableCount}/{r.filterCount}
                  </td>
                  <td className="p-3 text-center" onClick={(e) => e.stopPropagation()}>
                    {(() => {
                      // Primary of a multi-member cluster is never retirable.
                      const isPrimaryOfMulti = !!(cluster && isPrimary && cluster.size > 1);
                      const userRetained = retained.has(r.id);
                      const userRetired = retiredOverrides.has(r.id);
                      // Effective disposition: primary + user_retained + isFinalCanonical win; user_retired retires.
                      const kept = isPrimaryOfMulti
                        || userRetained
                        || (r.isFinalCanonical === true && !userRetired);
                      const badge = userRetained ? ' (user)' : userRetired ? ' (user)' : '';
                      const handleClick = () => {
                        if (isPrimaryOfMulti) return;
                        if (kept) onToggleRetire(r.id);
                        else onToggleRetain(r.id);
                      };
                      return (
                        <button
                          type="button"
                          onClick={handleClick}
                          disabled={isPrimaryOfMulti}
                          className={`text-xs px-2 py-0.5 rounded border font-semibold transition-colors w-28 ${
                            kept
                              ? 'bg-emerald-900/50 border-emerald-600 text-emerald-200 hover:bg-emerald-800/60'
                              : 'bg-red-950/40 border-red-700/50 text-red-300 hover:bg-red-900/50'
                          } ${isPrimaryOfMulti ? 'cursor-default opacity-80' : ''}`}
                          title={
                            isPrimaryOfMulti ? 'Primary of a multi-member cluster — always retained'
                              : kept
                                ? (userRetained
                                    ? 'You explicitly retained this. Click to flip to Retire.'
                                    : 'Currently retained by the pipeline. Click to flip to Retire.')
                                : (userRetired
                                    ? 'You explicitly retired this. Click to flip to Retain.'
                                    : 'Click to flip to Retain.')
                          }
                        >
                          {kept ? '✓ Retain' : '✗ Retire'}{badge}
                        </button>
                      );
                    })()}
                  </td>
                  <td className="p-3">
                    {cluster && isPrimary ? (
                      <button
                        onClick={(e) => { e.stopPropagation(); onOpenCluster(cluster); }}
                        className="text-xs text-emerald-400 hover:underline"
                        title="Primary of this cluster — click to open"
                      >
                        ✓ Primary of {r.clusterId} ({cluster.size})
                      </button>
                    ) : cluster ? (
                      <button
                        onClick={(e) => { e.stopPropagation(); onOpenCluster(cluster); }}
                        className="text-xs text-amber-400 hover:underline"
                        title="Member of this cluster (not the primary — collapsed under another report)"
                      >
                        Member of {r.clusterId}
                      </button>
                    ) : r.isFinalCanonical ? (
                      <span className="text-xs text-emerald-400" title="Post-AST singleton that survived to final">
                        Singleton (kept)
                      </span>
                    ) : (
                      <span className="text-xs text-gray-500" title="Not in final-kept set — likely collapsed at an earlier pipeline stage">
                        —
                      </span>
                    )}
                  </td>
                </tr>
              );
            };

            // Flat mode — used when scope=all or grouping disabled.
            if (!(scope === 'final' && groupByDomain && domainByReportId.size > 0)) {
              return <tbody>{reports.map(renderRow)}</tbody>;
            }

            // Grouped mode — one tbody per business domain, following
            // CLASSIFY_DOMAIN_TAXONOMY order; anything not in the taxonomy
            // lands in "Unclassified" at the end.
            const taxonomy = CLASSIFY_DOMAIN_TAXONOMY;
            const buckets: Map<string, ReportDetail[]> = new Map(taxonomy.map((d) => [d, [] as ReportDetail[]]));
            for (const r of reports) {
              const d = domainByReportId.get(r.id) || 'Unclassified';
              if (!buckets.has(d)) buckets.set(d, []);
              buckets.get(d)!.push(r);
            }
            const colspan = scope === 'all' ? 9 : 8;
            const blocks: React.ReactNode[] = [];
            for (const [domain, list] of buckets.entries()) {
              if (list.length === 0) continue;
              const c = DOMAIN_COLORS[domain] || DOMAIN_COLORS['Unclassified'];
              const expanded = expandedDomains[domain] ?? true;
              blocks.push(
                <tbody key={`hd-${domain}`}>
                  <tr
                    onClick={() =>
                      setExpandedDomains((p) => ({ ...p, [domain]: !expanded }))
                    }
                    className={`cursor-pointer border-t border-[#1e2d50] ${c.bg} hover:bg-opacity-70`}
                  >
                    <td colSpan={colspan} className="p-2">
                      <div className="flex items-center gap-2">
                        <VscChevronRight
                          className={`text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`}
                        />
                        <span className={`text-xs font-bold uppercase tracking-wider ${c.text}`}>
                          {domain}
                        </span>
                        <span className="text-xs text-gray-400">
                          · {list.length.toLocaleString()} {list.length === 1 ? 'report' : 'reports'}
                          {' · '}
                          {list.reduce((s, r) => s + (r.executions || 0), 0).toLocaleString()} execs
                        </span>
                      </div>
                    </td>
                  </tr>
                  {expanded && list.map(renderRow)}
                </tbody>,
              );
            }
            return <>{blocks}</>;
          })()}
        </table>
      </div>

      {/* Floating AI Assistant chat panel */}
      {chatOpen && (
        <AiAssistantChat
          projectId={projectId}
          projectName={projectName}
          onClose={() => setChatOpen(false)}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// AI ASSISTANT CHAT PANEL
// ─────────────────────────────────────────────────────────────────────

interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
  ts: number;
}

const SUGGESTED_QUESTIONS = [
  'How many reports survived the pipeline?',
  'Which reports got collapsed the most?',
  'Show me the biggest semantic cluster',
  'How many cube-sourced reports are kept?',
  'What are the top rationalization opportunities?',
  'Which families have the most duplicates?',
];

function AiAssistantChat({
  projectId,
  projectName,
  onClose,
}: {
  projectId: string;
  projectName: string;
  onClose: () => void;
}) {
  // If projectName is empty (not yet loaded), don't persist to the 'default'
  // bucket — use a sentinel and clear when the real name arrives.
  const effectiveKey = projectName || projectId;
  const chatKey = effectiveKey ? `ai-assistant.history.${effectiveKey}` : 'ai-assistant.history._pending';
  const [history, setHistory] = usePersistentState<ChatTurn[]>(chatKey, []);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // If the user switches projects, the chatKey changes and this component
  // reads history from the new localStorage slot. Make that explicit.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [history, busy, chatKey]);

  async function send(text?: string) {
    const q = (text ?? input).trim();
    if (!q || busy) return;
    setError(null);
    const turn: ChatTurn = { role: 'user', content: q, ts: Date.now() };
    const nextHistory = [...history, turn];
    setHistory(nextHistory);
    setInput('');
    setBusy(true);
    try {
      const msgs = nextHistory.map((t) => ({ role: t.role, content: t.content }));
      // Use GUID or name — backend resolves either
      const project = projectName || projectId;
      const res = await askAssistant(project, msgs);
      setHistory([...nextHistory, { role: 'assistant', content: res.answer, ts: Date.now() }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function clearHistory() {
    if (history.length === 0 || window.confirm('Clear chat history?')) {
      setHistory([]);
      setError(null);
    }
  }

  return (
    <div className="fixed bottom-6 right-6 w-[480px] h-[620px] bg-[#16213e] border border-fuchsia-700 rounded-xl shadow-2xl flex flex-col z-40">
      {/* Header */}
      <div className="p-3 border-b border-fuchsia-800/60 bg-gradient-to-r from-fuchsia-950/60 to-[#16213e] rounded-t-xl flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xl">🤖</span>
          <div>
            <div className="text-sm text-white font-semibold">Rationalization Assistant</div>
            <div className="text-[10px] text-gray-400">Scoped to <span className="text-fuchsia-300">{projectName || '(no project)'}</span></div>
          </div>
        </div>
        <div className="flex gap-1">
          <button onClick={clearHistory} className="text-[10px] text-gray-400 hover:text-white px-2 py-1"
            title="Clear chat history">Clear</button>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-lg leading-none px-2">×</button>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3 text-sm">
        {history.length === 0 && (
          <div className="space-y-3">
            <div className="text-xs text-gray-400 leading-relaxed">
              Ask questions about the rationalization data for <strong className="text-white">{projectName}</strong>.
              The assistant sees summary stats, top clusters, final-kept reports, and source-type breakdowns.
            </div>
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Try asking</div>
            <div className="flex flex-col gap-1.5">
              {SUGGESTED_QUESTIONS.map((q) => (
                <button
                  key={q}
                  onClick={() => send(q)}
                  disabled={busy}
                  className="text-left text-xs text-fuchsia-300 hover:text-fuchsia-200 bg-[#0f0f1a] hover:bg-[#1a2a4a] border border-fuchsia-900/50 rounded px-3 py-2 disabled:opacity-50"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {history.map((t, i) => (
          <div key={i} className={`flex ${t.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap ${
              t.role === 'user'
                ? 'bg-fuchsia-900/50 text-fuchsia-50 border border-fuchsia-700/50'
                : 'bg-[#0f0f1a] text-gray-200 border border-[#1e2d50]'
            }`}>
              {t.content}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex justify-start">
            <div className="bg-[#0f0f1a] border border-[#1e2d50] rounded-lg px-3 py-2 text-xs text-gray-400 flex items-center gap-2">
              <span className="animate-spin inline-block w-3 h-3 border-2 border-fuchsia-500 border-t-transparent rounded-full"></span>
              thinking…
            </div>
          </div>
        )}
        {error && (
          <div className="bg-red-950/40 border border-red-700/50 text-red-200 text-xs p-2 rounded">
            <strong>Error:</strong> {error}
          </div>
        )}
      </div>

      {/* Input */}
      <div className="p-2 border-t border-[#1e2d50]">
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKey}
            disabled={busy}
            placeholder={busy ? 'Waiting for response…' : 'Ask about this project (Enter to send, Shift+Enter for newline)'}
            className="flex-1 bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-sm text-white resize-none focus:outline-none focus:border-fuchsia-500"
            rows={2}
          />
          <button
            onClick={() => send()}
            disabled={busy || !input.trim()}
            className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:bg-fuchsia-900 disabled:text-gray-500 disabled:cursor-not-allowed text-white text-sm font-semibold px-4 rounded"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// REPORT DETAIL MODAL  (click from "Final Reports to Keep")
// ─────────────────────────────────────────────────────────────────────

function ReportDetailModal({
  report,
  clusters,
  onClose,
  onOpenCluster,
}: {
  report: ReportDetail;
  clusters: ClusterMeta[];
  onClose: () => void;
  onOpenCluster: (c: ClusterMeta) => void;
}) {
  const cluster = useMemo(
    () => (report.clusterId ? clusters.find((c) => c.id === report.clusterId) || null : null),
    [report.clusterId, clusters]
  );
  const isPrimary = cluster?.primaryReportId === report.id;

  function fmtDate(s: string | null | undefined): string {
    if (!s) return '—';
    try {
      return new Date(s).toLocaleString();
    } catch {
      return s;
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-[#16213e] rounded-lg max-w-4xl w-full max-h-[92vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-5 border-b border-[#1e2d50] flex justify-between items-start">
          <div className="min-w-0 flex-1">
            <div className="text-xs text-gray-500 font-mono mb-1 break-all">{report.id}</div>
            <h2 className="text-xl font-bold text-white">{report.name}</h2>
            <div className="text-xs text-gray-400 mt-2 flex items-center gap-3 flex-wrap">
              {isPrimary && cluster && (
                <span className="text-xs font-semibold bg-emerald-900/50 text-emerald-300 border border-emerald-700/50 px-2 py-0.5 rounded">
                  Primary of cluster {cluster.id} ({cluster.size} members)
                </span>
              )}
              {!cluster && (
                <span className="text-xs font-semibold bg-slate-800/60 text-slate-300 border border-slate-600/50 px-2 py-0.5 rounded">
                  Post-AST singleton — no similarity pair ≥ 0.80
                </span>
              )}
              <span className="flex items-center gap-1.5" title="Report source type">
                <span className="text-gray-500 text-[10px]">SOURCE:</span>
                <SourceTypePill sourceType={report.sourceType} />
              </span>
              {report.sql ? (
                <span className="text-xs font-semibold bg-emerald-900/50 text-emerald-300 border border-emerald-700/50 px-2 py-0.5 rounded">
                  ✓ SQL extracted
                </span>
              ) : (
                <span className="text-xs font-semibold bg-gray-800/60 text-gray-400 border border-gray-700/50 px-2 py-0.5 rounded">
                  — no SQL
                </span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white text-2xl leading-none flex-shrink-0 ml-3"
          >
            ×
          </button>
        </div>

        <div className="p-5 overflow-y-auto flex-1 space-y-5">
          {/* Core attributes */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <div className="text-xs text-gray-400 uppercase mb-1">Owner</div>
              <div className="text-gray-200">{report.owner || '—'}</div>
            </div>
            <div>
              <div className="text-xs text-gray-400 uppercase mb-1">Match Tier</div>
              <div className="text-gray-200">{report.matchTier || '—'}</div>
            </div>
            <div className="col-span-2">
              <div className="text-xs text-gray-400 uppercase mb-1">Path</div>
              <div className="text-gray-200 text-xs font-mono break-all">{report.path || '—'}</div>
            </div>
            <div>
              <div className="text-xs text-gray-400 uppercase mb-1">Family Base</div>
              <div className="text-gray-200">{report.familyBase || '—'}</div>
            </div>
            <div>
              <div className="text-xs text-gray-400 uppercase mb-1">Cluster</div>
              {cluster ? (
                <button
                  onClick={() => onOpenCluster(cluster)}
                  className="text-blue-400 hover:underline text-sm"
                >
                  {cluster.id} — open ({cluster.size} members)
                </button>
              ) : (
                <span className="text-gray-500">Singleton</span>
              )}
            </div>
          </div>

          {/* Usage */}
          <div>
            <div className="text-xs text-gray-400 uppercase mb-2">Usage</div>
            <div className="grid grid-cols-3 gap-4">
              <div className="bg-[#0f0f1a] p-3 rounded border border-[#1e2d50]">
                <div className="text-xs text-gray-500">Executions</div>
                <div className="text-lg font-bold text-white">
                  {report.executions.toLocaleString()}
                </div>
              </div>
              <div className="bg-[#0f0f1a] p-3 rounded border border-[#1e2d50]">
                <div className="text-xs text-gray-500">Users</div>
                <div className="text-lg font-bold text-white">{report.users}</div>
              </div>
              <div className="bg-[#0f0f1a] p-3 rounded border border-[#1e2d50]">
                <div className="text-xs text-gray-500">Last Execution</div>
                <div className="text-sm text-gray-200 mt-1">{report.lastExec || '—'}</div>
              </div>
            </div>
          </div>

          {/* Lineage */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <div className="text-xs text-gray-400 uppercase mb-1">Created</div>
              <div className="text-gray-200">{fmtDate(report.dateCreated)}</div>
            </div>
            <div>
              <div className="text-xs text-gray-400 uppercase mb-1">Modified</div>
              <div className="text-gray-200">{fmtDate(report.dateModified)}</div>
            </div>
          </div>

          {/* Feature sets */}
          <div className="space-y-3">
            <FeatureBlock title="Attributes" items={report.attributes || []} color="bg-amber-900 text-amber-200" />
            <FeatureBlock title="Metrics" items={report.metrics} color="bg-blue-900 text-blue-200" />
            <FeatureBlock title="Tables" items={report.tables} color="bg-emerald-900 text-emerald-200" />
            <FeatureBlock title="Filter Attributes" items={report.filters} color="bg-purple-900 text-purple-200" />
          </div>

          {/* SQL */}
          <div>
            <div className="text-xs text-gray-400 uppercase mb-2 flex items-center justify-between">
              <span>Extracted SQL</span>
              {report.sql && (
                <span className="text-gray-500">{report.sql.length.toLocaleString()} chars</span>
              )}
            </div>
            {report.sql ? (
              <pre className="bg-[#0f0f1a] border border-[#1e2d50] rounded p-3 text-xs text-gray-300 font-mono overflow-auto max-h-[40vh]">
                {report.sql}
              </pre>
            ) : (
              <div className="bg-[#0f0f1a] border border-[#1e2d50] rounded p-3 text-xs text-gray-500">
                No SQL available. {report.sqlError ? `Error: ${report.sqlError}` : 'Likely a cube-sourced or prompted report.'}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// Compact pill renderer for report source_type. Values from MSTR inventory:
//   "normal"               → SQL (warehouse-sourced)
//   "cube"                 → Cube (Intelligent Cube feed)
//   "custom_sql_free_form" → Free SQL (custom SQL report)
//   ""                     → ? (source unknown / not captured)
const SOURCE_TYPE_LABEL: Record<string, { short: string; tip: string; cls: string }> = {
  normal: {
    short: 'SQL',
    tip: 'Warehouse-sourced — generates SQL against the underlying DB',
    cls: 'bg-blue-900/50 text-blue-200 border border-blue-700/50',
  },
  cube: {
    short: 'Cube',
    tip: 'Intelligent Cube feed — reads from a pre-aggregated in-memory cube, no DB SQL',
    cls: 'bg-purple-900/50 text-purple-200 border border-purple-700/50',
  },
  custom_sql_free_form: {
    short: 'Free SQL',
    tip: 'Free-form custom SQL report — hand-written query',
    cls: 'bg-amber-900/40 text-amber-200 border border-amber-700/50',
  },
};

// Plain-English label for cluster data quality. When >80% of members have
// no captured metadata, a "cluster" isn't really a cluster — it's a bucket
// of reports we know nothing about. Don't pretend it's a rationalization
// target; label it honestly so business users don't act on it.
function DataQualityBadge({ cluster }: {
  cluster: { dataQuality?: string | null; dataQualityReason?: string | null; emptyMemberCount?: number; size?: number };
}) {
  const q = cluster.dataQuality;
  const reason = cluster.dataQualityReason;
  const empty = cluster.emptyMemberCount ?? 0;
  const size = cluster.size ?? 0;
  if (!q || q === 'high') return null;
  const pct = size > 0 ? Math.round((empty / size) * 100) : 0;
  if (q === 'low' && reason === 'empty_features') {
    return (
      <span
        className="text-[10px] font-semibold px-2 py-0.5 rounded bg-amber-900/50 text-amber-200 border border-amber-700/50"
        title={`${empty} of ${size} members (${pct}%) have NO captured metadata — no metrics, no tables, no filters, no attributes. These reports were grouped because their feature sets are identical (empty). This is a data-quality issue, not a real similarity cluster — their inventory extraction likely failed.`}
      >
        ⚠ We know nothing about {pct}% of these reports
      </span>
    );
  }
  if (q === 'low' && reason === 'sparse_features') {
    return (
      <span
        className="text-[10px] font-semibold px-2 py-0.5 rounded bg-amber-900/40 text-amber-200 border border-amber-700/40"
        title={`${empty} of ${size} members (${pct}%) have fewer than 3 features captured. Cluster similarity is weak signal — treat with caution.`}
      >
        ⚠ Weak signal ({pct}% have &lt;3 features)
      </span>
    );
  }
  if (q === 'mixed') {
    return (
      <span
        className="text-[10px] font-semibold px-2 py-0.5 rounded bg-yellow-900/30 text-yellow-200 border border-yellow-700/40"
        title={`${empty} of ${size} members (${pct}%) have no captured metadata. The cluster mixes real and unknown reports.`}
      >
        ⚠ Mixed quality ({pct}% unknown)
      </span>
    );
  }
  return null;
}

// Origin pill for reports living in the Combined Reports Project.
// Shows [GO] / [GI] / [IN] color-coded. Renders nothing for non-combined
// rows (where sourceProjectId is null).
function SourceProjectPill({ sourceProjectId }: { sourceProjectId: string | null | undefined }) {
  if (!sourceProjectId) return null;
  const meta: Record<string, { short: string; cls: string; name: string }> = {
    'global-operational': {
      short: 'GO',
      cls: 'bg-blue-900/50 text-blue-200 border-blue-700/60',
      name: 'Global Operational',
    },
    'global-insight': {
      short: 'GI',
      cls: 'bg-purple-900/50 text-purple-200 border-purple-700/60',
      name: 'Global Insight',
    },
    'insight': {
      short: 'IN',
      cls: 'bg-emerald-900/50 text-emerald-200 border-emerald-700/60',
      name: 'INSIGHT',
    },
  };
  const m = meta[sourceProjectId];
  if (!m) return null;
  return (
    <span className={`text-[9px] font-bold border rounded px-1 py-0.5 ${m.cls} whitespace-nowrap`}
      title={`Source project: ${m.name}`}>
      [{m.short}]
    </span>
  );
}

function SourceTypePill({ sourceType }: { sourceType: string | null | undefined }) {
  const key = (sourceType || '').trim();
  const meta = SOURCE_TYPE_LABEL[key];
  if (!meta) {
    return (
      <span
        className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-gray-700/50 bg-gray-800/60 text-gray-500"
        title={key ? `source_type=${key}` : 'source type not captured'}
      >
        ?
      </span>
    );
  }
  return (
    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${meta.cls}`} title={meta.tip}>
      {meta.short}
    </span>
  );
}

// Compact indicator showing the collapsed family siblings behind a canonical.
// Clicks toggle a popover listing each sibling with its variant suffix +
// status + executions. Absent or zero-sibling reports render nothing.
// Compact details panel shown under the embedding-source dropdown in the
// Playground + Density tabs. Exposes exactly which fields went into the
// embedding text blob so the user understands what "similar" will mean
// for this run without having to flip to the Embeddings tab.
function EmbeddingSetDetails({
  set,
  trailer,
}: {
  set: EmbeddingSet;
  trailer?: string;
}) {
  // Human-friendly labels + a stable order so the pills read the same way
  // every time.
  const FIELD_LABELS: Array<[string, string]> = [
    ['name', 'Name'],
    ['path', 'Path'],
    ['owner', 'Owner'],
    ['attributes', 'Attributes'],
    ['metrics', 'Metrics'],
    ['tables', 'Tables'],
    ['filters', 'Filters'],
    ['sql', 'SQL'],
  ];
  const on = FIELD_LABELS.filter(([k]) => set.fields[k] === true);
  const off = FIELD_LABELS.filter(([k]) => set.fields[k] !== true);
  const normalize = set.fields['normalizeSql'] === true;
  const maxSql = Number(set.fields['maxSqlChars']) || null;
  return (
    <div className="text-[10px] mt-1.5 space-y-1">
      <div className="flex items-center gap-1.5 flex-wrap text-violet-200">
        <span className="text-violet-400 uppercase font-semibold">Scope:</span>
        <code className="bg-[#0f0f1a] px-1 rounded">{set.scope}</code>
        <span className="text-violet-400 uppercase font-semibold">·  Source:</span>
        <code className="bg-[#0f0f1a] px-1 rounded">{set.sourceFilter}</code>
        <span className="text-violet-400 uppercase font-semibold">·  Model:</span>
        <code className="bg-[#0f0f1a] px-1 rounded">{set.model.replace('text-embedding-3-', '3-')}/{set.dim}d</code>
        <span className="text-emerald-300 font-semibold">· $0 cache hit{trailer ? ` — ${trailer}` : ''}</span>
      </div>
      <div className="flex items-start gap-1.5 flex-wrap">
        <span className="text-violet-400 uppercase font-semibold shrink-0 mt-0.5">Fields embedded:</span>
        {on.length === 0 ? (
          <span className="italic text-gray-500">(none — embedding is just report IDs; clustering will be useless)</span>
        ) : (
          on.map(([k, label]) => (
            <span key={k} className="bg-emerald-900/40 border border-emerald-700/60 text-emerald-200 rounded px-1.5 py-0.5 whitespace-nowrap">
              ✓ {label}
            </span>
          ))
        )}
        {off.length > 0 && off.map(([k, label]) => (
          <span key={k} className="bg-[#0f0f1a] border border-[#1e2d50] text-gray-600 rounded px-1.5 py-0.5 whitespace-nowrap line-through">
            {label}
          </span>
        ))}
      </div>
      {set.fields['sql'] === true && (
        <div className="text-gray-500">
          SQL: {normalize ? 'normalized' : 'raw'}
          {maxSql ? `, first ${maxSql.toLocaleString()} chars` : ''}
        </div>
      )}
    </div>
  );
}

function FamilySiblingsIndicator({
  siblings,
}: {
  siblings: FamilyCollapsedSibling[] | undefined;
}) {
  const [open, setOpen] = useState(false);
  if (!siblings || siblings.length === 0) return null;
  const titleText = `This canonical stands for ${siblings.length} family-collapsed sibling${siblings.length === 1 ? '' : 's'} — dated/variant saves of the same report that were merged away at the Family stage.`;
  return (
    <span className="relative inline-block">
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        className="text-[9px] font-semibold text-indigo-200 bg-indigo-950/60 border border-indigo-700/60 hover:bg-indigo-900/60 rounded px-1.5 py-0.5 whitespace-nowrap cursor-pointer"
        title={titleText}
      >
        ⚑ +{siblings.length} sib{siblings.length === 1 ? '' : 's'}
      </button>
      {open && (
        <div
          className="absolute z-20 left-0 mt-1 w-96 max-h-72 overflow-y-auto bg-[#0f0f1a] border border-indigo-700/60 rounded shadow-lg p-2 text-[11px]"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="text-indigo-200 font-semibold mb-1">
            {siblings.length} family-collapsed sibling{siblings.length === 1 ? '' : 's'}:
          </div>
          <div className="text-[10px] text-gray-500 mb-1.5 leading-snug">
            These reports share the same family name as the canonical and were
            merged into it by the Family stage. They're not in Jaccard/Semantic
            clusters themselves — this canonical represents the whole group.
          </div>
          <table className="w-full">
            <thead>
              <tr className="text-[9px] text-gray-500 uppercase">
                <th className="text-left py-1">Variant</th>
                <th className="text-left py-1">Status</th>
                <th className="text-right py-1">Execs</th>
              </tr>
            </thead>
            <tbody>
              {siblings.map((s) => (
                <tr key={s.id} className="border-t border-[#1e2d50]">
                  <td className="py-0.5 text-gray-200 truncate max-w-[16rem]" title={s.name}>
                    {s.suffix || s.name}
                  </td>
                  <td className="py-0.5">
                    <span className={`text-[9px] font-semibold px-1 py-0.5 rounded ${
                      s.status === 'active' ? 'text-emerald-300 bg-emerald-900/30 border border-emerald-800/50' :
                      s.status === 'retired' ? 'text-gray-400 bg-gray-800/40 border border-gray-700/50' :
                      'text-amber-300 bg-amber-900/30 border border-amber-800/50'
                    }`}>
                      {s.status || '?'}
                    </span>
                  </td>
                  <td className="py-0.5 text-right font-mono text-gray-400">
                    {s.executions.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setOpen(false); }}
            className="mt-2 text-[10px] text-gray-500 hover:text-white">
            close
          </button>
        </div>
      )}
    </span>
  );
}

function FeatureBlock({
  title,
  items,
  color,
}: {
  title: string;
  items: string[];
  color: string;
}) {
  return (
    <div>
      <div className="text-xs text-gray-400 uppercase mb-2">
        {title} <span className="text-gray-600">({items.length})</span>
      </div>
      {items.length === 0 ? (
        <div className="text-xs text-gray-600">—</div>
      ) : (
        <div className="flex flex-wrap gap-1">
          {items.map((it, i) => (
            <span key={i} className={`text-xs px-2 py-0.5 rounded ${color}`}>
              {it}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// JACCARD vs SEMANTIC COMPARISON TAB
// ─────────────────────────────────────────────────────────────────────

const COMPARISON_CATEGORY_STYLES: Record<ClusterComparisonCategory, {
  label: string; desc: string; border: string; bg: string; text: string;
}> = {
  both: {
    label: 'In Both',
    desc: 'Methods agree — report clustered by both Jaccard & Semantic',
    border: 'border-emerald-500',
    bg: 'bg-emerald-900/30',
    text: 'text-emerald-300',
  },
  jaccardOnly: {
    label: 'Jaccard Only',
    desc: 'Jaccard found peers; Semantic treated it as a singleton',
    border: 'border-blue-500',
    bg: 'bg-blue-900/30',
    text: 'text-blue-300',
  },
  semanticOnly: {
    label: 'Semantic Only',
    desc: 'Semantic found peers (synonym drift); Jaccard missed — key added value of embeddings',
    border: 'border-teal-500',
    bg: 'bg-teal-900/30',
    text: 'text-teal-300',
  },
  neither: {
    label: 'Singleton in Both',
    desc: 'Neither method found peers — truly unique reports',
    border: 'border-slate-500',
    bg: 'bg-slate-800/60',
    text: 'text-slate-300',
  },
};

function ClusterComparisonTab({
  data,
  category,
  setCategory,
  search,
  setSearch,
}: {
  data: ClusterComparison | null;
  category: 'all' | ClusterComparisonCategory;
  setCategory: (v: 'all' | ClusterComparisonCategory) => void;
  search: string;
  setSearch: (v: string) => void;
}) {
  const filtered = useMemo(() => {
    if (!data) return [];
    let list = data.reports;
    if (category !== 'all') list = list.filter((r) => r.category === category);
    const q = search.toLowerCase().trim();
    if (q) {
      list = list.filter(
        (r) =>
          r.name.toLowerCase().includes(q) ||
          (r.owner && r.owner.toLowerCase().includes(q)) ||
          (r.semanticLabel && r.semanticLabel.toLowerCase().includes(q)) ||
          (r.jaccardClusterId && r.jaccardClusterId.toLowerCase().includes(q)) ||
          (r.semanticClusterId && r.semanticClusterId.toLowerCase().includes(q))
      );
    }
    return list.slice(0, 2000);
  }, [data, category, search]);

  if (!data) {
    return (
      <div className="bg-[#16213e] p-8 rounded-lg text-center space-y-3">
        <div className="text-4xl">⚖️</div>
        <h2 className="text-xl font-bold text-white">No comparison data yet</h2>
        <p className="text-sm text-gray-400 max-w-2xl mx-auto">
          Run both Jaccard and Semantic clustering, then re-emit JSON to populate this view.
        </p>
      </div>
    );
  }

  const s = data.stats;
  const categories: Array<ClusterComparisonCategory> = ['both', 'semanticOnly', 'jaccardOnly', 'neither'];

  return (
    <div className="space-y-4">
      {/* Intro */}
      <div className="bg-gradient-to-r from-indigo-950/60 to-[#16213e] p-4 rounded-lg border border-indigo-800/40">
        <div className="text-xs uppercase tracking-wider text-indigo-300 font-bold mb-2">
          Jaccard vs Semantic — Per-Report Comparison
        </div>
        <p className="text-xs text-gray-400 leading-relaxed">
          Both clustering methods run on the same{' '}
          <strong className="text-white">{data.postAstTotal.toLocaleString()}</strong> post-AST
          canonicals. This tab partitions each report by which method (if any) clustered it,
          so you can see where the two agree, where they disagree, and which reports are truly
          unique. "Semantic Only" highlights the synonym-drift cases that Jaccard's lexical
          overlap misses.
        </p>
      </div>

      {/* Summary boxes */}
      <div className="grid grid-cols-4 gap-3">
        {categories.map((c) => {
          const st = COMPARISON_CATEGORY_STYLES[c];
          const n = s[c];
          const selected = category === c;
          return (
            <button
              key={c}
              onClick={() => setCategory(selected ? 'all' : c)}
              className={`text-left p-4 rounded-lg border-l-4 ${st.border} ${selected ? st.bg + ' ring-2 ring-white/20' : 'bg-[#0f0f1a] hover:bg-[#1a2a4a]'} transition-colors`}
              title={st.desc}
            >
              <div className={`text-[10px] uppercase tracking-wider font-bold ${st.text}`}>
                {st.label}
              </div>
              <div className="text-2xl font-bold text-white mt-1">{n.toLocaleString()}</div>
              <div className="text-[10px] text-gray-500 mt-1">
                {((100 * n) / Math.max(1, data.postAstTotal)).toFixed(0)}% of post-AST
              </div>
              <div className="text-[10px] text-gray-500 mt-1 line-clamp-2">{st.desc}</div>
            </button>
          );
        })}
      </div>

      {/* Search + filter controls */}
      <div className="bg-[#16213e] p-4 rounded-lg">
        <div className="flex gap-3 items-center flex-wrap">
          <button
            onClick={() => setCategory('all')}
            className={`text-xs px-3 py-1 rounded border ${
              category === 'all'
                ? 'bg-blue-600 text-white border-blue-500'
                : 'bg-[#0f0f1a] text-gray-400 border-[#1e2d50]'
            }`}
          >
            All ({data.reports.length})
          </button>
          {categories.map((c) => {
            const st = COMPARISON_CATEGORY_STYLES[c];
            return (
              <button
                key={c}
                onClick={() => setCategory(category === c ? 'all' : c)}
                className={`text-xs px-3 py-1 rounded border ${
                  category === c
                    ? st.bg + ' ' + st.text + ' ' + st.border
                    : 'bg-[#0f0f1a] text-gray-400 border-[#1e2d50]'
                }`}
              >
                {st.label} ({s[c]})
              </button>
            );
          })}
        </div>
        <div className="relative mt-3">
          <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name, owner, cluster ID, or semantic label..."
            className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded-lg pl-10 pr-4 py-2.5 text-white text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      <div className="bg-[#16213e] rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-[#1e2d50] text-sm text-gray-400">
          <span className="font-semibold text-white">{filtered.length.toLocaleString()}</span>
          {filtered.length === 2000 ? ' shown (capped)' : ''} reports
        </div>
        <div className="max-h-[65vh] overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-[#0f0f1a] sticky top-0">
              <tr>
                <th className="text-left p-3 text-xs text-gray-400 uppercase">Report Name</th>
                <th className="text-right p-3 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-left p-3 text-xs text-gray-400 uppercase">Category</th>
                <th className="text-left p-3 text-xs text-gray-400 uppercase">Jaccard cluster</th>
                <th className="text-left p-3 text-xs text-gray-400 uppercase">Semantic cluster</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => {
                const st = COMPARISON_CATEGORY_STYLES[r.category];
                return (
                  <tr key={r.id} className="border-t border-[#1e2d50] text-gray-300 hover:bg-[#1a2a4a]">
                    <td className="p-3 text-white">
                      {r.name}
                      {r.owner && (
                        <div className="text-[11px] text-gray-500">{r.owner}</div>
                      )}
                    </td>
                    <td className="p-3 text-right font-mono text-gray-400">
                      {r.executions.toLocaleString()}
                    </td>
                    <td className="p-3">
                      <span className={`text-xs px-2 py-0.5 rounded border ${st.bg} ${st.text} ${st.border.replace('border-', 'border-')}`}>
                        {st.label}
                      </span>
                    </td>
                    <td className="p-3">
                      {r.jaccardClusterId ? (
                        <span className="text-xs font-mono text-blue-300">
                          {r.jaccardClusterId}
                          <span className="text-gray-500 ml-1">({r.jaccardClusterSize})</span>
                        </span>
                      ) : (
                        <span className="text-xs text-gray-600">—</span>
                      )}
                    </td>
                    <td className="p-3">
                      {r.semanticClusterId ? (
                        <div>
                          <span className="text-xs font-mono text-teal-300">
                            {r.semanticClusterId}
                            <span className="text-gray-500 ml-1">({r.semanticClusterSize})</span>
                          </span>
                          {r.semanticLabel && (
                            <div className="text-[11px] text-gray-400 mt-0.5 line-clamp-1">
                              {r.semanticLabel}
                            </div>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-gray-600">—</span>
                      )}
                    </td>
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

// ─────────────────────────────────────────────────────────────────────
// CLASSIFY DOMAIN TAB
// LLM classifies post-Jaccard reports into fixed business domains
// ─────────────────────────────────────────────────────────────────────

const CLASSIFY_DOMAIN_TAXONOMY = [
  'Sales - DTC/Ecom',
  'Sales - Retail',
  'Sales - Wholesale',
  'Inventory',
  'Sourcing (Purchase Order)',
  'Master Data',
  'Unclassified',
];

const DOMAIN_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  'Sales - DTC/Ecom':         { bg: 'bg-blue-900/20',    border: 'border-blue-700/50',    text: 'text-blue-300' },
  'Sales - Retail':           { bg: 'bg-emerald-900/20', border: 'border-emerald-700/50', text: 'text-emerald-300' },
  'Sales - Wholesale':        { bg: 'bg-cyan-900/20',    border: 'border-cyan-700/50',    text: 'text-cyan-300' },
  'Inventory':                { bg: 'bg-amber-900/20',   border: 'border-amber-700/50',   text: 'text-amber-300' },
  'Sourcing (Purchase Order)':{ bg: 'bg-violet-900/20',  border: 'border-violet-700/50',  text: 'text-violet-300' },
  'Master Data':              { bg: 'bg-pink-900/20',    border: 'border-pink-700/50',    text: 'text-pink-300' },
  'Unclassified':             { bg: 'bg-gray-800/40',    border: 'border-gray-600/50',    text: 'text-gray-400' },
};

function ClassifyDomainTab({ projectId }: { projectId: string }) {
  const [results, setResults] = useState<Awaited<ReturnType<typeof fetchDomainResults>> | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [expandedDomain, setExpandedDomain] = useState<string | null>(null);

  async function loadResults() {
    if (!projectId) return;
    setLoading(true);
    try {
      const r = await fetchDomainResults(projectId);
      setResults(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadResults();
  }, [projectId]);

  // Poll the task until it completes, then reload results.
  useEffect(() => {
    if (!activeTaskId) return;
    let cancelled = false;
    (async () => {
      while (!cancelled) {
        try {
          const t = await fetchPlaygroundTask(activeTaskId);
          setTaskStatus(`${t.status}${t.name ? ' · ' + t.name : ''}`);
          if (t.status === 'completed') {
            setRunning(false);
            setActiveTaskId(null);
            setTaskStatus('');
            await loadResults();
            return;
          }
          if (t.status === 'failed' || t.status === 'interrupted') {
            setRunning(false);
            setActiveTaskId(null);
            setError(t.error || `Task ${t.status}`);
            return;
          }
        } catch {
          // Transient — try again next tick
        }
        await new Promise((r) => setTimeout(r, 3000));
      }
    })();
    return () => { cancelled = true; };
  }, [activeTaskId]);

  async function runClassification() {
    if (!projectId || running) return;
    setError(null);
    setRunning(true);
    setTaskStatus('queueing…');
    try {
      const task = await runDomainClassification({
        id: `exp-classify-domain-${projectId}-post-family`,
        name: `Classify Domain · ${projectId}`,
        project: projectId,
        scope: 'post-family',   // after family collapse — includes all
                                // business-distinct reports, not just the
                                // Jaccard survivors.
        sourceFilter: 'all',
        model: 'gpt-5.4',
        taxonomy: CLASSIFY_DOMAIN_TAXONOMY,
      });
      if (task.status === 'failed' || task.status === 'interrupted') {
        setRunning(false);
        setError(task.error || `Task ${task.status}`);
        return;
      }
      setActiveTaskId(task.id);
      setTaskStatus(`${task.status} · ${task.name || task.id}`);
    } catch (e: unknown) {
      setRunning(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const domains = CLASSIFY_DOMAIN_TAXONOMY;
  const byDomain = results?.byDomain || {};
  const totalClassified = results?.totalClassified || 0;

  return (
    <div className="space-y-4">
      {/* Intro card */}
      <div className="bg-gradient-to-r from-violet-950/60 to-[#16213e] p-4 rounded-lg border border-violet-800/40">
        <div className="text-xs uppercase tracking-wider text-violet-300 font-bold mb-2">
          Classify Domain — Business Bucketing of Post-Family Reports
        </div>
        <p className="text-xs text-gray-400 leading-relaxed">
          Runs GPT-5.4 over every report in the <strong>post-family canonical set</strong>{' '}
          (scope: <code className="text-violet-300">post-family</code>) and assigns each one to one of
          seven fixed business domains. Uses 50-report batches with 4 parallel workers for speed
          (~8× faster than the single-stream path). Results are cached per (report, content-hash),
          so re-running an unchanged report is a near-instant no-op.
        </p>
      </div>

      {/* Taxonomy + run */}
      <div className="bg-[#16213e] p-4 rounded-lg border border-[#1e2d50]">
        <div className="flex items-center justify-between gap-4 mb-3">
          <div>
            <div className="text-sm font-semibold text-white mb-1">Domains</div>
            <div className="flex flex-wrap gap-1.5">
              {domains.map((d) => {
                const c = DOMAIN_COLORS[d] || DOMAIN_COLORS['Unclassified'];
                return (
                  <span
                    key={d}
                    className={`text-xs px-2 py-0.5 rounded border ${c.bg} ${c.border} ${c.text}`}
                  >
                    {d}
                  </span>
                );
              })}
            </div>
          </div>
          <button
            onClick={runClassification}
            disabled={running || !projectId}
            className={`px-4 py-2 rounded-lg text-sm font-semibold whitespace-nowrap ${
              running || !projectId
                ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
                : 'bg-violet-600 hover:bg-violet-500 text-white'
            }`}
          >
            {running ? 'Classifying…' : totalClassified > 0 ? 'Re-run Classification' : 'Run Classification'}
          </button>
        </div>
        {taskStatus && (
          <div className="text-xs text-gray-400 bg-[#0f0f1a] rounded px-3 py-2 font-mono mt-2">
            Task: {taskStatus}
          </div>
        )}
        {error && (
          <div className="text-xs text-red-300 bg-red-950/40 border border-red-800/50 rounded px-3 py-2 mt-2">
            {error}
          </div>
        )}
        <div className="text-[11px] text-gray-500 mt-2">
          {totalClassified > 0
            ? `${totalClassified.toLocaleString()} reports classified${results?.lastRunAt ? ' · last run ' + new Date(results.lastRunAt).toLocaleString() : ''}`
            : 'No classification run yet for this project.'}
        </div>
      </div>

      {/* Loading */}
      {loading && !results && (
        <div className="bg-[#16213e] p-8 rounded-lg text-center text-gray-400 text-sm">
          Loading classification results…
        </div>
      )}

      {/* Empty state */}
      {!loading && totalClassified === 0 && (
        <div className="bg-[#16213e] p-8 rounded-lg text-center space-y-3">
          <div className="text-4xl">🏷️</div>
          <h2 className="text-xl font-bold text-white">No classifications yet</h2>
          <p className="text-sm text-gray-400 max-w-xl mx-auto">
            Click <strong>Run Classification</strong> above to classify every post-family report
            into the seven domains shown. Uses the cached prompt-per-report, so subsequent runs
            are cheap.
          </p>
        </div>
      )}

      {/* Results */}
      {!loading && totalClassified > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {domains.map((d) => {
            const reports = byDomain[d] || [];
            const c = DOMAIN_COLORS[d] || DOMAIN_COLORS['Unclassified'];
            const expanded = expandedDomain === d;
            const totalExecs = reports.reduce((s, r) => s + (r.executions || 0), 0);
            return (
              <div
                key={d}
                className={`${c.bg} border ${c.border} rounded-lg overflow-hidden`}
              >
                <button
                  type="button"
                  onClick={() => setExpandedDomain(expanded ? null : d)}
                  className="w-full text-left p-3 flex items-center justify-between hover:bg-black/10 transition-colors"
                >
                  <div>
                    <div className={`text-sm font-semibold ${c.text}`}>{d}</div>
                    <div className="text-xs text-gray-400 mt-0.5">
                      {reports.length.toLocaleString()} reports · {totalExecs.toLocaleString()} execs
                    </div>
                  </div>
                  <VscChevronRight className={`text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`} />
                </button>
                {expanded && (
                  <div className="max-h-[400px] overflow-y-auto border-t border-[#1e2d50] bg-[#0f0f1a]/60">
                    {reports.length === 0 ? (
                      <div className="p-3 text-xs text-gray-500 italic">No reports in this domain.</div>
                    ) : (
                      <table className="w-full text-xs">
                        <thead className="bg-[#0f0f1a] sticky top-0">
                          <tr>
                            <th className="text-left p-2 text-gray-400 font-semibold uppercase">Report</th>
                            <th className="text-right p-2 text-gray-400 font-semibold uppercase">Execs</th>
                            <th className="text-right p-2 text-gray-400 font-semibold uppercase">Conf</th>
                          </tr>
                        </thead>
                        <tbody>
                          {reports.slice(0, 200).map((r) => (
                            <tr key={r.id} className="border-t border-[#1e2d50] hover:bg-[#1a2a4a]">
                              <td className="p-2 text-gray-200 truncate max-w-[240px]" title={r.name}>
                                {r.name || r.id}
                              </td>
                              <td className="p-2 text-right text-gray-400 font-mono">
                                {r.executions.toLocaleString()}
                              </td>
                              <td className="p-2 text-right text-gray-500 font-mono">
                                {r.confidence != null ? r.confidence.toFixed(2) : '—'}
                              </td>
                            </tr>
                          ))}
                          {reports.length > 200 && (
                            <tr>
                              <td colSpan={3} className="p-2 text-center text-xs text-gray-500 italic">
                                … +{(reports.length - 200).toLocaleString()} more
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// HEAVY USERS TAB
// ─────────────────────────────────────────────────────────────────────

function HeavyUsersTab({
  users,
  search,
  setSearch,
  filter,
  setFilter,
  onOpen,
}: {
  users: HeavyUser[];
  search: string;
  setSearch: (v: string) => void;
  filter: 'all' | 'human' | 'service';
  setFilter: (v: 'all' | 'human' | 'service') => void;
  onOpen: (u: HeavyUser) => void;
}) {
  const filtered = useMemo(() => {
    let list = users;
    if (filter === 'human')   list = list.filter((u) => !u.isService);
    if (filter === 'service') list = list.filter((u) => u.isService);
    const q = search.toLowerCase().trim();
    if (q) list = list.filter((u) => u.user.toLowerCase().includes(q));
    return list;
  }, [users, filter, search]);

  const humanTotalExecs = useMemo(
    () => users.filter((u) => !u.isService).reduce((s, u) => s + u.totalExecutions, 0),
    [users]
  );
  const serviceTotalExecs = useMemo(
    () => users.filter((u) => u.isService).reduce((s, u) => s + u.totalExecutions, 0),
    [users]
  );
  const serviceCount = users.filter((u) => u.isService).length;

  if (users.length === 0) {
    return (
      <div className="bg-[#16213e] p-8 rounded-lg text-center space-y-3">
        <div className="text-4xl">👥</div>
        <h2 className="text-xl font-bold text-white">No user activity data yet</h2>
        <p className="text-sm text-gray-400 max-w-2xl mx-auto">
          Populate <code className="text-emerald-300">UserActivity.csv</code> and re-run the
          build to see who drives report executions.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Intro + stats */}
      <div className="bg-gradient-to-r from-blue-950/60 to-[#16213e] p-4 rounded-lg border border-blue-800/40">
        <div className="text-xs uppercase tracking-wider text-blue-300 font-bold mb-2">
          Heavy Users — Who Actually Drives Report Execution in This Project
        </div>
        <p className="text-xs text-gray-400 leading-relaxed">
          Top users by execution count against reports in this project (scoped by folder-path prefix).
          Click any user to see their top reports, whether those reports survived the pipeline as
          final canonicals, and whether they're singletons, cluster members, or non-inventory
          objects (dossiers, deleted items, project meta-objects).
        </p>
      </div>

      <div className="grid grid-cols-4 gap-4">
        {/* "All users" chip — clicking resets to both */}
        <button
          type="button"
          onClick={() => setFilter('all')}
          className={`text-left bg-[#16213e] p-4 rounded-lg border-l-4 border-violet-500 transition-all cursor-pointer ${
            filter === 'all' ? 'ring-2 ring-violet-500 bg-violet-900/20' : 'hover:bg-[#1a2a4a]'
          }`}
          title="Show all users (human + service)"
        >
          <div className="text-xs text-gray-400 uppercase mb-1">Users (all)</div>
          <div className="text-2xl font-bold text-white">{users.length.toLocaleString()}</div>
          <div className="text-xs text-gray-500">click to show both</div>
        </button>
        <button
          type="button"
          onClick={() => setFilter(filter === 'human' ? 'all' : 'human')}
          className={`text-left bg-[#16213e] p-4 rounded-lg border-l-4 border-blue-500 transition-all cursor-pointer ${
            filter === 'human' ? 'ring-2 ring-blue-500 bg-blue-900/20' : 'hover:bg-[#1a2a4a]'
          }`}
          title="Show only human users"
        >
          <div className="text-xs text-gray-400 uppercase mb-1">Users (human)</div>
          <div className="text-2xl font-bold text-white">
            {(users.length - serviceCount).toLocaleString()}
          </div>
          <div className="text-xs text-gray-500">
            {humanTotalExecs.toLocaleString()} execs
          </div>
        </button>
        <button
          type="button"
          onClick={() => setFilter(filter === 'service' ? 'all' : 'service')}
          className={`text-left bg-[#16213e] p-4 rounded-lg border-l-4 border-amber-500 transition-all cursor-pointer ${
            filter === 'service' ? 'ring-2 ring-amber-500 bg-amber-900/20' : 'hover:bg-[#1a2a4a]'
          }`}
          title="Show only service accounts"
        >
          <div className="text-xs text-gray-400 uppercase mb-1">Users (service)</div>
          <div className="text-2xl font-bold text-white">{serviceCount}</div>
          <div className="text-xs text-gray-500">
            {serviceTotalExecs.toLocaleString()} execs
          </div>
        </button>
        <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-slate-500">
          <div className="text-xs text-gray-400 uppercase mb-1">Total executions</div>
          <div className="text-2xl font-bold text-white">
            {(humanTotalExecs + serviceTotalExecs).toLocaleString()}
          </div>
          <div className="text-xs text-gray-500">human + service</div>
        </div>
      </div>

      <div className="bg-[#16213e] p-4 rounded-lg">
        <div className="relative">
          <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by user name..."
            className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded-lg pl-10 pr-4 py-2.5 text-white text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      <div className="bg-[#16213e] rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-[#1e2d50] text-sm text-gray-400">
          <span className="font-semibold text-white">{filtered.length.toLocaleString()}</span>
          {' '}users shown. Click a row for details.
        </div>
        <div className="max-h-[70vh] overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-[#0f0f1a] sticky top-0">
              <tr>
                <th className="text-left p-3 text-xs text-gray-400 uppercase">User</th>
                <th className="text-right p-3 text-xs text-gray-400 uppercase">Executions</th>
                <th className="text-right p-3 text-xs text-gray-400 uppercase">Unique Reports</th>
                <th className="text-right p-3 text-xs text-gray-400 uppercase">Sessions</th>
                <th className="text-right p-3 text-xs text-gray-400 uppercase">Errors</th>
                <th className="text-left p-3 text-xs text-gray-400 uppercase">Last Activity</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((u) => (
                <tr
                  key={u.user}
                  onClick={() => onOpen(u)}
                  className={`border-t border-[#1e2d50] cursor-pointer hover:bg-[#1a2a4a] ${
                    u.isService ? 'bg-amber-900/10' : ''
                  }`}
                >
                  <td className="p-3 text-white">
                    <div className="flex items-center gap-2">
                      <span>{u.user}</span>
                      {u.isService && (
                        <span className="text-[10px] font-bold bg-amber-900/50 text-amber-300 border border-amber-700/50 px-1.5 py-0.5 rounded">
                          SERVICE
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="p-3 text-right font-mono text-gray-200">
                    {u.totalExecutions.toLocaleString()}
                  </td>
                  <td className="p-3 text-right font-mono text-gray-300">
                    {u.uniqueReports.toLocaleString()}
                  </td>
                  <td className="p-3 text-right font-mono text-gray-400">
                    {(u.sessions || 0).toLocaleString()}
                  </td>
                  <td className={`p-3 text-right font-mono ${u.errors > 0 ? 'text-red-400' : 'text-gray-500'}`}>
                    {(u.errors || 0).toLocaleString()}
                  </td>
                  <td className="p-3 text-xs text-gray-400">{u.lastExec || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function HeavyUserModal({
  user,
  onClose,
}: {
  user: HeavyUser;
  onClose: () => void;
}) {
  const stats = useMemo(() => {
    const reports = user.topReports;
    const inInv = reports.filter((r) => r.inInventory).length;
    const kept = reports.filter((r) => r.isFinalCanonical).length;
    const nonInv = reports.filter((r) => !r.inInventory).length;
    const collapsed = reports.filter((r) => r.inInventory && r.status === 'collapsed').length;
    return { inInv, kept, nonInv, collapsed };
  }, [user]);

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-[#16213e] rounded-lg max-w-5xl w-full max-h-[92vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-5 border-b border-[#1e2d50] flex justify-between items-start">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1">
              <h2 className="text-xl font-bold text-white">{user.user}</h2>
              {user.isService && (
                <span className="text-xs font-bold bg-amber-900/50 text-amber-300 border border-amber-700/50 px-2 py-0.5 rounded">
                  SERVICE ACCOUNT
                </span>
              )}
            </div>
            <div className="text-xs text-gray-400 flex items-center gap-4 flex-wrap">
              <span>
                <strong className="text-white">{user.totalExecutions.toLocaleString()}</strong> executions
              </span>
              <span>
                <strong className="text-white">{user.uniqueReports.toLocaleString()}</strong> unique reports
              </span>
              <span>
                <strong className="text-white">{(user.sessions || 0).toLocaleString()}</strong> sessions
              </span>
              {user.errors > 0 && (
                <span className="text-red-400">
                  <strong>{user.errors.toLocaleString()}</strong> errors
                </span>
              )}
              <span>Last: {user.lastExec || '—'}</span>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-2xl leading-none ml-3">
            ×
          </button>
        </div>

        <div className="p-5 overflow-y-auto flex-1 space-y-4">
          <div className="grid grid-cols-4 gap-3">
            <div className="bg-[#0f0f1a] p-3 rounded border border-[#1e2d50]">
              <div className="text-xs text-gray-500">In inventory</div>
              <div className="text-xl font-bold text-emerald-400">{stats.inInv}</div>
            </div>
            <div className="bg-[#0f0f1a] p-3 rounded border border-[#1e2d50]">
              <div className="text-xs text-gray-500">Kept as final</div>
              <div className="text-xl font-bold text-emerald-300">{stats.kept}</div>
            </div>
            <div className="bg-[#0f0f1a] p-3 rounded border border-[#1e2d50]">
              <div className="text-xs text-gray-500">Collapsed</div>
              <div className="text-xl font-bold text-amber-400">{stats.collapsed}</div>
            </div>
            <div className="bg-[#0f0f1a] p-3 rounded border border-[#1e2d50]">
              <div className="text-xs text-gray-500">Not in inventory</div>
              <div className="text-xl font-bold text-red-400">{stats.nonInv}</div>
              <div className="text-[10px] text-gray-500">dossier / deleted / meta</div>
            </div>
          </div>

          <div className="text-xs text-gray-400 uppercase">Top reports this user runs</div>
          <table className="w-full text-sm">
            <thead className="bg-[#0f0f1a]">
              <tr>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Report Name</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Status</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Final?</th>
              </tr>
            </thead>
            <tbody>
              {user.topReports.map((r) => {
                const statusLabel = r.inInventory
                  ? (r.isFinalCanonical
                      ? 'kept as canonical'
                      : r.status === 'collapsed'
                      ? 'collapsed'
                      : r.clusterId
                      ? `cluster ${r.clusterId} member`
                      : r.status || 'active')
                  : 'not in inventory';
                const statusClass = r.isFinalCanonical
                  ? 'bg-emerald-900/50 text-emerald-300 border-emerald-700/50'
                  : !r.inInventory
                  ? 'bg-red-900/40 text-red-200 border-red-700/50'
                  : r.status === 'collapsed'
                  ? 'bg-amber-900/40 text-amber-200 border-amber-700/50'
                  : 'bg-slate-800/60 text-slate-300 border-slate-600/50';
                return (
                  <tr key={r.objectId} className="border-t border-[#1e2d50] text-gray-300">
                    <td className="p-2 text-white">
                      {r.reportName || r.inventoryName || '(unnamed)'}
                      {r.folderPath && (
                        <div className="text-[10px] text-gray-500 font-mono truncate max-w-[40vw]">
                          {r.folderPath}
                        </div>
                      )}
                    </td>
                    <td className="p-2 text-right font-mono">{r.executions.toLocaleString()}</td>
                    <td className="p-2">
                      <span className={`text-xs px-2 py-0.5 rounded border ${statusClass}`}>
                        {statusLabel}
                      </span>
                    </td>
                    <td className="p-2 text-xs">
                      {r.isFinalCanonical ? (
                        <span className="text-emerald-400">✓ in Final</span>
                      ) : (
                        <span className="text-gray-600">—</span>
                      )}
                    </td>
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

// ─────────────────────────────────────────────────────────────────────
// AI PLAYGROUND TAB
// ─────────────────────────────────────────────────────────────────────

// Map a project display name to the short project_id used throughout the
// backend (stored on experiments as config.project). Drives experiment-list
// filtering — without this, the UI's MSTR GUID (E77B77...) would never match
// the stored short id (global-operational).
const PROJECT_NAME_TO_SHORT: Record<string, string> = {
  'Global Operational': 'global-operational',
  'Global Insight': 'global-insight',
  'INSIGHT': 'insight',
  'Combined Reports Project': 'combined-reports',
};

// ─────────────────────────────────────────────────────────────────────
// EMBEDDINGS TAB — named embedding sets that clustering methods reuse
// ─────────────────────────────────────────────────────────────────────

function EmbeddingsTab({
  projectId,
  projectName,
}: {
  projectId: string;
  projectName: string;
}) {
  const currentShortId = PROJECT_NAME_TO_SHORT[projectName] || '';

  const DEFAULT_FORM = {
    name: '',
    scope: 'post-family',
    sourceFilter: 'all',
    domainFilter: 'all',
    model: 'text-embedding-3-large',
    dim: 3072,
    fields: {
      name: true, path: true, owner: false,
      attributes: true, metrics: true, tables: true, filters: true,
      sql: true, normalizeSql: true, maxSqlChars: 2000,
    } as Record<string, boolean | number>,
  };
  const [form, setForm] = usePersistentState('embeddings.form.v1', DEFAULT_FORM);
  // Available domains come from the most recent Classify Domain run for
  // this project. When the project has no classifications, the dropdown
  // only shows "All domains" (plus a hint to run Classify Domain first).
  const [availableDomains, setAvailableDomains] = useState<{ name: string; count: number }[]>([]);
  useEffect(() => {
    if (!projectId) { setAvailableDomains([]); return; }
    fetchDomainResults(projectId).then((r) => {
      const rows = Object.entries(r.byDomain || {}).map(([name, reps]) => ({
        name, count: (reps as unknown as unknown[]).length,
      }));
      rows.sort((a, b) => b.count - a.count);
      setAvailableDomains(rows);
    }).catch(() => setAvailableDomains([]));
  }, [projectId]);
  const patchForm = (p: Partial<typeof form>) => setForm((f) => ({ ...f, ...p }));
  const toggleField = (k: string) => patchForm({ fields: { ...form.fields, [k]: !form.fields[k] } });

  const [sets, setSets] = useState<EmbeddingSet[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAllProjects, setShowAllProjects] = usePersistentState<boolean>('embeddings.showAll', false);
  const [preview, setPreview] = useState<EmbeddingSetPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const [activeTask, setActiveTask] = useState<PlaygroundTask | null>(null);
  const [activeTaskId, setActiveTaskId] = usePersistentState<string | null>('embeddings.activeTaskId', null);
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null);
  const [sidecarOk, setSidecarOk] = useState<boolean | null>(null);
  const [apiKeySet, setApiKeySet] = useState<boolean | null>(null);
  const pollRef = useRef<number | null>(null);

  type LogEntry = { ts: string; level: 'info' | 'ok' | 'warn' | 'err'; msg: string };
  const [runLog, setRunLog] = useState<LogEntry[]>([]);
  const appendLog = (level: LogEntry['level'], msg: string) => {
    const ts = new Date().toISOString().slice(11, 19);
    setRunLog((prev) => [...prev.slice(-199), { ts, level, msg }]);
  };
  const clearLog = () => setRunLog([]);

  // ---- load list + health on project change ----------------------
  const loadList = async () => {
    setLoading(true);
    try {
      const rows = await fetchEmbeddingSets(showAllProjects ? undefined : (currentShortId || undefined));
      setSets(rows);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    loadList();
    checkPlaygroundHealth().then((h) => {
      setSidecarOk(h !== null);
      setApiKeySet(h?.openai_key_set ?? null);
    });
    if (activeTaskId) {
      fetchPlaygroundTask(activeTaskId).then((t) => { if (t) setActiveTask(t); });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, currentShortId, showAllProjects]);

  // ---- debounced cost preview whenever form changes --------------
  useEffect(() => {
    if (!projectId) { setPreview(null); return; }
    const handle = window.setTimeout(async () => {
      setPreviewing(true);
      try {
        const cfg = {
          name: form.name || 'preview',
          project: projectId,
          scope: form.scope, sourceFilter: form.sourceFilter,
          domainFilter: form.domainFilter,
          model: form.model, dimensions: form.dim,
          fields: form.fields,
        };
        const p = await previewEmbeddingSet(cfg);
        setPreview(p);
      } finally {
        setPreviewing(false);
      }
    }, 400);
    return () => window.clearTimeout(handle);
  }, [projectId, form.scope, form.sourceFilter, form.domainFilter, form.model, form.dim,
      // JSON-stringify the fields so React effect deps stay shallow
      JSON.stringify(form.fields)]);

  // ---- task polling ---------------------------------------------
  useEffect(() => {
    if (!activeTask || activeTask.status !== 'running') {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    const tick = async () => {
      const t = await fetchPlaygroundTask(activeTask.id);
      if (!t) return;
      setActiveTask(t);
      if (t.status === 'completed') {
        appendLog('ok', `✓ Embedding set created/refreshed.`);
        await loadList();
        setActiveTaskId(null);
        setRunStartedAt(null);
        setActiveTask(null);
      } else if (t.status === 'failed' || t.status === 'interrupted') {
        appendLog('err', `✗ Task ${t.status}: ${t.error || 'no detail'}`);
        setActiveTaskId(null);
        setRunStartedAt(null);
      } else {
        appendLog('info', `poll: status=${t.status}`);
      }
    };
    pollRef.current = window.setInterval(tick, 2000);
    tick();
    return () => {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTask?.id, activeTask?.status]);

  const running = activeTask?.status === 'running';
  const runElapsed = runStartedAt ? Math.floor((Date.now() - runStartedAt) / 1000) : 0;
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setTick((x) => x + 1), 1000);
    return () => window.clearInterval(id);
  }, [running]);

  // ---- run + delete ---------------------------------------------
  async function handleRun() {
    // Diagnostic: this line fires unconditionally so we can tell from the
    // run-log panel whether the click is even reaching the handler (vs.
    // being swallowed by a disabled button or a stale bundle).
    const clickTs = new Date().toISOString().slice(11, 19);
    console.log('[embeddings] handleRun fired', { name: form.name, projectId, sidecarOk, apiKeySet });
    appendLog('info', `button clicked @ ${clickTs}  (project=${projectId || '(none)'}, sidecar=${String(sidecarOk)}, apiKey=${String(apiKeySet)})`);

    // Fall back to the auto-suggested name if the user didn't type one.
    let name = (form.name || '').trim();
    if (!name) {
      name = suggestedName;
      patchForm({ name });
      appendLog('info', `No name typed — using suggested "${name}".`);
    }
    if (!projectId) {
      appendLog('err', 'No project selected. Pick one from the sidebar.');
      return;
    }
    clearLog();
    setRunStartedAt(Date.now());
    appendLog('info', `▶ Creating embedding set "${name}"`);
    if (preview) {
      appendLog('info', `  ${preview.totalReports} reports · ${preview.cached} cached · ${preview.new} new calls · ~$${preview.estimatedCostUsd.toFixed(4)}`);
    }
    appendLog('info', `POST ${window.location.origin}/playground_api/embeddings/run …`);
    try {
      const task = await runEmbeddingSet({
        name, project: projectId,
        scope: form.scope, sourceFilter: form.sourceFilter,
        domainFilter: form.domainFilter,
        model: form.model, dimensions: form.dim,
        fields: form.fields,
      });
      appendLog('info', `  POST returned`);
      if (task.status === 'failed' || task.status === 'interrupted') {
        appendLog('err', `✗ Task failed at creation: ${task.error || 'no detail'}`);
        setRunStartedAt(null);
        return;
      }
      appendLog('ok', `✓ Task queued: ${task.id}`);
      setActiveTask(task);
      setActiveTaskId(task.id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      appendLog('err', `✗ POST failed: ${msg}`);
      setRunStartedAt(null);
    }
  }

  async function handleDelete(name: string) {
    if (!window.confirm(`Delete embedding set "${name}"? The underlying vectors stay in the cache.`)) return;
    await deleteEmbeddingSet(name);
    appendLog('info', `Removed "${name}" (cached vectors untouched).`);
    await loadList();
  }

  // Auto-suggest a name based on fields + model when the field empty
  const suggestedName = useMemo(() => {
    const on = Object.entries(form.fields)
      .filter(([k, v]) => typeof v === 'boolean' && v && k !== 'normalizeSql')
      .map(([k]) => k);
    const fieldTag = on.length === 0 ? 'nofield'
                   : on.length === 9 ? 'full'
                   : on.slice(0, 3).join('-');
    const modelTag = form.model.includes('large') ? 'lg' : 'sm';
    return `${currentShortId || 'proj'}-${fieldTag}-${modelTag}-${form.dim}`;
  }, [form.fields, form.model, form.dim, currentShortId]);

  return (
    <div className="space-y-4">
      <div className="bg-gradient-to-r from-violet-950/60 to-[#16213e] p-4 rounded-lg border border-violet-800/40">
        <div className="text-xs uppercase tracking-wider text-violet-300 font-bold mb-2">
          Embeddings — the asset layer that clustering builds on
        </div>
        <p className="text-xs text-gray-400 leading-relaxed">
          An embedding turns each report's text (name, path, metrics, etc.) into a high-dim vector. It's the expensive
          step — once computed, <strong className="text-violet-300">Semantic</strong>, <strong className="text-cyan-300">Density</strong>,
          and other clustering methods reuse it for free. Create named sets here, then reference them from the clustering tabs.
          The cache is shared — if another run already embedded the same text with the same model/dim, this is a zero-cost lookup.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* Left: config form */}
        <div className="lg:col-span-2 bg-[#16213e] p-5 rounded-lg border border-violet-900/40">
          <h3 className="text-white font-bold mb-3">Define an Embedding Set</h3>
          <div className="space-y-3 text-sm">
            <div>
              <label className="block text-xs text-gray-400 uppercase mb-1">Name</label>
              <input
                value={form.name}
                onChange={(e) => patchForm({ name: e.target.value })}
                placeholder={suggestedName}
                className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-3 py-2 text-white text-sm"
              />
              <div className="text-[10px] text-gray-500 mt-1">
                Unique name. Suggested: <code className="text-violet-300">{suggestedName}</code>
                {' '}
                <button onClick={() => patchForm({ name: suggestedName })}
                  className="text-[10px] text-violet-300 hover:text-white underline ml-1">use</button>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">Scope</label>
                <select value={form.scope} onChange={(e) => patchForm({ scope: e.target.value })}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm">
                  <option value="post-family">post-family</option>
                  <option value="post-ast">post-AST</option>
                  <option value="active">active</option>
                  <option value="final-kept">final-kept</option>
                  <option value="post-collision">post-collision</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">Source filter</label>
                <select value={form.sourceFilter} onChange={(e) => patchForm({ sourceFilter: e.target.value })}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm">
                  <option value="all">All sources</option>
                  <option value="normal">SQL only</option>
                  <option value="cube">Cube only</option>
                  <option value="custom_sql_free_form">Free SQL only</option>
                  <option value="exclude-cube">Exclude cubes</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">
                  Business domain
                  {availableDomains.length === 0 && (
                    <span className="text-[9px] text-amber-400 ml-1 normal-case" title="No classification data for this project yet — run Classify Domain first.">
                      · none classified
                    </span>
                  )}
                </label>
                <select
                  value={form.domainFilter}
                  onChange={(e) => patchForm({ domainFilter: e.target.value })}
                  disabled={availableDomains.length === 0}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm disabled:opacity-50"
                >
                  <option value="all">All domains</option>
                  {availableDomains.map((d) => (
                    <option key={d.name} value={d.name}>
                      {d.name} ({d.count.toLocaleString()})
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label className="block text-xs text-gray-400 uppercase mb-1">Fields embedded</label>
              <div className="grid grid-cols-3 gap-1.5 text-xs">
                {[
                  ['name', 'Report name'],
                  ['path', 'Folder path'],
                  ['owner', 'Owner'],
                  ['attributes', 'Attributes'],
                  ['metrics', 'Metrics'],
                  ['tables', 'Tables'],
                  ['filters', 'Filters'],
                  ['sql', 'SQL text'],
                  ['normalizeSql', 'Normalize SQL'],
                ].map(([k, label]) => (
                  <label key={k} className="flex items-center gap-1.5 cursor-pointer text-gray-300 hover:text-white">
                    <input type="checkbox" checked={!!form.fields[k]} onChange={() => toggleField(k)} />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
              {form.fields.sql && (
                <div className="mt-2 flex items-center gap-2 text-xs text-gray-400">
                  <span>max SQL chars:</span>
                  <input type="number" min={100} max={8000} step={100}
                    value={Number(form.fields.maxSqlChars) || 2000}
                    onChange={(e) => patchForm({ fields: { ...form.fields, maxSqlChars: parseInt(e.target.value) || 2000 } })}
                    className="w-24 bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1 text-white" />
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">Model</label>
                <select value={form.model} onChange={(e) => {
                  const m = e.target.value;
                  patchForm({ model: m, ...(m.includes('small') ? { dim: 1536 } : {}) });
                }}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm">
                  <option value="text-embedding-3-large">3-large (3072d)</option>
                  <option value="text-embedding-3-small">3-small (1536d, cheaper)</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">Dim</label>
                <select value={form.dim} onChange={(e) => patchForm({ dim: parseInt(e.target.value) })}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm">
                  {form.model.includes('large') && <option value={3072}>3072 (full)</option>}
                  <option value={1536}>1536</option>
                  <option value={768}>768</option>
                </select>
              </div>
            </div>

            {/* Live cost preview */}
            <div className="pt-2 border-t border-[#1e2d50] text-xs">
              <div className="text-[10px] uppercase text-gray-500 mb-1">Cost preview</div>
              {!preview && !previewing && (
                <div className="text-gray-500 italic">pick a project to preview</div>
              )}
              {previewing && (
                <div className="text-gray-400">computing…</div>
              )}
              {preview && !previewing && (
                <div className="flex items-center flex-wrap gap-2">
                  <span className="text-gray-400">{preview.totalReports.toLocaleString()} reports</span>
                  <span className="text-gray-600">·</span>
                  <span className="text-emerald-300">{preview.cached.toLocaleString()} cached</span>
                  <span className="text-gray-600">·</span>
                  <span className={preview.new === 0 ? 'text-emerald-300' : 'text-amber-300 font-semibold'}>
                    {preview.new.toLocaleString()} new
                  </span>
                  <span className="text-gray-600">·</span>
                  <span className={preview.estimatedCostUsd === 0 ? 'text-emerald-300 font-semibold' : 'text-amber-300 font-semibold'}>
                    ~${preview.estimatedCostUsd.toFixed(4)}
                  </span>
                </div>
              )}
            </div>

            <div className="text-[11px] text-gray-500 pt-1">
              {sidecarOk === null ? 'Checking sidecar…' : sidecarOk
                ? <span className="text-emerald-300">Sidecar connected · OpenAI key {apiKeySet ? 'set ✓' : 'MISSING ✗'}</span>
                : <span className="text-red-300">Sidecar not running.</span>}
            </div>

            <div className="flex gap-2 items-start">
              <button onClick={handleRun}
                disabled={running || sidecarOk === false || apiKeySet === false}
                className="bg-violet-600 hover:bg-violet-500 disabled:bg-violet-900 disabled:text-gray-500 disabled:cursor-not-allowed text-white text-sm font-semibold px-5 py-2.5 rounded flex items-center gap-2"
                title={
                  sidecarOk === false ? 'Start the sidecar first (python playground_server.py)' :
                  apiKeySet === false ? 'OPENAI_API_KEY missing on server' :
                  !form.name.trim() ? `Will use suggested name "${suggestedName}"` :
                  'Create the named embedding set'
                }>
                {running ? (
                  <><span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full"></span>
                  Running… <span className="text-xs font-normal opacity-80">({runElapsed}s)</span></>
                ) : <>▶ Create Embedding Set</>}
              </button>
              <button onClick={() => setForm(DEFAULT_FORM)}
                disabled={running}
                className="bg-[#0f0f1a] hover:bg-[#1a2a4a] disabled:opacity-40 text-gray-300 text-sm font-semibold px-4 py-2 rounded border border-[#1e2d50]">
                Reset
              </button>
              {/* Visible reason when the button is disabled, so user understands why */}
              {(sidecarOk === false || apiKeySet === false) && (
                <div className="text-[11px] text-red-300 self-center">
                  {sidecarOk === false
                    ? '⚠ Sidecar offline — start it with python playground_server.py'
                    : '⚠ OPENAI_API_KEY missing in .env.local'}
                </div>
              )}
              {sidecarOk && apiKeySet && !form.name.trim() && (
                <div className="text-[11px] text-gray-400 self-center">
                  Leave the name empty and I'll use <code className="text-violet-300">{suggestedName}</code>
                </div>
              )}
            </div>

            {runLog.length > 0 && (
              <div className="bg-[#0f0f1a] border border-violet-700/40 rounded max-h-48 overflow-auto p-2 text-[10px] font-mono">
                {runLog.map((l, i) => (
                  <div key={i} className={
                    l.level === 'err' ? 'text-red-300' :
                    l.level === 'warn' ? 'text-amber-300' :
                    l.level === 'ok' ? 'text-emerald-300' : 'text-gray-400'
                  }>
                    <span className="text-gray-600">{l.ts}</span> {l.msg}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right: list of existing embedding sets */}
        <div className="lg:col-span-3 bg-[#16213e] p-5 rounded-lg border border-violet-900/40">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-white font-bold">
              Saved Embedding Sets
              <span className="text-sm text-gray-400 font-normal ml-2">({sets.length})</span>
            </h3>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-[11px] text-gray-400 cursor-pointer">
                <input type="checkbox" checked={showAllProjects}
                  onChange={(e) => setShowAllProjects(e.target.checked)} />
                All projects
              </label>
              <button onClick={loadList}
                className="text-[11px] text-gray-400 hover:text-white border border-[#1e2d50] rounded px-2 py-1">↻</button>
            </div>
          </div>
          {loading ? (
            <div className="text-xs text-gray-500 py-4">Loading…</div>
          ) : sets.length === 0 ? (
            <div className="text-xs text-gray-500 py-4">
              No saved embedding sets for this project yet. Define one on the left.
            </div>
          ) : (
            <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-1">
              {sets.map((s) => {
                const onFields = Object.entries(s.fields)
                  .filter(([k, v]) => typeof v === 'boolean' && v && k !== 'normalizeSql')
                  .map(([k]) => k);
                return (
                  <div key={s.name} className="bg-[#0f0f1a] p-3 rounded border-l-4 border-violet-500">
                    <div className="flex items-start justify-between gap-2 mb-1">
                      <div className="min-w-0 flex-1">
                        <div className="text-sm text-white font-semibold truncate">{s.name}</div>
                        <div className="text-[11px] text-gray-500 font-mono truncate">
                          {s.projectId} · {s.scope} · src:{s.sourceFilter}
                        </div>
                      </div>
                      <button onClick={() => handleDelete(s.name)}
                        className="text-xs text-red-400 hover:text-red-300"
                        title="Remove this set (vectors stay cached)">✕</button>
                    </div>
                    <div className="flex items-center gap-2 flex-wrap text-[11px]">
                      <span className="px-1.5 py-0.5 rounded bg-[#16213e] text-violet-300 font-mono">
                        {s.model.replace('text-embedding-3-', '3-')}/{s.dim}d
                      </span>
                      <span className="px-1.5 py-0.5 rounded bg-[#16213e] text-gray-300">
                        {s.reportCount.toLocaleString()} reports
                      </span>
                      {onFields.length > 0 && (
                        <span className="px-1.5 py-0.5 rounded bg-[#16213e] text-gray-400" title={onFields.join(', ')}>
                          fields: {onFields.length <= 3 ? onFields.join(',') : `${onFields.slice(0, 2).join(',')}+${onFields.length - 2}`}
                        </span>
                      )}
                      <span className="text-gray-500 ml-auto">
                        {s.refreshedAt ? new Date(s.refreshedAt).toLocaleDateString() : new Date(s.createdAt).toLocaleDateString()}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AiPlaygroundTab({ projectId, projectName, reportById, onActiveChanged }: { projectId: string; projectName: string; reportById: Map<string, ReportDetail>; onActiveChanged?: () => void }) {
  // Which cluster (if any) is expanded in the results pane
  const [selectedClusterId, setSelectedClusterId] = useState<string | null>(null);
  // Scope toggle — filter experiments to this project or show all
  const [showAllProjects, setShowAllProjects] = usePersistentState<boolean>('ai-playground.showAllProjects', false);
  // ===== Persistent form config (localStorage-backed, survives reload) =====
  const DEFAULT_FORM = {
    expName: 'my-experiment',
    scope: 'post-family',
    sourceFilter: 'all',
    model: 'text-embedding-3-large',
    dim: 3072,
    threshold: 0.85,
    minSize: 2,
    limit: null as number | null,
    fields: {
      name: true, path: true, owner: false,
      attributes: true, metrics: true, tables: true, filters: true,
      sql: true, normalizeSql: true, maxSqlChars: 2000,
    } as Record<string, boolean | number>,
  };
  const [formConfig, setFormConfig] = usePersistentState<typeof DEFAULT_FORM>(
    'ai-playground.form.v2', DEFAULT_FORM,
  );
  const patchForm = (patch: Partial<typeof formConfig>) => setFormConfig((f) => ({ ...f, ...patch }));
  const { expName, scope, sourceFilter, model, dim, threshold, minSize, limit, fields } = formConfig;
  // "New experiment" button resets every field to its default so the user
  // can start fresh instead of editing the prior run's values.
  const resetForm = () => {
    setFormConfig({ ...DEFAULT_FORM, fields: { ...DEFAULT_FORM.fields } });
  };

  // Persist selection + active run so tab switches restore immediately.
  const [selectedExpId, setSelectedExpId] = usePersistentState<string | null>('ai-playground.selectedExpId', null);
  const [activeTaskId, setActiveTaskId] = usePersistentState<string | null>('ai-playground.activeTaskId', null);

  // Phase 2 — saved embedding sets the user can reference to skip
  // re-embedding. Empty string means "build an embedding inline from the
  // fields/model/dim below" (legacy behavior).
  const [embeddingSetName, setEmbeddingSetName] = usePersistentState<string>(
    'ai-playground.embeddingSetName', '',
  );
  const [embeddingSets, setEmbeddingSets] = useState<EmbeddingSet[]>([]);
  const selectedSet = embeddingSets.find((s) => s.name === embeddingSetName) || null;

  // Ephemeral UI state (safe to reset on unmount — we recover from the above)
  const [selectedExp, setSelectedExp] = useState<PlaygroundExperiment | null>(null);
  const [experiments, setExperiments] = useState<PlaygroundExperimentIndexEntry[]>([]);
  const [activeTask, setActiveTask] = useState<PlaygroundTask | null>(null);
  const [loadingList, setLoadingList] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null);
  const [sidecarOk, setSidecarOk] = useState<boolean | null>(null);
  const [apiKeySet, setApiKeySet] = useState<boolean | null>(null);
  const pollRef = useRef<number | null>(null);


  // Step-by-step run log — visible in the UI so the user sees exactly
  // what happens when they click Run (helps a lot when things go wrong).
  type LogEntry = { ts: string; level: 'info' | 'ok' | 'warn' | 'err'; msg: string };
  const [runLog, setRunLog] = useState<LogEntry[]>([]);
  const appendLog = (level: LogEntry['level'], msg: string) => {
    const ts = new Date().toISOString().slice(11, 19);
    setRunLog((prev) => [...prev.slice(-199), { ts, level, msg }]);
  };
  const clearLog = () => setRunLog([]);

  const currentShortId = PROJECT_NAME_TO_SHORT[projectName] || '';

  // Which experiment (if any) is promoted as "primary" for this project.
  // When set, the Semantic Clusters tab loads this experiment instead of the
  // static pipeline file.
  const [activeExpId, setActiveExpId] = useState<string | null>(null);

  const loadList = async () => {
    setLoadingList(true);
    try {
      const idx = await fetchPlaygroundIndex();
      if (showAllProjects || !currentShortId) {
        setExperiments(idx);
      } else {
        setExperiments(idx.filter((e) => e.project === currentShortId));
      }
    } finally {
      setLoadingList(false);
    }
  };

  const loadActive = async () => {
    if (!currentShortId) { setActiveExpId(null); return; }
    try {
      const info = await fetchActiveExperiment(currentShortId);
      setActiveExpId(info.expId);
    } catch {
      setActiveExpId(null);
    }
  };

  // Phase 2 — pull saved embedding sets for this project so the user can
  // reference one instead of re-defining fields/model/dim inline.
  const loadEmbeddingSets = async () => {
    if (!currentShortId) { setEmbeddingSets([]); return; }
    try {
      const rows = await fetchEmbeddingSets(currentShortId);
      setEmbeddingSets(rows);
      // If the picked set is gone (deleted in Embeddings tab), clear it.
      if (embeddingSetName && !rows.find((r) => r.name === embeddingSetName)) {
        setEmbeddingSetName('');
      }
    } catch {
      setEmbeddingSets([]);
    }
  };

  async function handleMakePrimary(expId: string) {
    if (!currentShortId) return;
    try {
      await setActiveExperiment(currentShortId, expId);
      setActiveExpId(expId);
      onActiveChanged?.();
      appendLog('ok', `✓ "${expId}" is now the primary semantic clustering for ${currentShortId}. Go to the Semantic Clusters tab to see it.`);
      // Note: LLM review is NOT auto-triggered. Use the "🤖 LLM Review"
      // button on each experiment card when you actually want the GPT pass.
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      appendLog('err', `✗ Could not promote: ${msg}`);
    }
  }

  async function handleRunLlmReview(expId: string) {
    if (!projectId) return;
    try {
      appendLog('info', `POST /playground_api/exp/${expId}/llm_review …`);
      const task = await runExperimentLlmReview(expId, projectId);
      if (task.status === 'failed' || task.status === 'interrupted') {
        appendLog('err', `✗ LLM review failed at creation: ${task.error || 'no detail'}`);
        return;
      }
      appendLog('ok', `✓ LLM review queued: ${task.id}. Polls every 2s.`);
      setActiveTask(task);
      setActiveTaskId(task.id);
      setRunStartedAt(Date.now());
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      appendLog('err', `✗ LLM review POST failed: ${msg}`);
    }
  }

  async function handleClearPrimary() {
    if (!currentShortId) return;
    try {
      await clearActiveExperiment(currentShortId);
      setActiveExpId(null);
      onActiveChanged?.();
      appendLog('info', `Reverted ${currentShortId} to the pipeline's semantic clustering.`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      appendLog('err', `✗ Could not clear: ${msg}`);
    }
  }

  // On mount + project change: refresh list, health, restore state.
  // If the last-selected experiment belongs to a different project, clear it
  // so the user doesn't see cross-project results by accident.
  useEffect(() => {
    loadList();
    loadActive();
    loadEmbeddingSets();
    checkPlaygroundHealth().then((h) => {
      setSidecarOk(h !== null);
      setApiKeySet(h?.openai_key_set ?? null);
    });
    if (selectedExpId) {
      fetchPlaygroundExperiment(selectedExpId).then((e) => {
        if (!e) {
          setSelectedExpId(null);
          setSelectedExp(null);
          return;
        }
        const expProject = (e.config as { project?: string })?.project;
        if (currentShortId && expProject && expProject !== currentShortId && !showAllProjects) {
          // Stale selection from a different project — clear
          setSelectedExpId(null);
          setSelectedExp(null);
        } else {
          setSelectedExp(e);
        }
      });
    }
    if (activeTaskId) {
      fetchPlaygroundTask(activeTaskId).then((t) => {
        if (t) setActiveTask(t);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, showAllProjects, currentShortId]);

  // Task polling — runs whenever activeTask exists and is still "running"
  useEffect(() => {
    if (!activeTask || activeTask.status !== 'running') {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    const tick = async () => {
      const t = await fetchPlaygroundTask(activeTask.id);
      if (!t) {
        appendLog('warn', `poll: task not found (sidecar may have restarted)`);
        return;
      }
      setActiveTask(t);
      if (t.status === 'completed') {
        const isLlmReview = (t as { mode?: string }).mode === 'llm_review';
        appendLog('ok', `✓ Task completed in ${Math.floor((Date.now() - (runStartedAt || Date.now())) / 1000)}s`);
        if (t.expId) appendLog('info', `  experiment id: ${t.expId}`);
        setActiveTaskId(null);
        setRunStartedAt(null);
        // Always refresh the list so llmReviewedAt + llmReviewSummary badges update.
        await loadList();
        if (t.expId) {
          appendLog('info', `Loading result …`);
          setSelectedExpId(t.expId);
          const e = await fetchPlaygroundExperiment(t.expId);
          if (e) {
            setSelectedExp(e);
            if (isLlmReview) {
              appendLog('ok', `✓ LLM review finished. Refresh the Semantic Clusters tab to see labels & actions.`);
              onActiveChanged?.();
            } else {
              appendLog('ok', `✓ Result loaded: ${e.stats.multiClusters} multi-clusters, ${e.stats.singletons} singletons, ${e.stats.finalUnique} unique`);
            }
          } else {
            appendLog('warn', `result file not found for ${t.expId}`);
          }
        }
        // After a successful run (but NOT an LLM-review task, which just
        // annotates an existing experiment), reset the form to defaults so
        // the user starts fresh instead of editing the last run's values.
        // The result is already in the registry; the config is not lost.
        if (!isLlmReview) {
          resetForm();
          appendLog('info', `Form reset — ready for a new experiment.`);
        }
        appendLog('ok', `✓ Done.`);
      } else if (t.status === 'failed' || t.status === 'interrupted') {
        appendLog('err', `✗ Task ${t.status}: ${t.error || 'no detail'}`);
        setActiveTaskId(null);
        setRunStartedAt(null);
        setRunError(t.error || 'Experiment failed');
      } else {
        appendLog('info', `poll: status=${t.status}`);
      }
    };
    pollRef.current = window.setInterval(tick, 2000);
    // Fire one immediately
    tick();
    return () => {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTask?.id, activeTask?.status]);

  function toggleField(k: string) {
    patchForm({ fields: { ...fields, [k]: !fields[k] } });
  }

  const slug = (expName || 'experiment').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'experiment';
  const expId = `exp-${slug}-${scope}-${sourceFilter}-${model.includes('large') ? 'lg' : 'sm'}-d${dim}-thr${Math.round(threshold * 100)}`;

  // When the user picks an embedding set, we still send scope/fields/etc
  // so the backend can fall back gracefully if the set is missing — but
  // the backend's resolve_config_from_set will override them with the
  // stored values to guarantee cache hits.
  const config = {
    id: expId, name: expName,
    project: projectId, scope, sourceFilter,
    model, dimensions: dim, threshold, minClusterSize: minSize,
    fields, ...(limit ? { limit } : {}),
    ...(embeddingSetName ? { embeddingSetName } : {}),
  };

  const running = activeTask?.status === 'running';

  async function handleRun() {
    setRunError(null);
    setRunStartedAt(Date.now());
    clearLog();
    appendLog('info', `▶ Starting experiment "${expName}" on project ${projectId || '(none)'}`);
    appendLog('info', `  config: scope=${scope}, source=${sourceFilter}, model=${model}, dim=${dim}, thr=${threshold.toFixed(2)}, minSize=${minSize}${limit ? `, limit=${limit}` : ''}`);
    const enabledFields = Object.entries(fields).filter(([, v]) => v === true || (typeof v === 'number' && v > 0)).map(([k]) => k);
    appendLog('info', `  fields: ${enabledFields.join(', ')}`);
    appendLog('info', `POST /playground_api/run …`);
    try {
      const task = await runPlaygroundExperiment(config);
      if (task.status === 'failed' || task.status === 'interrupted') {
        appendLog('err', `✗ Task failed at creation: ${task.error || 'no detail'}`);
        setRunError(task.error || `Task ${task.status}`);
        setRunStartedAt(null);
        return;
      }
      appendLog('ok', `✓ Task queued: ${task.id} (status: ${task.status})`);
      appendLog('info', `Polling every 2s until done…`);
      setActiveTask(task);
      setActiveTaskId(task.id);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      appendLog('err', `✗ POST failed: ${msg}`);
      setRunError(msg);
      setRunStartedAt(null);
    }
  }

  async function handleRunDomain() {
    setRunError(null);
    setRunStartedAt(Date.now());
    clearLog();
    const domainId = `exp-domains-${slug}-${scope}-${sourceFilter}`;
    const domainName = `Domains: ${expName}`;
    appendLog('info', `▶ Starting domain classification on project ${projectId || '(none)'}`);
    appendLog('info', `  scope=${scope} source=${sourceFilter} — fixed taxonomy (15 domains)`);
    appendLog('info', `POST /playground_api/domains/run …`);
    try {
      const task = await runDomainClassification({
        id: domainId, name: domainName,
        project: projectId, scope, sourceFilter,
        model: 'gpt-5.4',
        ...(limit ? { limit } : {}),
      });
      if (task.status === 'failed' || task.status === 'interrupted') {
        appendLog('err', `✗ Task failed at creation: ${task.error || 'no detail'}`);
        setRunError(task.error || `Task ${task.status}`);
        setRunStartedAt(null);
        return;
      }
      appendLog('ok', `✓ Task queued: ${task.id} (status: ${task.status})`);
      appendLog('info', `Polling every 2s until done…`);
      setActiveTask(task);
      setActiveTaskId(task.id);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      appendLog('err', `✗ POST failed: ${msg}`);
      setRunError(msg);
      setRunStartedAt(null);
    }
  }

  async function openExperiment(id: string) {
    setSelectedExpId(id);
    const e = await fetchPlaygroundExperiment(id);
    if (e) setSelectedExp(e);
  }

  function closeExperiment() {
    setSelectedExp(null);
    setSelectedExpId(null);
  }

  async function handleDelete(id: string) {
    if (!window.confirm(`Delete experiment "${id}"? This cannot be undone.`)) return;
    await deletePlaygroundExperiment(id);
    if (selectedExp?.id === id) closeExperiment();
    await loadList();
  }

  const runElapsed = runStartedAt ? Math.floor((Date.now() - runStartedAt) / 1000) : 0;
  // Force re-render of elapsed time every second while running
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setTick((x) => x + 1), 1000);
    return () => window.clearInterval(id);
  }, [running]);

  return (
    <div className="space-y-4">
      {/* Intro */}
      <div className="bg-gradient-to-r from-fuchsia-950/60 to-[#16213e] p-4 rounded-lg border border-fuchsia-800/40">
        <div className="text-xs uppercase tracking-wider text-fuchsia-300 font-bold mb-2">
          AI Playground — Iterate on Embedding Experiments
        </div>
        <p className="text-xs text-gray-400 leading-relaxed">
          Try different combinations of <strong>scope</strong> (which reports go in),{' '}
          <strong>fields</strong> (what text gets embedded), <strong>model</strong>,{' '}
          <strong>dimensions</strong>, and <strong>cosine threshold</strong>. Each experiment
          is independent from the production Semantic stage — nothing in the main pipeline
          changes. Embeddings are cached by content hash, so re-running with small tweaks
          reuses prior work for free.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Config form */}
        <div className="bg-[#16213e] p-5 rounded-lg border border-fuchsia-900/40">
          <h3 className="text-white font-bold mb-3">Configure Experiment</h3>

          <div className="space-y-3 text-sm">
            {/* Phase 2: choose a saved embedding set (skips re-embedding) */}
            <div className="p-2 rounded border border-violet-800/40 bg-violet-950/20">
              <label className="block text-[10px] uppercase tracking-wider text-violet-300 font-bold mb-1">
                Embedding source
              </label>
              {embeddingSets.length === 0 ? (
                <div className="text-[11px] text-gray-500">
                  No saved embedding sets for this project yet. Create one on the <strong className="text-violet-300">Embeddings</strong> tab
                  to reuse across runs. (This form will build one inline for now.)
                </div>
              ) : (
                <>
                  <select
                    value={embeddingSetName}
                    onChange={(e) => setEmbeddingSetName(e.target.value)}
                    className="w-full bg-[#0f0f1a] border border-violet-700/50 rounded px-2 py-1.5 text-white text-sm"
                  >
                    <option value="">Build inline from the fields below (legacy)</option>
                    {embeddingSets.map((s) => (
                      <option key={s.name} value={s.name}>
                        {s.name} — {s.reportCount.toLocaleString()} reports · {s.model.replace('text-embedding-3-', '3-')}/{s.dim}d
                      </option>
                    ))}
                  </select>
                  {selectedSet && (
                    <EmbeddingSetDetails set={selectedSet} />
                  )}
                </>
              )}
            </div>

            <div>
              <label className="block text-xs text-gray-400 uppercase mb-1">Name</label>
              <input
                value={expName}
                onChange={(e) => patchForm({ expName: e.target.value })}
                className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-3 py-2 text-white text-sm"
                placeholder="my-experiment"
              />
              <div className="text-[10px] text-gray-500 mt-1">
                Experiment id: <code className="text-fuchsia-300">{expId}</code>
              </div>
            </div>

            <div className={selectedSet ? 'opacity-40 pointer-events-none' : ''}>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">Scope</label>
                <select value={scope} onChange={(e) => patchForm({ scope: e.target.value })}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm">
                  <option value="post-family">post-family (production)</option>
                  <option value="post-ast">post-AST</option>
                  <option value="active">active</option>
                  <option value="final-kept">final-kept</option>
                  <option value="post-collision">post-collision</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">Source filter</label>
                <select value={sourceFilter} onChange={(e) => patchForm({ sourceFilter: e.target.value })}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm">
                  <option value="all">All sources</option>
                  <option value="normal">SQL only</option>
                  <option value="cube">Cube only</option>
                  <option value="custom_sql_free_form">Free SQL only</option>
                  <option value="exclude-cube">Exclude cubes</option>
                </select>
              </div>
            </div>

            <div>
              <label className="block text-xs text-gray-400 uppercase mb-1">Fields embedded</label>
              <div className="grid grid-cols-2 gap-2 text-xs">
                {['name','path','owner','attributes','metrics','tables','filters','sql','normalizeSql'].map((k) => (
                  <label key={k} className="flex items-center gap-2 cursor-pointer text-gray-300">
                    <input type="checkbox" checked={!!fields[k]} onChange={() => toggleField(k)} />
                    <span>{k}</span>
                  </label>
                ))}
              </div>
              {fields.sql && (
                <div className="mt-2 flex items-center gap-2 text-xs text-gray-400">
                  <span>max SQL chars:</span>
                  <input type="number" min={100} max={8000} step={100}
                    value={Number(fields.maxSqlChars) || 2000}
                    onChange={(e) => patchForm({ fields: { ...fields, maxSqlChars: parseInt(e.target.value) || 2000 } })}
                    className="w-24 bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1 text-white" />
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">Model</label>
                <select value={model} onChange={(e) => {
                  const m = e.target.value;
                  patchForm({ model: m, ...(m.includes('small') ? { dim: 1536 } : {}) });
                }}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm">
                  <option value="text-embedding-3-large">3-large (3072d, higher quality)</option>
                  <option value="text-embedding-3-small">3-small (1536d, cheaper)</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">Dimensions</label>
                <select value={dim} onChange={(e) => patchForm({ dim: parseInt(e.target.value) })}
                  disabled={model.includes('small')}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm disabled:opacity-50">
                  {model.includes('small') ? (
                    <option value={1536}>1536</option>
                  ) : (
                    <>
                      <option value={3072}>3072</option>
                      <option value={1536}>1536 (reduced)</option>
                      <option value={768}>768 (reduced)</option>
                    </>
                  )}
                </select>
              </div>
            </div>
            </div>{/* end of `selectedSet` disable-wrapper — embedding fields only */}

            {/* Clustering knobs — always editable, even when a saved embedding is picked */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">
                  Cosine threshold: <span className="text-fuchsia-300 font-bold">{threshold.toFixed(2)}</span>
                </label>
                <input type="range" min={0.70} max={0.98} step={0.01}
                  value={threshold}
                  onChange={(e) => patchForm({ threshold: parseFloat(e.target.value) })}
                  className="w-full accent-fuchsia-500" />
              </div>
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">Min cluster size</label>
                <input type="number" min={2} value={minSize}
                  onChange={(e) => patchForm({ minSize: parseInt(e.target.value) || 2 })}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm" />
              </div>
            </div>

            <div>
              <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
                <input type="checkbox" checked={limit !== null}
                  onChange={(e) => patchForm({ limit: e.target.checked ? 200 : null })} />
                <span>Dry-run limit (cheap testing):</span>
                {limit !== null && (
                  <input type="number" min={10} value={limit}
                    onChange={(e) => patchForm({ limit: parseInt(e.target.value) || 200 })}
                    className="w-20 bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-0.5 text-white" />
                )}
              </label>
            </div>
          </div>

          <div className="mt-4 space-y-2">
            {/* Sidecar status pill */}
            <div className="flex items-center gap-2 text-xs">
              <span className={`w-2 h-2 rounded-full ${
                sidecarOk == null ? 'bg-gray-500' : sidecarOk ? 'bg-emerald-500' : 'bg-red-500'
              }`}></span>
              {sidecarOk == null ? (
                <span className="text-gray-500">checking sidecar…</span>
              ) : sidecarOk ? (
                <span className="text-emerald-300">
                  Sidecar connected · OpenAI key {apiKeySet ? 'set ✓' : 'MISSING ✗'}
                </span>
              ) : (
                <span className="text-red-300">
                  Sidecar not running. Start it: <code className="bg-[#0f0f1a] px-1 rounded text-emerald-300">python playground_server.py</code>
                </span>
              )}
            </div>

            <div className="flex gap-2">
              <button
                onClick={handleRun}
                disabled={running || sidecarOk === false || apiKeySet === false}
                className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:bg-fuchsia-900 disabled:text-gray-500 disabled:cursor-not-allowed text-white text-sm font-semibold px-5 py-2.5 rounded flex items-center gap-2"
                title={
                  sidecarOk === false ? 'Start the sidecar first'
                  : apiKeySet === false ? 'OPENAI_API_KEY missing on server'
                  : 'Run experiment now'
                }
              >
                {running ? (
                  <>
                    <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full"></span>
                    Running… <span className="text-xs font-normal opacity-80">({runElapsed}s)</span>
                  </>
                ) : (
                  <>▶ Run Experiment</>
                )}
              </button>
              <button
                onClick={handleRunDomain}
                disabled={running || sidecarOk === false || apiKeySet === false}
                className="bg-amber-600 hover:bg-amber-500 disabled:bg-amber-900 disabled:text-gray-500 disabled:cursor-not-allowed text-white text-sm font-semibold px-4 py-2.5 rounded flex items-center gap-2"
                title="Classify reports into a fixed list of business domains (Inventory, Purchase Orders, Sales, …) using GPT. Produces a domain-per-cluster experiment that can be Made Primary just like a cosine experiment."
              >
                🏷 Classify by Domain
              </button>
              <button onClick={loadList}
                className="bg-[#0f0f1a] hover:bg-[#1a2a4a] text-gray-300 text-sm font-semibold px-4 py-2 rounded border border-[#1e2d50]">
                ↻ Refresh list
              </button>
              <button
                onClick={() => {
                  if (running) return;
                  if (window.confirm('Reset the form to defaults? Current experiment values will be cleared.')) {
                    resetForm();
                    appendLog('info', 'Form reset to defaults.');
                  }
                }}
                disabled={running}
                className="bg-[#0f0f1a] hover:bg-[#1a2a4a] disabled:opacity-40 disabled:cursor-not-allowed text-gray-300 text-sm font-semibold px-4 py-2 rounded border border-[#1e2d50]"
                title="Reset form to defaults — use this after a completed run to start a fresh experiment without inheriting the last config"
              >
                🆕 New
              </button>
              <details className="ml-auto">
                <summary className="cursor-pointer text-xs text-gray-500 hover:text-gray-300 py-2">
                  View CLI equivalent
                </summary>
                <pre className="bg-[#0f0f1a] border border-[#1e2d50] text-emerald-300 text-[10px] font-mono p-2 rounded mt-1 max-h-40 overflow-auto">{`echo '${JSON.stringify(config)}' | python -m db.compute.playground -`}</pre>
              </details>
            </div>

            {runError && (
              <div className="bg-red-950/40 border border-red-700/50 text-red-200 text-xs p-3 rounded">
                <strong>Run failed:</strong> {runError}
              </div>
            )}

            {/* Step-by-step run log */}
            {runLog.length > 0 && (
              <div className="bg-[#0f0f1a] border border-fuchsia-700/40 rounded overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2 bg-fuchsia-950/30 border-b border-fuchsia-700/40">
                  <span className="text-[10px] uppercase tracking-wider text-fuchsia-300 font-bold">
                    Run log
                  </span>
                  <button onClick={clearLog} className="text-[10px] text-gray-500 hover:text-gray-300">
                    clear
                  </button>
                </div>
                <div className="max-h-48 overflow-y-auto font-mono text-[11px] p-2 space-y-0.5">
                  {runLog.map((e, i) => {
                    const cls = e.level === 'err' ? 'text-red-300'
                      : e.level === 'warn' ? 'text-amber-300'
                      : e.level === 'ok' ? 'text-emerald-300'
                      : 'text-gray-400';
                    return (
                      <div key={i} className="flex gap-2">
                        <span className="text-gray-600">{e.ts}</span>
                        <span className={cls}>{e.msg}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="text-[11px] text-gray-500 leading-relaxed">
              Clicking Run POSTs the config to the Python sidecar. It embeds (cached by content hash),
              clusters, and writes <code className="text-gray-400">public/data/_playground/{expId}.json</code>.
              Duration: ~5s if all embeddings cached, up to a few minutes for fresh embedding passes.
            </div>
          </div>
        </div>

        {/* Experiments list */}
        <div className="bg-[#16213e] p-5 rounded-lg border border-fuchsia-900/40">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-white font-bold">Saved Experiments</h3>
            <span className="text-xs text-gray-500">{experiments.length} total</span>
          </div>
          {/* Project-scope toggle */}
          <div className="flex items-center gap-2 mb-3 text-xs">
            <button
              onClick={() => setShowAllProjects(false)}
              className={`px-2.5 py-1 rounded border ${
                !showAllProjects
                  ? 'bg-fuchsia-900/50 text-fuchsia-200 border-fuchsia-700'
                  : 'bg-[#0f0f1a] text-gray-400 border-[#1e2d50] hover:bg-[#1a2a4a]'
              }`}
              title={`Only experiments run against ${projectName || '(no project)'}`}
            >
              This project only
            </button>
            <button
              onClick={() => setShowAllProjects(true)}
              className={`px-2.5 py-1 rounded border ${
                showAllProjects
                  ? 'bg-blue-900/50 text-blue-200 border-blue-700'
                  : 'bg-[#0f0f1a] text-gray-400 border-[#1e2d50] hover:bg-[#1a2a4a]'
              }`}
            >
              All projects
            </button>
            {currentShortId && !showAllProjects && (
              <span className="text-[10px] text-gray-500 ml-auto">
                filtering by <code className="text-gray-400">{currentShortId}</code>
              </span>
            )}
          </div>
          {loadingList ? (
            <div className="text-xs text-gray-500 py-4">Loading…</div>
          ) : experiments.length === 0 ? (
            <div className="text-xs text-gray-500 py-4">
              None yet for this project. Configure + run via the command on the left.
            </div>
          ) : (
            <div className="space-y-2 max-h-[60vh] overflow-y-auto">
              {activeExpId && (
                <div className="text-[11px] text-amber-300 bg-amber-950/30 border border-amber-700/40 rounded px-2 py-1.5 flex items-center gap-2">
                  <span>⭐</span>
                  <span className="flex-1 min-w-0 truncate">
                    Primary semantic clustering for <code className="text-amber-200">{currentShortId}</code> is
                    {' '}<strong className="text-amber-200">{activeExpId}</strong>. The Semantic Clusters tab now loads from this experiment.
                  </span>
                  <button onClick={handleClearPrimary}
                    className="text-[10px] text-amber-200 hover:text-white underline flex-shrink-0">
                    Revert to pipeline
                  </button>
                </div>
              )}
              {experiments.map((e) => {
                const isSelected = selectedExp?.id === e.id;
                const isActive = activeExpId === e.id;
                const canPromote = e.project === currentShortId; // same-project only
                const mode = (e.config as { mode?: string })?.mode;
                const isDomain = mode === 'domain';
                const isDensity = mode === 'density';
                const cfgEmbSet = (e.config as { embeddingSetName?: string })?.embeddingSetName;
                return (
                  <div key={e.id}
                    className={`p-3 rounded border-l-4 ${
                      isActive ? 'border-amber-400'
                        : isDomain ? 'border-sky-500'
                        : isDensity ? 'border-cyan-500'
                        : 'border-fuchsia-500'
                    } ${
                      isSelected ? 'bg-fuchsia-900/30 ring-1 ring-fuchsia-500' : 'bg-[#0f0f1a] hover:bg-[#1a2a4a]'
                    }`}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0 cursor-pointer" onClick={() => openExperiment(e.id)}>
                        <div className="text-sm text-white font-semibold truncate flex items-center gap-1.5">
                          {isActive && <span className="text-amber-300" title="Primary semantic clustering">⭐</span>}
                          {/* Clustering kind badge */}
                          {isDomain ? (
                            <span className="text-sky-300 text-[9px] font-bold bg-sky-950/60 border border-sky-700/60 rounded px-1 py-0.5" title="Business-domain classification experiment">🏷 DOMAIN</span>
                          ) : isDensity ? (
                            <span className="text-cyan-300 text-[9px] font-bold bg-cyan-950/60 border border-cyan-700/60 rounded px-1 py-0.5" title="UMAP + HDBSCAN density clustering">🔷 DENSITY</span>
                          ) : (
                            <span className="text-fuchsia-300 text-[9px] font-bold bg-fuchsia-950/60 border border-fuchsia-700/60 rounded px-1 py-0.5" title="Cosine similarity + union-find clustering">🔀 COSINE</span>
                          )}
                          <span className="truncate">{e.name}</span>
                        </div>
                        <div className="text-[11px] text-gray-500 font-mono truncate">{e.id}</div>
                        {/* Embedding source chip — the named set, else fields used inline */}
                        <div className="text-[10px] mt-0.5 flex flex-wrap items-center gap-1">
                          {cfgEmbSet ? (
                            <span className="text-violet-300 bg-violet-950/40 border border-violet-700/50 rounded px-1 py-0.5" title="This experiment used a saved embedding set (0 embedding cost beyond the set itself)">
                              🎯 embedding: <code className="text-violet-200">{cfgEmbSet}</code>
                            </span>
                          ) : (() => {
                            const cfg = (e.config as {
                              fields?: Record<string, boolean | number>;
                              scope?: string;
                              sourceFilter?: string;
                              model?: string;
                              dimensions?: number;
                            }) || {};
                            const activeFields = Object.entries(cfg.fields || {})
                              .filter(([, v]) => v === true)
                              .map(([k]) => k);
                            return (
                              <>
                                <span
                                  className="text-violet-300 bg-violet-900/30 border border-violet-700/40 rounded px-1 py-0.5"
                                  title="Embeddings built inline from these fields. Use the Embeddings tab to save a reusable set."
                                >
                                  🎯 inline:{' '}
                                  <code className="text-violet-200">
                                    {activeFields.length ? activeFields.join(',') : '(none)'}
                                  </code>
                                </span>
                                {cfg.model && (
                                  <span
                                    className="text-gray-500"
                                    title={`Model ${cfg.model} @ ${cfg.dimensions ?? 'default'} dims`}
                                  >
                                    {cfg.model.replace('text-embedding-3-', '').toUpperCase()}
                                    {cfg.dimensions ? `-${cfg.dimensions}` : ''}
                                  </span>
                                )}
                                {cfg.scope && (
                                  <span
                                    className="text-gray-500"
                                    title={`Scope: ${cfg.scope} · source filter: ${cfg.sourceFilter || 'all'}`}
                                  >
                                    · {cfg.scope}
                                    {cfg.sourceFilter && cfg.sourceFilter !== 'all' ? `/${cfg.sourceFilter}` : ''}
                                  </span>
                                )}
                              </>
                            );
                          })()}
                        </div>
                      </div>
                      <button
                        onClick={(ev) => { ev.stopPropagation(); handleDelete(e.id); }}
                        className="text-xs text-red-400 hover:text-red-300 flex-shrink-0"
                        title="Delete experiment"
                      >
                        ✕
                      </button>
                    </div>
                    <div className="text-xs text-gray-400 mt-1 flex flex-wrap gap-2 cursor-pointer" onClick={() => openExperiment(e.id)}>
                      <span>{e.config.scope}</span>
                      <span>· src:{e.config.sourceFilter}</span>
                      <span>· {e.config.model.replace('text-embedding-3-','3-')}/{e.config.dim}d</span>
                      <span>· thr {e.config.threshold.toFixed(2)}</span>
                    </div>
                    <div className="text-xs mt-1 flex items-center justify-between gap-2">
                      <div className="cursor-pointer flex-1 min-w-0" onClick={() => openExperiment(e.id)}>
                        <span className="text-emerald-300">{e.stats.multiClusters}</span>{' '}
                        <span className="text-gray-500">multi ·</span>{' '}
                        <span className="text-slate-300">{e.stats.singletons}</span>{' '}
                        <span className="text-gray-500">singletons ·</span>{' '}
                        <span className="text-amber-300">{e.stats.finalUnique}</span>{' '}
                        <span className="text-gray-500">unique</span>
                      </div>
                      {(() => {
                        // "Reviewed" only if the review actually produced
                        // labels. If llmReviewedAt is set but reviewed=0, the
                        // run failed (e.g. the model-param bug) — keep the
                        // button visible for a retry.
                        const reviewedCount = e.llmReviewSummary?.reviewed ?? 0;
                        const isReviewed = !!e.llmReviewedAt && reviewedCount > 0;
                        if (isReviewed) {
                          return (
                            <span
                              className="text-[10px] text-emerald-300 bg-emerald-950/30 border border-emerald-700/50 rounded px-1.5 py-0.5 flex-shrink-0 cursor-pointer hover:text-emerald-100"
                              onClick={(ev) => { ev.stopPropagation(); handleRunLlmReview(e.id); }}
                              title={`LLM-reviewed ${new Date(e.llmReviewedAt!).toLocaleString()} — ${reviewedCount} reviewed, ${e.llmReviewSummary?.removable ?? 0} removable. Click to re-run.`}
                            >
                              🤖 reviewed
                              {e.llmReviewSummary && (
                                <span className="text-emerald-200 ml-1 font-semibold">
                                  {' '}−{e.llmReviewSummary.removable}
                                </span>
                              )}
                            </span>
                          );
                        }
                        const hadAttempt = !!e.llmReviewedAt;
                        return (
                          <button
                            onClick={(ev) => { ev.stopPropagation(); handleRunLlmReview(e.id); }}
                            disabled={running}
                            className={`text-[10px] ${hadAttempt ? 'text-amber-300 hover:text-amber-100 border-amber-700/60 hover:border-amber-500' : 'text-indigo-300 hover:text-indigo-100 border-indigo-700/60 hover:border-indigo-500'} border disabled:opacity-40 disabled:cursor-not-allowed rounded px-1.5 py-0.5 flex-shrink-0`}
                            title={hadAttempt
                              ? 'Previous LLM review errored for every cluster — click to retry.'
                              : 'Run GPT LLM review on every multi-cluster in this experiment (caches per cluster — re-runs are free)'}
                          >
                            {hadAttempt ? '🤖 Retry LLM Review' : '🤖 LLM Review'}
                          </button>
                        );
                      })()}
                      {canPromote && (
                        isActive ? (
                          <span
                            className="text-[10px] font-bold text-amber-300 bg-amber-950/40 border border-amber-700/60 rounded px-2 py-0.5 flex-shrink-0"
                            title="This experiment is the primary semantic clustering for this project"
                          >
                            PRIMARY
                          </span>
                        ) : (
                          <button
                            onClick={(ev) => { ev.stopPropagation(); handleMakePrimary(e.id); }}
                            className="text-[10px] text-amber-300 hover:text-amber-100 border border-amber-700/50 hover:border-amber-500 rounded px-2 py-0.5 flex-shrink-0"
                            title="Use this experiment as the primary Semantic Clusters view for this project."
                          >
                            Make Primary
                          </button>
                        )
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Results pane */}
      {selectedExp && (
        <div className="bg-[#16213e] p-5 rounded-lg border border-fuchsia-700/50">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 className="text-lg text-white font-bold">{selectedExp.name}</h3>
              <div className="text-xs text-gray-500 font-mono">{selectedExp.id}</div>
            </div>
            <button onClick={closeExperiment}
              className="text-gray-400 hover:text-white text-xl leading-none">×</button>
          </div>

          <div className="grid grid-cols-5 gap-3 mb-4">
            {[
              ['Input reports', selectedExp.stats.inputReports, 'border-blue-500'],
              ['Embedded', selectedExp.stats.reportsEmbedded, 'border-blue-500'],
              ['Cosine edges', selectedExp.stats.edges, 'border-fuchsia-500'],
              ['Multi-clusters', selectedExp.stats.multiClusters, 'border-emerald-500'],
              ['Final unique', selectedExp.stats.finalUnique, 'border-amber-500'],
            ].map(([label, val, cls]) => (
              <div key={label as string} className={`bg-[#0f0f1a] p-3 rounded border-l-4 ${cls}`}>
                <div className="text-[10px] text-gray-400 uppercase">{label as string}</div>
                <div className="text-lg font-bold text-white">{(val as number).toLocaleString()}</div>
              </div>
            ))}
          </div>

          {/* Interactive 2D Graph View */}
          {selectedExp.viz && selectedExp.viz.scatter.length > 0 ? (
            <ExperimentGraphView exp={selectedExp} onClusterClick={setSelectedClusterId} />
          ) : (
            <div className="bg-[#0f0f1a] border border-amber-700/40 rounded-lg p-4 mb-4">
              <div className="text-sm font-bold text-amber-300 mb-1">📊 Graph View unavailable for this experiment</div>
              <div className="text-xs text-gray-400 leading-relaxed">
                This experiment was run before the Graph View feature was added, so it has no 2D projection or
                pairwise cosine data stored. Re-run it (same config) from the panel above and the new result will
                include the interactive graph. Embeddings are cached — it'll be fast.
              </div>
            </div>
          )}

          <div className="flex items-center justify-between mb-2">
            <div className="text-xs text-gray-400 uppercase">
              Clusters <span className="text-gray-600">({selectedExp.clusters.length} total · click any to expand)</span>
            </div>
            {selectedClusterId && (
              <button onClick={() => setSelectedClusterId(null)}
                className="text-xs text-gray-400 hover:text-white">
                Collapse all
              </button>
            )}
          </div>
          <div className="space-y-1 max-h-[60vh] overflow-y-auto pr-1">
            {selectedExp.clusters.slice(0, 200).map((c) => {
              const isOpen = selectedClusterId === c.id;
              return (
                <div key={c.id} className="bg-[#0f0f1a] rounded border-l-2 border-fuchsia-700/50 text-sm">
                  {/* Header row — clickable */}
                  <div
                    className="p-2 cursor-pointer hover:bg-[#1a2a4a]"
                    onClick={() => setSelectedClusterId(isOpen ? null : c.id)}
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-gray-500 text-xs w-3">{isOpen ? '▾' : '▸'}</span>
                      <span className="text-[10px] font-mono text-fuchsia-300">{c.id}</span>
                      <span className="text-xs font-semibold text-white">{c.size} reports</span>
                      {c.avgCosine != null && (
                        <span className="text-[11px] text-gray-500">
                          avg cos {c.avgCosine.toFixed(3)}
                          {c.minCosine != null && ` · min ${c.minCosine.toFixed(3)}`}
                        </span>
                      )}
                      <span className="text-[11px] text-gray-500">
                        {c.totalExecutions.toLocaleString()} execs
                      </span>
                    </div>
                    <div className="text-xs text-gray-300 truncate ml-6">{c.primaryName}</div>
                  </div>

                  {/* Expanded member list */}
                  {isOpen && (
                    <div className="border-t border-[#1e2d50] px-2 py-2 space-y-1">
                      <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">
                        All {c.memberIds.length} members (primary marked ✓)
                      </div>
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-[10px] text-gray-500 uppercase">
                            <th className="text-left py-1 px-1 w-20">Role</th>
                            <th className="text-left py-1 px-1">Name</th>
                            <th className="text-left py-1 px-1 w-14">Source</th>
                            <th className="text-right py-1 px-1">Execs</th>
                            <th className="text-right py-1 px-1">Users</th>
                            <th className="text-right py-1 px-1">A/M/T/F</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[
                            c.primaryReportId,
                            ...c.memberIds
                              .filter((mid) => mid !== c.primaryReportId)
                              .sort((a, b) => (reportById.get(b)?.executions ?? 0) - (reportById.get(a)?.executions ?? 0)),
                          ].map((mid) => {
                            const r = reportById.get(mid);
                            const isPrimary = mid === c.primaryReportId;
                            return (
                              <tr key={mid} className={`border-t border-[#1e2d50] ${isPrimary ? 'bg-amber-950/30' : ''}`}>
                                <td className="py-1 px-1 whitespace-nowrap">
                                  <div className="flex items-center gap-1 flex-wrap">
                                    {isPrimary ? (
                                      <span className="text-[9px] font-bold text-amber-300 bg-amber-950/50 border border-amber-700/60 rounded px-1 py-0.5">⭐ PRIMARY</span>
                                    ) : (
                                      <span className="text-[9px] text-gray-500">member</span>
                                    )}
                                    <FamilySiblingsIndicator siblings={r?.familySiblings} />
                                  </div>
                                </td>
                                <td className={`py-1 px-1 ${isPrimary ? 'text-amber-100 font-semibold' : 'text-gray-200'}`}>
                                  {r?.name || <span className="text-gray-600 font-mono">{mid.slice(0, 8)}…</span>}
                                  {r?.path && (
                                    <div className="text-[10px] text-gray-500 truncate max-w-[40vw]" title={r.path}>
                                      {r.path}
                                    </div>
                                  )}
                                </td>
                                <td className="py-1 px-1"><SourceTypePill sourceType={r?.sourceType} /></td>
                                <td className="py-1 px-1 text-right font-mono text-gray-400">
                                  {r?.executions?.toLocaleString() ?? '—'}
                                </td>
                                <td className="py-1 px-1 text-right font-mono text-gray-500">
                                  {r?.users ?? '—'}
                                </td>
                                <td className="py-1 px-1 text-right font-mono text-[10px] text-gray-500">
                                  {r ? `${r.attributeCount ?? 0}/${r.metricCount}/${r.tableCount}/${r.filterCount}` : '—'}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                      {c.memberIds.some((mid) => !reportById.get(mid)) && (
                        <div className="text-[10px] text-amber-400 mt-1">
                          Some members aren't in the current reports.json view (e.g., retired or different project).
                          Switch to the relevant project to see their full detail.
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {selectedExp.clusters.length > 200 && (
              <div className="text-[11px] text-gray-500 text-center py-2">
                Showing first 200 of {selectedExp.clusters.length} clusters. Use search/filter to narrow down.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// DENSITY CLUSTERING TAB — UMAP + HDBSCAN, separate from AI Playground
// ─────────────────────────────────────────────────────────────────────

function DensityClusteringTab({
  projectId, projectName, reportById, onActiveChanged,
}: {
  projectId: string;
  projectName: string;
  reportById: Map<string, ReportDetail>;
  onActiveChanged?: () => void;
}) {
  const currentShortId = PROJECT_NAME_TO_SHORT[projectName] || '';

  const DENSITY_DEFAULT_FORM = {
    name: 'density-v1',
    scope: 'post-family',
    sourceFilter: 'all',
    umapNeighbors: 30,
    umapMinDist: 0.0,
    umapComponents: 10,
    minClusterSize: 15,
    minSamples: 5,
    clusterSelection: 'eom' as 'eom' | 'leaf',
    limit: null as number | null,
    // What goes into the embedding text. Flipping these changes what
    // "similar" means — e.g., turn off SQL to cluster on business metadata
    // only, turn off name/path to cluster by SQL shape only.
    fields: {
      name: true,
      path: true,
      owner: false,
      attributes: true,
      metrics: true,
      tables: true,
      filters: true,
      sql: true,
      normalizeSql: true,
      maxSqlChars: 1500 as number,
    } as Record<string, boolean | number>,
  };
  const [form, setForm] = usePersistentState('density.form.v2', DENSITY_DEFAULT_FORM);
  const patch = (p: Partial<typeof form>) => setForm((f) => ({ ...f, ...p }));
  const resetForm = () => {
    setForm({ ...DENSITY_DEFAULT_FORM, fields: { ...DENSITY_DEFAULT_FORM.fields } });
  };

  // Defensive defaults — if a previous version of this tab was persisted
  // without `fields` (or any other new field), fall back to sensible values
  // rather than crashing the render with form.fields[k] on undefined.
  const DEFAULT_FIELDS: Record<string, boolean | number> = {
    name: true, path: true, owner: false,
    attributes: true, metrics: true, tables: true, filters: true,
    sql: true, normalizeSql: true, maxSqlChars: 1500,
  };
  const fields = form.fields ?? DEFAULT_FIELDS;
  // One-time self-heal: if state is missing fields, patch it in now.
  useEffect(() => {
    if (!form.fields) patch({ fields: DEFAULT_FIELDS });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const toggleField = (k: string) => patch({ fields: { ...fields, [k]: !fields[k] } });

  // Phase 2 — saved embedding sets the user can reference. When set, the
  // form's scope/fields/model/dim are ignored in favor of the set's values.
  const [embeddingSetName, setEmbeddingSetName] = usePersistentState<string>(
    'density.embeddingSetName', '',
  );
  const [embeddingSets, setEmbeddingSets] = useState<EmbeddingSet[]>([]);
  const selectedSet = embeddingSets.find((s) => s.name === embeddingSetName) || null;

  const [experiments, setExperiments] = useState<PlaygroundExperimentIndexEntry[]>([]);
  const [selectedExpId, setSelectedExpId] = usePersistentState<string | null>('density.selectedExpId', null);
  const [selectedExp, setSelectedExp] = useState<PlaygroundExperiment | null>(null);
  const [selectedClusterId, setSelectedClusterId] = useState<string | null>(null);
  const [activeTaskId, setActiveTaskId] = usePersistentState<string | null>('density.activeTaskId', null);
  const [activeTask, setActiveTask] = useState<PlaygroundTask | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null);
  const [activeExpId, setActiveExpId] = useState<string | null>(null);
  const [sidecarOk, setSidecarOk] = useState<boolean | null>(null);
  const [apiKeySet, setApiKeySet] = useState<boolean | null>(null);
  const pollRef = useRef<number | null>(null);

  type LogEntry = { ts: string; level: 'info' | 'ok' | 'warn' | 'err'; msg: string };
  const [runLog, setRunLog] = useState<LogEntry[]>([]);
  const appendLog = (level: LogEntry['level'], msg: string) => {
    const ts = new Date().toISOString().slice(11, 19);
    setRunLog((prev) => [...prev.slice(-199), { ts, level, msg }]);
  };
  const clearLog = () => setRunLog([]);

  const loadList = async () => {
    const idx = await fetchPlaygroundIndex();
    const forProject = currentShortId ? idx.filter((e) => e.project === currentShortId) : idx;
    // Only density experiments
    setExperiments(forProject.filter((e) => (e.config as { mode?: string })?.mode === 'density'));
  };
  const loadActive = async () => {
    if (!currentShortId) { setActiveExpId(null); return; }
    try {
      const info = await fetchActiveExperiment(currentShortId);
      setActiveExpId(info.expId);
    } catch {
      setActiveExpId(null);
    }
  };
  const loadEmbeddingSets = async () => {
    if (!currentShortId) { setEmbeddingSets([]); return; }
    try {
      const rows = await fetchEmbeddingSets(currentShortId);
      setEmbeddingSets(rows);
      if (embeddingSetName && !rows.find((r) => r.name === embeddingSetName)) {
        setEmbeddingSetName('');
      }
    } catch {
      setEmbeddingSets([]);
    }
  };

  useEffect(() => {
    loadList();
    loadActive();
    loadEmbeddingSets();
    fetchDensityDefaults().then((d) => {
      if (d) patch({
        umapNeighbors: d.umap_n_neighbors,
        umapMinDist: d.umap_min_dist,
        umapComponents: d.umap_n_components,
        minClusterSize: d.hdbscan_min_cluster_size,
        minSamples: d.hdbscan_min_samples,
        clusterSelection: d.hdbscan_cluster_selection_method as 'eom' | 'leaf',
      });
    });
    checkPlaygroundHealth().then((h) => {
      setSidecarOk(h !== null);
      setApiKeySet(h?.openai_key_set ?? null);
    });
    if (selectedExpId) {
      fetchPlaygroundExperiment(selectedExpId).then((e) => {
        if (e) setSelectedExp(e); else { setSelectedExpId(null); setSelectedExp(null); }
      });
    }
    if (activeTaskId) {
      fetchPlaygroundTask(activeTaskId).then((t) => { if (t) setActiveTask(t); });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, currentShortId]);

  useEffect(() => {
    if (!activeTask || activeTask.status !== 'running') {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    const tick = async () => {
      const t = await fetchPlaygroundTask(activeTask.id);
      if (!t) return;
      setActiveTask(t);
      if (t.status === 'completed') {
        const isLlmReview = (t as { mode?: string }).mode === 'llm_review';
        appendLog('ok', `✓ Task completed. Loading result…`);
        if (t.expId) {
          const e = await fetchPlaygroundExperiment(t.expId);
          if (e) { setSelectedExp(e); setSelectedExpId(e.id); }
          await loadList();
        }
        setActiveTask(null); setActiveTaskId(null); setRunStartedAt(null);
        // Reset the form after a successful density run so the user can
        // start a new experiment without inheriting prior config. Skip for
        // LLM-review tasks — those just annotate an existing experiment.
        if (!isLlmReview) {
          resetForm();
          appendLog('info', `Form reset — ready for a new experiment.`);
        }
      } else if (t.status === 'failed' || t.status === 'interrupted') {
        appendLog('err', `✗ Task ${t.status}: ${t.error || 'no detail'}`);
        setActiveTask(null); setActiveTaskId(null); setRunStartedAt(null);
        setRunError(t.error || `Task ${t.status}`);
      } else {
        appendLog('info', `poll: status=${t.status}`);
      }
    };
    pollRef.current = window.setInterval(tick, 2000);
    tick();
    return () => {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTask?.id, activeTask?.status]);

  const running = activeTask?.status === 'running';
  const runElapsed = runStartedAt ? Math.floor((Date.now() - runStartedAt) / 1000) : 0;
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setTick((x) => x + 1), 1000);
    return () => window.clearInterval(id);
  }, [running]);

  const slug = (form.name || 'density').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'density';
  const expId = `exp-density-${slug}-${form.scope}-${form.sourceFilter}-n${form.umapNeighbors}-m${form.minClusterSize}`;

  async function handleRun() {
    setRunError(null);
    setRunStartedAt(Date.now());
    clearLog();
    appendLog('info', `▶ Starting density clustering on project ${projectId || '(none)'}`);
    appendLog('info', `  UMAP: n_neighbors=${form.umapNeighbors}, min_dist=${form.umapMinDist}, n_components=${form.umapComponents}`);
    appendLog('info', `  HDBSCAN: min_cluster_size=${form.minClusterSize}, min_samples=${form.minSamples}, selection=${form.clusterSelection}`);
    appendLog('info', `POST /playground_api/density/run …`);
    try {
      const task = await runDensityClustering({
        id: expId,
        name: form.name,
        project: projectId,
        scope: form.scope,
        sourceFilter: form.sourceFilter,
        umapNeighbors: form.umapNeighbors,
        umapMinDist: form.umapMinDist,
        umapComponents: form.umapComponents,
        minClusterSize: form.minClusterSize,
        minSamples: form.minSamples,
        clusterSelection: form.clusterSelection,
        fields,
        ...(form.limit ? { limit: form.limit } : {}),
        ...(embeddingSetName ? { embeddingSetName } : {}),
      });
      if (task.status === 'failed' || task.status === 'interrupted') {
        appendLog('err', `✗ Task failed at creation: ${task.error || 'no detail'}`);
        setRunError(task.error || `Task ${task.status}`);
        setRunStartedAt(null);
        return;
      }
      appendLog('ok', `✓ Task queued: ${task.id}`);
      appendLog('info', `Polling every 2s… (UMAP + HDBSCAN take ~30-90s on ~4k reports)`);
      setActiveTask(task);
      setActiveTaskId(task.id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      appendLog('err', `✗ POST failed: ${msg}`);
      setRunError(msg);
      setRunStartedAt(null);
    }
  }

  async function openExperiment(id: string) {
    setSelectedExpId(id);
    const e = await fetchPlaygroundExperiment(id);
    if (e) setSelectedExp(e);
  }
  function closeExperiment() { setSelectedExp(null); setSelectedExpId(null); }
  async function handleDelete(id: string) {
    if (!window.confirm(`Delete "${id}"? This cannot be undone.`)) return;
    await deletePlaygroundExperiment(id);
    if (selectedExp?.id === id) closeExperiment();
    await loadList();
  }
  async function handleMakePrimary(id: string) {
    if (!currentShortId) return;
    try {
      await setActiveExperiment(currentShortId, id);
      setActiveExpId(id);
      onActiveChanged?.();
      appendLog('ok', `✓ "${id}" is now the primary clustering for ${currentShortId}.`);
      const entry = experiments.find((e) => e.id === id);
      if (!entry?.llmReviewedAt) {
        appendLog('info', `Experiment has no LLM reviews yet — starting review now.`);
        await handleRunLlmReview(id);
      }
    } catch (e) {
      appendLog('err', `✗ Could not promote: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  async function handleRunLlmReview(id: string) {
    if (!projectId) return;
    try {
      appendLog('info', `POST /playground_api/exp/${id}/llm_review …`);
      const task = await runExperimentLlmReview(id, projectId);
      if (task.status === 'failed' || task.status === 'interrupted') {
        appendLog('err', `✗ LLM review failed at creation: ${task.error || 'no detail'}`);
        return;
      }
      appendLog('ok', `✓ LLM review queued: ${task.id}`);
      setActiveTask(task);
      setActiveTaskId(task.id);
      setRunStartedAt(Date.now());
    } catch (e) {
      appendLog('err', `✗ LLM review POST failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  async function handleClearPrimary() {
    if (!currentShortId) return;
    await clearActiveExperiment(currentShortId);
    setActiveExpId(null);
    onActiveChanged?.();
    appendLog('info', `Reverted ${currentShortId} to the pipeline's clustering.`);
  }

  return (
    <div className="space-y-4">
      <div className="bg-gradient-to-r from-cyan-950/60 to-[#16213e] p-4 rounded-lg border border-cyan-800/40">
        <div className="text-xs uppercase tracking-wider text-cyan-300 font-bold mb-2">
          Density Clustering — UMAP + HDBSCAN
        </div>
        <p className="text-xs text-gray-400 leading-relaxed">
          An alternative to the cosine-threshold Playground, tuned for ~1k–10k reports. <strong className="text-cyan-200">UMAP</strong> projects
          OpenAI embeddings to ~10 dims (nonlinear, preserves local structure), then <strong className="text-cyan-200">HDBSCAN</strong> finds
          dense regions. It auto-picks the number of clusters and explicitly labels reports that don't belong anywhere as <em>noise</em>
          (instead of forcing them into a weak cluster). Expected on ~4k reports: ~60–120 clusters, ~5–10% noise, silhouette &gt; 0.35.
          Embeddings are cached with the AI Playground, so re-runs that only change UMAP/HDBSCAN params are fast.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-[#16213e] p-5 rounded-lg border border-cyan-900/40">
          <h3 className="text-white font-bold mb-3">Configure Density Run</h3>
          <div className="space-y-3 text-sm">
            {/* Phase 2: pick a saved embedding set */}
            <div className="p-2 rounded border border-violet-800/40 bg-violet-950/20">
              <label className="block text-[10px] uppercase tracking-wider text-violet-300 font-bold mb-1">
                Embedding source
              </label>
              {embeddingSets.length === 0 ? (
                <div className="text-[11px] text-gray-500">
                  No saved embedding sets. Create one on the <strong className="text-violet-300">Embeddings</strong> tab
                  to reuse across Density and Semantic runs.
                </div>
              ) : (
                <>
                  <select value={embeddingSetName}
                    onChange={(e) => setEmbeddingSetName(e.target.value)}
                    className="w-full bg-[#0f0f1a] border border-violet-700/50 rounded px-2 py-1.5 text-white text-sm">
                    <option value="">Build inline from the fields below</option>
                    {embeddingSets.map((s) => (
                      <option key={s.name} value={s.name}>
                        {s.name} — {s.reportCount.toLocaleString()} reports · {s.model.replace('text-embedding-3-', '3-')}/{s.dim}d
                      </option>
                    ))}
                  </select>
                  {selectedSet && (
                    <EmbeddingSetDetails set={selectedSet} trailer="UMAP + HDBSCAN only" />
                  )}
                </>
              )}
            </div>

            <div>
              <label className="block text-xs text-gray-400 uppercase mb-1">Name</label>
              <input value={form.name} onChange={(e) => patch({ name: e.target.value })}
                className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-3 py-2 text-white text-sm" />
              <div className="text-[10px] text-gray-500 mt-1">ID: <code className="text-cyan-300">{expId}</code></div>
            </div>
            <div className={selectedSet ? 'opacity-40 pointer-events-none space-y-3' : 'space-y-3'}>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">Scope</label>
                <select value={form.scope} onChange={(e) => patch({ scope: e.target.value })}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm">
                  <option value="post-family">post-family</option>
                  <option value="post-ast">post-AST</option>
                  <option value="active">active</option>
                  <option value="final-kept">final-kept</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-400 uppercase mb-1">Source Filter</label>
                <select value={form.sourceFilter} onChange={(e) => patch({ sourceFilter: e.target.value })}
                  className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm">
                  <option value="all">all</option>
                  <option value="normal">SQL only</option>
                  <option value="exclude-cube">exclude cubes</option>
                  <option value="cube">cubes only</option>
                </select>
              </div>
            </div>

            <div className="pt-2 border-t border-[#1e2d50]">
              <div className="flex items-center justify-between mb-2">
                <div className="text-[10px] uppercase tracking-wider text-cyan-400 font-bold">
                  Fields embedded <span className="text-gray-500 font-normal normal-case">(signals the LLM uses to decide "similar")</span>
                </div>
                <div className="flex gap-1">
                  <button type="button"
                    onClick={() => patch({ fields: { ...fields, name: true, path: true, owner: false, attributes: true, metrics: true, tables: true, filters: true, sql: true, normalizeSql: true } })}
                    className="text-[9px] text-gray-400 hover:text-white border border-[#1e2d50] rounded px-1.5 py-0.5">
                    all
                  </button>
                  <button type="button"
                    onClick={() => patch({ fields: { ...fields, name: true, path: true, attributes: true, metrics: true, tables: true, filters: true, sql: false } })}
                    className="text-[9px] text-gray-400 hover:text-white border border-[#1e2d50] rounded px-1.5 py-0.5"
                    title="Business metadata only — cluster by what the report is, not how it fetches data">
                    metadata only
                  </button>
                  <button type="button"
                    onClick={() => patch({ fields: { ...fields, name: false, path: false, owner: false, attributes: false, metrics: false, tables: true, filters: false, sql: true, normalizeSql: true } })}
                    className="text-[9px] text-gray-400 hover:text-white border border-[#1e2d50] rounded px-1.5 py-0.5"
                    title="SQL shape only — cluster by query structure regardless of naming">
                    SQL only
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-1.5 text-xs">
                {[
                  ['name', 'Report name'],
                  ['path', 'Folder path'],
                  ['owner', 'Owner'],
                  ['attributes', 'Attributes'],
                  ['metrics', 'Metrics'],
                  ['tables', 'Tables'],
                  ['filters', 'Filters'],
                  ['sql', 'SQL text'],
                  ['normalizeSql', 'Normalize SQL'],
                ].map(([k, label]) => (
                  <label key={k} className="flex items-center gap-1.5 cursor-pointer text-gray-300 hover:text-white">
                    <input type="checkbox" checked={!!fields[k]} onChange={() => toggleField(k)} />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
              {fields.sql && (
                <div className="mt-2 flex items-center gap-2 text-xs text-gray-400">
                  <span>max SQL chars:</span>
                  <input type="number" min={100} max={8000} step={100}
                    value={Number(fields.maxSqlChars) || 1500}
                    onChange={(e) => patch({ fields: { ...fields, maxSqlChars: parseInt(e.target.value) || 1500 } })}
                    className="w-24 bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1 text-white" />
                </div>
              )}
              <div className="mt-2 text-[10px] text-gray-500 leading-snug">
                {(() => {
                  const on = Object.entries(fields).filter(([k, v]) => typeof v === 'boolean' && v && k !== 'normalizeSql').map(([k]) => k);
                  if (on.length === 0) return 'No fields selected — classifier will see only report IDs (useless). Turn on at least one.';
                  return `Embedding will see: ${on.join(', ')}.`;
                })()}
              </div>
            </div>
            </div>{/* end of `selectedSet` disable-wrapper — embedding fields only */}

            {/* Clustering knobs — always editable, even when a saved embedding is picked */}
            <div className="pt-2 border-t border-[#1e2d50]">
              <div className="text-[10px] uppercase tracking-wider text-cyan-400 font-bold mb-2">UMAP (dimensionality reduction)</div>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="block text-[10px] text-gray-400 mb-1" title="How many neighbors define the local neighborhood. Higher = more global structure.">n_neighbors</label>
                  <input type="number" min={5} max={200} value={form.umapNeighbors}
                    onChange={(e) => patch({ umapNeighbors: parseInt(e.target.value) || 30 })}
                    className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm" />
                </div>
                <div>
                  <label className="block text-[10px] text-gray-400 mb-1" title="Minimum distance between points in reduced space. 0.0 = tight clusters.">min_dist</label>
                  <input type="number" min={0} max={1} step={0.05} value={form.umapMinDist}
                    onChange={(e) => patch({ umapMinDist: parseFloat(e.target.value) || 0 })}
                    className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm" />
                </div>
                <div>
                  <label className="block text-[10px] text-gray-400 mb-1" title="Dimensionality for HDBSCAN input (not the 2D viz).">n_components</label>
                  <input type="number" min={2} max={50} value={form.umapComponents}
                    onChange={(e) => patch({ umapComponents: parseInt(e.target.value) || 10 })}
                    className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm" />
                </div>
              </div>
            </div>

            <div className="pt-2 border-t border-[#1e2d50]">
              <div className="text-[10px] uppercase tracking-wider text-cyan-400 font-bold mb-2">HDBSCAN (density clustering)</div>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="block text-[10px] text-gray-400 mb-1" title="Smallest group to call a cluster. Lower = more, smaller clusters + fewer noise points.">min_cluster_size</label>
                  <input type="number" min={2} max={200} value={form.minClusterSize}
                    onChange={(e) => patch({ minClusterSize: parseInt(e.target.value) || 15 })}
                    className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm" />
                </div>
                <div>
                  <label className="block text-[10px] text-gray-400 mb-1" title="Required neighbors for a point to be 'core'. Higher = more conservative, more noise.">min_samples</label>
                  <input type="number" min={1} max={50} value={form.minSamples}
                    onChange={(e) => patch({ minSamples: parseInt(e.target.value) || 5 })}
                    className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm" />
                </div>
                <div>
                  <label className="block text-[10px] text-gray-400 mb-1" title="eom = Excess of Mass (balanced). leaf = finer-grained clusters.">selection</label>
                  <select value={form.clusterSelection} onChange={(e) => patch({ clusterSelection: e.target.value as 'eom' | 'leaf' })}
                    className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded px-2 py-1.5 text-white text-sm">
                    <option value="eom">eom</option>
                    <option value="leaf">leaf</option>
                  </select>
                </div>
              </div>
            </div>

            <div className="text-[11px] text-gray-500 pt-2">
              {sidecarOk === null ? 'Checking sidecar…' : sidecarOk
                ? <span className="text-emerald-300">Sidecar connected · OpenAI key {apiKeySet ? 'set ✓' : 'MISSING ✗'}</span>
                : <span className="text-red-300">Sidecar not running. Start with <code className="bg-[#0f0f1a] px-1 rounded">python playground_server.py</code></span>}
            </div>
            <div className="flex gap-2">
              <button onClick={handleRun}
                disabled={running || sidecarOk === false || apiKeySet === false}
                className="bg-cyan-600 hover:bg-cyan-500 disabled:bg-cyan-900 disabled:text-gray-500 disabled:cursor-not-allowed text-white text-sm font-semibold px-5 py-2.5 rounded flex items-center gap-2">
                {running ? (
                  <><span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full"></span>
                  Running… <span className="text-xs font-normal opacity-80">({runElapsed}s)</span></>
                ) : <>▶ Run Density Clustering</>}
              </button>
              <button onClick={loadList}
                className="bg-[#0f0f1a] hover:bg-[#1a2a4a] text-gray-300 text-sm font-semibold px-4 py-2 rounded border border-[#1e2d50]">
                ↻ Refresh
              </button>
              <button
                onClick={() => {
                  if (running) return;
                  if (window.confirm('Reset the form to defaults? Current values will be cleared.')) {
                    resetForm();
                    appendLog('info', 'Form reset to defaults.');
                  }
                }}
                disabled={running}
                className="bg-[#0f0f1a] hover:bg-[#1a2a4a] disabled:opacity-40 disabled:cursor-not-allowed text-gray-300 text-sm font-semibold px-4 py-2 rounded border border-[#1e2d50]"
                title="Reset form to defaults — use after a completed run to start a fresh experiment"
              >
                🆕 New
              </button>
            </div>
            {runError && (
              <div className="bg-red-950/40 border border-red-700/50 text-red-200 text-xs p-3 rounded">
                <strong>Run failed:</strong> {runError}
              </div>
            )}
            {runLog.length > 0 && (
              <div className="bg-[#0f0f1a] border border-cyan-700/40 rounded max-h-48 overflow-auto p-2 text-[10px] font-mono">
                {runLog.map((l, i) => (
                  <div key={i} className={
                    l.level === 'err' ? 'text-red-300' :
                    l.level === 'warn' ? 'text-amber-300' :
                    l.level === 'ok' ? 'text-emerald-300' : 'text-gray-400'
                  }>
                    <span className="text-gray-600">{l.ts}</span> {l.msg}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="bg-[#16213e] p-5 rounded-lg border border-cyan-900/40">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-white font-bold">Past Density Runs</h3>
            <span className="text-[10px] text-gray-500">project: <code>{currentShortId || '—'}</code></span>
          </div>
          {experiments.length === 0 ? (
            <div className="text-xs text-gray-500 py-4">
              No density runs for this project yet. Configure + run on the left.
            </div>
          ) : (
            <div className="space-y-2 max-h-[60vh] overflow-y-auto">
              {activeExpId && (
                <div className="text-[11px] text-amber-300 bg-amber-950/30 border border-amber-700/40 rounded px-2 py-1.5 flex items-center gap-2">
                  <span>⭐</span>
                  <span className="flex-1 min-w-0 truncate">
                    Primary for <code className="text-amber-200">{currentShortId}</code>: <strong>{activeExpId}</strong>
                  </span>
                  <button onClick={handleClearPrimary}
                    className="text-[10px] text-amber-200 hover:text-white underline flex-shrink-0">Revert</button>
                </div>
              )}
              {experiments.map((e) => {
                const isSelected = selectedExp?.id === e.id;
                const isActive = activeExpId === e.id;
                const s = e.stats as { silhouette?: number; noisePct?: number; avgClusterSize?: number; multiClusters: number; singletons: number; finalUnique: number };
                return (
                  <div key={e.id}
                    className={`p-3 rounded border-l-4 ${
                      isActive ? 'border-amber-400' : 'border-cyan-500'
                    } ${isSelected ? 'bg-cyan-900/30 ring-1 ring-cyan-500' : 'bg-[#0f0f1a] hover:bg-[#1a2a4a]'}`}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0 cursor-pointer" onClick={() => openExperiment(e.id)}>
                        <div className="text-sm text-white font-semibold truncate flex items-center gap-1.5">
                          {isActive && <span className="text-amber-300" title="Primary">⭐</span>}
                          <span className="text-[9px] font-bold text-cyan-300 bg-cyan-950/60 border border-cyan-700/60 rounded px-1 py-0.5" title="UMAP + HDBSCAN density clustering">🔷 DENSITY</span>
                          <span className="truncate">{e.name}</span>
                        </div>
                        <div className="text-[11px] text-gray-500 font-mono truncate">{e.id}</div>
                        {/* Embedding source chip — the named set, else fields used inline */}
                        <div className="text-[10px] mt-0.5 flex flex-wrap items-center gap-1">
                          {(() => {
                            const cfg = (e.config as {
                              embeddingSetName?: string;
                              fields?: Record<string, boolean | number>;
                              scope?: string;
                              sourceFilter?: string;
                              model?: string;
                              dimensions?: number;
                            }) || {};
                            if (cfg.embeddingSetName) {
                              return (
                                <span className="text-violet-300 bg-violet-950/40 border border-violet-700/50 rounded px-1 py-0.5" title="This run used a saved embedding set">
                                  🎯 embedding: <code className="text-violet-200">{cfg.embeddingSetName}</code>
                                </span>
                              );
                            }
                            const activeFields = Object.entries(cfg.fields || {})
                              .filter(([, v]) => v === true)
                              .map(([k]) => k);
                            return (
                              <>
                                <span
                                  className="text-violet-300 bg-violet-900/30 border border-violet-700/40 rounded px-1 py-0.5"
                                  title="Embeddings built inline from these fields. Use the Embeddings tab to save a reusable set."
                                >
                                  🎯 inline:{' '}
                                  <code className="text-violet-200">
                                    {activeFields.length ? activeFields.join(',') : '(none)'}
                                  </code>
                                </span>
                                {cfg.model && (
                                  <span className="text-gray-500" title={`Model ${cfg.model} @ ${cfg.dimensions ?? 'default'} dims`}>
                                    {cfg.model.replace('text-embedding-3-', '').toUpperCase()}
                                    {cfg.dimensions ? `-${cfg.dimensions}` : ''}
                                  </span>
                                )}
                                {cfg.scope && (
                                  <span className="text-gray-500" title={`Scope: ${cfg.scope} · source filter: ${cfg.sourceFilter || 'all'}`}>
                                    · {cfg.scope}
                                    {cfg.sourceFilter && cfg.sourceFilter !== 'all' ? `/${cfg.sourceFilter}` : ''}
                                  </span>
                                )}
                              </>
                            );
                          })()}
                        </div>
                      </div>
                      <button onClick={(ev) => { ev.stopPropagation(); handleDelete(e.id); }}
                        className="text-xs text-red-400 hover:text-red-300 flex-shrink-0">✕</button>
                    </div>
                    <div className="text-xs mt-1 flex items-center justify-between gap-2">
                      <div className="cursor-pointer flex-1 min-w-0" onClick={() => openExperiment(e.id)}>
                        <span className="text-emerald-300">{s.multiClusters}</span>{' '}
                        <span className="text-gray-500">clusters ·</span>{' '}
                        <span className="text-slate-300">{s.singletons}</span>{' '}
                        <span className="text-gray-500">noise ·</span>{' '}
                        {s.silhouette != null && (
                          <>
                            <span className={s.silhouette >= 0.35 ? 'text-emerald-300' : s.silhouette >= 0.2 ? 'text-amber-300' : 'text-red-300'}>
                              silhouette {s.silhouette.toFixed(2)}
                            </span>{' '}
                            <span className="text-gray-500">·</span>{' '}
                          </>
                        )}
                        {s.avgClusterSize != null && (
                          <span className="text-gray-500">avg {s.avgClusterSize}/cluster</span>
                        )}
                      </div>
                      {e.llmReviewedAt ? (
                        <span
                          className="text-[10px] text-emerald-300 bg-emerald-950/30 border border-emerald-700/50 rounded px-1.5 py-0.5 flex-shrink-0"
                          title={`LLM-reviewed ${new Date(e.llmReviewedAt).toLocaleString()}${e.llmReviewSummary ? ` — ${e.llmReviewSummary.reviewed} reviewed, ${e.llmReviewSummary.removable} removable` : ''}`}
                        >
                          🤖 reviewed
                          {e.llmReviewSummary && (
                            <span className="text-emerald-200 ml-1 font-semibold">
                              {' '}−{e.llmReviewSummary.removable}
                            </span>
                          )}
                        </span>
                      ) : (
                        <button onClick={(ev) => { ev.stopPropagation(); handleRunLlmReview(e.id); }}
                          className="text-[10px] text-indigo-300 hover:text-indigo-100 border border-indigo-700/60 hover:border-indigo-500 rounded px-1.5 py-0.5 flex-shrink-0"
                          title="Run GPT LLM review on every multi-cluster in this experiment">
                          🤖 LLM Review
                        </button>
                      )}
                      {isActive ? (
                        <span className="text-[10px] font-bold text-amber-300 bg-amber-950/40 border border-amber-700/60 rounded px-2 py-0.5 flex-shrink-0">PRIMARY</span>
                      ) : (
                        <button onClick={(ev) => { ev.stopPropagation(); handleMakePrimary(e.id); }}
                          className="text-[10px] text-amber-300 hover:text-amber-100 border border-amber-700/50 hover:border-amber-500 rounded px-2 py-0.5 flex-shrink-0"
                          title="Use this as primary.">
                          Make Primary
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {selectedExp && (
        <div className="bg-[#16213e] p-5 rounded-lg border border-cyan-700/50">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 className="text-lg text-white font-bold">{selectedExp.name}</h3>
              <div className="text-xs text-gray-500 font-mono">{selectedExp.id}</div>
            </div>
            <button onClick={closeExperiment} className="text-gray-400 hover:text-white text-xl leading-none">×</button>
          </div>

          <div className="grid grid-cols-6 gap-3 mb-4">
            {(() => {
              const s = selectedExp.stats as { inputReports: number; multiClusters: number; singletons: number; noisePct?: number; avgClusterSize?: number; silhouette?: number };
              const cards: Array<[string, string | number, string]> = [
                ['Input reports', s.inputReports?.toLocaleString() ?? '—', 'border-blue-500'],
                ['Clusters', s.multiClusters?.toLocaleString() ?? '—', 'border-emerald-500'],
                ['Noise points', s.singletons?.toLocaleString() ?? '—', 'border-slate-500'],
                ['Noise %', s.noisePct != null ? `${(s.noisePct * 100).toFixed(1)}%` : '—', 'border-amber-500'],
                ['Avg cluster size', s.avgClusterSize?.toLocaleString() ?? '—', 'border-cyan-500'],
                ['Silhouette', s.silhouette != null ? s.silhouette.toFixed(3) : '—',
                  s.silhouette != null && s.silhouette >= 0.35 ? 'border-emerald-500' :
                  s.silhouette != null && s.silhouette >= 0.2 ? 'border-amber-500' : 'border-red-500'],
              ];
              return cards.map(([label, val, cls]) => (
                <div key={label} className={`bg-[#0f0f1a] p-3 rounded border-l-4 ${cls}`}>
                  <div className="text-[10px] text-gray-400 uppercase">{label}</div>
                  <div className="text-lg font-bold text-white">{val}</div>
                </div>
              ));
            })()}
          </div>

          {selectedExp.viz && selectedExp.viz.scatter.length > 0 && (
            <ExperimentGraphView exp={selectedExp} onClusterClick={setSelectedClusterId} />
          )}

          <div className="text-xs text-gray-400 uppercase mb-2">
            Clusters <span className="text-gray-600">({selectedExp.clusters.length} · click any to expand)</span>
          </div>
          <div className="space-y-1 max-h-[60vh] overflow-y-auto pr-1">
            {selectedExp.clusters.slice(0, 200).map((c) => {
              const isOpen = selectedClusterId === c.id;
              const primaryRec = reportById.get(c.primaryReportId);
              // Pin the primary at the top; sort the rest by executions desc.
              const ordered = [
                c.primaryReportId,
                ...c.memberIds
                  .filter((mid) => mid !== c.primaryReportId)
                  .sort((a, b) => (reportById.get(b)?.executions ?? 0) - (reportById.get(a)?.executions ?? 0)),
              ];
              return (
                <div key={c.id} className="bg-[#0f0f1a] rounded border-l-2 border-cyan-700/50 text-sm">
                  <div className="p-2 cursor-pointer hover:bg-[#1a2a4a]"
                    onClick={() => setSelectedClusterId(isOpen ? null : c.id)}>
                    <div className="flex items-center gap-3">
                      <span className="text-gray-500 text-xs w-3">{isOpen ? '▾' : '▸'}</span>
                      <span className="font-mono text-cyan-300 text-xs w-14">{c.id}</span>
                      <span className="text-[9px] font-bold text-amber-300 bg-amber-950/40 border border-amber-700/50 rounded px-1 py-0.5 flex-shrink-0" title="Primary / keep-candidate report of this cluster (highest executions)">
                        ⭐ PRIMARY
                      </span>
                      <FamilySiblingsIndicator siblings={reportById.get(c.primaryReportId)?.familySiblings} />
                      <span className="text-white truncate flex-1" title={c.primaryReportId}>{c.primaryName || c.primaryReportId || '—'}</span>
                      <span className="text-gray-400 text-xs whitespace-nowrap">
                        {c.size} reports · {c.totalExecutions.toLocaleString()} execs
                      </span>
                    </div>
                  </div>
                  {isOpen && (
                    <div className="px-4 pb-3 text-xs">
                      <div className="text-gray-500 mb-2">
                        The <strong className="text-amber-300">⭐ primary</strong> is the keep-candidate — the highest-executions
                        report in the cluster. The other {Math.max(0, c.size - 1)} are consolidation candidates.
                      </div>
                      <div className="grid grid-cols-[auto_1fr_auto] gap-x-3 gap-y-0.5">
                        <div className="text-[10px] uppercase text-gray-600 font-semibold">Role</div>
                        <div className="text-[10px] uppercase text-gray-600 font-semibold">Report</div>
                        <div className="text-[10px] uppercase text-gray-600 font-semibold text-right">Execs</div>
                        {ordered.slice(0, 50).map((mid) => {
                          const r = reportById.get(mid);
                          const isPrimary = mid === c.primaryReportId;
                          return (
                            <div key={mid} className={`contents ${isPrimary ? 'font-semibold' : ''}`}>
                              <div className={`text-[10px] whitespace-nowrap flex items-center gap-1 ${isPrimary ? 'text-amber-300' : 'text-gray-600'}`}>
                                {isPrimary ? '⭐ PRIMARY' : '• member'}
                                <FamilySiblingsIndicator siblings={r?.familySiblings} />
                              </div>
                              <div className="truncate">
                                <span className={isPrimary ? 'text-amber-100' : 'text-gray-300'} title={mid}>
                                  {r?.name || mid}
                                </span>
                                {r?.path && <span className="text-gray-600 ml-2">{r.path}</span>}
                              </div>
                              <div className={`font-mono text-right ${isPrimary ? 'text-amber-200' : 'text-gray-500'}`}>
                                {r?.executions?.toLocaleString() ?? '—'}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                      {c.size > 50 && (
                        <div className="text-[10px] text-gray-500 mt-2">
                          Showing first 50 of {c.size} members.
                        </div>
                      )}
                      {!primaryRec && c.primaryReportId && (
                        <div className="text-[10px] text-amber-400 mt-2">
                          Primary report {c.primaryReportId} isn't in the current reports.json view — may be filtered/retired.
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// EXPERIMENT GRAPH VIEW — 2D scatter + threshold slider + hover tooltip
// ─────────────────────────────────────────────────────────────────────

// Deterministic pastel color derived from a cluster id string so dots from
// the same cluster match across renders. Singletons get a muted gray.
function _clusterColor(cid: string | null | undefined): string {
  if (!cid) return '#475569'; // slate-600 for singletons
  let h = 0;
  for (let i = 0; i < cid.length; i++) {
    h = ((h << 5) - h) + cid.charCodeAt(i);
    h = h & h;
  }
  const hue = Math.abs(h) % 360;
  return `hsl(${hue}, 65%, 60%)`;
}

function ExperimentGraphView({
  exp,
  onClusterClick,
}: {
  exp: PlaygroundExperiment;
  onClusterClick: (id: string) => void;
}) {
  const viz = exp.viz!;
  const runThreshold = viz.thresholdUsed;

  const [liveThreshold, setLiveThreshold] = useState<number>(runThreshold);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [hoverXY, setHoverXY] = useState<{ x: number; y: number } | null>(null);
  const [selectedClusterViz, setSelectedClusterViz] = useState<string | null>(null);
  const [showEdges, setShowEdges] = useState(true);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const WIDTH = 720;
  const HEIGHT = 420;
  const PAD = 20;
  const toX = (x: number) => PAD + x * (WIDTH - 2 * PAD);
  const toY = (y: number) => PAD + y * (HEIGHT - 2 * PAD);

  // Recompute cluster assignment + edges at the live threshold.
  // When the slider is at the run threshold we trust the backend's
  // authoritative assignments (from `scatter[].clusterId`). topPairs is a
  // truncated sample, so re-running union-find on it produces wildly wrong
  // cluster counts — e.g. for experiments with >>50k edges, the sample keeps
  // only the strongest cosines and misses the marginal pairs that perform
  // most merges.
  const atRunThreshold = Math.abs(liveThreshold - runThreshold) < 1e-6;

  const { clusterByRid, multiCount, singletonCount, edgeCount, edgesToDraw, isApproximate } = useMemo(() => {
    const scatterMap = new Map(viz.scatter.map((p) => [p.id, p]));

    if (atRunThreshold) {
      // Trust backend. Use stored clusterId per scatter point.
      const map = new Map<string, string | null>();
      const rootSize = new Map<string, number>();
      for (const p of viz.scatter) {
        map.set(p.id, p.clusterId ?? null);
        if (p.clusterId) {
          rootSize.set(p.clusterId, (rootSize.get(p.clusterId) || 0) + 1);
        }
      }
      let singles = 0;
      for (const p of viz.scatter) if (!p.clusterId) singles++;
      // Edge draw list (just a visual aid; capped)
      const edgesFull: Array<[PlaygroundVizPoint, PlaygroundVizPoint, number]> = [];
      for (const [a, b, s] of viz.topPairs) {
        if (s < liveThreshold) continue;
        const pa = scatterMap.get(a), pb = scatterMap.get(b);
        if (pa && pb && edgesFull.length < 4000) edgesFull.push([pa, pb, s]);
        if (edgesFull.length >= 4000) break;
      }
      return {
        clusterByRid: map,
        multiCount: rootSize.size,
        singletonCount: singles,
        edgeCount: exp.stats.edges ?? viz.topPairs.length,
        edgesToDraw: edgesFull,
        isApproximate: false,
      };
    }

    // Live re-cluster on truncated topPairs (approximation — flag it).
    const parent = new Map<string, string>();
    viz.scatter.forEach((p) => parent.set(p.id, p.id));
    const find = (x: string): string => {
      let r = x;
      while (parent.get(r)! !== r) r = parent.get(r)!;
      let cur = x;
      while (parent.get(cur)! !== r) {
        const next = parent.get(cur)!;
        parent.set(cur, r);
        cur = next;
      }
      return r;
    };
    const union = (a: string, b: string) => {
      const ra = find(a), rb = find(b);
      if (ra !== rb) parent.set(ra, rb);
    };
    let edgeN = 0;
    const edgesFull: Array<[PlaygroundVizPoint, PlaygroundVizPoint, number]> = [];
    for (const [a, b, s] of viz.topPairs) {
      if (s < liveThreshold) continue;
      union(a, b);
      edgeN++;
      const pa = scatterMap.get(a), pb = scatterMap.get(b);
      if (pa && pb && edgesFull.length < 4000) edgesFull.push([pa, pb, s]);
    }
    const rootSize = new Map<string, number>();
    for (const p of viz.scatter) {
      const r = find(p.id);
      rootSize.set(r, (rootSize.get(r) || 0) + 1);
    }
    const rootToLabel = new Map<string, string>();
    let idx = 1;
    for (const [r, n] of Array.from(rootSize.entries())
      .filter(([, n]) => n >= 2)
      .sort((a, b) => b[1] - a[1])) {
      void n;
      rootToLabel.set(r, `LC${String(idx).padStart(3, '0')}`);
      idx++;
    }
    const map = new Map<string, string | null>();
    let singletons = 0;
    for (const p of viz.scatter) {
      const r = find(p.id);
      const label = rootToLabel.get(r) || null;
      map.set(p.id, label);
      if (!label) singletons++;
    }
    // Approximation only when topPairs was truncated by the backend cap.
    const truncated = viz.topPairs.length >= 50000 && (exp.stats.edges ?? 0) > viz.topPairs.length;
    return {
      clusterByRid: map,
      multiCount: rootToLabel.size,
      singletonCount: singletons,
      edgeCount: edgeN,
      edgesToDraw: edgesFull,
      isApproximate: truncated,
    };
  }, [viz.topPairs, viz.scatter, liveThreshold, atRunThreshold, runThreshold, exp.stats.edges]);

  // Draw to canvas whenever clusters, threshold, or hover change.
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext('2d');
    if (!ctx) return;

    // Support high DPI
    const ratio = window.devicePixelRatio || 1;
    c.width = WIDTH * ratio;
    c.height = HEIGHT * ratio;
    c.style.width = WIDTH + 'px';
    c.style.height = HEIGHT + 'px';
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

    ctx.fillStyle = '#0f0f1a';
    ctx.fillRect(0, 0, WIDTH, HEIGHT);

    // Draw edges
    if (showEdges) {
      ctx.globalAlpha = 0.35;
      for (const [pa, pb, s] of edgesToDraw) {
        const sameCluster = clusterByRid.get(pa.id) === clusterByRid.get(pb.id);
        ctx.strokeStyle = _clusterColor(sameCluster ? clusterByRid.get(pa.id) : null);
        ctx.lineWidth = 0.3 + Math.max(0, s - liveThreshold) * 4;
        ctx.beginPath();
        ctx.moveTo(toX(pa.x), toY(pa.y));
        ctx.lineTo(toX(pb.x), toY(pb.y));
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // Draw dots — clustered last so they pop above singletons
    const draw = (p: PlaygroundVizPoint, isHover: boolean) => {
      const cid = clusterByRid.get(p.id);
      const isSel = selectedClusterViz && cid === selectedClusterViz;
      const r = isHover ? 5 : (cid ? 3 : 1.8);
      const op = selectedClusterViz ? (isSel ? 1 : 0.22) : (cid ? 1 : 0.45);
      ctx.globalAlpha = op;
      ctx.fillStyle = _clusterColor(cid);
      ctx.beginPath();
      ctx.arc(toX(p.x), toY(p.y), r, 0, Math.PI * 2);
      ctx.fill();
      if (isSel || isHover) {
        ctx.globalAlpha = 1;
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = isHover ? 1.5 : 1;
        ctx.stroke();
      }
    };
    for (const p of viz.scatter) {
      if (!clusterByRid.get(p.id)) draw(p, p.id === hoverId);
    }
    for (const p of viz.scatter) {
      if (clusterByRid.get(p.id)) draw(p, p.id === hoverId);
    }
    ctx.globalAlpha = 1;
  }, [viz.scatter, clusterByRid, edgesToDraw, hoverId, selectedClusterViz, liveThreshold, showEdges]);

  // Hover + click — nearest-dot lookup on mousemove.
  function handleMouseMove(ev: React.MouseEvent<HTMLCanvasElement>) {
    const rect = ev.currentTarget.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;
    let best: string | null = null;
    let bestD = 36; // 6px radius
    for (const p of viz.scatter) {
      const dx = toX(p.x) - mx;
      const dy = toY(p.y) - my;
      const d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = p.id; }
    }
    setHoverId(best);
    setHoverXY(best ? { x: mx, y: my } : null);
  }

  function handleClick() {
    if (!hoverId) {
      setSelectedClusterViz(null);
      return;
    }
    const cid = clusterByRid.get(hoverId);
    if (!cid) return;
    setSelectedClusterViz((s) => (s === cid ? null : cid));
    const match = exp.clusters.find((c) => c.memberIds.includes(hoverId));
    if (match) onClusterClick(match.id);
  }

  const hoverPoint = hoverId ? viz.scatter.find((p) => p.id === hoverId) : null;

  return (
    <div className="bg-[#0f0f1a] border border-fuchsia-700/40 rounded-lg p-4 mb-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h4 className="text-sm font-bold text-fuchsia-200">📊 Graph View — embedding space</h4>
          <div className="text-[10px] text-gray-500 mt-0.5">
            Each dot is a report; position is a 2D projection (PCA) of its {(exp.config.dimensions as number) || 3072}-dim embedding.
            Same-color dots share a cluster at the current threshold. Drag the threshold slider below to watch clusters form and dissolve live.
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer">
            <input type="checkbox" checked={showEdges} onChange={(e) => setShowEdges(e.target.checked)} />
            edges
          </label>
        </div>
      </div>

      <div className="flex items-center gap-3 mb-3">
        <span className="text-xs text-gray-400 whitespace-nowrap">Threshold:</span>
        <input type="range" min={0.50} max={0.99} step={0.01}
          value={liveThreshold}
          onChange={(e) => setLiveThreshold(parseFloat(e.target.value))}
          className="flex-1 accent-fuchsia-500" />
        <span className="font-mono text-sm text-fuchsia-300 w-14 text-right">{liveThreshold.toFixed(2)}</span>
        <button
          onClick={() => setLiveThreshold(runThreshold)}
          className="text-[10px] text-gray-400 hover:text-white px-2 py-1 bg-[#16213e] rounded border border-[#1e2d50]"
          title={`Reset to the threshold this experiment was run at (${runThreshold.toFixed(2)})`}
        >
          ↺ {runThreshold.toFixed(2)}
        </button>
      </div>

      <div className="flex items-center gap-3 text-xs text-gray-400 mb-2 flex-wrap">
        <span><strong className="text-fuchsia-300">{multiCount}</strong> multi-clusters</span>
        <span>·</span>
        <span><strong className="text-slate-300">{singletonCount}</strong> singletons</span>
        <span>·</span>
        <span><strong className="text-amber-300">{multiCount + singletonCount}</strong> final unique</span>
        <span>·</span>
        <span><strong className="text-white">{edgeCount.toLocaleString()}</strong> edges &#8805; {liveThreshold.toFixed(2)}</span>
        <span>· <strong className="text-gray-300">{viz.scatter.length.toLocaleString()}</strong> dots</span>
        {liveThreshold !== runThreshold && (
          <span className="ml-auto text-[10px] text-amber-400">
            {isApproximate
              ? `⚠ approx · only top ${viz.topPairs.length.toLocaleString()} of ${(exp.stats.edges ?? 0).toLocaleString()} edges sampled`
              : '⚡ live re-cluster (not saved)'}
          </span>
        )}
      </div>

      <div ref={containerRef} className="relative bg-[#0f0f1a] border border-[#1e2d50] rounded overflow-hidden inline-block">
        <canvas
          ref={canvasRef}
          onMouseMove={handleMouseMove}
          onMouseLeave={() => { setHoverId(null); setHoverXY(null); }}
          onClick={handleClick}
          style={{ cursor: hoverPoint && clusterByRid.get(hoverPoint.id) ? 'pointer' : 'default' }}
        />
        {hoverPoint && hoverXY && (
          <div
            className="absolute bg-[#16213e] border border-fuchsia-700 rounded px-2 py-1.5 text-xs pointer-events-none shadow-lg max-w-[320px] z-10"
            style={{
              left: Math.min(WIDTH - 300, hoverXY.x + 10),
              top: Math.min(HEIGHT - 60, hoverXY.y + 10),
            }}
          >
            <div className="text-white font-semibold truncate">{hoverPoint.name || hoverPoint.id.slice(0, 12)}</div>
            <div className="text-[10px] text-gray-400 mt-0.5 flex items-center gap-2">
              <span className="inline-block w-2 h-2 rounded-full" style={{ background: _clusterColor(clusterByRid.get(hoverPoint.id)) }}></span>
              <span>{clusterByRid.get(hoverPoint.id) || '(singleton)'}</span>
              <span>· {hoverPoint.executions.toLocaleString()} execs</span>
            </div>
          </div>
        )}
        {selectedClusterViz && (
          <div className="absolute bottom-2 right-2 bg-fuchsia-900/70 border border-fuchsia-600 rounded px-2 py-1 text-[10px] text-fuchsia-100">
            focused on <strong>{selectedClusterViz}</strong> · click empty area to reset
          </div>
        )}
      </div>

      <div className="mt-3 text-[10px] text-gray-500 leading-relaxed">
        Canvas-rendered so the slider stays smooth even at 5,000+ reports. Dot color = cluster at the
        current threshold. Gray = singletons. The 2D projection (PCA) preserves rough similarity —
        nearby dots embed to similar vectors — but distance in the plot isn't exact cosine. Edges show
        the actual pairwise cosine graph. Click a colored dot to focus its cluster and scroll to it below.
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

  const members = useMemo(() => {
    const list = cluster.memberIds
      .map((id) => reportById.get(id))
      .filter((r): r is ReportDetail => !!r);
    const primary = list.find((r) => r.id === cluster.primaryReportId);
    const rest = list
      .filter((r) => r.id !== cluster.primaryReportId)
      .sort((a, b) => b.executions - a.executions);
    return primary ? [primary, ...rest] : rest;
  }, [cluster, reportById]);

  const sqlCount = useMemo(() => members.filter((m) => !!m.sql).length, [members]);

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
            <div className="text-sm text-gray-400 mt-1 flex items-center gap-3 flex-wrap">
              <span>
                {cluster.size} reports · {cluster.totalExecutions.toLocaleString()} total executions
              </span>
              <span
                className={`text-xs px-2 py-0.5 rounded font-semibold ${
                  sqlCount === members.length
                    ? 'bg-emerald-900/50 text-emerald-300 border border-emerald-700/50'
                    : sqlCount === 0
                    ? 'bg-gray-800/60 text-gray-400 border border-gray-700/50'
                    : 'bg-amber-900/40 text-amber-300 border border-amber-700/50'
                }`}
                title="Count of members with successfully extracted SQL"
              >
                SQL: {sqlCount} / {members.length}
              </span>
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
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-24">Role</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Report Name</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-16">Source</th>
                <th className="text-center p-2 text-xs text-gray-400 uppercase w-16">SQL</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Last Exec</th>
              </tr>
            </thead>
            <tbody>
              {members.map((r) => {
                const isSelected = selectedForCompare.includes(r.id);
                const hasSql = !!r.sql;
                const isPrimary = r.id === cluster.primaryReportId;
                return (
                  <tr
                    key={r.id}
                    onClick={() => toggleSelect(r.id)}
                    className={`border-t border-[#1e2d50] cursor-pointer ${
                      isSelected ? 'bg-blue-900/30'
                        : isPrimary ? 'bg-amber-950/30 hover:bg-amber-900/30'
                        : 'hover:bg-[#1a2a4a]'
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
                      <div className="flex items-center gap-1 flex-wrap">
                        {isPrimary ? (
                          <span className="text-[10px] font-bold text-amber-300 bg-amber-950/50 border border-amber-700/60 rounded px-1.5 py-0.5 whitespace-nowrap"
                            title="Keep-candidate — the primary report of this cluster">
                            ⭐ PRIMARY
                          </span>
                        ) : (
                          <span className="text-[10px] text-gray-500">member</span>
                        )}
                        <FamilySiblingsIndicator siblings={r.familySiblings} />
                      </div>
                    </td>
                    <td className={`p-2 ${isPrimary ? 'text-amber-100 font-semibold' : 'text-white'}`}>{r.name}</td>
                    <td className="p-2">
                      <div className="flex items-center gap-1 flex-wrap">
                        <SourceProjectPill sourceProjectId={r.sourceProjectId} />
                        <SourceTypePill sourceType={r.sourceType} />
                      </div>
                    </td>
                    <td className="p-2 text-center">
                      {hasSql ? (
                        <span
                          className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-300 bg-emerald-900/40 border border-emerald-700/50 px-2 py-0.5 rounded"
                          title="SQL successfully extracted"
                        >
                          {'\u2713'} Yes
                        </span>
                      ) : (
                        <span
                          className="inline-flex items-center gap-1 text-xs font-semibold text-gray-400 bg-gray-800/60 border border-gray-700/50 px-2 py-0.5 rounded"
                          title={r.sqlError || 'SQL not available for this report'}
                        >
                          {'\u2014'} No
                        </span>
                      )}
                    </td>
                    <td className={`p-2 text-right font-mono ${isPrimary ? 'text-amber-200' : 'text-gray-300'}`}>
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
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-16">Source</th>
                <th className="text-center p-2 text-xs text-gray-400 uppercase">SQL</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Last Exec</th>
              </tr>
            </thead>
            <tbody>
              {visibleMembers.length === 0 && (
                <tr>
                  <td colSpan={8} className="p-4 text-center text-xs text-gray-500 italic">
                    No reports match the current SQL filter.
                  </td>
                </tr>
              )}
              {visibleMembers.map((m, i) => {
                const isSelected = selectedForCompare.includes(m.id);
                const rd = reportById.get(m.id);
                const hasSql = !!rd?.sql;
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
                    <td className="p-2"><SourceTypePill sourceType={rd?.sourceType} /></td>
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
                <th className="text-left p-2 text-xs text-gray-400 uppercase w-16">Source</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Path</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Execs</th>
                <th className="text-right p-2 text-xs text-gray-400 uppercase">Users</th>
                <th className="text-left p-2 text-xs text-gray-400 uppercase">Last Exec</th>
              </tr>
            </thead>
            <tbody>
              {group.members.map((m, i) => {
                const isSelected = selectedForCompare.includes(m.id);
                const rd = reportById.get(m.id);
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
                    <td className="p-2"><SourceTypePill sourceType={rd?.sourceType} /></td>
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
  const [srcFilter, setSrcFilter] = useState<'all' | 'normal' | 'cube' | 'custom_sql_free_form' | 'none'>('all');

  const filtered = (data || []).filter((r) => {
    if (kind === 'inventory' && statusFilter !== 'all' && r.status !== statusFilter) return false;
    if (kind === 'retired' && bucketFilter !== 'all' && r.bucket !== bucketFilter) return false;
    if (srcFilter !== 'all') {
      const src = (r.sourceType || '').trim();
      if (srcFilter === 'none' ? src !== '' : src !== srcFilter) return false;
    }
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

  // Counts per source type for the dropdown labels
  const srcCounts = data ? {
    all: data.length,
    normal: data.filter((r) => (r.sourceType || '') === 'normal').length,
    cube: data.filter((r) => (r.sourceType || '') === 'cube').length,
    custom_sql_free_form: data.filter((r) => (r.sourceType || '') === 'custom_sql_free_form').length,
    none: data.filter((r) => (r.sourceType || '').trim() === '').length,
  } : null;

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
            {srcCounts && (
              <select
                value={srcFilter}
                onChange={(e) => setSrcFilter(e.target.value as typeof srcFilter)}
                className="bg-[#0f0f1a] border border-[#1e2d50] rounded-lg px-3 py-2 text-white text-xs focus:outline-none"
                title="Filter by report source type"
              >
                <option value="all">Source: All ({srcCounts.all.toLocaleString()})</option>
                <option value="normal">Source: SQL ({srcCounts.normal.toLocaleString()})</option>
                <option value="cube">Source: Cube ({srcCounts.cube.toLocaleString()})</option>
                <option value="custom_sql_free_form">Source: Free SQL ({srcCounts.custom_sql_free_form.toLocaleString()})</option>
                <option value="none">Source: Unknown ({srcCounts.none.toLocaleString()})</option>
              </select>
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
                    <th className="text-left p-2 text-[10px] text-gray-400 uppercase w-16">Source</th>
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
                      <td className="p-2"><SourceTypePill sourceType={r.sourceType} /></td>
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
  semanticClusters,
  reportById,
  onOpen,
}: {
  reviews: LlmReview[];
  totalCount: number;
  search: string;
  setSearch: (v: string) => void;
  actionFilter: string;
  setActionFilter: (v: string) => void;
  summary: RationalizationSummary | null;
  semanticClusters: SemanticCluster[];
  reportById: Map<string, ReportDetail>;
  onOpen: (r: LlmReview) => void;
}) {
  const byAction = summary?.llmReviewsByAction || {};
  const safeClusters = summary?.llmSafeClusters ?? 0;
  const totalClusters = summary?.llmReviewsTotal ?? 0;
  const totalRemovable = summary?.llmRemovableRaw ?? 0;

  // Cluster lookup so each review can show its member reports. Uses
  // semanticClusters since that's what the LLM reviews were run on. Key by
  // both numeric form (42) and padded form (C0042 / XC0042) to tolerate
  // different id shapes across experiment types.
  const clusterById = useMemo(() => {
    const m = new Map<string, SemanticCluster>();
    for (const c of semanticClusters) {
      if (c.isSingletons) continue;
      m.set(c.id, c);
    }
    return m;
  }, [semanticClusters]);

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggleExpanded = (cid: string) => {
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(cid)) next.delete(cid); else next.add(cid);
      return next;
    });
  };

  return (
    <div className="space-y-3">
      {/* Compact single-line lineage + LLM summary */}
      {summary && (
        <div className="bg-gradient-to-r from-indigo-950/40 to-[#16213e] px-3 py-2 rounded border border-indigo-800/40">
          <div className="flex items-center gap-2 text-xs flex-wrap">
            <span className="text-[10px] uppercase tracking-wider text-indigo-300 font-bold mr-1">Lineage</span>
            <span className="text-gray-400">{summary.totalInventory.toLocaleString()}</span>
            <span className="text-gray-600">→</span>
            <span className="text-gray-400" title="After telemetry retirement">{summary.afterTelemetry?.toLocaleString()}</span>
            <span className="text-gray-600">→</span>
            <span className="text-gray-400" title="After fingerprint dedup">{summary.afterFingerprint?.toLocaleString()}</span>
            <span className="text-gray-600">→</span>
            <span className="text-gray-400" title="After AST dedup">{summary.afterAst?.toLocaleString()}</span>
            <span className="text-gray-600">→</span>
            <span className="text-orange-300" title="Post-family — feeds clustering">{(summary.afterPostAstFamily ?? summary.afterFamily)?.toLocaleString()}</span>
            <span className="text-gray-600">→</span>
            <span className="text-teal-300" title="Multi-clusters formed">{semanticClusters.filter((c) => !c.isSingletons).length.toLocaleString()} clusters</span>
            <span className="text-gray-600">→</span>
            <span className="px-1.5 py-0.5 bg-indigo-900/60 rounded text-indigo-100 font-semibold" title="Clusters GPT has labeled">
              {totalClusters.toLocaleString()} reviewed
            </span>
            {safeClusters > 0 && (
              <span className="px-1.5 py-0.5 bg-green-900/40 rounded text-green-200" title="Clusters actionable without human intervention">
                {safeClusters.toLocaleString()} safe-to-auto
              </span>
            )}
            {totalRemovable > 0 && (
              <span className="px-1.5 py-0.5 bg-red-900/30 rounded text-red-200" title="Total reports flagged as removable across all reviews">
                −{totalRemovable.toLocaleString()} removable
              </span>
            )}
          </div>
        </div>
      )}
      {/* Filter pills (one row, replaces the 4 tall tiles) */}
      <div className="flex items-center gap-2 flex-wrap text-xs">
        <button
          onClick={() => setActionFilter('all')}
          className={`px-2.5 py-1 rounded border transition-colors ${
            actionFilter === 'all'
              ? 'bg-[#1a2a4a] border-blue-500 text-white ring-1 ring-blue-500/50'
              : 'bg-[#0f0f1a] border-[#1e2d50] text-gray-300 hover:bg-[#1a2a4a]'
          }`}
        >
          All <span className="text-gray-500 ml-1">{totalCount}</span>
        </button>
        {Object.entries(LLM_ACTION_STYLES).map(([key, s]) => {
          const count = byAction[key] || 0;
          const selected = actionFilter === key;
          return (
            <button
              key={key}
              onClick={() => setActionFilter(selected ? 'all' : key)}
              className={`px-2.5 py-1 rounded border transition-colors ${
                selected
                  ? `${s.bg} text-white border-transparent ring-1 ring-white/30`
                  : `bg-[#0f0f1a] ${s.border} text-gray-300 hover:bg-[#1a2a4a]`
              }`}
            >
              {s.label} <span className={`${selected ? 'text-white/90' : 'text-gray-500'} ml-1`}>{count}</span>
            </button>
          );
        })}
        <div className="flex-1 min-w-[220px] relative ml-auto">
          <VscSearch className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500 text-xs" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search label, primary, keep_report, business…"
            className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded pl-7 pr-3 py-1.5 text-white text-xs focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      {/* Review list — click a row to expand in place and see every report GPT looked at */}
      <div className="space-y-1.5 max-h-[75vh] overflow-y-auto pr-1">
        {reviews.map((r) => {
          const s = actionStyle(r.consolidation_action);
          const isOpen = expanded.has(r.clusterId);
          const cluster = clusterById.get(r.clusterId);
          const memberIds = cluster?.memberIds ?? [];
          const keepId = cluster?.primaryReportId;
          const members = memberIds
            .map((mid) => reportById.get(mid))
            .filter((x): x is ReportDetail => !!x)
            .sort((a, b) => {
              if (a.id === keepId) return -1;
              if (b.id === keepId) return 1;
              return (b.executions || 0) - (a.executions || 0);
            });
          return (
            <div
              key={r.clusterId}
              className={`bg-[#0f0f1a] rounded border-l-2 ${s.border} text-sm`}
            >
              <div
                className="p-2.5 cursor-pointer hover:bg-[#1a2a4a] flex items-center gap-2"
                onClick={() => toggleExpanded(r.clusterId)}
              >
                <span className="text-gray-500 text-xs w-3">{isOpen ? '▾' : '▸'}</span>
                <span className="font-mono text-xs text-gray-500 w-16 flex-shrink-0">{r.clusterId}</span>
                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${s.bg} text-white flex-shrink-0`}>
                  {s.label}
                </span>
                <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${confidenceStyle(r.confidence)} flex-shrink-0`}>
                  {r.confidence}
                </span>
                <span className="text-white text-sm truncate flex-1" title={r.business_function}>
                  {r.label || <span className="italic text-gray-500">(no label)</span>}
                </span>
                <span className="text-xs text-gray-400 whitespace-nowrap flex-shrink-0">
                  {r.clusterSize} reports
                </span>
                {r.removable_count > 0 && (
                  <span className="text-xs text-red-300 font-semibold whitespace-nowrap flex-shrink-0">
                    {'−'}{r.removable_count}
                  </span>
                )}
              </div>

              {isOpen && (
                <div className="px-3 pb-3 border-t border-[#1e2d50]">
                  <div className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
                    <div className="md:col-span-2">
                      <div className="text-[10px] uppercase text-gray-500 mb-0.5">Business function</div>
                      <div className="text-gray-200">{r.business_function || <span className="italic text-gray-600">{'—'}</span>}</div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase text-gray-500 mb-0.5">Relationship</div>
                      <div className="text-gray-200">{r.relationship || '—'}</div>
                    </div>
                  </div>
                  {r.consolidation_detail && (
                    <div className="mt-2 text-xs">
                      <div className="text-[10px] uppercase text-gray-500 mb-0.5">GPT recommendation</div>
                      <div className="text-gray-300 leading-snug">{r.consolidation_detail}</div>
                    </div>
                  )}

                  <div className="mt-3 flex items-center justify-between">
                    <div className="text-[10px] uppercase text-gray-500">
                      Reports GPT reviewed{' '}
                      <span className="text-gray-400 normal-case">
                        ({members.length} shown{memberIds.length > members.length ? ` · ${memberIds.length - members.length} not in current project view` : ''})
                      </span>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); onOpen(r); }}
                      className="text-[10px] text-blue-300 hover:text-blue-100 border border-blue-800/60 rounded px-2 py-0.5"
                      title="Open full drill-down modal"
                    >
                      Open modal {'→'}
                    </button>
                  </div>

                  {members.length > 0 ? (
                    <div className="mt-1.5 bg-[#0a0a14] border border-[#1e2d50] rounded overflow-hidden">
                      <table className="w-full text-xs">
                        <thead className="bg-[#0f0f1a] text-[10px] uppercase text-gray-500">
                          <tr>
                            <th className="text-left px-2 py-1 w-24">Role</th>
                            <th className="text-left px-2 py-1">Report</th>
                            <th className="text-left px-2 py-1 w-16">Source</th>
                            <th className="text-right px-2 py-1 w-20">Execs</th>
                            <th className="text-right px-2 py-1 w-14">Users</th>
                          </tr>
                        </thead>
                        <tbody>
                          {members.slice(0, 50).map((m) => {
                            const isKeep = m.id === keepId || m.name === r.keep_report;
                            return (
                              <tr
                                key={m.id}
                                className={`border-t border-[#1e2d50] ${isKeep ? 'bg-amber-950/20' : ''}`}
                              >
                                <td className="px-2 py-1">
                                  {isKeep ? (
                                    <span className="text-[10px] font-bold text-amber-300 bg-amber-950/50 border border-amber-700/60 rounded px-1.5 py-0.5 whitespace-nowrap">
                                      {'⭐'} KEEP
                                    </span>
                                  ) : (
                                    <span className="text-[10px] text-red-400 bg-red-950/30 border border-red-800/50 rounded px-1.5 py-0.5 whitespace-nowrap">
                                      {'❌'} remove
                                    </span>
                                  )}
                                </td>
                                <td className="px-2 py-1">
                                  <div className={`truncate ${isKeep ? 'text-amber-100 font-semibold' : 'text-gray-200'}`} title={m.id}>
                                    {m.name}
                                  </div>
                                  {m.path && (
                                    <div className="text-[10px] text-gray-600 truncate">{m.path}</div>
                                  )}
                                </td>
                                <td className="px-2 py-1"><SourceTypePill sourceType={m.sourceType} /></td>
                                <td className={`px-2 py-1 text-right font-mono ${isKeep ? 'text-amber-200' : 'text-gray-400'}`}>
                                  {(m.executions ?? 0).toLocaleString()}
                                </td>
                                <td className="px-2 py-1 text-right font-mono text-gray-500">
                                  {m.users ?? 0}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                      {members.length > 50 && (
                        <div className="text-[10px] text-gray-500 text-center py-1 border-t border-[#1e2d50]">
                          Showing first 50 of {members.length} members.
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="mt-1.5 text-[11px] text-amber-400 italic">
                      Member reports aren't in the current project's reports.json view (e.g., retired or filtered out).
                      Open the full drill-down modal to see them anyway.
                    </div>
                  )}
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
